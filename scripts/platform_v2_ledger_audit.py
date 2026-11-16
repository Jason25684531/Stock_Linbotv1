"""Reconcile saved V2 cost tracks without changing immutable run directories."""
from __future__ import annotations

import json
import sys
from collections import defaultdict
from datetime import datetime, timezone
from math import floor
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from core.backtest import run_store
from core.research.platform.ledger_v2 import daily_ledger, reconcile

OUT = ROOT / 'outputs/platform_v2/acceptance/ledger/independent_ledger_audit.json'


def daily_ledger_mismatch(saved_ledger, expected_ledger, nav):
    if len(saved_ledger) != len(expected_ledger):
        index = min(len(saved_ledger), len(expected_ledger))
        return nav[index][0] if index < len(nav) else str(saved_ledger.iloc[index]['date'])[:10]
    for i, (saved, expected) in enumerate(zip(saved_ledger.to_dict('records'), expected_ledger.to_dict('records'))):
        day = str(nav[i][0])[:10]
        if str(saved.get('date'))[:10] != day or str(expected.get('date'))[:10] != day:
            return day
        numeric = ('cash', 'positions_value', 'nav')
        try:
            differs = (any(not np.isclose(float(saved.get(key)), float(expected.get(key)), rtol=1e-9, atol=0.01)
                           for key in numeric)
                       or int(saved.get('holdings')) != int(expected.get('holdings')))
        except (TypeError, ValueError, OverflowError):
            return day
        if differs:
            return day
    return None


def recovered_actions(base_actions, fills, dates):
    """Reapply saved base event terms to stress holdings; this is not native stress evidence."""
    actions_by_day, fills_by_day = defaultdict(list), defaultdict(list)
    for row in base_actions:
        actions_by_day[row['date']].append(row)
    for row in fills:
        fills_by_day[row['date']].append(row)
    held, actions = {}, []
    for day in dates:
        for row in actions_by_day[day]:
            action = dict(row)
            if action['status'] == 'BOOKED':
                before = held.get(action['stock_id'], 0)
                exact = before * float(action['share_multiplier'])
                after = floor(exact + 1e-9)
                action.update(shares_before=before, shares_after=after,
                              cash_credited=before * float(action['cash_per_share']) + (exact - after) * 10)
                held[action['stock_id']] = after
            actions.append(action)
        for fill in fills_by_day[day]:
            sid = fill['stock_id']
            held[sid] = held.get(sid, 0) + int(fill['shares']) * (1 if fill['side'] == 'BUY' else -1)
        held = {sid: shares for sid, shares in held.items() if shares}
    return actions


def audit_run(folder):
    loaded = run_store.load_run(folder.name, root=folder.parent)
    record = loaded['run']
    result = {'kind': folder.parent.name, 'run_id': folder.name, 'strategy_id': record['strategy_id'],
              'run_identity': record.get('run_identity'), 'snapshot_id': record.get('snapshot_id'),
              'saved_publishable': record.get('publishable'),
              'manifest_sha256': run_store.file_hash(folder / 'manifest.json'), 'tracks': {}}
    nav = run_store.read_frame(loaded, 'nav')
    for track, column, suffix in (('base', 'nav_base', ''), ('stress', 'nav_stress', '_stress')):
        needed = [f'fills{suffix}', f'positions_daily{suffix}', f'corporate_actions{suffix}', f'open_positions{suffix}', f'daily_ledger{suffix}']
        missing = [name for name in needed if f'{name}.csv' not in loaded['files'] and f'{name}.json' not in loaded['files']]
        for name, columns in ((f'fills{suffix}', {'date', 'stock_id', 'side', 'shares', 'cash_delta'}),
                              (f'positions_daily{suffix}', {'date', 'stock_id', 'shares', 'value'}),
                              (f'daily_ledger{suffix}', {'date', 'cash', 'positions_value', 'nav', 'holdings'})):
            if name not in missing and not columns.issubset(run_store.read_frame(loaded, name).columns):
                missing.append(f'{name}:required_columns')
        if track == 'stress' and missing == ['corporate_actions_stress']:
            actions = recovered_actions(run_store.read_frame(loaded, 'corporate_actions').to_dict('records'),
                                        run_store.read_frame(loaded, 'fills_stress').to_dict('records'), nav['date'])
            mode = 'RECOVERED_FROM_BASE_EVENT_TERMS'
        elif missing:
            result['tracks'][track] = {'status': 'BLOCKED', 'reason': 'MISSING_SAVED_EVIDENCE',
                                       'missing_files': missing, 'evidence_mode': 'NONE'}
            continue
        else:
            actions = run_store.read_frame(loaded, f'corporate_actions{suffix}').to_dict('records')
            mode = 'NATIVE_SAVED_EVENTS'
        fills = run_store.read_frame(loaded, f'fills{suffix}').to_dict('records')
        positions = run_store.read_frame(loaded, f'positions_daily{suffix}').to_dict('records')
        opened = run_store.read_frame(loaded, f'open_positions{suffix}')
        nav_track = list(nav[['date', column]].itertuples(index=False, name=None))
        check = reconcile(record['parameters']['capital'], nav_track,
                          fills, actions, positions, opened)
        if check['status'] == 'PUBLISHABLE':
            saved_ledger = run_store.read_frame(loaded, f'daily_ledger{suffix}')
            expected_ledger = pd.DataFrame(daily_ledger(nav_track, positions))
            mismatch = daily_ledger_mismatch(saved_ledger, expected_ledger, nav_track)
            if mismatch is not None:
                check = {'status': 'NOT_PUBLISHABLE', 'first_mismatch': mismatch,
                         'reason': 'DAILY_LEDGER_MISMATCH', 'days_checked': check.get('days_checked', 0)}
        result['tracks'][track] = check | {'evidence_mode': mode, 'missing_files': missing}
    return result


def main():
    rows = []
    for kind in ('runs', 'portfolios'):
        root = ROOT / 'outputs/platform_v2' / kind
        for folder in sorted(root.glob('2*')):
            if folder.is_dir():
                loaded = run_store.load_run(folder.name, root=root)
                if loaded['run'].get('status') == 'SUCCESS':
                    rows.append(audit_run(folder))
    payload = {'generated_at_utc': datetime.now(timezone.utc).isoformat(),
               'audit_code_sha256': run_store.file_hash(Path(__file__)),
               'reconcile_code_sha256': run_store.file_hash(ROOT / 'core/research/platform/ledger_v2.py'),
               'source': 'manifest-verified immutable saved runs; no historical run changed', 'runs': rows}
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False), encoding='utf-8')
    print(json.dumps({'output': str(OUT), 'runs': len(rows), 'tracks': {
        status: sum(t['status'] == status for row in rows for t in row['tracks'].values())
        for status in ('PUBLISHABLE', 'NOT_PUBLISHABLE', 'BLOCKED')}}, ensure_ascii=False))


if __name__ == '__main__':
    main()
