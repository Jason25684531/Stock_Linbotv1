"""Seed and refresh the persistent task state of the 2026-11-10 engine-accounting and history-gap round (F01-F10, 21 strategies).

    python -m scripts.engine_fix_state seed        # F01-F10 rows with what is already verifiable on disk
    python -m scripts.engine_fix_state strategies  # one S:<strategy_id> row per catalog strategy from the latest cross-regime report

Pure bookkeeping on outputs/engine_history_fix/state.json through core.research.history.taskstate; nothing here runs the engine or fetches.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from core.research.history import taskstate as ts

OUT = ROOT / 'outputs/engine_history_fix'
TITLES = {'F01': '配股誤計為虧損：持股、現金、NAV 會計', 'F02': '現金股利、除權息入帳與重複入帳保護', 'F03': '減資、分割、面額變更一致處理',
          'F04': '期末未平倉部位 NAV 與暴露', 'F05': '2016～2018 八個週六交易日', 'F06': '2023～2025 減資／面額變更', 'F07': '2015 年 2384、5280 交易狀態',
          'F08': '2019 年市場環境標籤（Benchmark）', 'F09': '新版引擎重跑 21 策略', 'F10': 'Web 呈現新舊差異、可信度與執行狀態'}
FINAL_TO_STATE = {'NEW_RUN_COMPLETE': 'PASS', 'REUSE_VERIFIED': 'PASS', 'DATA_BLOCKED': 'BLOCKED', 'ENGINE_UNSUPPORTED': 'BLOCKED', 'GOVERNED_ONLY': 'BLOCKED',
                  'FAILED': 'FAILED'}


def _evidence() -> dict:
    try:
        return json.loads((OUT / 'evidence.json').read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return {}


def seed() -> None:
    for task_id, title in TITLES.items():
        if task_id not in ts.load()['tasks']:
            ts.update(task_id, 'PENDING', title=title)
    evidence = _evidence()
    if evidence:
        sat = {d: v for d, v in evidence.get('saturdays', {}).items() if not d.startswith('_')}
        ok = len(sat) == 8 and all(v.get('in_snapshot') and v.get('date_matches') and v.get('finlab', {}).get('compared') == v.get('finlab', {}).get('agreed')
                                   for v in sat.values())
        ts.update('F05', 'PASS' if ok else 'FAILED', evidence={'files': len(sat), 'http_requests': evidence.get('budget', {}).get('http_requests_this_round')},
                  checkpoint='snapshot:' + str(evidence.get('snapshot', {}).get('new')), retryable=not ok, error_class=None if ok else 'DATA_MISSING')
        cap = evidence.get('capital_changes_2023_2025', {})
        ok = bool(cap.get('TWTAUU')) and bool(cap.get('TWTB8U')) and cap.get('unexplained_jumps_after') == 0
        ts.update('F06', 'PASS' if ok else 'FAILED', evidence={k: cap.get(k) for k in ('unexplained_jumps_before', 'unexplained_jumps_after')}, retryable=not ok,
                  note='2024～2025 無獨立對帳來源（FinLab 至 2023），覆蓋率為 PARTIAL；官方來源完整、無未解釋跳空', error_class=None if ok else 'DATA_INCONSISTENT')
        d = evidence.get('delisted_2015', {})
        ts.update('F07', 'PASS' if d.get('verdict', '').startswith('VERIFIED') else 'BLOCKED', evidence=d.get('verdict'), retryable=False)
        b = evidence.get('benchmark_2019', {})
        ok = b.get('sessions_without_index_after') == 0
        ts.update('F08', 'PASS' if ok else 'FAILED', evidence={k: b.get(k) for k in ('sessions_without_index_before', 'sessions_without_index_after', 'fixed_date')}, retryable=not ok)


def strategies() -> None:
    from scripts import historical_foundation as hf
    pointer = json.loads((hf.REPORTS / 'LATEST.json').read_text(encoding='utf-8'))
    if not pointer.get('cross_regime'):
        return
    report = json.loads((hf.REPORTS / pointer['cross_regime']).read_text(encoding='utf-8'))
    for entry in report['strategies']:
        final = entry.get('final_state', 'FAILED')
        status = FINAL_TO_STATE.get(final, 'FAILED')
        ts.update(f"S:{entry['strategy_id']}", status, title=entry['strategy_id'], retryable=status == 'FAILED',
                  error_class={'DATA_BLOCKED': 'DATA_MISSING', 'GOVERNED_ONLY': 'AUTHORIZATION', 'ENGINE_UNSUPPORTED': 'CODE_DEFECT', 'FAILED': 'CODE_DEFECT'}.get(final),
                  evidence={'final_state': final, 'run_id': entry.get('run_id'), 'reason': entry.get('reason'), 'accounting': entry.get('accounting'),
                            'priority': (entry.get('decision') or {}).get('priority')}, checkpoint=report.get('snapshot_id'))
    terminal = all(FINAL_TO_STATE.get(e.get('final_state'), 'FAILED') in ('PASS', 'BLOCKED') for e in report['strategies'])
    ts.update('F09', 'PASS' if terminal else 'FAILED', evidence={'strategies': len(report['strategies']), 'snapshot_id': report.get('snapshot_id'),
                                                                 'states': {e['strategy_id']: e.get('final_state') for e in report['strategies']}}, retryable=not terminal)


def main(argv=None) -> int:
    command = (argv or sys.argv[1:] or ['seed'])[0]
    {'seed': seed, 'strategies': strategies}[command]()
    print(json.dumps(ts.summary(), ensure_ascii=False))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
