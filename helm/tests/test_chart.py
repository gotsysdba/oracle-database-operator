from pathlib import Path
import json
import re
import shutil
import subprocess
import tarfile
import tempfile
import unittest


CHART = Path(__file__).resolve().parents[1]


def render(*args):
    return subprocess.check_output([
        'helm', 'template', 'oraoperator', str(CHART),
        '--namespace', 'oracle-database-operator-system', *args,
    ], text=True)


class ChartTests(unittest.TestCase):
    def test_certificate_resources_are_persistent(self):
        for template, kind in (
            ('issuer', 'Issuer'),
            ('certificate', 'Certificate'),
            ('metrics-certificate', 'Certificate'),
        ):
            with self.subTest(template=template):
                rendered = render('--show-only', f'templates/{template}.yaml')
                self.assertIn(f'kind: {kind}\n', rendered)
                self.assertIn('namespace: oracle-database-operator-system\n', rendered)
                self.assertNotIn('helm.sh/hook', rendered)
                self.assertNotIn('ownerReferences:', rendered)

    def test_custom_certificate_secret(self):
        args = ('--set', 'webhook.certificateSecretName=custom-tls')
        certificate = render('--show-only', 'templates/certificate.yaml', *args)
        self.assertIn('namespace: oracle-database-operator-system\n', certificate)
        self.assertIn('secretName: custom-tls\n', certificate)
        self.assertIn('oracle-database-operator-webhook-service.oracle-database-operator-system.svc\n', certificate)
        self.assertIn('oracle-database-operator-webhook-service.oracle-database-operator-system.svc.cluster.local\n', certificate)
        self.assertIn('name: oracle-database-operator-selfsigned-issuer\n', certificate)
        issuer = render('--show-only', 'templates/issuer.yaml', *args)
        self.assertIn('namespace: oracle-database-operator-system\n', issuer)
        self.assertIn('name: oracle-database-operator-selfsigned-issuer\n', issuer)
        deployment = render('--show-only', 'templates/deployment.yaml', *args)
        self.assertIn('secretName: custom-tls\n', deployment)

    def test_metrics_certificate_is_mounted(self):
        certificate = render('--show-only', 'templates/metrics-certificate.yaml')
        self.assertIn('name: oracle-database-operator-metrics-certs\n', certificate)
        self.assertIn('secretName: metrics-server-cert\n', certificate)
        self.assertIn('oracle-database-operator-controller-manager-metrics-service.oracle-database-operator-system.svc\n', certificate)
        self.assertIn('oracle-database-operator-controller-manager-metrics-service.oracle-database-operator-system.svc.cluster.local\n', certificate)

        deployment = render('--show-only', 'templates/deployment.yaml')
        self.assertIn('mountPath: /metrics-certs\n          name: metrics-certs\n', deployment)
        self.assertIn('name: metrics-certs\n        secret:\n          defaultMode: 420\n          secretName: metrics-server-cert\n', deployment)

    def test_manager_can_manage_serviceaccounts(self):
        rendered = render('--show-only', 'templates/clusterrole.yaml')
        self.assertIn('  - serviceaccounts\n', rendered)

    def test_chart_uses_external_cert_manager_without_bootstrap(self):
        rendered = render()
        self.assertEqual(rendered.count('\nkind: Issuer\n'), 1)
        self.assertEqual(rendered.count('\nkind: Certificate\n'), 2)
        self.assertEqual(rendered.count('\nkind: Job\n'), 1)
        self.assertNotIn('cert-bootstrap', rendered)
        self.assertNotIn('certificate_bootstrap.py', rendered)
        self.assertNotIn('/charts/cert-manager/', rendered)

    def test_admission_webhooks_are_persistent(self):
        for name in ('mutatingwebhookconfiguration', 'validatingwebhookconfiguration'):
            with self.subTest(template=name):
                rendered = render('--show-only', f'templates/{name}.yaml')
                self.assertNotIn('helm.sh/hook', rendered)
                self.assertIn('cert-manager.io/inject-ca-from:', rendered)

    def test_single_instance_validator_uses_registered_v4_endpoint(self):
        rendered = render('--show-only', 'templates/validatingwebhookconfiguration.yaml')
        self.assertNotIn('/validate-database-oracle-com-v1alpha1-singleinstancedatabase', rendered)
        self.assertNotIn('name: vsingleinstancedatabase.kb.io', rendered)
        self.assertEqual(rendered.count('/validate-database-oracle-com-v4-singleinstancedatabase'), 1)

    def test_crds_use_native_helm_directory(self):
        self.assertEqual(len(list((CHART / 'crds').glob('*.yaml'))), 17)
        self.assertNotIn('kind: CustomResourceDefinition\n', render())
        rendered = render('--include-crds')
        self.assertEqual(rendered.count('kind: CustomResourceDefinition\n'), 17)
        self.assertNotIn('{{', rendered)

    def test_crd_metadata_and_conversion_match_supplied_manifests(self):
        for manifest in ('oracle-database-operator.yaml', 'oracle-database-operator-system.yaml'):
            documents = re.split(r'^---\s*$', (CHART.parent / manifest).read_text(), flags=re.MULTILINE)
            headers = {}
            for document in documents:
                if '\nkind: CustomResourceDefinition\n' in document:
                    header = document.strip().split('  group:', 1)[0]
                    name = re.search(r'^  name: (.+)$', header, re.MULTILINE).group(1)
                    headers[name] = header
            self.assertEqual(len(headers), 17)
            for path in (CHART / 'crds').glob('*.yaml'):
                with self.subTest(manifest=manifest, crd=path.name):
                    header = path.read_text().removeprefix('---\n').split('  group:', 1)[0]
                    self.assertEqual(header, headers[path.stem])

    def test_incompatible_operator_overrides_fail(self):
        for setting, message in (
            ('namespace=custom', 'use --namespace'),
            ('nameOverride=custom', 'bundled CRD webhook references'),
        ):
            with self.subTest(setting=setting):
                result = subprocess.run([
                    'helm', 'template', 'oraoperator', str(CHART), '--set', setting,
                ], capture_output=True, text=True)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn(message, result.stderr)

    def test_release_and_watch_namespaces_are_independent(self):
        rendered = render('--namespace', 'platform',
                          '--set', 'scope.mode=namespace',
                          '--set', 'scope.watchNamespaces={databases}')
        self.assertIn('namespace: platform\n', rendered)
        self.assertNotIn('oracle-database-operator-system', rendered)
        self.assertIn('namespace: databases\n', rendered)
        self.assertIn('name: WATCH_NAMESPACE\n          value: "databases"', rendered)
        self.assertNotIn('kind: Namespace\n', rendered)

    def test_custom_namespace_certificates_and_webhooks(self):
        rendered = render('--namespace', 'platform')
        for service in ('webhook', 'controller-manager-metrics'):
            self.assertIn(f'oracle-database-operator-{service}-service.platform.svc\n', rendered)
            self.assertIn(f'oracle-database-operator-{service}-service.platform.svc.cluster.local\n', rendered)
        self.assertIn('cert-manager.io/inject-ca-from: platform/oracle-database-operator-serving-cert', rendered)
        self.assertNotIn('oracle-database-operator-system', rendered)

    def test_crd_configuration_uses_operator_scheduling(self):
        settings = {
            'nodeSelector': {'workload': 'oracle'},
            'tolerations': [{'key': 'dedicated', 'operator': 'Equal',
                             'value': 'oracle', 'effect': 'NoSchedule'}],
            'affinity': {'nodeAffinity': {'requiredDuringSchedulingIgnoredDuringExecution': {
                'nodeSelectorTerms': [{'matchExpressions': [
                    {'key': 'workload', 'operator': 'In', 'values': ['oracle']},
                ]}],
            }}},
        }
        args = []
        for key, value in settings.items():
            args.extend(['--set-json', f'{key}={json.dumps(value)}'])
        deployment = render('--show-only', 'templates/deployment.yaml', *args)
        job = render('--show-only', 'templates/crd-configuration.yaml', *args)
        for key in settings:
            pattern = rf'^      {key}:\n(?:^        .*\n)+'
            expected = re.search(pattern, deployment, re.MULTILINE)
            self.assertIsNotNone(expected, key)
            self.assertIn(expected.group(), job)
        defaults = render('--show-only', 'templates/crd-configuration.yaml')
        for key in settings:
            self.assertNotIn(f'      {key}:', defaults)

    def test_crd_configuration_patches_only_references(self):
        rendered = render('--namespace', 'platform', '--show-only',
                          'templates/crd-configuration.yaml')
        self.assertIn('helm.sh/hook: post-install,post-upgrade', rendered)
        self.assertIn('helm.sh/hook-delete-policy: before-hook-creation,hook-succeeded', rendered)
        self.assertIn('verbs: [get, patch]', rendered)
        self.assertNotIn('/bin/sh', rendered)
        # A request-timeout override prevents kubectl's in-cluster fallback.
        self.assertNotIn('--request-timeout', rendered)
        self.assertIn('activeDeadlineSeconds: 180', rendered)
        blocks = re.findall(r'^        args:\n((?:^        - .*\n)+)', rendered, re.MULTILINE)
        self.assertEqual(len(blocks), 2)
        commands = []
        for block in blocks:
            args = [line.removeprefix('        - ') for line in block.splitlines()]
            args = [json.loads(arg) if arg.startswith('"') else arg for arg in args]
            self.assertEqual(args[0], 'patch')
            self.assertIn('--type=merge', args)
            commands.append((args[1:-3], json.loads(args[-1])))
        names = {f'customresourcedefinition/{path.stem}' for path in (CHART / 'crds').glob('*.yaml')}
        self.assertEqual(set(commands[0][0]), names)
        self.assertEqual(commands[0][1], {'metadata': {'annotations': {
            'cert-manager.io/inject-ca-from': 'platform/oracle-database-operator-serving-cert',
        }}})
        conversion_names = {f'customresourcedefinition/{path.stem}'
                            for path in (CHART / 'crds').glob('*.yaml')
                            if 'strategy: Webhook' in path.read_text()}
        self.assertEqual(set(commands[1][0]), conversion_names)
        self.assertEqual(commands[1][1], {'spec': {'conversion': {'webhook': {
            'clientConfig': {'service': {
                'name': 'oracle-database-operator-webhook-service', 'namespace': 'platform',
            }},
        }}}})

        if shutil.which('kubectl'):
            for path in (CHART / 'crds').glob('*.yaml'):
                original = json.loads(subprocess.check_output([
                    'kubectl', 'patch', '--local', '--type=merge', '--patch', '{}',
                    '-f', str(path), '-o', 'json',
                ], text=True))
                patched = original
                for targets, patch in commands:
                    if f'customresourcedefinition/{path.stem}' in targets:
                        patched = json.loads(subprocess.check_output([
                            'kubectl', 'patch', '--local', '-f', '-', '--type=merge',
                            '--patch', json.dumps(patch), '-o', 'json',
                        ], input=json.dumps(patched), text=True))
                original['metadata']['annotations']['cert-manager.io/inject-ca-from'] = (
                    'platform/oracle-database-operator-serving-cert')
                if f'customresourcedefinition/{path.stem}' in conversion_names:
                    original['spec']['conversion']['webhook']['clientConfig']['service']['namespace'] = 'platform'
                self.assertEqual(patched, original, path.name)

    def test_watching_release_namespace_has_one_manager_binding(self):
        rendered = render('--namespace', 'platform', '--set', 'scope.mode=namespace',
                          '--set', 'scope.watchNamespaces={platform,databases}',
                          '--show-only', 'templates/rolebinding.yaml')
        self.assertEqual(rendered.count('name: oracle-database-operator-manager-rolebinding\n'), 2)

    def test_default_release_namespace(self):
        for args in ([], ['--namespace', 'default'], ['--is-upgrade'],
                     ['--is-upgrade', '--set', 'namespace=oracle-database-operator-system']):
            with self.subTest(args=args):
                rendered = subprocess.check_output([
                    'helm', 'template', 'oraoperator', str(CHART), *args,
                ], text=True)
                self.assertIn('namespace: oracle-database-operator-system\n', rendered)
                self.assertNotIn('namespace: default\n', rendered)
                self.assertIn('kind: Namespace\nmetadata:\n  name: oracle-database-operator-system\n', rendered)
                self.assertIn('helm.sh/resource-policy: keep', rendered)
                self.assertIn('oracle-database-operator-webhook-service.oracle-database-operator-system.svc\n', rendered)

    def test_package_and_umbrella_expose_native_crds(self):
        with tempfile.TemporaryDirectory() as directory:
            temporary = Path(directory)
            subprocess.check_output(['helm', 'package', str(CHART), '--destination', directory], text=True)
            package = next(temporary.glob('*.tgz'))
            with tarfile.open(package) as archive:
                names = archive.getnames()
            self.assertEqual(sum('/crds/' in name and name.endswith('.yaml') for name in names), 17)
            self.assertFalse(any('/templates/crds/' in name for name in names))
            umbrella = temporary / 'platform'
            umbrella.mkdir()
            (umbrella / 'Chart.yaml').write_text('apiVersion: v2\nname: platform\nversion: 1.0.0\n')
            charts = umbrella / 'charts'
            charts.mkdir()
            shutil.copy2(package, charts / package.name)
            for chart in (package, umbrella):
                crds = subprocess.check_output(['helm', 'show', 'crds', str(chart)], text=True)
                self.assertEqual(crds.count('kind: CustomResourceDefinition\n'), 17)


if __name__ == '__main__':
    unittest.main()
