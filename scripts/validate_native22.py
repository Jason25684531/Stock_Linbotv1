"""Native 22 family validation: reuse the 2026-10-21 audit, add data/engine checks, never certify blocked data."""
import argparse
from collections import Counter
import json
import math
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.audit_strategy_history import HISTORY, csv_rows, digest, read, write
from scripts.revalidate_strategy_history import emit_csv

AUDIT = ROOT / 'outputs/strategy_revalidation/2026-10-21-audit'
OUT = ROOT / 'outputs/native22_validation/2026-10-22'
NATIVE = ('defensive_low_volatility', 'growth_momentum_breakout', 'hybrid_trend_rank', 'institutional_flow_confirmation',
          'mean_reversion', 'quality_growth', 'quality_value_low_volatility')
DAY3 = ('A101_003', 'A101_003+A191_088', 'A101_003+A191_088+A191_132+RS_004_NEAR_HIGH_120D', 'A101_003+A191_132',
        'A101_003+RS_004_NEAR_HIGH_120D', 'A191_088', 'A191_088+A191_132', 'A191_088+RS_004_NEAR_HIGH_120D',
        'A191_132', 'A191_132+RS_004_NEAR_HIGH_120D', 'RS_004_NEAR_HIGH_120D')
FAMILIES = (
    [('A_FUNDAMENTAL', 'family:fundamental_strategy_validation:' + x) for x in
     ('G2_OPERATING_INCOME_YOY', 'G3_EPS_YOY', 'G2_OPERATING_INCOME_YOY+G3_EPS_YOY')]
    + [('B_NATIVE', 'family:native:' + x) for x in NATIVE]
    + [('C_D5', 'family:portfolio_research:amihud_20d+momentum_12_1+momentum_20d+momentum_60d+near_high_252d')]
    + [('D_DAY3', 'family:strategy_research:' + x) for x in DAY3])
EXPECTED_PARAMETERS = {'A_FUNDAMENTAL': 36, 'B_NATIVE': 0, 'C_D5': 48, 'D_DAY3': 300}
ENTRY = {
    'A_FUNDAMENTAL': ['jobs/run_fundamental_strategy_validation.py', 'core/research/fundamental_strategy_validation.py:373 simulate_target_weights'],
    'C_D5': ['jobs/run_portfolio_research.py', 'core/research/portfolio_research.py', 'core/research/vectorbt_adapter.py'],
    'D_DAY3': ['jobs/run_strategy_search.py', 'core/research/strategy_search.py', 'core/research/day2_candidates.py'],
}
CONTRACT = {
    'A_FUNDAMENTAL': 'signal_date < execution_date; missing price DEFERRED (fundamental_strategy_validation.py:207-211,404-424)',
    'B_NATIVE': 'historical: legacy_same_close (runner.py process_close_signals); corrected: next_session_open_v2',
    'C_D5': 'DISCRETE_EXECUTION_DATE_ONLY; observed-price-only rebalance (fixed fb39b99)',
    'D_DAY3': 'DISCRETE_EXECUTION_DATE_ONLY; D3 execution_date > asof_date (strategy_search.py:642)',
}


def out(name):
    path = OUT / name
    if path.exists():  # immutable evidence; rerun into a new directory
        raise FileExistsError(path)
    return path


def git_head():
    return subprocess.run(['git', 'rev-parse', 'HEAD'], cwd=ROOT, capture_output=True, text=True).stdout.strip()


def identities():
    records = read(AUDIT / 'identity_matrix_final.json')['records']
    wanted = {family for _, family in FAMILIES}
    return records, [r for r in records if r['family_id'] in wanted]


def group_of(family_id):
    return next(group for group, family in FAMILIES if family == family_id)


def num(value):
    try:
        value = float(value)
    except (TypeError, ValueError):
        return None
    return value if math.isfinite(value) else None


def historical_metrics(record):
    """Unify owner scoreboard columns; absent fields stay None (caller adds the reason)."""
    evidence = record.get('performance_evidence') or {}
    row = evidence.get('metrics') or {}
    pick = lambda *keys: next((num(row[k]) for k in keys if num(row.get(k)) is not None), None)
    return {
        'historical_source': f"{evidence['path']}:{evidence['line']}" if evidence else None,
        'historical_owner_gate': record['historical_performance'],
        'historical_cagr': pick('net_base_annualized_return', 'cagr', 'annualized_return'),
        'historical_sharpe': pick('net_base_sharpe', 'sharpe'),
        'historical_sortino': pick('net_base_sortino', 'sortino'),
        'historical_max_drawdown': pick('net_base_max_drawdown', 'mdd', 'max_drawdown'),
        'historical_win_rate': pick('win_rate', 'hit_rate'),
        'historical_trade_count': pick('net_base_trade_count', 'trade_count'),
        'historical_turnover': pick('net_base_turnover', 'turnover'),
        'historical_gross_return': pick('gross_total_return'),
        'historical_net_return': pick('net_base_total_return', 'total_return'),
        'historical_stress_cagr': pick('net_stress_annualized_return', 'stress_cagr'),
        'historical_stress_sharpe': pick('net_stress_sharpe', 'stress_sharpe'),
        'historical_positive_fold_ratio': pick('positive_fold_ratio'),
    }


