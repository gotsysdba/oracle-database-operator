"""Decode Kubernetes YAML streams into Python dictionaries using kubectl local mode.

Shared by chart generation and semantic tests. Requires Python 3.10+ and kubectl
on PATH; kubectl decoding failures propagate as subprocess.CalledProcessError.

Example from the repository root:
    PYTHONPATH=helm/scripts python3 - <<'PY'
    from pathlib import Path
    from manifest_utils import decode

    documents = decode(Path('helm/crds/autonomousdatabases.database.oracle.com.yaml').read_text())
    print([document['metadata']['name'] for document in documents])
    PY
"""

import json
import subprocess


def decode(text):
    if not text.strip():
        return []
    result = subprocess.check_output([
        'kubectl', 'patch', '--local', '--type=merge', '--patch', '{}',
        '-f', '-', '-o', 'json',
    ], input=text, text=True)
    decoder = json.JSONDecoder()
    documents = []
    while result.strip():
        result = result.lstrip()
        document, end = decoder.raw_decode(result)
        documents.append(document)
        result = result[end:]
    return documents
