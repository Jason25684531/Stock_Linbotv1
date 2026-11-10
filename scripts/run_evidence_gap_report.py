"""Evidence gap reports for change 2026-11-09-autonomous-data-readiness-v1 (P1.3, P2.3, P4.5). Read-only over runs, ledgers and data;
writes only under outputs/autonomous_data_readiness/. Never sends a request, never rewrites a run, manifest or ledger.

python scripts/run_evidence_gap_report.py corporate-actions | pit | benchmark | runs | all
"""
from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
OUT = ROOT / 'outputs/autonomous_data_readiness'
RECOVERY = ROOT / 'outputs/research_mode/legacy_od29_recovery_v1'
STRATEGIES = ('hybrid_trend_rank', 'institutional_flow_confirmation', 'mean_reversion', 'quality_value_low_volatility')
NOTE = 'Events were applied after these runs finished; the stored run verdicts and files are unchanged.'


def _write(folder, name, rows, summary, lines):
    folder = OUT / folder
    folder.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows).to_csv(folder / f'{name}.csv', index=False, encoding='utf-8')
    (folder / 'summary.json').write_text(json.dumps(summary, ensure_ascii=False, indent=1, default=str), encoding='utf-8')
    (folder / 'report.md').write_text('\n'.join(lines) + '\n', encoding='utf-8')
    return summary


def corporate_actions_report():
    from core.backtest import run_store
    from core.research import corporate_actions as ca
    table = ca.load()
    rows, statuses = [], {}
    for sid in STRATEGIES:
        evidence = json.loads((RECOVERY / f'{sid}.json').read_text(encoding='utf-8'))
        run_store.load_run(evidence['run_id'])  # raises if the manifest no longer verifies
        for track, item in evidence['checks']['valuation'].items():
            verdict = ca.verdict(item['events'], table)
            statuses[f'{sid}:{track}'] = {'stored': item['quality']['status'], 'with_actions_after_run': verdict['status']}
            for event, found in zip(item['events'], verdict['event_match']):
                status = {'MATCHED_ACTION': 'VALUATION_BLOCKED_CORPORATE_ACTION', 'NO_EVENT_IN_SOURCE': 'ACTIONS_UNVERIFIED',
                          'OUTSIDE_SOURCE_COVERAGE': 'CORPORATE_ACTION_NOT_COVERED'}[found['match']]
                rows.append({'strategy_id': sid, 'run_id': evidence['run_id'], 'track': track, 'date': event['date'], 'stock_id': event['stock_id'],
                             'reason': event['reason'], 'price_source_date': event['price_source_date'], 'stale_days': event['stale_days'],
                             'match': found['match'], 'status': status})
    unique = {(r['date'], r['stock_id']) for r in rows}
    summary = {'source': ca.SOURCE, 'source_sha256': table['source_sha256'], 'coverage': table['status'], 'events_in_source': len(table['events']),
               'stale_event_rows': len(rows), 'unique_stale_events': len(unique), 'by_status': dict(Counter(r['status'] for r in rows)),
               'verdicts': statuses, 'note': NOTE}
    lines = ['# Corporate action diagnosis of the OD-29 recovery runs', '', f'- Source: {ca.SOURCE}', f"- Coverage: **{table['status']}**",
             f"- Stale event rows (base and stress): {len(rows)}; unique (date, stock): {len(unique)}", f'- {NOTE}', '',
             '| Strategy | Track | Date | Stock | Match | Status |', '|---|---|---|---|---|---|',
             *[f"| {r['strategy_id']} | {r['track']} | {r['date']} | {r['stock_id']} | {r['match']} | {r['status']} |" for r in rows], '',
             '| Run track | Stored verdict | With actions (after run) |', '|---|---|---|',
             *[f'| {k} | {v["stored"]} | {v["with_actions_after_run"]} |' for k, v in statuses.items()]]
    return _write('corporate_actions', 'events', rows, summary, lines)


