# Moving release-managed CRDs to native Helm CRDs

Existing releases that list CRDs in `helm get manifest` need a retention
transition before upgrading to a chart that places those CRDs in `crds/`.
Removing a CRD from the release manifest can delete it and all its custom resources.

1. Back up the affected CRDs and their custom resources. Identify every CRD in
   the existing release manifest and confirm its owner.
2. Prepare a transition version of the currently installed chart. Keep its CRD
   templates and schemas unchanged, adding `helm.sh/resource-policy: keep` to
   each CRD's metadata annotations.
3. Upgrade to that transition version with the existing configuration. Verify
   that `helm get manifest` contains the retention annotation on every CRD.
   Annotating only the live Kubernetes object is insufficient: retention must
   also be recorded in Helm's release manifest.
4. Upgrade to the native-CRD chart. Verify the existing CRDs and custom resources
   remain present. Treat the transition revision as the rollback floor; older
   revisions may reintroduce release-managed CRDs without retention.

CRDs already installed through `crds/` are outside the release manifest and need
no ownership transfer. Helm skips existing native CRDs; review and apply schema
updates separately before upgrading the operator, preserving stored API versions
and the deployed conversion-webhook configuration.

Validate this migration in a disposable cluster before using it with database
resources. Uninstall/reinstall and force replacement are unsuitable migration
procedures because they can destroy custom resources.

See [Helm's CRD lifecycle guidance](https://helm.sh/docs/chart_best_practices/custom_resource_definitions/).
