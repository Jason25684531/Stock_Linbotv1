"""Freeze the pre-fix state of the historical reference engine (round 2026-11-10 engine accounting and history gaps, F01-F10, step 2.1).

    python -m scripts.engine_fix_baseline

Writes outputs/engine_history_fix/legacy_baseline.json once: SHA256 of every engine code file, the latest snapshot id, every stored
historical and reference run (id, strategy, window, run.json / nav.csv / trades.csv SHA256), the cost assumptions and the corporate-action
source versions. Read-only on everything else; an existing baseline is never rewritten.
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

OUT = ROOT / 'outputs/engine_history_fix'
BASELINE = OUT / 'legacy_baseline.json'


def _runs(root: Path) -> list[dict]:
    from core.research.history.snapshot import sha256
    rows = []
    for folder in sorted(root.glob('2*')):
        record = json.loads((folder / 'run.json').read_text(encoding='utf-8'))
        rows.append({'run_id': folder.name, 'strategy_id': record.get('strategy_id'), 'status': record.get('status'), 'period': record.get('period'),
                     'snapshot_id': record.get('snapshot_id'), 'run_identity': record.get('run_identity'), 'accounting': record.get('accounting', 'LEGACY_RAW_PRICE'),
                     'sha256': {name: sha256(folder / name) for name in ('run.json', 'nav.csv', 'trades.csv') if (folder / name).exists()}})
    return rows


def build() -> dict:
    from core.research.history import snapshot as hs
    from core.research.reference import runner
    from scripts import historical_foundation as hf
    from scripts import legacy_baseline_run as legacy
    code = sorted({*legacy.code_paths(), ROOT / 'scripts/history_engine.py', ROOT / 'scripts/reference_engine.py', Path(hs.__file__).resolve(),
                   ROOT / 'core/strategies/_tquant.py'})
    snap = hf.snapshot()
    actions = {f'{g}/{n}': e['sha256'] for g in ('twt49u', 'capital') for n, e in snap.manifest['sources'].get(g, {}).items()}
    return {'frozen_at': datetime.now(timezone.utc).isoformat(timespec='seconds'), 'engine_version': 'LEGACY_RAW_PRICE',
            'engine_code_sha256': {hs._key(p): hs.sha256(p) for p in code}, 'snapshot_id': snap.snapshot_id,
            'snapshot_manifest_sha256': hs.sha256(snap.root / 'manifest.json'), 'costs': legacy._costs(), 'stress_multiple': legacy.STRESS_MULTIPLE,
            'valuation_policy': 'finite_positive_stale', 'corporate_action_sources': actions,
            'historical_runs': _runs(hf.RUNS), 'reference_runs': _runs(runner._runs()),
            'label': 'LEGACY_ACCOUNTING/PROVISIONAL: raw-price engine without corporate-action ledger; kept immutable for comparison'}


def main() -> int:
    if BASELINE.exists():
        print(json.dumps({'baseline': str(BASELINE), 'state': 'REUSE'}))
        return 0
    OUT.mkdir(parents=True, exist_ok=True)
    baseline = build()
    with open(BASELINE, 'x', encoding='utf-8') as stream:
        json.dump(baseline, stream, ensure_ascii=False, indent=1)
    print(json.dumps({'baseline': str(BASELINE), 'state': 'WRITTEN', 'historical_runs': len(baseline['historical_runs']),
                      'reference_runs': len(baseline['reference_runs']), 'snapshot_id': baseline['snapshot_id']}))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
