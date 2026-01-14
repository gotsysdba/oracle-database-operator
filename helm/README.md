# Oracle Database Operator Helm Chart

This Helm chart installs the Oracle Database Operator for Kubernetes.

## Prerequisites

- Kubernetes 1.21+
- Helm 3.x
- [cert-manager](https://cert-manager.io/docs/installation/) installed in the cluster

## Installation

```bash
# Install with default configuration (cluster-scoped)
helm install oracle-db-operator ./helm

# Uninstall
helm uninstall oracle-db-operator
```

## Configuration

| Parameter | Description | Default |
|-----------|-------------|---------|
| `scope.mode` | Deployment scope: `cluster` (all namespaces) or `namespace` (specific namespaces) | `cluster` |
| `scope.watchNamespaces` | List of namespaces to watch when `scope.mode=namespace` | `[]` |
| `rbac.nodeAccess` | Grant permission to list/watch nodes (required for NodePort services) | `false` |
| `controllerManager.replicas` | Number of controller manager replicas | `3` |
| `controllerManager.image.repository` | Controller manager image repository | `container-registry.oracle.com/database/operator` |
| `controllerManager.image.tag` | Controller manager image tag | `latest` |
| `controllerManager.image.pullPolicy` | Image pull policy | `Always` |
| `controllerManager.resources.limits.cpu` | CPU limit | `400m` |
| `controllerManager.resources.limits.memory` | Memory limit | `400Mi` |
| `controllerManager.resources.requests.cpu` | CPU request | `400m` |
| `controllerManager.resources.requests.memory` | Memory request | `400Mi` |
| `webhook.failurePolicy` | Webhook failure policy | `Fail` |
| `ociCredentials.configMapName` | Name of existing ConfigMap with OCI credentials | `""` |
| `ociCredentials.tenancy` | OCI tenancy OCID (creates ConfigMap if configMapName empty) | `""` |
| `ociCredentials.user` | OCI user OCID | `""` |
| `ociCredentials.fingerprint` | OCI API key fingerprint | `""` |
| `ociCredentials.region` | OCI region | `""` |
| `ociCredentials.passphrase` | Passphrase for encrypted private key (optional) | `""` |
| `ociCredentials.secretName` | Name of existing Secret with OCI API private key | `""` |

## Generating YAML Manifests

Use `helm template` to generate Kubernetes YAML manifests without installing.

### Generate oracle-database-operator.yaml (default cluster-scoped)

```bash
helm template oracle-db-operator ./helm \
  --include-crds \
  > oracle-database-operator.yaml
```

### Cluster-Scoped Deployment

Watches all namespaces. Creates a ClusterRoleBinding for the manager role.

```bash
helm template oracle-db-operator ./helm \
  --include-crds \
  --set scope.mode=cluster \
  > oracle-database-operator-cluster-scoped.yaml
```

### Namespace-Scoped Deployment

Watches only specified namespaces. Creates RoleBindings in each watched namespace.

```bash
helm template oracle-db-operator ./helm \
  --include-crds \
  --set scope.mode=namespace \
  --set 'scope.watchNamespaces={default,my-app-ns}' \
  > oracle-database-operator-namespace-scoped.yaml
```

### With Node RBAC (for NodePort services)

Adds ClusterRole/ClusterRoleBinding to list/watch nodes. Required if database CRs will use NodePort service type.

```bash
helm template oracle-db-operator ./helm \
  --include-crds \
  --set rbac.nodeAccess=true \
  > oracle-database-operator-with-node-rbac.yaml
```

### Combined Options

```bash
# Namespace-scoped with node RBAC
helm template oracle-db-operator ./helm \
  --include-crds \
  --set scope.mode=namespace \
  --set 'scope.watchNamespaces={default,production}' \
  --set rbac.nodeAccess=true \
  > oracle-database-operator-custom.yaml
```

## Deployment Modes

### Cluster-Scoped (Default)

The operator monitors all namespaces in the cluster.

- `WATCH_NAMESPACE` environment variable is set to `""`
- ClusterRoleBinding grants cluster-wide permissions

### Namespace-Scoped

The operator monitors only specified namespaces, providing more granular access control.

- `WATCH_NAMESPACE` is set to comma-separated list of namespaces
- RoleBindings are created in each watched namespace

## OCI Credentials

Autonomous Database operations require OCI credentials. The chart supports two approaches:

### Option 1: Reference Existing Resources

If you already have OCI credentials created (e.g., using `set_ocicredentials.sh`):

```bash
helm install oracle-db-operator ./helm \
  --set ociCredentials.configMapName=oci-cred \
  --set ociCredentials.secretName=oci-privatekey
```

### Option 2: Provide Values Directly

The chart creates the ConfigMap when you provide credential values:

```bash
# First, create the secret with your private key
kubectl create secret generic oci-privatekey \
  --from-file=privatekey=/path/to/oci_api_key.pem \
  -n oracle-database-operator-system

# Then install with credential values
helm install oracle-db-operator ./helm \
  --set ociCredentials.tenancy=ocid1.tenancy.oc1..xxx \
  --set ociCredentials.user=ocid1.user.oc1..xxx \
  --set ociCredentials.fingerprint=aa:bb:cc:dd:... \
  --set ociCredentials.region=us-ashburn-1 \
  --set ociCredentials.secretName=oci-privatekey
```

Note: The Secret containing the private key must be created separately for security reasons.

## Notes

- This chart installs resources into the `oracle-database-operator-system` namespace. This is hardcoded due to CRDs containing webhook configurations.
- CRDs are automatically installed from the `crds/` directory.
- cert-manager must be installed before deploying this chart. The chart will fail at install time if cert-manager CRDs are not present.
