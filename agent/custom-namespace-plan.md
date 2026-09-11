# Fresh Helm installation in a custom namespace

Use the Helm release namespace for one operator installation. Preserve native
Helm CRD retention and the existing controller image.

When the release namespace is default, preserve oracle-database-operator-system
and its managed Namespace resource. Retain that Namespace on uninstall.

## Implementation

- Replace the fixed namespace helper with the release namespace.
- Patch native CRD certificate and conversion-service references with a
  post-install/post-upgrade Job using CRD-specific patch permissions.
- Update chart documentation and tests for default and custom namespaces.

## Validation

Run chart tests and Helm lint. Check the rendered kubectl arguments and patches
for correct targets and preservation of CRD schemas and conversion settings.
Cluster validation requires certificate issuance and cross-version CRD reads.

## Progress

- Inspected chart templates, CRDs, controller namespace configuration and tests.
- The referenced agent/PLANS.md is absent; this file records the execution plan.
- Implemented release namespace selection, native CRD reference configuration,
  matching RBAC and certificate references, documentation and regression tests.
- Simplify review removed the shell dependency from the configuration Job and
  duplicate RoleBindings when watching the release namespace.
- Live kind testing found that kubectl's request-timeout override prevents its
  in-cluster configuration fallback. The Job uses its execution deadline.
- Verified certificate issuance, conversion from stored v4 to requested v1alpha1,
  successful upgrade hooks and retention of all 17 CRDs after Helm uninstall.
- Removed the test CRDs after retention verification to repeat a fresh install.
- Fresh installation in oracle-fresh passed with the default images, both
  certificates ready and successful v4-to-v1alpha1 API conversion.
- All 16 chart tests, local CRD merge-patch checks and Helm lint passed.
- The disposable oracle-namespace-test kind cluster remains available with
  kubeconfig /private/tmp/oracle-namespace-test.ifgl5r/kubeconfig. Conversion
  probe resources were deleted; no external database credentials were supplied.
- The configuration Job shares nodeSelector, tolerations and explicit affinity.
  All 17 chart tests and Helm lint passed. A live upgrade succeeded with the
  cluster's only node tainted NoSchedule and matching scheduling settings.
