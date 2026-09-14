from copy import deepcopy
from functools import lru_cache
import base64
import itertools
import json
from pathlib import Path
import subprocess
import sys
import tarfile
import tempfile
import unittest


CHART = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(CHART / 'scripts'))
from manifest_utils import decode
from sync_manifests import generated_files

NAME = 'oracle-database-operator'
KUBE_VERSION = '1.36.3'


def helm(*args, **kwargs):
    return subprocess.check_output(['helm', *map(str, args)], text=True, **kwargs)


@lru_cache(maxsize=None)
def render(*args, release='oraoperator'):
    return decode(helm('template', release, CHART, '--namespace', 'platform',
                       '--kube-version', KUBE_VERSION, *args))


def objects(documents, kind):
    return [doc for doc in documents if doc['kind'] == kind]


def one(documents, kind):
    result = objects(documents, kind)
    if len(result) != 1:
        raise AssertionError(f'expected one {kind}, found {len(result)}')
    return result[0]


def pod(documents):
    return one(documents, 'Deployment')['spec']['template']['spec']


def permissions(role):
    return {(group, resource, verb)
            for rule in role['rules'] for group in rule.get('apiGroups', [])
            for resource in rule.get('resources', []) for verb in rule['verbs']}


class ChartTests(unittest.TestCase):
    def test_oci_credentials_match_operator_contract(self):
        self.assertEqual(objects(render(), 'ConfigMap'), [])
        self.assertEqual(objects(render(), 'Secret'), [])
        credentials = {
            'namespace': 'databases', 'tenancy': 'test-tenancy', 'user': 'test-user',
            'fingerprint': 'test-fingerprint', 'region': 'uk-london-1',
        }
        key = '-----BEGIN PRIVATE KEY-----\nchart-test-only\n-----END PRIVATE KEY-----\n'
        with tempfile.TemporaryDirectory() as directory:
            key_file = Path(directory) / 'key.pem'
            key_file.write_text(key)
            docs = render('--set-json', f'ociCredentials={json.dumps([credentials])}',
                          '--set-file', f'ociCredentials[0].privateKey={key_file}',
                          '--set', 'namespaceOverride=operators',
                          '--set', 'scope.mode=namespace,scope.watchNamespaces={databases}')
        config = one(docs, 'ConfigMap')
        secret = one(docs, 'Secret')
        self.assertEqual(config['metadata']['name'], 'oci-cred')
        self.assertEqual(config['data'], {k: v for k, v in credentials.items() if k != 'namespace'})
        self.assertEqual(secret['metadata']['name'], 'oci-privatekey')
        self.assertEqual(secret['type'], 'Opaque')
        self.assertEqual(set(secret['data']), {'privatekey'})
        self.assertEqual(base64.b64decode(secret['data']['privatekey']).decode(), key)
        for resource in (config, secret):
            self.assertEqual(resource['metadata']['namespace'], 'databases')
            self.assertEqual(resource['metadata']['labels']['app.kubernetes.io/instance'], 'oraoperator')
            self.assertNotIn('helm.sh/hook', resource['metadata'].get('annotations', {}))

    def test_oci_profiles_and_external_private_key(self):
        first = {'namespace': 'databases', 'configMapName': 'oci-profile', 'secretName': 'oci-key',
                 'tenancy': 'first-tenancy', 'user': 'first-user', 'region': 'uk-london-1',
                 'fingerprint': 'first-fingerprint', 'passphrase': 'test-passphrase', 'privateKey': 'test-key'}
        second = {**first, 'namespace': 'reporting', 'tenancy': 'second-tenancy'}
        external = {**first, 'configMapName': 'oci-external', 'existingSecretName': 'external-key'}
        del external['privateKey'], external['secretName'], external['passphrase']
        docs = render('--set-json', f'ociCredentials={json.dumps([first, second, external])}')
        configs = {(c['metadata']['namespace'], c['metadata']['name']): c['data']
                   for c in objects(docs, 'ConfigMap')}
        self.assertEqual(len(configs), 3)
        self.assertEqual(configs['databases', 'oci-profile']['passphrase'], 'test-passphrase')
        self.assertEqual(configs['reporting', 'oci-profile']['tenancy'], 'second-tenancy')
        self.assertNotIn('passphrase', configs['databases', 'oci-external'])
        self.assertEqual({(s['metadata']['namespace'], s['metadata']['name']) for s in objects(docs, 'Secret')},
                         {('databases', 'oci-key'), ('reporting', 'oci-key')})
        self.assertEqual(one(docs, 'Deployment'), one(render(), 'Deployment'))

    def test_invalid_oci_credentials_fail_validation(self):
        valid = {'namespace': 'databases', 'tenancy': 'test', 'user': 'test',
                 'region': 'test', 'fingerprint': 'test', 'privateKey': 'test-key'}
        invalid = [[{k: v for k, v in valid.items() if k != required}]
                   for required in valid]
        invalid += [[{**valid, field: value}] for field, value in (
            ('namespace', 'Invalid'), ('configMapName', ''), ('secretName', 'invalid/name'),
            ('privateKey', ''), ('privateKey', '  '), ('tenancy', 123), ('region', ''),
            ('existingSecretName', 'external'), ('unknown', 'value'))]
        external = {k: v for k, v in valid.items() if k != 'privateKey'}
        invalid += [[{**external, 'existingSecretName': ''}],
                    [{**external, 'existingSecretName': 'external', 'secretName': 'unused'}]]
        for credentials in invalid:
            with self.subTest(credentials=credentials):
                result = subprocess.run(['helm', 'lint', str(CHART), '--strict', '--kube-version', KUBE_VERSION,
                                         '--set-json', f'ociCredentials={json.dumps(credentials)}'], capture_output=True, text=True)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn('schema', result.stdout + result.stderr)
        for credentials, settings, message in (
            ([valid, valid], [], 'duplicate resource ConfigMap/databases/oci-cred'),
            ([valid, {**valid, 'configMapName': 'another'}], [], 'duplicate resource Secret/databases/oci-privatekey'),
            ([valid], ['--set', 'scope.mode=namespace,scope.watchNamespaces={other}'], 'must be in scope.watchNamespaces'),
        ):
            with self.subTest(message=message):
                result = subprocess.run(['helm', 'template', 'review', str(CHART), '--kube-version', KUBE_VERSION,
                                         '--set-json', f'ociCredentials={json.dumps(credentials)}', *settings], capture_output=True, text=True)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn(message, result.stderr)

    def test_dedicated_service_accounts_and_bindings(self):
        docs = render()
        accounts = {doc['metadata']['name'] for doc in objects(docs, 'ServiceAccount')}
        self.assertEqual(accounts, {NAME, NAME + '-crd-configuration'})
        self.assertEqual(pod(docs)['serviceAccountName'], NAME)
        for binding in objects(docs, 'RoleBinding') + objects(docs, 'ClusterRoleBinding'):
            for subject in binding['subjects']:
                self.assertIn(subject['name'], accounts)
                self.assertEqual(subject['namespace'], 'platform')

    def test_existing_accounts_are_used_without_creation(self):
        docs = render('--set', 'serviceAccount.create=false,serviceAccount.name=external-manager',
                      '--set', 'crdConfiguration.serviceAccount.create=false',
                      '--set', 'crdConfiguration.serviceAccount.name=external-hook')
        self.assertEqual(objects(docs, 'ServiceAccount'), [])
        self.assertEqual(pod(docs)['serviceAccountName'], 'external-manager')
        self.assertEqual(one(docs, 'Job')['spec']['template']['spec']['serviceAccountName'], 'external-hook')
        for binding in objects(docs, 'RoleBinding') + objects(docs, 'ClusterRoleBinding'):
            expected = 'external-hook' if binding['metadata']['name'].endswith('-crd-configuration') else 'external-manager'
            self.assertEqual(binding['subjects'][0]['name'], expected)

    def test_external_rbac_is_independent_of_service_account_creation(self):
        docs = render('--set', 'rbac.create=false,rbac.nodeAccess=true')
        for kind in ('Role', 'ClusterRole', 'RoleBinding', 'ClusterRoleBinding'):
            self.assertEqual(objects(docs, kind), [])
        self.assertEqual(len(objects(docs, 'ServiceAccount')), 2)
        self.assertEqual(len(objects(docs, 'Job')), 1)

    def test_scope_and_cluster_permissions(self):
        for mode, cluster_resources, nodes in itertools.product(('cluster', 'namespace'), (True, False), (True, False)):
            with self.subTest(mode=mode, cluster_resources=cluster_resources, nodes=nodes):
                args = ['--set', f'scope.mode={mode}', '--set', f'rbac.clusterResources={str(cluster_resources).lower()}',
                        '--set', f'rbac.nodeAccess={str(nodes).lower()}']
                if mode == 'namespace':
                    args += ['--set', 'scope.watchNamespaces={databases,platform}']
                docs = render(*args)
                roles = {role['metadata']['name']: role for role in objects(docs, 'ClusterRole')}
                bound = set()
                for binding in objects(docs, 'ClusterRoleBinding'):
                    if binding['subjects'][0]['name'] == NAME:
                        bound |= permissions(roles[binding['roleRef']['name']])
                self.assertEqual(('', 'persistentvolumes', 'delete') in bound, cluster_resources)
                self.assertEqual(('storage.k8s.io', 'storageclasses', 'get') in bound, cluster_resources)
                self.assertEqual(('', 'nodes', 'list') in bound, nodes)
                self.assertNotIn(('', 'namespaces', 'delete'), bound)
                self.assertNotIn(('coordination.k8s.io', 'leases', 'create'), bound)
                self.assertFalse(any(resource == 'leases' for role in roles.values()
                                     for _, resource, _ in permissions(role)))
                self.assertIn(('coordination.k8s.io', 'leases', 'create'), permissions(one(docs, 'Role')))
                self.assertEqual(('', 'secrets', 'get') in bound, mode == 'cluster')
                manager_bindings = [doc for doc in objects(docs, 'RoleBinding')
                                    if doc['roleRef']['name'].endswith('-manager-role')]
                self.assertEqual({doc['metadata']['namespace'] for doc in manager_bindings},
                                 {'databases', 'platform'} if mode == 'namespace' else set())
                self.assertEqual(pod(docs)['containers'][0]['env'][0],
                                 {'name': 'WATCH_NAMESPACE', 'value': 'databases,platform' if mode == 'namespace' else ''})

    def test_manager_can_manage_serviceaccounts(self):
        role = next(doc for doc in objects(render(), 'ClusterRole') if doc['metadata']['name'].endswith('-manager-role'))
        self.assertIn(('', 'serviceaccounts', 'create'), permissions(role))

    def test_releases_have_independent_resources_and_references(self):
        identities = []
        for namespace, watched, owner in [('operator-a', 'databases-a', True), ('operator-b', 'databases-b', False)]:
            docs = render('--namespace', namespace, '--set', 'scope.mode=namespace,rbac.nodeAccess=true',
                          '--set', f'scope.watchNamespaces={{{watched}}}',
                          '--set', f'crdConfiguration.enabled={str(owner).lower()}')
            keys = {(doc['kind'], doc['metadata'].get('namespace', ''), doc['metadata']['name']) for doc in docs}
            self.assertEqual(len(keys), len(docs))
            identities.append(keys)
            for binding in objects(docs, 'RoleBinding') + objects(docs, 'ClusterRoleBinding'):
                ref = binding['roleRef']
                role_namespace = binding['metadata']['namespace'] if ref['kind'] == 'Role' else ''
                self.assertIn((ref['kind'], role_namespace, ref['name']), keys)
                for subject in binding['subjects']:
                    self.assertEqual(subject['namespace'], namespace)
                    self.assertIn(('ServiceAccount', namespace, subject['name']), keys)
            for kind in ('MutatingWebhookConfiguration', 'ValidatingWebhookConfiguration'):
                configuration = one(docs, kind)
                self.assertEqual(configuration['metadata']['annotations']['cert-manager.io/inject-ca-from'],
                                 f'{namespace}/{NAME}-serving-cert')
                for webhook in configuration['webhooks']:
                    service = webhook['clientConfig']['service']
                    self.assertEqual(service['namespace'], namespace)
                    self.assertIn(('Service', namespace, service['name']), keys)
                    self.assertEqual(webhook['namespaceSelector'], {'matchExpressions': [
                        {'key': 'kubernetes.io/metadata.name', 'operator': 'In', 'values': [watched]}]})
            self.assertEqual(len(objects(docs, 'Job')), int(owner))
        self.assertFalse(identities[0] & identities[1])

    def test_cluster_resource_names_include_namespace_and_release(self):
        identities = []
        for namespace, release in [('operator-a', 'oraoperator'), ('operator-b', 'oraoperator'),
                                   ('operator-a', 'another'), ('a-b', 'c'), ('a', 'b-c'), ('n' * 63, 'r' * 53)]:
            docs = render('--namespace', namespace, '--set', 'rbac.nodeAccess=true', release=release)
            names = {(doc['kind'], doc['metadata']['name']) for doc in docs if 'namespace' not in doc['metadata']}
            for _, name in names:
                self.assertLessEqual(len(name), 253)
                self.assertRegex(name, r'^[a-z0-9]([-a-z0-9]*[a-z0-9])?$')
            for previous in identities:
                self.assertFalse(names & previous)
            identities.append(names)

    def test_admission_scope_matches_watch_namespaces(self):
        for mode, watched in [('cluster', []), ('namespace', ['databases', 'reporting'])]:
            settings = ['--set', f'scope.mode={mode}']
            if watched:
                settings += ['--set-json', f'scope.watchNamespaces={json.dumps(watched)}']
            docs = render(*settings)
            for kind in ('MutatingWebhookConfiguration', 'ValidatingWebhookConfiguration'):
                for webhook in one(docs, kind)['webhooks']:
                    if watched:
                        self.assertEqual(webhook['namespaceSelector'], {'matchExpressions': [
                            {'key': 'kubernetes.io/metadata.name', 'operator': 'In', 'values': watched}]})
                    else:
                        self.assertNotIn('namespaceSelector', webhook)

    def test_release_namespace_is_used_including_default(self):
        for namespace in ('default', 'platform', 'operator-system'):
            docs = render('--namespace', namespace)
            self.assertEqual(objects(docs, 'Namespace'), [])
            for doc in docs:
                if 'namespace' in doc['metadata']:
                    self.assertEqual(doc['metadata']['namespace'], namespace)
            for doc in objects(docs, 'MutatingWebhookConfiguration') + objects(docs, 'ValidatingWebhookConfiguration'):
                self.assertEqual(doc['metadata']['annotations']['cert-manager.io/inject-ca-from'],
                                 f'{namespace}/{NAME}-serving-cert')
                self.assertTrue(all(hook['clientConfig']['service']['namespace'] == namespace for hook in doc['webhooks']))
        default = decode(helm('template', 'oraoperator', CHART, '--kube-version', KUBE_VERSION))
        self.assertEqual(one(default, 'Deployment')['metadata']['namespace'], 'default')

    def test_namespace_override_updates_operator_references(self):
        for release_namespace, target in [('platform', 'operators'), ('default', 'operators'), ('platform', 'default')]:
            with self.subTest(release_namespace=release_namespace, target=target):
                docs = render('--namespace', release_namespace, '--set', f'namespaceOverride={target}',
                              '--set', 'scope.mode=namespace,scope.watchNamespaces={databases},rbac.nodeAccess=true')
                self.assertEqual(objects(docs, 'Namespace'), [])
                for doc in docs:
                    if 'namespace' in doc['metadata']:
                        expected = 'databases' if doc['kind'] == 'RoleBinding' and doc['roleRef']['kind'] == 'ClusterRole' else target
                        self.assertEqual(doc['metadata']['namespace'], expected)
                for binding in objects(docs, 'RoleBinding') + objects(docs, 'ClusterRoleBinding'):
                    self.assertTrue(all(subject['namespace'] == target for subject in binding['subjects']))
                for certificate in objects(docs, 'Certificate'):
                    self.assertTrue(all(f'.{target}.svc' in name for name in certificate['spec']['dnsNames']))
                ca = f'{target}/{NAME}-serving-cert'
                for webhook in objects(docs, 'MutatingWebhookConfiguration') + objects(docs, 'ValidatingWebhookConfiguration'):
                    self.assertEqual(webhook['metadata']['annotations']['cert-manager.io/inject-ca-from'], ca)
                    for entry in webhook['webhooks']:
                        self.assertEqual(entry['clientConfig']['service']['namespace'], target)
                        self.assertEqual(entry['namespaceSelector']['matchExpressions'][0]['values'], ['databases'])
                hook = one(docs, 'Job')['spec']['template']['spec']
                annotation_patch = json.loads(hook['initContainers'][0]['args'][-1])
                self.assertEqual(annotation_patch['metadata']['annotations']['cert-manager.io/inject-ca-from'], ca)
                conversion_patch = json.loads(hook['containers'][0]['args'][-1])
                self.assertEqual(conversion_patch['spec']['conversion']['webhook']['clientConfig']['service']['namespace'], target)
                self.assertEqual(pod(docs)['containers'][0]['env'][0]['value'], 'databases')
                baseline = render('--namespace', release_namespace,
                                  '--set', 'scope.mode=namespace,scope.watchNamespaces={databases},rbac.nodeAccess=true')
                cluster_names = lambda documents: {(d['kind'], d['metadata']['name']) for d in documents if 'namespace' not in d['metadata']}
                self.assertEqual(cluster_names(docs), cluster_names(baseline))

    def test_pdb_boundaries_and_selection(self):
        for settings, expected in [([], {'minAvailable': 1}),
                                   (['--set', 'pdb.minAvailable=0'], {'minAvailable': 0}),
                                   (['--set', 'pdb.maxUnavailable=0'], {'maxUnavailable': 0}),
                                   (['--set', 'pdb.maxUnavailable=2'], {'maxUnavailable': 2}),
                                   (['--set', 'pdb.minAvailable=50%'], {'minAvailable': '50%'}),
                                   (['--set', 'pdb.maxUnavailable=100%'], {'maxUnavailable': '100%'})]:
            with self.subTest(settings=settings):
                spec = one(render(*settings), 'PodDisruptionBudget')['spec']
                self.assertEqual({key: value for key, value in spec.items() if key != 'selector'}, expected)
                self.assertEqual(spec['selector'], one(render(*settings), 'Deployment')['spec']['selector'])
        for setting in ('replicas=1', 'replicas=0', 'pdb.enabled=false'):
            self.assertEqual(objects(render('--set', setting), 'PodDisruptionBudget'), [])

    def test_explicit_leader_election_and_zero_grace_period(self):
        manager = pod(render('--set', 'replicas=1,leaderElection=false,terminationGracePeriodSeconds=0'))
        self.assertIn('--enable-leader-election=false', manager['containers'][0]['args'])
        self.assertEqual(manager['terminationGracePeriodSeconds'], 0)
        self.assertIn('--enable-leader-election=true', pod(render())['containers'][0]['args'])

    def test_image_digest_and_tag_precedence(self):
        digest = 'sha256:' + 'a' * 64
        locations = [
            ([], 'container-registry.oracle.com/database/operator'),
            (['--set', 'image.registry=myregistry.example.com:5000'], 'myregistry.example.com:5000/database/operator'),
            (['--set', 'image.repository=team/operator'], 'container-registry.oracle.com/team/operator'),
            (['--set', 'image.registry=myregistry.example.com,image.repository=team/operator'], 'myregistry.example.com/team/operator'),
        ]
        versions = [([], ':2.2.0'), (['--set', 'image.tag=test'], ':test'),
                    (['--set', f'image.tag=test,image.digest={digest}'], '@' + digest)]
        for (location, repository), (version, suffix) in itertools.product(locations, versions):
            with self.subTest(repository=repository, suffix=suffix):
                self.assertEqual(pod(render(*location, *version))['containers'][0]['image'], repository + suffix)

    def test_webhook_port_matches_service_target(self):
        docs = render()
        port = next(port for port in pod(docs)['containers'][0]['ports'] if port['name'] == 'webhook-server')
        self.assertEqual(port['containerPort'], 9443)
        service = next(doc for doc in objects(docs, 'Service') if doc['metadata']['name'].endswith('-webhook-service'))
        self.assertEqual(service['spec']['ports'][0]['targetPort'], port['name'])

    def test_certificates_are_persistent_and_mounted(self):
        docs = render('--set', 'webhook.certificateSecretName=custom-tls')
        self.assertEqual(len(objects(docs, 'Issuer')), 1)
        certificates = objects(docs, 'Certificate')
        self.assertEqual(len(certificates), 2)
        mounted = {volume['secret']['secretName'] for volume in pod(docs)['volumes']}
        self.assertEqual(mounted, {'custom-tls', 'metrics-server-cert'})
        for certificate in certificates:
            self.assertIn(certificate['spec']['secretName'], mounted)
            self.assertNotIn('helm.sh/hook', certificate['metadata'].get('annotations', {}))
            self.assertTrue(all('.platform.svc' in name for name in certificate['spec']['dnsNames']))
        self.assertEqual(objects(docs, 'Secret'), [])

    def test_admission_matches_served_versions_and_source_policies(self):
        docs = render()
        for kind in ('MutatingWebhookConfiguration', 'ValidatingWebhookConfiguration'):
            configuration = one(docs, kind)
            self.assertNotIn('helm.sh/hook', configuration['metadata'].get('annotations', {}))
            hooks = configuration['webhooks']
            self.assertEqual(len({hook['name'] for hook in hooks}), len(hooks))
            observers = [hook for hook in hooks if hook['rules'][0]['resources'] == ['databaseobservers']]
            self.assertEqual({hook['rules'][0]['apiVersions'][0] for hook in observers}, {'v1alpha1', 'v1', 'v4'})
            self.assertTrue(all(hook['matchPolicy'] == 'Exact' for hook in observers))
            sidb = [hook for hook in hooks if hook['rules'][0]['resources'] == ['singleinstancedatabases']]
            self.assertEqual(len(sidb), 1)
            self.assertIn('-v4-', sidb[0]['clientConfig']['service']['path'])

    def test_crd_hook_can_be_disabled_with_its_account_and_permissions(self):
        docs = render('--set', 'crdConfiguration.enabled=false')
        self.assertEqual(objects(docs, 'Job'), [])
        self.assertFalse(any(doc['metadata']['name'].endswith('-crd-configuration') for doc in docs))
        self.assertEqual(len(objects(docs, 'Deployment')), 1)

    def test_crd_hook_has_resources_and_preserves_schemas(self):
        docs = render()
        hook = one(docs, 'Job')
        self.assertEqual(hook['metadata']['annotations']['helm.sh/hook'], 'post-install,post-upgrade')
        self.assertEqual(hook['spec']['activeDeadlineSeconds'], 180)
        hook_pod = hook['spec']['template']['spec']
        containers = hook_pod['initContainers'] + hook_pod['containers']
        crds = decode('\n'.join(path.read_text() for path in sorted((CHART / 'crds').glob('*.yaml'))))
        for container in containers:
            self.assertEqual(container['command'], ['kubectl'])
            self.assertTrue(container['resources']['requests'])
            self.assertTrue(container['resources']['limits'])
            self.assertEqual(container['args'][0], 'patch')
            self.assertNotIn('--request-timeout', container['args'])
        targets = set(containers[0]['args'][1:-3])
        self.assertEqual(targets, {f"customresourcedefinition/{crd['metadata']['name']}" for crd in crds})
        conversion_targets = set(containers[1]['args'][1:-3])
        self.assertEqual(conversion_targets, {f"customresourcedefinition/{crd['metadata']['name']}" for crd in crds
                                             if crd['spec'].get('conversion', {}).get('strategy') == 'Webhook'})
        for crd in crds:
            original = deepcopy(crd)
            for container in containers:
                if f"customresourcedefinition/{crd['metadata']['name']}" in container['args']:
                    crd = json.loads(subprocess.check_output([
                        'kubectl', 'patch', '--local', '-f', '-', '--type=merge', '--patch', container['args'][-1], '-o', 'json',
                    ], input=json.dumps(crd), text=True))
            original['metadata']['annotations']['cert-manager.io/inject-ca-from'] = f'platform/{NAME}-serving-cert'
            if f"customresourcedefinition/{crd['metadata']['name']}" in conversion_targets:
                original['spec']['conversion']['webhook']['clientConfig']['service'].update(name=NAME + '-webhook-service', namespace='platform')
            self.assertEqual(crd, original)

    def test_pod_and_hook_customization(self):
        settings = {
            'nodeSelector': {'kubernetes.io/os': 'linux'},
            'tolerations': [{'key': 'dedicated', 'operator': 'Exists', 'effect': 'NoSchedule'}],
            'affinity': {'nodeAffinity': {'requiredDuringSchedulingIgnoredDuringExecution': {'nodeSelectorTerms': [
                {'matchExpressions': [{'key': 'workload', 'operator': 'In', 'values': ['oracle']}]}]}}},
            'podAnnotations': {'example.com/test': 'true'},
            'topologySpreadConstraints': [{'maxSkew': 1, 'topologyKey': 'topology.kubernetes.io/zone',
                                          'whenUnsatisfiable': 'ScheduleAnyway',
                                          'labelSelector': {'matchLabels': {'app.kubernetes.io/name': NAME}}}],
        }
        args = tuple(item for key, value in settings.items() for item in ('--set-json', f'{key}={json.dumps(value)}'))
        docs = render(*args, '--set', 'securityContext.runAsUser=1003', '--set', 'podSecurityContext.fsGroup=1003',
                      '--set', 'serviceAccount.annotations.example=value', '--set', 'crdConfiguration.resources.requests.cpu=20m')
        manager = pod(docs)
        hook = one(docs, 'Job')['spec']['template']['spec']
        for key in ('nodeSelector', 'tolerations', 'affinity'):
            self.assertEqual(manager[key], settings[key])
            self.assertEqual(hook[key], settings[key])
        self.assertEqual(manager['topologySpreadConstraints'], settings['topologySpreadConstraints'])
        self.assertEqual(one(docs, 'Deployment')['spec']['template']['metadata']['annotations'], settings['podAnnotations'])
        self.assertEqual(manager['containers'][0]['securityContext']['runAsUser'], 1003)
        self.assertFalse(manager['containers'][0]['securityContext']['allowPrivilegeEscalation'])
        self.assertEqual(manager['securityContext']['fsGroup'], 1003)
        self.assertTrue(manager['securityContext']['runAsNonRoot'])
        self.assertEqual(hook['containers'][0]['resources']['requests']['cpu'], '20m')
        account = next(doc for doc in objects(docs, 'ServiceAccount') if doc['metadata']['name'] == NAME)
        self.assertEqual(account['metadata']['annotations'], {'example': 'value'})

    def test_invalid_values_fail_lint(self):
        settings = [
            'enabled=invalid', 'replicas=-1', 'replicas=invalid', 'leaderElection=invalid', 'replicas=3,leaderElection=false',
            'webhook.failurePolicy=invalid', 'webhook.timeoutSeconds=0', 'webhook.timeoutSeconds=31',
            'webhook.port=10443', 'scope.mode=invalid', 'scope.mode=namespace',
            'scope.watchNamespaces={databases}', 'scope.mode=namespace,scope.watchNamespaces={a,a}',
            'scope.mode=namespace,scope.watchNamespaces={Invalid}',
            'pdb.minAvailable=-1', 'pdb.minAvailable=101%', 'pdb.minAvailable=1,pdb.maxUnavailable=0',
            'serviceAccount.create=false', 'crdConfiguration.serviceAccount.create=false',
            'ociCredentials.existingSecretName=oci-cred', 'nameOverride=custom', 'fullnameOverride=custom',
            'namespace=custom', 'image.digest=invalid', 'terminationGracePeriodSeconds=-1',
            'image.registry=', 'image.registry=https://registry.example.com', 'image.registry=registry.example.com/team',
            'image.repository=', 'image.repository=/database/operator', 'image.repository=database/operator/',
            'image.repository=database/operator:tag', 'image.repository=database//operator',
            'namespaceOverride=Invalid', 'namespaceOverride=invalid.namespace',
            'namespaceOverride=' + 'n' * 64, 'namespaceOverride=123',
            'extraEnv[0].name=WATCH_NAMESPACE,extraEnv[0].value=override',
        ]
        for setting in settings:
            with self.subTest(setting=setting):
                result = subprocess.run(['helm', 'lint', str(CHART), '--strict', '--kube-version', KUBE_VERSION,
                                         '--set', setting], capture_output=True, text=True)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn('schema', result.stdout + result.stderr)

    def test_target_kubernetes_version(self):
        for version in ('1.21.0', '1.34.0', '1.35.9', '1.38.0'):
            result = subprocess.run(['helm', 'template', 'review', str(CHART), '--kube-version', version], capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn('kubeVersion', result.stderr)
        render('--kube-version', '1.36.0')
        render('--kube-version', '1.36.3-gke.1000')
        render('--kube-version', '1.37.0')

    def test_generated_manifests_are_current(self):
        for path, expected in generated_files().items():
            with self.subTest(path=path):
                self.assertEqual(path.read_text(), expected)

    def test_examples_lint(self):
        for example in (CHART / 'examples').glob('*.yaml'):
            with self.subTest(example=example):
                helm('lint', CHART, '--strict', '--kube-version', KUBE_VERSION, '-f', example)

    def test_package_and_umbrella_render_with_native_crds(self):
        self.assertEqual(objects(render(), 'CustomResourceDefinition'), [])
        self.assertEqual(len(objects(render('--include-crds'), 'CustomResourceDefinition')), 17)
        with tempfile.TemporaryDirectory() as directory:
            temporary = Path(directory)
            helm('package', CHART, '--destination', directory)
            package = next(temporary.glob('*.tgz'))
            with tarfile.open(package) as archive:
                names = archive.getnames()
            self.assertEqual(sum('/crds/' in name and name.endswith('.yaml') for name in names), 17)
            self.assertFalse(any('/tests/' in name or '/scripts/' in name for name in names))
            self.assertIn(f'{NAME}/files/webhooks.json', names)
            self.assertIn(f'{NAME}/values.schema.json', names)
            umbrella = temporary / 'platform'
            charts = umbrella / 'charts'
            charts.mkdir(parents=True)
            (umbrella / 'Chart.yaml').write_text(
                'apiVersion: v2\nname: platform\nversion: 1.0.0\n'
                f'dependencies:\n- name: {NAME}\n  version: "*"\n  condition: {NAME}.enabled\n')
            (charts / package.name).write_bytes(package.read_bytes())
            (umbrella / 'values.yaml').write_text(f'global:\n  example: value\n{NAME}:\n  enabled: true\n')
            for chart in (package, umbrella):
                docs = decode(helm('template', 'review', chart, '-n', 'platform', '--kube-version', KUBE_VERSION, '--include-crds'))
                self.assertEqual(len(objects(docs, 'CustomResourceDefinition')), 17)
                self.assertEqual(pod(docs)['serviceAccountName'], NAME)
            overridden = decode(helm('template', 'review', umbrella, '-n', 'platform', '--kube-version', KUBE_VERSION,
                                     '--include-crds', '--set', f'{NAME}.namespaceOverride=operators'))
            for doc in overridden:
                if 'namespace' in doc['metadata']:
                    self.assertEqual(doc['metadata']['namespace'], 'operators')
            for configuration in objects(overridden, 'MutatingWebhookConfiguration') + objects(overridden, 'ValidatingWebhookConfiguration'):
                self.assertTrue(all(entry['clientConfig']['service']['namespace'] == 'operators' for entry in configuration['webhooks']))
            self.assertEqual(decode(helm('template', 'review', umbrella, '--kube-version', KUBE_VERSION,
                                         '--include-crds', '--set', f'{NAME}.enabled=false')), [])


if __name__ == '__main__':
    unittest.main()
