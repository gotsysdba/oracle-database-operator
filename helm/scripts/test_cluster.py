"""Verify chart installation, upgrades, admission, conversion, and CRD retention.

Requires Python 3.10+, Helm, and kubectl on PATH, plus access to an explicitly
selected disposable cluster supported by Chart.yaml with healthy cert-manager. Oracle
CRDs and the requested test namespaces must be absent before starting.

Creates releases and namespaces; database admission uses server dry-runs. A
successful run removes its releases, CRDs, and namespaces. Failures and --keep
retain resources for inspection. Failed checks exit nonzero.

Usage from the repository root:
    python3 helm/scripts/test_cluster.py --context kind-helm-test
    python3 helm/scripts/test_cluster.py --context kind-helm-test --second-namespace helm-chart-test-secondary
    python3 helm/scripts/test_cluster.py --context kind-helm-test --values helm/examples/small.yaml --keep

--second-namespace tests two independent releases sharing a conversion provider.
Repeat --values to layer image, resource, or scheduling overrides.
"""

import argparse
import json
from pathlib import Path
import subprocess
import time


CHART = Path(__file__).resolve().parents[1]
NAME = 'oracle-database-operator'


def run(*args, input=None, check=True):
    return subprocess.run(list(map(str, args)), input=input, text=True, capture_output=True, check=check)


