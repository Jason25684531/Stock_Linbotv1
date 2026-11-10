"""Per-strategy assessment of a reference batch (read-only): what ran, what it returned, whether it reproduced, what blocks trust.

Every number comes from the batch report, the run files (nav.csv, trades.csv) and the gap report; nothing is typed by hand. Derived
values are labelled: annualized return = (NAV_end / NAV_start) ** (252 / trading days) - 1 on the base-cost track; trade cost is the
sum of the `cost` column of the base-cost trade ledger (the 2x-cost track keeps a NAV only, so its cost amount is None with a reason).

The research flag is a stated rule, not a certification: CANDIDATE when the run reproduced and its 2x-cost return is positive,
PAUSE when the 2x-cost return is zero or negative, BLOCKED when it did not run. 21 fixed settings were compared, so any pick among
them is one of 21 choices and is not evidence.

CLI: python -m scripts.strategy_assessment BATCH_ID [--repro FILE] [--out-dir DIR]
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'outputs' / 'reference_mode'


def _metric(row, track, name):
    m = ((row.get('metrics') or {}).get(track) or {}).get(name) or {}
    return m.get('value'), m.get('reason')


def _run_numbers(run_id) -> dict:
    folder = OUT / 'runs' / run_id
    nav = pd.read_csv(folder / 'nav.csv')
    try:
        trades = pd.read_csv(folder / 'trades.csv')
    except pd.errors.EmptyDataError:
        trades = pd.DataFrame(columns=['cost'])
    days = len(nav) - 1
    start, end = nav.nav_base.iloc[0], nav.nav_base.iloc[-1]
    return {'period': [nav.date.iloc[0], nav.date.iloc[-1]], 'trading_days': days,
            'annualized_return': (end / start) ** (252 / days) - 1 if days > 0 and start > 0 else None,
            'annualized_return_x2': (nav.nav_stress.iloc[-1] / nav.nav_stress.iloc[0]) ** (252 / days) - 1 if days > 0 else None,
            'trades': len(trades), 'trade_cost': float(trades['cost'].sum()) if len(trades) else 0.0,
            'trade_cost_x2': None, 'trade_cost_x2_reason': 'STRESS_TRACK_HAS_NO_TRADE_LEDGER'}


def assess(batch_id, repro=None) -> list:
    report = json.loads((OUT / 'batches' / batch_id / 'report.json').read_text(encoding='utf-8'))
    gaps = json.loads((OUT / 'batches' / batch_id / 'gap_report.json').read_text(encoding='utf-8')).get('strategies', {}) \
        if (OUT / 'batches' / batch_id / 'gap_report.json').is_file() else {}
    repro = (repro or {}).get('strategies', {})
    from core.research.reference import catalog
    needs = {c.strategy_id: list(c.data_needs) for c in catalog.capabilities()}
    out = []
    for row in report['rows']:
        sid = row['strategy_id']
        item = {'strategy_id': sid, 'state': row['state'], 'reason': row['reason'], 'run_id': row.get('run_id'),
                'run_identity': row.get('run_identity'), 'comparison_group': row.get('comparison_group'),
                'data_needs': needs.get(sid, []), 'tej_required': False, 'levels': row.get('levels'), 'level_reasons': row.get('level_reasons')}
        for track in ('base', 'x2'):
            for name in ('total_return', 'sharpe', 'max_drawdown'):
                item[f'{name}_{track}'], item[f'{name}_{track}_reason'] = _metric(row, track, name)
        if row.get('run_id'):
            item.update(_run_numbers(row['run_id']))
        check = repro.get(sid, {})
        item['reproduced'] = check.get('pass')
        item['blocking_gaps'] = [{'field': g['field'], 'status': (g.get('recovery') or {}).get('status'), 'reason': (g.get('recovery') or {}).get('reason'),
                                  'next_step': (g.get('recovery') or {}).get('next_step')} for g in (gaps.get(sid) or {}).get('gaps', [])]
        x2 = item.get('total_return_x2')
        item['research_flag'] = ('BLOCKED' if not row.get('run_id') else 'CANDIDATE' if item['reproduced'] and x2 is not None and x2 > 0
                                 else 'PAUSE' if x2 is not None else 'BLOCKED')
        out.append(item)
    return out


def _pct(v):
    return 'None' if v is None else f'{v * 100:.2f}%'


def markdown(items) -> str:
    head = ('| 策略 | 狀態 | 期間 | 累積報酬 | 年化 | Sharpe | MDD | 2x 報酬 | 2x Sharpe | 2x MDD | 交易筆數 | 成本 (元) | 重現 | 研究判定 |\n'
            '|---|---|---|---|---|---|---|---|---|---|---|---|---|---|')
    lines = [head]
    for i in items:
        sharpe = lambda k: 'None (' + str(i.get(k + '_reason')) + ')' if i.get(k) is None else f'{i[k]:.4f}'
        if i.get('run_id'):
            lines.append(f"| {i['strategy_id']} | {i['state']} | {i['period'][0]}~{i['period'][1]} | {_pct(i['total_return_base'])} | "
                         f"{_pct(i['annualized_return'])} | {sharpe('sharpe_base')} | {_pct(i['max_drawdown_base'])} | {_pct(i['total_return_x2'])} | "
                         f"{sharpe('sharpe_x2')} | {_pct(i['max_drawdown_x2'])} | {i['trades']} | {i['trade_cost']:,.0f} | "
                         f"{'YES' if i['reproduced'] else 'NO' if i['reproduced'] is False else 'N/A'} | {i['research_flag']} |")
        else:
            lines.append(f"| {i['strategy_id']} | {i['state']}（{i['reason']}） | - | - | - | - | - | - | - | - | - | - | N/A | {i['research_flag']} |")
    return '\n'.join(lines) + '\n'


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument('batch_id')
    parser.add_argument('--repro')
    parser.add_argument('--out-dir')
    args = parser.parse_args(argv)
    repro = json.loads(Path(args.repro).read_text(encoding='utf-8')) if args.repro else None
    items = assess(args.batch_id, repro)
    if args.out_dir:
        folder = Path(args.out_dir)
        folder.mkdir(parents=True, exist_ok=True)
        (folder / 'strategy_assessment.json').write_text(json.dumps(items, ensure_ascii=False, indent=1, default=str), encoding='utf-8')
        (folder / 'strategy_assessment.md').write_text(markdown(items), encoding='utf-8')
    print(markdown(items))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