def matrix():
    OUT.mkdir(parents=True, exist_ok=True)
    records, scoped = identities()
    family_rows = {r['family_id']: r for r in csv_rows(AUDIT / 'family_matrix.csv')}
    assert set(family_rows) >= {f for _, f in FAMILIES} and len(FAMILIES) == 22, 'FAMILY_SCOPE'
    params = [r for r in scoped if r['role'] == 'PARAMETER_CONFIG']
    counts = Counter(group_of(r['family_id']) for r in params)
    assert {g: counts.get(g, 0) for g in EXPECTED_PARAMETERS} == EXPECTED_PARAMETERS, counts
    assert len({r['id'] for r in params}) == 384, 'PARAMETER_UNIQUENESS'
    manifests = {Path(m['path']).parent.as_posix(): m['status'] for m in read(AUDIT / 'manifest_verification_corrected.json')['manifests']}
    stocks = read(ROOT / HISTORY / 'stocks.json')
    rows = []
    for group, family in FAMILIES:
        members = [r for r in scoped if r['family_id'] == family]
        runs = sorted({link['path'] for r in members for link in r['historical_runs'] if link['configuration_match']})
        native = next((r for r in members if r['role'] == 'NATIVE_PYTHON'), None)
        if native:
            runs = [HISTORY.as_posix() + '/stocks.json#' + native['id']]
        rows.append({
            'group': group, 'family_id': family, 'name': family_rows[family]['name'],
            'parameter_count': sum(r['role'] == 'PARAMETER_CONFIG' for r in members),
            'other_identities': ';'.join(f"{r['id']}[{r['role']}]" for r in members if r['role'] != 'PARAMETER_CONFIG'),
            'entry': ';'.join(ENTRY.get(group, [f"core/strategies/{native['id']}.py" if native else ''] + ['core/backtest/runner.py BacktestEngine'])),
            'execution_contract': CONTRACT[group],
            'historical_backtest': family_rows[family]['historical_backtest'],
            'historical_runs': ';'.join(runs),
            'manifest_status': ';'.join(sorted({manifests.get(Path(p).parent.as_posix(), 'NOT_VERIFIABLE') for p in runs if not p.endswith(tuple(NATIVE))})) or 'NOT_VERIFIABLE',
            'historical_evidence_verified': dict(Counter(r['historical_evidence_verified'] for r in members)),
            'reproducible': dict(Counter(r['reproducible'] for r in members)),
            'historical_owner_gate': dict(Counter(r['historical_performance'] for r in members)),
            'native_diagnostic_trades': len(stocks[native['id']].get('closed_trades', [])) if native else None,
            'source_sha256': {s['path']: s['sha256'] for r in members for s in r.get('sources', [])} if native else None,
        })
    emit_csv(out('family_validation_matrix.csv'), rows)
    param_rows = []
    for r in sorted(params, key=lambda r: (r['family_id'], r['id'])):
        links = [l for l in r['historical_runs'] if l['configuration_match']]
        param_rows.append({'family_id': r['family_id'], 'group': group_of(r['family_id']), 'id': r['id'],
            'parameters': r.get('parameters'), 'config_hash': (r.get('performance_evidence') or {}).get('metrics', {}).get('config_hash'),
            'historical_runs': len(links), 'historical_run_sha256': {f"{l['path']}:{l['line']}": l['sha256'] for l in links},
            'historical_evidence_verified': r['historical_evidence_verified'], 'reproducible': r['reproducible'],
            'corrected_replay': (r.get('corrected_replay') or {}).get('status'),
            **historical_metrics(r)})
    emit_csv(out('parameter_validation_matrix.csv'), param_rows)
    totals = {'families': len(rows), 'parameters': len(param_rows), 'by_group': dict(counts),
              'source_records': len(records), 'validation_identities': read(AUDIT / 'family_matrix.json')['summary']['historical_validation_identities']}
    print(json.dumps(totals, ensure_ascii=False))
    return totals


def preservation():
    """Historical evidence and identity sources must be byte-identical to the 2026-10-21 baseline."""
    hashes = read(AUDIT / 'historical_hashes.json')
    changed = [name for name, expected in hashes.items() if digest(ROOT / name) != expected]
    protected = read(ROOT / HISTORY / 'safety_verification.json')['protected_files']
    # runner.py: intentional versioned engine fixes (fb39b99 and this change); .gitignore: user's own edit.
    protected_changed = [name for name, rec in protected.items() if name not in {'.gitignore', 'core/backtest/runner.py'}
                         and digest(ROOT / name) != rec['sha256']]
    records = read(AUDIT / 'identity_matrix_final.json')['records']
    inventory = {r['id'] for r in read(ROOT / HISTORY / 'inventory.json')['records']}
    result = {'status': 'PASS' if not changed and not protected_changed and {r['id'] for r in records} == inventory else 'FAIL',
              'historical_hashes_checked': len(hashes), 'historical_changed': changed,
              'protected_sources_checked': len(protected) - 2, 'protected_changed': protected_changed,
              'source_records': len(records), 'inventory_identities_equal': {r['id'] for r in records} == inventory}
    print(json.dumps({k: v for k, v in result.items()}, ensure_ascii=False))
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode', choices=('matrix', 'preservation'))
    args = parser.parse_args()
    globals()[args.mode]()
