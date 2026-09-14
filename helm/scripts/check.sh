#!/usr/bin/env bash
# Run strict Helm lint and the chart's semantic, generation, and packaging tests.
# Requires Python 3.10+, Helm 3.22.0 or Helm 4.2.4, and kubectl on PATH.
# Kubernetes manifests are decoded locally; packages use temporary directories.
# Exits nonzero if lint or any test fails.
#
# Usage from the repository root:
#   bash helm/scripts/check.sh

set -euo pipefail
chart_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
helm lint "$chart_dir" --strict --kube-version 1.36.3
python3 -m unittest discover -s "$chart_dir/tests" -v
