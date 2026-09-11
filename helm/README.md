# Oracle Database Operator Helm Chart

This Helm chart installs the Oracle Database Operator for Kubernetes.

## Prerequisites

- Kubernetes 1.21+
- Helm 3.7+
- A separately managed, healthy cert-manager installation, including its CRDs and webhook

## Install

```bash
helm upgrade --install oraoperator . --namespace my-oracle-operator --create-namespace
```

The chart manages a self-signed Issuer and Certificate for the operator's webhook.
cert-manager issues the TLS Secret and injects the webhook CA bundle.

The operator defaults to `oracle-database-operator-system` when the Helm release
namespace is `default`, including when `--namespace` is omitted in the default
Kubernetes context. The chart creates and retains that operator namespace.
Select another installation namespace with `--namespace my-oracle-operator`.
Create referenced Secrets and custom ServiceAccounts in the operator namespace.
`scope.watchNamespaces` configures the database namespaces the operator watches.

This chart supports one operator installation per cluster.

If installation fails because cert-manager is missing or unhealthy, resolve the
prerequisite and rerun the Helm command. Helm wait and rollback options retain
their standard behavior.

Database resources require registered Oracle CRDs and usable operator webhooks.
An umbrella chart must account for both before creating database resources.

## CRD lifecycle

CRDs are packaged in `crds/`, following [Helm's native CRD installation
pattern](https://helm.sh/docs/chart_best_practices/custom_resource_definitions/).
Helm registers them before processing ordinary resources, including when this
chart is an umbrella dependency. Use `--skip-crds` when CRDs are managed separately.

A post-install/post-upgrade Job configures the CRDs' certificate annotations and
conversion webhook Service references for the release namespace. Its ServiceAccount
can get and patch only the chart's CRDs. The Job also runs with `--skip-crds`, which
requires all 17 CRDs to exist. Allow Helm hooks to run and wait for the Job to succeed
before creating database resources. An umbrella chart should install database
resources in a subsequent release because this Job runs after ordinary resources.

The Job uses `crdConfiguration.image`; choose a kubectl version compatible with
your Kubernetes server. The Job preserves CRD schemas, stored versions and CA bundles.
It shares the operator's `nodeSelector`, `tolerations` and explicitly configured
`affinity` settings.

Helm skips existing CRDs and retains native CRDs on uninstall and rollback.
Review and apply CRD schema updates separately before upgrading the operator;
`helm upgrade` does not update their definitions. Preserve stored API versions
and conversion settings when updating existing CRDs.

**Existing releases with templated CRDs require the
[retention transition](CRD-MIGRATION.md) before upgrading to this layout.**
This also applies to the Autonomous Database CRDs managed by earlier chart versions.

CRD registration establishes the API; operator admission becomes usable after
certificate issuance, CA injection, and webhook startup. For a reliable first
deployment, install the operator and verify its webhooks before installing the
chart containing database resources. `helm template` tests rendering, not API
discovery or admission readiness.

## Uninstall

```bash
helm uninstall oraoperator --namespace my-oracle-operator
```

### Full cleanup

The chart provides no `values.yaml` flag to delete native CRDs. Their removal is
an explicit administrator action because they are cluster-wide and may be used
by other releases.

1. Delete the intended database custom resources while the operator is running,
   and wait for their cleanup and finalizers to complete.
2. Uninstall the operator release.
3. After confirming no other installation uses these CRDs, run from this chart's
   `helm/` directory:

   ```bash
   kubectl delete -f crds/
   ```

**Deleting these CRDs removes all remaining instances across every namespace.**
It does not guarantee cleanup of external databases or retained storage; review
the relevant controller's deletion policy and clean up retained assets separately.
Retaining CRDs also does not protect custom resources when their namespace or
owning workload release is deleted.

## Configuration

### General Settings

| Parameter | Description | Default |
|-----------|-------------|---------|
| `imagePullSecrets` | Image pull secrets for private registries | `[]` |
| `crdConfiguration.image` | kubectl image for CRD reference configuration | `registry.k8s.io/kubectl:v1.35.0` |
| `crdConfiguration.imagePullPolicy` | CRD configuration image pull policy | `IfNotPresent` |

### Scope Settings

| Parameter | Description | Default |
|-----------|-------------|---------|
| `scope.mode` | Deployment scope: `cluster` or `namespace` | `cluster` |
| `scope.watchNamespaces` | Namespaces to watch when `scope.mode=namespace` | `[]` |
| `rbac.nodeAccess` | Grant permission to list/watch nodes (for NodePort services) | `false` |

### Operator Deployment Settings

| Parameter | Description | Default |
|-----------|-------------|---------|
| `replicas` | Number of replicas | `3` |
| `image.repository` | Image repository | `container-registry.oracle.com/database/operator` |
| `image.tag` | Image tag | `2.2.0` |
| `image.pullPolicy` | Image pull policy | `IfNotPresent` |
| `resources.limits.cpu` | CPU limit | `400m` |
| `resources.limits.memory` | Memory limit | `400Mi` |
| `resources.requests.cpu` | CPU request | `400m` |
| `resources.requests.memory` | Memory request | `400Mi` |
| `leaderElection` | Enable leader election | `true` |
| `pdb.enabled` | Enable PodDisruptionBudget (when replicas > 1) | `true` |
| `pdb.minAvailable` | Minimum available pods | `1` |
| `pdb.maxUnavailable` | Maximum unavailable pods (alternative to minAvailable) | - |
| `affinity` | Pod affinity rules (default: soft anti-affinity when replicas > 1) | `{}` |
| `nodeSelector` | Node selector for scheduling | `{}` |
| `tolerations` | Pod tolerations | `[]` |
| `terminationGracePeriodSeconds` | Termination grace period | `10` |
| `extraEnv` | Extra environment variables | `[]` |

### Webhook Settings

| Parameter | Description | Default |
|-----------|-------------|---------|
| `webhook.failurePolicy` | Webhook failure policy (`Fail` or `Ignore`) | `Fail` |
| `webhook.port` | Webhook server port | `9443` |
| `webhook.certificateSecretName` | Secret name for webhook TLS certificate | `webhook-server-cert` |
| `webhook.timeoutSeconds` | Webhook timeout in seconds | `10` |

### OCI Credentials Settings

| Parameter | Description | Default |
|-----------|-------------|---------|
| `ociCredentials.existingSecretName` | Existing Secret with OCI credentials | `""` |
| `ociCredentials.tenancy` | OCI tenancy OCID | `""` |
| `ociCredentials.user` | OCI user OCID | `""` |
| `ociCredentials.fingerprint` | OCI API key fingerprint | `""` |
| `ociCredentials.region` | OCI region | `""` |
| `ociCredentials.passphrase` | Passphrase for encrypted private key | `""` |
| `ociCredentials.secretName` | Existing Secret with OCI API private key | `""` |

## Deployment Modes

### Cluster-Scoped (Default)

The operator monitors all namespaces in the cluster.

```bash
helm upgrade --install oraoperator . --namespace my-oracle-operator --create-namespace --set scope.mode=cluster
```

### Namespace-Scoped

The operator monitors only specified namespaces.

```bash
helm upgrade --install oraoperator . --namespace my-oracle-operator --create-namespace \
  --set scope.mode=namespace \
  --set 'scope.watchNamespaces={default,my-app-ns}'
```

## High Availability

When running multiple replicas (`replicas > 1`), the chart automatically:

1. **Enables leader election** - Only one replica processes events at a time
2. **Applies pod anti-affinity** - Spreads pods across nodes (soft preference)
3. **Creates PodDisruptionBudget** - Ensures minimum availability during disruptions

To customize HA behavior:

```bash
helm upgrade --install oraoperator . --namespace my-oracle-operator --create-namespace \
  --set replicas=3 \
  --set pdb.minAvailable=2
```

## OCI Credentials

Autonomous Database operations require OCI credentials.

### Option 1: Reference Existing Secret

```bash
helm upgrade --install oraoperator . --namespace my-oracle-operator --create-namespace \
  --set ociCredentials.existingSecretName=oci-cred \
  --set ociCredentials.secretName=oci-privatekey
```

### Option 2: Provide Values Directly

```bash
# Create the secret with your private key first
kubectl create secret generic oci-privatekey \
  --from-file=privatekey=/path/to/oci_api_key.pem \
  -n my-oracle-operator

# Install with credential values
helm upgrade --install oraoperator . --namespace my-oracle-operator --create-namespace \
  --set ociCredentials.tenancy=ocid1.tenancy.oc1..xxx \
  --set ociCredentials.user=ocid1.user.oc1..xxx \
  --set ociCredentials.fingerprint=aa:bb:cc:dd:... \
  --set ociCredentials.region=us-ashburn-1 \
  --set ociCredentials.secretName=oci-privatekey
```

## Generating YAML Manifests

```bash
# Generate with defaults
helm template oraoperator . --namespace my-oracle-operator --include-crds > oracle-database-operator.yaml

# Namespace-scoped
helm template oraoperator . \
  --namespace my-oracle-operator \
  --include-crds \
  --set scope.mode=namespace \
  --set 'scope.watchNamespaces={default,my-app-ns}' \
  > oracle-database-operator-namespace-scoped.yaml
```
