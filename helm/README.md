# Oracle Database Operator Helm Chart

Installs the Oracle Database Operator in the selected operator namespace, alongside its
dedicated ServiceAccount and cert-manager certificates. Multiple releases can
manage separate sets of database namespaces in the same cluster.

## Install

Prerequisites:

- Kubernetes 1.36 or 1.37.
- Helm 3.7+ or Helm 4.
- A healthy, **separately managed cert-manager** installation with its CRDs and cainjector.

Run from the repository root:

```bash
helm upgrade --install oraoperator ./helm \
  --namespace oracle-database-operator-system --create-namespace --wait --timeout 5m
```

The operator uses the Helm release namespace, including `default`, unless
`namespaceOverride` selects another existing namespace. Pre-create referenced
Secrets and existing ServiceAccounts in the operator namespace. Watched database
namespaces must also exist before installation.

The chart supports Kubernetes 1.36–1.37 and pins its configuration Job to kubectl 1.36.3.
This keeps the Job within the [kubectl version-skew policy](https://kubernetes.io/releases/version-skew-policy/#kubectl).
Validate the selected operator image with the cluster test below before promoting
it to production. Kubernetes compatibility beyond this target requires validation
with both the operator and hook images.

The operator serves admission on port 9443. Application health endpoints remain
an operator backlog item. Verify admission after installation; Deployment readiness
and the configuration Job's completion alone do not establish webhook readiness.

## Configuration

Use [values.yaml](values.yaml) for defaults and [values.schema.json](values.schema.json)
for accepted values. Unsupported keys fail validation.

| Setting | Behavior |
|---|---|
| `namespaceOverride` | Operator namespace; empty uses the Helm release namespace |
| `replicas` | Defaults to 3; multiple replicas require leader election |
| `leaderElection` | Explicitly passed to the manager; defaults to `true` |
| `image.registry`, `image.repository`, `image.tag`, `image.digest` | Registry host (optional port) and repository path; empty tag uses `Chart.appVersion`; digest takes precedence |
| `image.pullPolicy`, `imagePullSecrets` | Image retrieval settings; pull secrets also apply to the hook |
| `scope.mode`, `scope.watchNamespaces` | `cluster`, or `namespace` with a unique nonempty namespace list |
| `serviceAccount.create`, `.name`, `.annotations` | Create a dedicated account, or use an explicitly named existing account |
| `rbac.create` | Create all chart RBAC, including the configuration Job's permissions |
| `rbac.clusterResources` | PV management and Namespace/StorageClass reads; defaults to `true` |
| `rbac.nodeAccess` | Node list/watch access for NodePort services; defaults to `false` |
| `resources` | Manager requests and limits |
| `pdb.enabled`, `.minAvailable`, `.maxUnavailable` | Budget for multiple replicas; choose at most one availability field |
| `podAnnotations` | Annotations on manager pods |
| `podSecurityContext`, `securityContext` | Pod and manager-container security contexts |
| `nodeSelector`, `tolerations`, `affinity` | Scheduling for manager and configuration Job |
| `topologySpreadConstraints` | Manager pod distribution; selectors should identify this release |
| `terminationGracePeriodSeconds` | Defaults to 10; explicit zero is preserved |
| `extraEnv` | Additional manager environment variables; `scope` owns `WATCH_NAMESPACE` |
| `webhook.failurePolicy`, `.timeoutSeconds` | Admission policy and timeout of 1–30 seconds |
| `webhook.certificateSecretName` | TLS Secret issued by cert-manager and mounted into the manager |
| `crdConfiguration.enabled` | Configure CRD references after installation and upgrade; defaults to `true` |
| `crdConfiguration.image`, `.imagePullPolicy` | Complete kubectl image reference, including optional digest |
| `crdConfiguration.resources` | Requests and limits for both hook containers |
| `crdConfiguration.serviceAccount.create`, `.name` | Independent hook account; existing accounts require an explicit name |

The default container runs as UID 1002, drops capabilities, and disables privilege
escalation. Kubernetes merges nested value overrides with chart defaults; remove
individual inherited fields with `null` when the target security policy requires it.

### Namespace scope and permissions

```bash
helm upgrade --install oraoperator ./helm \
  --namespace oracle-database-operator-system --create-namespace \
  -f helm/examples/namespace.yaml
```

Namespace mode binds the manager role in each watched namespace. Leader election
uses a separate Role in the operator namespace. Admission webhooks select the
same watched namespaces. Cluster-resource permissions use an independent
ClusterRoleBinding in both modes. Set `rbac.clusterResources=false`
when database features requiring PV management or StorageClass/Namespace reads
are unused or their permissions are managed externally.

For externally managed RBAC, provision the required accounts and permissions,
then use [external-rbac.yaml](examples/external-rbac.yaml). Render with
`rbac.create=true` to inspect the roles and bindings to provision. The hook's
ServiceAccount and permissions are separate from the operator's account.

### Multiple releases in one cluster

Install each release in a separate operator namespace and configure disjoint
`scope.watchNamespaces` lists. Separate installation namespaces isolate the
operator's leader-election leases; cluster-scoped RBAC and admission configuration
names include the release identity.

CRDs are shared across the cluster, and each CRD has one
[conversion-webhook destination](https://kubernetes.io/docs/tasks/extend-kubernetes/custom-resources/custom-resource-definition-versioning/#webhook-conversion).
Choose one release to manage these references with
`crdConfiguration.enabled=true`. Additional releases use `--skip-crds` and
`crdConfiguration.enabled=false`:

```bash
kubectl create namespace databases-a
kubectl create namespace databases-b

helm upgrade --install oraoperator ./helm \
  --namespace oracle-database-operator-system --create-namespace --wait \
  --set scope.mode=namespace --set 'scope.watchNamespaces={databases-a}'

helm upgrade --install oraoperator ./helm \
  --namespace oracle-database-operator-team-b --create-namespace --wait \
  --skip-crds --set crdConfiguration.enabled=false \
  --set scope.mode=namespace --set 'scope.watchNamespaces={databases-b}'
```

All releases must use operator versions compatible with the shared CRD schemas
and conversion provider. Keep the conversion-providing release available for
every namespace using those CRDs. Before removing it, transfer CRD configuration
to a surviving release and verify conversion. A cluster-scoped release watches
every namespace, so use namespace scope for independent operators.

### Availability and sizing

Defaults reserve 1.2 CPUs and 1200Mi memory across three replicas. One elected
leader reconciles resources; each replica serves webhooks. Empty `affinity`
applies a soft preference for separate nodes.

- [small.yaml](examples/small.yaml): one replica and a lower CPU request.
- [ha.yaml](examples/ha.yaml): three replicas, a two-pod minimum, and zone spreading.

Measure memory and CPU use for the number and types of databases managed. These
examples are starting points; the single-replica profile has a webhook outage
during replacement or node failure.

PDB settings accept nonnegative integers or percentages, including zero. Both
availability fields default to `null`, which selects `minAvailable: 1`:

```bash
helm upgrade --install oraoperator ./helm --namespace oracle-database-operator-system \
  --set pdb.maxUnavailable=0
```

### Oracle Cloud Infrastructure credentials

Set `ociCredentials` to create OCI API-key credentials with the chart. Each entry
creates a ConfigMap and a private-key Secret in the database resource's namespace.
Namespaces must already exist and, in namespace mode, be in `scope.watchNamespaces`.
The default empty list leaves credential provisioning to your existing tooling.

Save your OCI profile fields in `oci-values.yaml`:

```yaml
ociCredentials:
  - namespace: databases
    configMapName: oci-cred
    secretName: oci-privatekey
    tenancy: ocid1.tenancy.oc1..example
    user: ocid1.user.oc1..example
    fingerprint: "00:00:00:00:00:00:00:00:00:00:00:00:00:00:00:00"
    region: uk-london-1
```

Supply the corresponding private-key file when installing or upgrading:

```bash
helm upgrade --install oraoperator ./helm --namespace oracle-database-operator-system \
  --create-namespace -f helm/examples/namespace.yaml -f oci-values.yaml \
  --set-file "ociCredentials[0].privateKey=$HOME/.oci/oci_api_key.pem"
```

This provisions the resources created by `set_ocicredentials.sh`; profile fields
are supplied through Helm values. `configMapName` defaults to `oci-cred` and
`secretName` to `oci-privatekey`. Add entries for additional profiles or namespaces,
using distinct resource names within each namespace.

To use an existing private-key Secret, replace `secretName` with
`existingSecretName` and omit `privateKey` and its `--set-file` argument. The Secret
must contain the `privatekey` key in the entry's namespace; see
[oci-credentials.yaml](examples/oci-credentials.yaml).

Reference the ConfigMap and the created or existing Secret in the database resource:

```yaml
spec:
  ociConfig:
    configMapName: oci-cred
    secretName: oci-privatekey
```

Helm manages the created resources on upgrade and removes them when their entry
is removed or the release is uninstalled. Existing Secrets retain their external
ownership. Values supplied through `--set-file` are stored in Helm release history.
The optional `passphrase` field is stored in the ConfigMap, matching the operator's
credential format.

See the [Autonomous Database setup](../docs/adb/README.md) for other authentication
modes and the [credential helper](../set_ocicredentials.sh) for local OCI-profile parsing.

## CRD lifecycle

The CRDs use [Helm's native `crds/` lifecycle](https://helm.sh/docs/chart_best_practices/custom_resource_definitions/):
Helm installs missing definitions before ordinary resources and retains them on
uninstall. Apply reviewed schema updates separately before upgrading the operator,
preserving stored versions and deployed conversion settings.

A post-install/post-upgrade Job patches certificate annotations and conversion
Service references to the release namespace. Its account can get and patch only
these CRDs. The Job preserves schemas, stored versions, and CA bundles.

`--skip-crds` uses externally installed definitions; the enabled Job still requires
all bundled definitions. Set `crdConfiguration.enabled=false` when another release or
an external owner configures both certificate annotations and conversion references.
Helm still installs missing native CRDs unless `--skip-crds` is also supplied.

Allow hooks to finish before creating database resources. In an umbrella setup,
install database resources in a subsequent release, after certificate issuance,
CA injection, and webhook startup have been verified.

For releases that previously managed CRDs as templates, follow the
[retention transition](CRD-MIGRATION.md).

## Uninstall

```bash
helm uninstall oraoperator --namespace oracle-database-operator-system
```

For full cleanup, delete database custom resources while the operator is running
and wait for their finalizers. Then uninstall the operator. Once the retained CRDs
are unused, remove them explicitly:

```bash
kubectl delete -f helm/crds/
```

Deleting a CRD deletes all remaining instances across all namespaces. Review each
controller's deletion policy for external databases and retained storage.

## Chart maintenance and verification

All chart tooling lives under `helm/`; repository CI can invoke it separately.
Local checks require Python 3.10+, Helm 3.12+ or Helm 4, and kubectl. YAML decoding
uses kubectl's local mode.

```bash
bash helm/scripts/check.sh
python3 helm/scripts/sync_manifests.py --check
```

After regenerating the repository's operator bundle and API schemas, synchronize
chart artifacts:

```bash
python3 helm/scripts/sync_manifests.py
```

The script reads the operator bundle, generated CRD bases, and webhook markers.
It writes only `helm/crds/` and `helm/files/webhooks.json`. Webhook entries cover
served API versions and preserve per-entry matching policies. Conflicting markers
and inconsistent source schemas fail generation.

Run integration checks against an explicitly selected disposable Kubernetes 1.36 or 1.37
cluster with healthy cert-manager and no Oracle CRDs:

```bash
python3 helm/scripts/test_cluster.py --context kind-helm-test \
  --second-namespace helm-chart-test-secondary
```

The script installs and upgrades the chart, checks certificates and CRD references,
and exercises successful and rejected admission using server dry-runs. With
`--second-namespace`, it also checks independent admission and shared conversion
across two releases, including removal of the secondary release. Successful
runs verify CRD retention on uninstall and clean up their resources. Failed runs
retain resources for inspection; `--keep` also retains a successful installation.
Use repeated `--values` arguments for image or scheduling customization.

For offline rendering, specify the target Kubernetes version:

```bash
helm template oraoperator ./helm --namespace oracle-database-operator-system \
  --kube-version 1.36.3 --include-crds > /tmp/oracle-database-operator.yaml
```
