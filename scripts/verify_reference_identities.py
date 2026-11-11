"""Read-only check that the 2023-2025 reference runs still REUSE: recompute every identity and look it up (writes nothing).

    python -m scripts.verify_reference_identities [--out FILE]
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def check() -> dict:
    from core.research.reference import catalog, runner
    caps = catalog.capabilities()
    ids = runner.identities(caps)
    rows = {sid: {'run_identity': identity, 'reuse_run': runner.lookup(identity)} for sid, identity in ids.items()}
    reused = sum(r['reuse_run'] is not None for r in rows.values())
    return {'executable': len(rows), 'reuse': reused, 'status': 'UNCHANGED' if reused == len(rows) else 'IDENTITY_CHANGED', 'rows': rows}


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--out')
    args = parser.parse_args()
    result = check()
    text = json.dumps(result, ensure_ascii=False, indent=1)
    if args.out:
        Path(args.out).write_text(text, encoding='utf-8')
    print(json.dumps({k: result[k] for k in ('executable', 'reuse', 'status')}))
