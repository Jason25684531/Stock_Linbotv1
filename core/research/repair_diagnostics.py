"""Read-only, provenance-labelled historical strategy status for Research consumers."""
import csv
import hashlib
import io
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MATRIX = ROOT / 'outputs/research_workflow/2026-10-28/strategy_qualification_matrix.csv'
A191_SUMMARY = ROOT / 'outputs/research_workflow/2026-10-29/a191_full_recheck_133235/run/summary.json'


def assess(row: dict) -> str:
    """Apply the existing staged criteria to this strategy's own recorded evidence."""
    if (row.get('pit_status', '').startswith('BLOCKED') or row.get('DATA_STATUS') != 'DATA_CERTIFIED' or row.get('SIGNAL_STATUS') != 'KEEP'
            or row.get('BACKTEST_STATUS') != 'CREDIBLE' or row.get('STATISTICAL_STATUS') not in ('PASS', 'FAIL')):
        return 'BLOCKED'
    return 'ELIGIBLE_FOR_OOS_REVIEW' if row['STATISTICAL_STATUS'] == 'PASS' else 'REJECTED'


def diagnostics() -> dict:
    """Read existing outputs only; never emit signals, make trades, or alter recorded verdicts."""
    try:
        raw_matrix, raw_a191 = MATRIX.read_bytes(), A191_SUMMARY.read_bytes()
        records = list(csv.DictReader(io.StringIO(raw_matrix.decode('utf-8-sig'), newline='')))
        summary = json.loads(raw_a191.decode('utf-8'))
        required = {'group', 'strategy', 'DATA_STATUS', 'SIGNAL_STATUS', 'BACKTEST_STATUS', 'STATISTICAL_STATUS', 'FINAL_QUALIFICATION', 'reasons'}
        if (not records or not required.issubset(records[0]) or any(not r.get('strategy', '').strip() for r in records)
                or len({r['strategy'] for r in records}) != len(records) or 'A191_132' not in {r['strategy'] for r in records}):
            raise ValueError('MATRIX_INVALID')
        if summary['label'] != 'DIAGNOSTIC_ONLY' or summary['outcome'] not in ('REJECTED', 'NOT_REJECTED_DIAGNOSTIC_ONLY'):
            raise ValueError('A191_DIAGNOSTIC_INVALID')
        for row in records:
            if row['group'] in ('B2_D5', 'B3_DAY3'):
                row['pit_status'] = 'BLOCKED_LEGACY_NEXT_SESSION_TRADABILITY'
            row['repair_decision'] = assess(row)
            if row['strategy'] == 'A191_132':
                row['diagnostic_outcome'] = summary['outcome']
        return {'label': 'READ_ONLY_RESEARCH_DIAGNOSTIC', 'status': 'OK', 'strategies': records,
                'sources': {str(MATRIX): hashlib.sha256(raw_matrix).hexdigest(), str(A191_SUMMARY): hashlib.sha256(raw_a191).hexdigest()}}
    except (OSError, ValueError, KeyError, TypeError) as exc:
        return {'label': 'READ_ONLY_RESEARCH_DIAGNOSTIC', 'status': 'BLOCKED', 'reason': str(exc), 'strategies': []}