def wait_for(check, description, timeout=180):
    deadline = time.monotonic() + timeout
    last_error = None
    while time.monotonic() < deadline:
        try:
            if check():
                return
        except (subprocess.CalledProcessError, KeyError) as error:
            last_error = error
        time.sleep(2)
    raise RuntimeError(f'timed out waiting for {description}: {last_error}')


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--context', required=True, help='disposable Kubernetes context with cert-manager installed')
    parser.add_argument('--namespace', default='helm-chart-test', help='new namespace for this test')
    parser.add_argument('--second-namespace', help='also test an independent release in this new namespace')
    parser.add_argument('--values', type=Path, action='append', default=[])
    parser.add_argument('--keep', action='store_true', help='retain successful installation for inspection')
    args = parser.parse_args()
    kubectl = ['kubectl', '--context', args.context]
    helm = ['helm', '--kube-context', args.context]
    release = 'oraoperator'

    version = json.loads(run(*kubectl, 'version', '-o', 'json').stdout)['serverVersion']
    run(*helm, 'template', release, CHART, '--kube-version', version['gitVersion'])
    existing = json.loads(run(*kubectl, 'get', 'crds', '-o', 'json').stdout)['items']
    names = {path.stem for path in (CHART / 'crds').glob('*.yaml')}
    if any(crd['metadata']['name'] in names for crd in existing):
        parser.error('use a disposable cluster with no existing Oracle CRDs')
    run(*kubectl, 'get', 'crd', 'certificates.cert-manager.io', 'issuers.cert-manager.io')
    namespaces = [args.namespace]
    if args.second_namespace:
        if args.second_namespace == args.namespace:
            parser.error('the two releases need separate installation namespaces')
        namespaces.append(args.second_namespace)
    for namespace in namespaces:
        if run(*kubectl, 'get', 'namespace', namespace, '--ignore-not-found', '-o', 'name').stdout:
            parser.error(f'use a new namespace; {namespace} already exists')

    flags = ['--wait', '--timeout', '5m']
    for values in args.values:
        flags += ['--values', str(values.resolve())]

    def release_flags(namespace):
        owner = namespace == args.namespace
        return [*flags, '--namespace', namespace,
                '--set', 'scope.mode=namespace', '--set', f'scope.watchNamespaces={{{namespace}}}',
                '--set', f'crdConfiguration.enabled={str(owner).lower()}',
                *([] if owner else ['--skip-crds'])]

    # Server dry-runs exercise admission without provisioning a database.
    def admission_ready(namespace, version):
        valid = {'apiVersion': f'database.oracle.com/{version}', 'kind': 'AutonomousDatabase',
                 'metadata': {'name': 'helm-admission-check', 'namespace': namespace},
                 'spec': {'action': 'Sync', 'details': {'id': 'ocid1.autonomousdatabase.oc1..helmtest'}}}
        run(*kubectl, 'create', '--dry-run=server', '-f', '-', input=json.dumps(valid))
        return True

    def verify(namespace):
        run(*kubectl, '-n', namespace, 'wait', 'certificate', '--all', '--for=condition=Ready', '--timeout=180s')
        for kind in ('mutatingwebhookconfiguration', 'validatingwebhookconfiguration'):
            def injected():
                configurations = json.loads(run(*kubectl, 'get', kind, '-l',
                                                f'app.kubernetes.io/instance={release}', '-o', 'json').stdout)['items']
                matching = [config for config in configurations if config['webhooks'] and all(
                    hook['clientConfig']['service']['namespace'] == namespace for hook in config['webhooks'])]
                return len(matching) == 1 and all(hook['clientConfig'].get('caBundle') for hook in matching[0]['webhooks'])
            wait_for(injected, f'{namespace} {kind} CA injection')
        for version in ('v4', 'v1alpha1'):
            wait_for(lambda: admission_ready(namespace, version), f'{namespace} {version} admission and conversion')
        rejected = {'apiVersion': 'observability.oracle.com/v4', 'kind': 'DatabaseObserver',
                    'metadata': {'name': 'helm-invalid-check', 'namespace': namespace},
                    'spec': {'database': {'oci': {'vaultID': 'helm-test'}}}}
        response = run(*kubectl, 'create', '--dry-run=server', '-f', '-', input=json.dumps(rejected), check=False)
        expected = 'a field for configuring the vault has a value but the other required field(s) is missing or does not have a value'
        if response.returncode == 0 or expected not in response.stderr:
            raise RuntimeError(f'expected incomplete vault rejection from the webhook: {response.stdout}{response.stderr}')

    def verify_crds():
        crds = json.loads(run(*kubectl, 'get', 'crds', *sorted(names), '-o', 'json').stdout)['items']
        for crd in crds:
            name = crd['metadata']['name']
            if crd['metadata']['annotations']['cert-manager.io/inject-ca-from'] != f'{args.namespace}/{NAME}-serving-cert':
                raise RuntimeError(f'incorrect CA source on {name}')
            if crd['spec'].get('conversion', {}).get('strategy') == 'Webhook':
                if crd['spec']['conversion']['webhook']['clientConfig']['service']['namespace'] != args.namespace:
                    raise RuntimeError(f'incorrect conversion namespace on {name}')

    installed = []
    for namespace in namespaces:
        print(f'Installing chart in {namespace}', flush=True)
        run(*helm, 'install', release, CHART, '--create-namespace', *release_flags(namespace))
        installed.append(namespace)
        for active in installed:
            verify(active)
        verify_crds()
    for namespace in namespaces:
        print(f'Upgrading chart in {namespace}', flush=True)
        run(*helm, 'upgrade', release, CHART, *release_flags(namespace), '--set', 'podAnnotations.helm-test-revision=upgrade')
        for active in installed:
            verify(active)
        verify_crds()
    if not args.keep:
        for namespace in reversed(namespaces):
            run(*helm, 'uninstall', release, '-n', namespace, '--wait')
            installed.remove(namespace)
            for active in installed:
                verify(active)
            verify_crds()
        retained = json.loads(run(*kubectl, 'get', 'crds', '-o', 'json').stdout)['items']
        if not names.issubset({crd['metadata']['name'] for crd in retained}):
            raise RuntimeError('native CRDs were removed during uninstall')
        run(*kubectl, 'delete', '-f', CHART / 'crds')
        run(*kubectl, 'delete', 'namespace', *namespaces)
    print('Install, upgrade, admission, and CRD lifecycle checks passed.', flush=True)


if __name__ == '__main__':
    try:
        main()
    except subprocess.CalledProcessError as error:
        raise SystemExit(error.stderr or error.stdout) from error
