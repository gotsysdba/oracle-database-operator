"""Synchronize chart CRDs and admission definitions from repository sources.

Requires Python 3.10+ and kubectl on PATH. Reads oracle-database-operator.yaml,
config/crd/bases/, and webhook markers under apis/. Refresh the repository's
generated bundle and CRD bases before syncing; schema mismatches fail validation.

Writes helm/crds/*.yaml and helm/files/webhooks.json. Removed CRDs require manual
review. With --check, reports drift without writing and exits 1 on drift, 0 on a
match. Invalid source definitions also cause a nonzero exit.

Usage from the repository root:
    python3 helm/scripts/sync_manifests.py --check
    python3 helm/scripts/sync_manifests.py
"""

import argparse
import json
from pathlib import Path
import re

from manifest_utils import decode


CHART = Path(__file__).resolve().parents[1]
ROOT = CHART.parent


def generated_files():
    bundle = (ROOT / 'oracle-database-operator.yaml').read_text()
    texts = [text.strip() for text in re.split(r'^---\s*$', bundle, flags=re.MULTILINE)
             if re.search(r'^kind: CustomResourceDefinition$', text, re.MULTILINE)]
    crds = decode('\n---\n'.join(texts))
    if not crds:
        raise ValueError('the operator bundle contains no CRDs')
    files = {}
    served = set()
    for text, crd in zip(texts, crds, strict=True):
        name = crd['metadata']['name']
        files[CHART / 'crds' / f'{name}.yaml'] = f'---\n{text}\n'
        spec = crd['spec']
        served.update((spec['group'], spec['names']['plural'], version['name'])
                      for version in spec['versions'] if version['served'])
        base = ROOT / 'config/crd/bases' / f"{spec['group']}_{spec['names']['plural']}.yaml"
        if decode(base.read_text())[0]['spec']['versions'] != spec['versions']:
            raise ValueError(f'{base} and the operator bundle have different CRD schemas')

    hooks = {'mutating': {}, 'validating': {}}
    for source in sorted((ROOT / 'apis').rglob('*webhook.go')):
        for marker in re.findall(r'kubebuilder:webhook:([^\n]+)', source.read_text()):
            fields = dict(field.split('=', 1) for field in re.split(r',(?=\w+=)', marker))
            identity = (fields['groups'], fields['resources'], fields['versions'])
            if identity not in served:
                continue
            hook = {
                'name': fields['name'], 'path': fields['path'],
                'apiGroup': fields['groups'], 'apiVersion': fields['versions'],
                'resources': fields['resources'],
                'operations': [verb.upper() for verb in fields['verbs'].split(';')],
                'matchPolicy': fields.get('matchPolicy', 'Equivalent'),
            }
            kind = 'mutating' if fields['mutating'] == 'true' else 'validating'
            previous = hooks[kind].get(hook['name'])
            if previous is not None and previous != hook:
                raise ValueError(f"conflicting webhook markers: {hook['name']}")
            hooks[kind][hook['name']] = hook
    data = {kind: [entries[name] for name in sorted(entries)]
            for kind, entries in hooks.items()}
    files[CHART / 'files/webhooks.json'] = json.dumps(data, indent=2) + '\n'
    return files


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--check', action='store_true', help='report drift without writing')
    args = parser.parse_args()
    expected = generated_files()
    stale = set((CHART / 'crds').glob('*.yaml')) - expected.keys()
    changed = [path for path, text in expected.items()
               if not path.exists() or path.read_text() != text]
    if args.check:
        for path in sorted(stale | set(changed)):
            print(f'Out of date: {path.relative_to(CHART)}')
        return int(bool(stale or changed))
    if stale:
        raise ValueError('review removed CRDs before deleting: ' + ', '.join(map(str, sorted(stale))))
    for path in changed:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(expected[path])
        print(f'Updated {path.relative_to(CHART)}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