def pit_report():
    """V38 filings in the development window without a MOPS publication date. Reads keys only (no values) of fiscal years <= 2025."""
    from core.research import native_frame as nf
    from core.runtime import research_budget
    from scripts import legacy_baseline_run as lb
    before = len(research_budget.audit_rows())
    keys = pd.read_parquet(lb.input_paths()['financial_statements'], columns=['stock_id', 'year', 'quarter'], filters=[('year', '<=', 2025)])
    keys = keys[(keys.year >= 2022)].astype({'stock_id': str, 'year': int, 'quarter': int}).drop_duplicates()
    deadline = keys.quarter.map(nf.QUARTER_DEADLINES)
    due = nf._due(keys.year + deadline.str[2], deadline.str[0], deadline.str[1]).set_axis(keys.index)
    keys = keys[due <= pd.Timestamp('2025-12-31')]
    # read-time filter: no filing of a fiscal year after 2025, and nothing published after 2025-12-31 is used
    records = pd.read_parquet(nf.PIT_RECORDS, columns=['ticker', 'fiscal_year', 'quarter', 'publication_date', 'data_version'],
                              filters=[('fiscal_year', '<=', 2025), ('data_version', '==', 'fundamental-pit-v3')])
    records = records[pd.to_datetime(records.publication_date) <= pd.Timestamp('2025-12-31')]
    first = records.groupby(['ticker', 'fiscal_year', 'quarter']).publication_date.min().reset_index()
    published = pd.DataFrame({'stock_id': first.ticker.astype(str), 'year': first.fiscal_year.astype(int), 'quarter': first.quarter.astype(int),
                              'publication_date': pd.to_datetime(first.publication_date)})
    merged = keys.merge(published, on=['stock_id', 'year', 'quarter'], how='left')
    missing = merged[merged.publication_date.isna()][['stock_id', 'year', 'quarter']].sort_values(['year', 'quarter', 'stock_id'])
    v38 = json.loads((RECOVERY / 'quality_value_low_volatility.json').read_text(encoding='utf-8'))
    summary = {'filings_in_scope': len(keys), 'with_mops_publication_date': int(merged.publication_date.notna().sum()), 'missing_publication_date': len(missing),
               'missing_by_year': {str(k): int(v) for k, v in missing.year.value_counts().sort_index().items()},
               'endpoint': 'POST https://doc.twse.com.tw/server-java/t57sb01 (registered public filing form, purpose mops_research)',
               'requests_needed': len(missing), 'budget': 'none assigned; Owner decision (OWNER_ACTIONS)',
               'http_attempts_by_this_report': len(research_budget.audit_rows()) - before,
               'historical_run': {'run_id': v38['run_id'], 'pit_detail': 'HISTORICAL_PIT_DETAIL_MISSING', 'pit_status': v38['checks']['pit_status'],
                                  'pit_checks': v38['checks']['pit_checks'], 'pit_unverified': v38['checks']['pit_unverified']},
               'rule': 'No period-end or statutory-deadline date is used as a publication date; such filings stay PIT_UNVERIFIED.'}
    lines = ['# V38 PIT publication-date gap', '', *[f'- {k}: {v}' for k, v in summary.items()]]
    return _write('pit', 'missing_publication_dates', missing.to_dict('records'), summary, lines)


def benchmark_report():
    """Provenance and diagnostic anomalies of the local total-return series; the price index comes from MI_INDEX files chosen by name."""
    from core.research import benchmark_tri as tri
    rows, _ = tri.index_from_mi_index(tri.PRICE_ROW)
    price = pd.Series([float(v) for _, v in rows], index=pd.to_datetime([d for d, _ in rows]))
    summary = tri.provenance(tri.research_calendar(), price=price)
    gaps = summary['DIAGNOSTIC_ANOMALY'].get('return_gap_over_3pct', [])
    lines = ['# Benchmark provenance (diagnostic only)', '', *[f'- {k}: {v}' for k, v in summary.items() if k != 'file_sha256'],
             '', 'Differences and jumps are diagnostics. They do not show that any source is wrong, and nothing was changed.']
    return _write('benchmark', 'return_gap_days', [{'date': d} for d in gaps], summary, lines)


def runs_report():
    from core.backtest import run_store
    from scripts import legacy_baseline_run as lb, research_workflow as rw
    verified = rw.verify_trials() == 'PASS'
    ledger = {}
    for row in rw._trial_rows():
        ledger.setdefault(row['experiment_id'], row)  # the admission (TRIAL) row, not a later REPLAY of it
    rows = []
    for run in run_store.history():
        if not run['verified']:
            rows.append({'run_id': run['run_id'], 'status': 'MANIFEST_MISMATCH', 'evidence_status': None})
            continue
        loaded = run_store.load_run(run['run_id'])
        found = ledger.get(run.get('trial_id'))
        evidence = run_store.evidence_status(loaded, found, verified, rw._tree_sha)
        inputs = (found or {}).get('inputs_sha256') or {}
        unpinned = [role for role, name in lb.PINNED_INPUTS.items() if isinstance(name, str) and name not in inputs] if found else []
        rows.append({'run_id': run['run_id'], 'strategy_id': run.get('strategy_id'), 'status': run.get('status'), 'status_class': run.get('status_class'),
                     'evidence_status': evidence['status'], 'reasons': ';'.join(evidence['reasons']),
                     'ledger_inputs_without_pinned': ';'.join(unpinned), 'reproducible_offline': evidence['reproducible_offline'],
                     'missing_v2_files': ';'.join(n for n in run_store.REQUIRED_FILES_V2 if f'{n}.csv' not in loaded['files'] and f'{n}.json' not in loaded['files'])})
    summary = {'runs': len(rows), 'trial_ledger_verify': rw.verify_trials(), 'by_evidence_status': dict(Counter(r['evidence_status'] for r in rows)),
               'reproducible_offline': sum(bool(r.get('reproducible_offline')) for r in rows),
               'note': 'Historical runs keep their files and manifests; this classification is a separate diagnosis.'}
    lines = ['# Run Store evidence gaps', '', *[f'- {k}: {v}' for k, v in summary.items()], '',
             '| Run | Strategy | Status | Evidence | Reasons | Unpinned in ledger |', '|---|---|---|---|---|---|',
             *[f"| {r['run_id']} | {r.get('strategy_id')} | {r['status']} | {r['evidence_status']} | {r.get('reasons')} | {r.get('ledger_inputs_without_pinned')} |"
               for r in rows]]
    return _write('runs', 'run_evidence', rows, summary, lines)


def main(argv=None):
    reports = {'corporate-actions': corporate_actions_report, 'pit': pit_report, 'benchmark': benchmark_report, 'runs': runs_report}
    chosen = (argv or sys.argv[1:] or ['all'])[0]
    for name, report in reports.items():
        if chosen in (name, 'all'):
            print(name, json.dumps({k: v for k, v in report().items() if not isinstance(v, (dict, list)) or k.startswith('by_')}, ensure_ascii=False, default=str))


if __name__ == '__main__':
    main()
