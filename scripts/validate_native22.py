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
BASELINE_COMMIT = '5c4a4b29a1b12621c99a08a5b0487696a6b67f63'  # last commit before this change
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
    emit_csv(out('family_history_matrix.csv'), rows)
    param_rows = []
    for r in sorted(params, key=lambda r: (r['family_id'], r['id'])):
        links = [l for l in r['historical_runs'] if l['configuration_match']]
        param_rows.append({'family_id': r['family_id'], 'group': group_of(r['family_id']), 'id': r['id'],
            'parameters': r.get('parameters'), 'config_hash': (r.get('performance_evidence') or {}).get('metrics', {}).get('config_hash'),
            'historical_runs': len(links), 'historical_run_sha256': {f"{l['path']}:{l['line']}": l['sha256'] for l in links},
            'historical_evidence_verified': r['historical_evidence_verified'], 'reproducible': r['reproducible'],
            'corrected_replay': (r.get('corrected_replay') or {}).get('status'),
            **historical_metrics(r)})
    emit_csv(out('parameter_history_matrix.csv'), param_rows)
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


def fundamental_checks():
    import pandas as pd
    from core.research.fundamental_pit_v3 import add_derived_metrics

    records = pd.read_parquet(ROOT / 'data/processed/fundamental_pit_v3/fundamental_records.parquet')
    pub, avail = pd.to_datetime(records.publication_date), pd.to_datetime(records.available_date)
    raw = records[records.data_version == 'fundamental-pit-v3']
    stored = records[records.data_version == 'fundamental-pit-v3-derived'].set_index(['ticker', 'fiscal_year', 'quarter', 'metric'])
    # Full recompute with the owner's formula; compares every derived row, not a sample.
    recomputed = add_derived_metrics(raw)
    recomputed = recomputed[recomputed.data_version == 'fundamental-pit-v3-derived'].set_index(['ticker', 'fiscal_year', 'quarter', 'metric'])
    joined = stored[['value']].join(recomputed[['value']], rsuffix='_recomputed', how='outer')
    mismatch = ~((joined.value - joined.value_recomputed).abs() <= 1e-12 + 1e-9 * joined.value_recomputed.abs())
    # YoY uses the LATEST revision of the prior-year period; flag when it became available after the current row.
    prior = raw.sort_values(['publication_date', 'revision_id']).drop_duplicates(['ticker', 'fiscal_year', 'quarter', 'metric'], keep='last')
    prior = prior.assign(fiscal_year=prior.fiscal_year + 1).set_index(['ticker', 'fiscal_year', 'quarter', 'metric'])
    yoy = records[records.metric.isin(['eps_yoy', 'operating_income_yoy'])].copy()
    yoy['base_metric'] = yoy.metric.map({'eps_yoy': 'eps', 'operating_income_yoy': 'operating_profit'})
    base = yoy.join(prior[['available_date', 'value']].rename(columns={'available_date': 'prior_available', 'value': 'prior_value'}),
                    on=['ticker', 'fiscal_year', 'quarter', 'base_metric'])
    late_prior = pd.to_datetime(base.prior_available) > pd.to_datetime(base.available_date)
    revisions = raw.groupby(['ticker', 'fiscal_year', 'quarter', 'metric']).revision_id.nunique()
    return {
        'path': 'data/processed/fundamental_pit_v3/fundamental_records.parquet',
        'sha256': digest(ROOT / 'data/processed/fundamental_pit_v3/fundamental_records.parquet'),
        'rows': len(records), 'available_before_publication': int((avail < pub).sum()),
        'derived_rows': len(stored), 'derived_recompute_mismatch': int(mismatch.sum()), 'recompute_tolerance': 'rtol=1e-9 atol=1e-12',
        'yoy_rows_with_prior_revision_available_later': {m: int(late_prior[base.metric == m].sum()) for m in ('eps_yoy', 'operating_income_yoy')},
        'yoy_rows_with_negative_prior_base': {m: int(((base.metric == m) & (base.prior_value < 0)).sum()) for m in ('eps_yoy', 'operating_income_yoy')},
        'periods_with_multiple_revisions': int((revisions > 1).sum()),
        'missing_policy': 'preserve_null_no_fill (fundamental_factor_validation.py CandidateSpec G2/G3); zero prior base skipped, no YoY emitted',
        'formula': 'current / prior_year_same_quarter - 1 (fundamental_pit_v3.py:141-155)',
        'formula_caveat': 'Negative prior base inverts the sign of improvement; counted above, factor unchanged (owner decision).',
    }


def research_dataset_checks():
    import pandas as pd

    base = ROOT / 'artifacts/factors/d3_full_20230103_20260728/research_dataset'
    columns = ['asof_date', 'asset_id', 'member', 'rank_value', 'is_tradable_t1', 'entry_price',
               'factor_asof_date', 'source_max_trade_date', 'execution_date', 'raw_value']
    result = {}
    for factor in ('amihud_20d', 'momentum_12_1', 'momentum_20d', 'momentum_60d', 'near_high_252d'):
        frame = pd.concat([pd.read_csv(p, usecols=columns) for p in sorted((base / factor).glob('*.csv'))], ignore_index=True)
        asof = pd.to_datetime(frame.asof_date)
        member = frame.member.astype(str).str.lower().eq('true')
        rank = pd.to_numeric(frame.rank_value, errors='coerce')
        raw = pd.to_numeric(frame.raw_value, errors='coerce')
        result[factor] = {
            'rows': len(frame), 'member_rows': int(member.sum()),
            'duplicate_asset_dates': int(frame.duplicated(['asset_id', 'asof_date']).sum()),
            'weekend_signal_rows': int((asof.dt.dayofweek >= 5).sum()),
            'factor_asof_after_signal': int((pd.to_datetime(frame.factor_asof_date) > asof).sum()),
            'source_after_signal': int((pd.to_datetime(frame.source_max_trade_date) > asof).sum()),
            'execution_not_after_signal': int((pd.to_datetime(frame.execution_date) <= asof).sum()),
            'member_rank_out_of_unit_interval': int((member & rank.notna() & ((rank < 0) | (rank > 1))).sum()),
            'member_missing_raw_value': int((member & raw.isna()).sum()),
            'member_nonfinite_raw_value': int((member & raw.notna() & ~raw.apply(math.isfinite)).sum()),
            'not_tradable_t1_rows': int((~frame.is_tradable_t1.astype(str).str.lower().eq('true')).sum()),
        }
    return result


def mysql_checks():
    from sqlalchemy import text
    from config import Config
    from core.db_helper import get_db_engine

    sql = {
        'rows': "SELECT COUNT(*) FROM daily_market_data",
        'weekend_rows': "SELECT COUNT(*) FROM daily_market_data WHERE DAYOFWEEK(trade_date) IN (1, 7)",
        'duplicate_asset_dates': "SELECT COUNT(*) FROM (SELECT stock_id, DATE(trade_date) d FROM daily_market_data GROUP BY stock_id, d HAVING COUNT(*) > 1) x",
        'nonpositive_or_inconsistent_ohlc': "SELECT COUNT(*) FROM daily_market_data WHERE open_price <= 0 OR close_price <= 0 OR high_price < low_price OR close_price > high_price OR close_price < low_price",
        'diag_window_stock_rows': "SELECT COUNT(*) FROM daily_market_data WHERE trade_date BETWEEN '2026-09-01' AND '2026-10-20' AND close_price BETWEEN 10 AND 500",
        'diag_window_null_ma60': "SELECT SUM(ma60 IS NULL) FROM daily_market_data WHERE trade_date BETWEEN '2026-09-01' AND '2026-10-20' AND close_price BETWEEN 10 AND 500",
        'diag_window_null_kd_k': "SELECT SUM(kd_k IS NULL) FROM daily_market_data WHERE trade_date BETWEEN '2026-09-01' AND '2026-10-20' AND close_price BETWEEN 10 AND 500",
        'diag_window_zero_natr': "SELECT SUM(natr = 0) FROM daily_market_data WHERE trade_date BETWEEN '2026-09-01' AND '2026-10-20' AND close_price BETWEEN 10 AND 500",
        'taiex_rows': "SELECT COUNT(*) FROM daily_market_data WHERE stock_id IN ('TAIEX', '^TWII', 'IX0001', 'Y9999')",
    }
    with get_db_engine().connect() as connection:
        connection.exec_driver_sql('START TRANSACTION WITH CONSISTENT SNAPSHOT, READ ONLY')
        values = {key: int(connection.execute(text(q)).scalar() or 0) for key, q in sql.items()}
        columns = {t: [r[0] for r in connection.execute(text(f'SHOW COLUMNS FROM {t}'))] for t in ('monthly_revenue', 'financial_statements')}
        connection.rollback()
    values['diag_window_null_ma60_ratio'] = values['diag_window_null_ma60'] / values['diag_window_stock_rows'] if values['diag_window_stock_rows'] else None
    values['publication_date_columns'] = {t: [c for c in cols if 'publi' in c or 'announce' in c] for t, cols in columns.items()}
    values['market_symbol'] = Config.MARKET_SYMBOL
    values['status'] = 'BLOCKED'
    values['reasons'] = ['weekend rows and duplicate asset/date observations present',
                         'technical indicators NULL for most rows in the native diagnostic window',
                         'monthly_revenue/financial_statements carry no publication date (only created_at/updated_at)',
                         f'market regime and benchmark use MARKET_SYMBOL={Config.MARKET_SYMBOL} (single equity, not TAIEX); TAIEX series absent']
    return values


def code_drift():
    """Compare code hashes declared by the latest historical run manifests with the current tree."""
    runs = {'A_FUNDAMENTAL': 'outputs/fundamental_strategy_validation/add-fundamental-strategy-robustness-and-fresh-oos-v1_20260917_v4/strategy_validation_manifest.json',
            'C_D5': 'outputs/portfolio_research/d5_portfolio_research_20260817_v2/run_manifest.json',
            'D_DAY3': 'outputs/strategy_research/strategy_search_20260914_v2/run_manifest.json'}
    files = {'A_FUNDAMENTAL': ['core/research/fundamental_strategy_validation.py', 'jobs/run_fundamental_strategy_validation.py'],
             'C_D5': ['core/research/portfolio_research.py', 'core/research/vectorbt_adapter.py', 'jobs/run_portfolio_research.py'],
             'D_DAY3': ['core/research/strategy_search.py', 'core/research/vectorbt_adapter.py', 'jobs/run_strategy_search.py']}
    result = {}
    for group, path in runs.items():
        manifest = read(ROOT / path)
        commit = manifest.get('git_head') or (manifest.get('provenance') or {}).get('git_commit')
        declared = manifest.get('code_file_sha256') or {}
        changed = ([n for n, sha in declared.items() if (ROOT / n).is_file() and digest(ROOT / n) != sha] if declared else
                   subprocess.run(['git', 'diff', '--name-only', commit, 'HEAD', '--'] + files[group], cwd=ROOT,
                                  capture_output=True, text=True).stdout.split() if commit else None)
        result[group] = {'manifest': path, 'commit': commit, 'declared_file_hashes': len(declared),
                         'changed_since_run': changed,
                         'status': 'NOT_VERIFIABLE: no commit or code hash declared' if changed is None else 'UNCHANGED' if not changed else 'CHANGED'}
    return result


def data():
    previous = read(AUDIT / 'data_validation.json')
    report = {
        'scope': 'Per-snapshot checks; defects are not transferred between independent snapshots.',
        'fundamental_pit_v3': {**previous['fundamental_records'], 'matrix': previous['fundamental_matrix'], **fundamental_checks(),
                               'certification': 'NOT_VERIFIABLE: no independent MOPS reconciliation of values; revisions/publication order PASS'},
        'd3_research_universe': {**previous['d3_research_universe'], 'd5_factor_checks': research_dataset_checks()},
        'day3_inputs': {'data_version': 'd3_full_20230103_20260728', 'factor_source': 'core/research/day2_candidates.py (_alpha101/_alpha191/near_high)',
                        'A191_132': 'IMPLEMENTATION_APPROXIMATE: 20d mean amount (day2_candidates.py:194-201)',
                        'causality': 'test/test_native22_engine_contracts.py::test_factor_values_ignore_future_rows'},
        'local_mysql': {'snapshot_2026_10_21': previous['local_mysql_snapshot'], 'live_read_only': mysql_checks()},
        'benchmark': {**previous['benchmark'], 'research_families': 'G2/G3, D5 and Day3 scoreboards have no TAIEX benchmark column; excess return undefined'},
        'code_drift': code_drift(),
        'limit_up_down_and_suspension': {'d3': 'is_tradable_t1 flag (counts per factor above); limit-lock fills not modelled',
                                         'mysql': 'NOT_VERIFIABLE: no limit/suspension fields'},
    }
    fundamental_ok = (not report['fundamental_pit_v3']['available_before_publication']
                      and not report['fundamental_pit_v3']['derived_recompute_mismatch'])
    d3_ok = all(not v[k] for v in report['d3_research_universe']['d5_factor_checks'].values()
                for k in ('duplicate_asset_dates', 'weekend_signal_rows', 'factor_asof_after_signal', 'source_after_signal', 'execution_not_after_signal', 'member_rank_out_of_unit_interval'))
    common = ['Official TWSE/TPEx calendar not independently matched', 'Corporate-action adjustments not reconciled action-by-action',
              'Delisted-universe completeness not proven', 'TAIEX benchmark series unavailable']
    families = {}
    for group, family in FAMILIES:
        if group == 'B_NATIVE':
            reasons = report['local_mysql']['live_read_only']['reasons']
        else:
            reasons = list(common)
            if group == 'A_FUNDAMENTAL':
                reasons.append('Fundamental values not independently reconciled to MOPS filings')
        # Internal consistency checks can pass while certification stays blocked; DATA_PASS needs both.
        internal = {'A_FUNDAMENTAL': fundamental_ok and d3_ok, 'C_D5': d3_ok, 'D_DAY3': d3_ok, 'B_NATIVE': False}[group]
        families[family] = {'DATA': 'DATA_BLOCKED', 'snapshot_internal_checks': 'PASS' if internal else 'FAIL', 'reasons': reasons}
    report['families'] = families
    report['unblock_conditions'] = ['official session calendar matched day-by-day', 'action-by-action adjustment reconciliation',
        'point-in-time universe including delisted names', 'TAIEX (price and total-return) series',
        'MySQL: remove weekend/duplicate rows, backfill indicators, publish snapshot hash', 'MOPS publication timestamps for revenue/financials',
        'ML training-data cutoff metadata', 'Fresh OOS accumulated to frozen minimums']
    write(out('data_quality_report.json'), report)
    blocked = [{'family_id': f, 'axis': 'DATA', 'status': v['DATA'], 'reason': r} for f, v in families.items() for r in v['reasons']]
    emit_csv(out('blocked_reasons_data.csv'), blocked)
    print(json.dumps({'fundamental_ok': fundamental_ok, 'd3_ok': d3_ok, 'mysql': {k: v for k, v in report['local_mysql']['live_read_only'].items() if k != 'reasons'},
                      'fundamental': {k: report['fundamental_pit_v3'][k] for k in ('available_before_publication', 'derived_recompute_mismatch', 'yoy_rows_with_prior_revision_available_later', 'yoy_rows_with_negative_prior_base')},
                      'code_drift': {g: v['status'] for g, v in report['code_drift'].items()}}, ensure_ascii=False, default=str))


ENGINE_TESTS = ['test/test_native22_engine_contracts.py', 'test/test_next_session_execution.py',
                'test/test_research_execution_price.py', 'test/characterization']
NATIVE_ENGINE_DEFECTS = [
    'Historical runs used legacy_same_close: signal and fill at the same close',
    'Legacy backtest joins the latest revenue/financials regardless of signal date (look-ahead); fixed only under next_session_open_v2',
    'Strategies zero-fill NULL indicators so ma60=0 passes close>ma60 and kd_k=0 passes kd_k<35; v2 now excludes rows with missing inputs',
    'Backtest AI ranking differs from live: runner loads generic stock_ai_model.pkl (feature sentiment_score absent, zero-filled); '
    'live run_daily loads dedicated *_z-feature models that no backtest path can compute',
]
NATIVE_SPECIFIC = {
    'institutional_flow_confirmation': 'chip_score/foreign_consec_days/trust_consec_days exist only in run_daily; backtest silently skips Stage 2 institutional confirmation',
    'hybrid_trend_rank': 'ML training-data cutoff not recorded (model metadata has training_date only); windows before 2026-02-11 cannot exclude leakage',
}


def engine():
    import xml.etree.ElementTree as ET

    junit = out('engine_junit.xml')
    proc = subprocess.run([sys.executable, '-m', 'pytest', *ENGINE_TESTS, '-q', '-p', 'no:cacheprovider', f'--junitxml={junit}'],
                          cwd=ROOT, capture_output=True, text=True)
    suites = ET.parse(junit).getroot()
    cases = [{'test': f"{c.get('classname')}::{c.get('name')}",
              'status': 'FAIL' if c.find('failure') is not None or c.find('error') is not None else 'SKIP' if c.find('skipped') is not None else 'PASS'}
             for c in suites.iter('testcase')]
    counts = Counter(c['status'] for c in cases)
    green = proc.returncode == 0 and not counts.get('FAIL')
    family_status = {}
    for group, family in FAMILIES:
        if group == 'B_NATIVE':
            sid = family.split(':')[-1]
            reasons = NATIVE_ENGINE_DEFECTS + ([NATIVE_SPECIFIC[sid]] if sid in NATIVE_SPECIFIC else [])
            family_status[family] = {'ENGINE': 'ENGINE_FAIL', 'corrected_contract_checks': 'PASS' if green else 'FAIL', 'reasons': reasons}
        else:
            reasons = {'A_FUNDAMENTAL': ['research cost model has no NT$20 minimum fee (immaterial above ~14k notional per order)'],
                       'C_D5': ['vectorbt fee approximation fee + tax/2 per side (round-trip equal, per-side not)'],
                       'D_DAY3': ['vectorbt fee approximation fee + tax/2 per side; base track slippage 0.0',
                                  'A191_132 IMPLEMENTATION_APPROXIMATE (20d mean amount)']}[group]
            family_status[family] = {'ENGINE': 'ENGINE_PASS' if green else 'ENGINE_FAIL', 'caveats': reasons}
    result = {'command': ' '.join(['python -m pytest', *ENGINE_TESTS, '-q']), 'returncode': proc.returncode,
              'counts': dict(counts), 'cases': cases, 'tail': proc.stdout.strip().splitlines()[-1:],
              'coverage': {
                  'same_close_lookahead_rejected': 'test_next_session_execution::test_same_session_execution_is_rejected_and_legacy_behavior_is_preserved',
                  'missing_price_no_fill': ['test_next_session_execution::test_missing_open_expires_unfilled_without_close_substitution',
                                            'test_research_execution_price::test_d5_rebalance_never_fills_missing_open_with_prior_price',
                                            'test_native22_engine_contracts::test_fundamental_replay_defers_missing_price_without_stale_fill'],
                  'non_trading_day_rejected': 'test_native22_engine_contracts::test_weekend_duplicate_or_unordered_sessions_fail_loudly',
                  'insufficient_cash': 'test_native22_engine_contracts::test_insufficient_cash_never_overbuys',
                  'costs': 'test_native22_engine_contracts::test_costs_minimum_fee_tax_on_sell_only_and_adverse_slippage',
                  'reproducible': 'test_native22_engine_contracts::test_same_inputs_reproduce_identical_execution',
                  'data_anomaly_explicit': ['test_native22_engine_contracts::test_weekend_duplicate_or_unordered_sessions_fail_loudly',
                                            'test_native22_engine_contracts::test_research_contract_blocks_lookahead_fundamentals_and_missing_inputs'],
                  'pit_fundamentals': 'test_native22_engine_contracts::test_pit_fundamentals_use_only_periods_past_filing_deadline',
                  'factor_causality': 'test_native22_engine_contracts::test_factor_values_ignore_future_rows'},
              'scope': 'Synthetic fixtures verify engineering behavior only; never investment evidence.',
              'families': family_status}
    write(out('engine_test_results.json'), result)
    print(json.dumps({'returncode': proc.returncode, 'counts': dict(counts)}))


def native(start='2026-09-01', end='2026-10-20', contracts=('legacy_same_close', 'next_session_open_v2'), out_dir=None):
    """Replay the 7 native strategies under both contracts on one read-only MySQL snapshot (diagnostic only)."""
    import contextlib
    import random
    import tempfile
    from unittest.mock import patch
    from sqlalchemy import text
    from sqlalchemy.engine import Connection
    from config import Config
    from core.backtest import runner
    from core.backtest.metrics import calculate_metrics
    from core.db_helper import get_db_engine
    from core.strategy_manager import StrategyManager

    class Recording(runner.BacktestEngine):
        def buy(self, stock_id, price, date_str):
            before = stock_id in self.positions
            super().buy(stock_id, price, date_str)
            if not before and stock_id in self.positions:
                p = self.positions[stock_id]
                self.fills.append({'date': date_str, 'signal_date': self._execution_asof or date_str, 'stock_id': stock_id,
                                   'side': 'BUY', 'shares': p['shares'], 'price': p['cost']})

        def sell(self, stock_id, price, date_str, reason):
            shares = self.positions[stock_id]['shares'] if stock_id in self.positions else None
            super().sell(stock_id, price, date_str, reason)
            if shares is not None and stock_id not in self.positions:
                self.fills.append({'date': date_str, 'signal_date': self._execution_asof or date_str, 'stock_id': stock_id,
                                   'side': 'SELL', 'shares': shares, 'price': self.trades[-1]['sell_price'], 'reason': reason})

    target = out_dir or OUT
    logs = target / 'native_logs'
    logs.mkdir(parents=True, exist_ok=True)
    engine_ = get_db_engine()
    settings_bytes = StrategyManager.SETTINGS_FILE.read_bytes()
    result = {'window': [start, end], 'seed': '2026-10-21', 'capital': 1_000_000,
              'scope': 'DIAGNOSTIC_ONLY on DATA_BLOCKED MySQL; not investment performance',
              'session_source': f'weekday dates carrying MARKET_SYMBOL={Config.MARKET_SYMBOL} rows; NOT an official calendar', 'strategies': {}}
    with tempfile.TemporaryDirectory(prefix='native22-') as tmp:
        settings = Path(tmp) / 'settings.json'
        settings.write_bytes(settings_bytes)
        with patch.object(StrategyManager, 'SETTINGS_FILE', settings), patch.object(StrategyManager, '_instance', None), engine_.connect() as conn:
            conn.exec_driver_sql('START TRANSACTION WITH CONSISTENT SNAPSHOT, READ ONLY')
            sessions = [str(d)[:10] for (d,) in conn.execute(text(
                'SELECT DISTINCT DATE(trade_date) FROM daily_market_data WHERE stock_id = :m AND trade_date BETWEEN :s AND :e '
                'AND DAYOFWEEK(trade_date) NOT IN (1, 7) ORDER BY 1'), {'m': Config.MARKET_SYMBOL, 's': start, 'e': end})]
            result['sessions'] = sessions
            with patch.object(engine_, 'connect', return_value=conn), patch.object(Connection, '__exit__', return_value=False), \
                    patch.object(runner, 'get_db_engine', return_value=engine_):
                for sid in NATIVE:
                    result['strategies'][sid] = {}
                    for contract in contracts:
                        print('NATIVE', sid, contract, flush=True)
                        random.seed('2026-10-21')
                        extra = {'trading_sessions': sessions} if contract == 'next_session_open_v2' else {}
                        with (logs / f'{sid}__{contract}.log').open('w', encoding='utf-8') as log, contextlib.redirect_stdout(log):
                            e = Recording(mode=sid, start_date=start, end_date=end, initial_capital=1_000_000,
                                          persist_results=False, persist_to_db=False, execution_contract=contract, **extra)
                            e.fills = []
                            e.run(return_metrics=True)
                        m = calculate_metrics(e.daily_assets, e.trades)
                        metrics = {k: (lambda v: {'value': v.value if v.value is None or math.isfinite(v.value) else None,
                                                  'reason': v.reason})(m.get(k))
                                   for k in ('cagr', 'sharpe', 'sortino', 'max_drawdown', 'win_rate', 'trade_count')}
                        result['strategies'][sid][contract] = {
                            'fills': e.fills, 'closed_trades': e.trades, 'dates': e.daily_dates, 'equity': e.daily_assets,
                            'execution_events': e.execution_events, 'unexecuted_signals': e.pending_orders,
                            'data_exclusions': e.data_exclusions, 'metrics': metrics}
            conn.rollback()
    assert StrategyManager.SETTINGS_FILE.read_bytes() == settings_bytes
    if len(contracts) == 2:
        historical = read(ROOT / HISTORY / 'stocks.json')
        key = lambda f: (f['date'], f['stock_id'], f['side'])
        for sid, runs in result['strategies'].items():
            old, new = runs['legacy_same_close'], runs['next_session_open_v2']
            hist = [{'date': d, 'stock_id': s, 'side': side} for d, s, side, *_ in historical[sid].get('events', [])]
            result['strategies'][sid]['diff'] = {
                'legacy_fills': len(old['fills']), 'v2_fills': len(new['fills']),
                'legacy_only': sorted(set(map(key, old['fills'])) - set(map(key, new['fills']))),
                'v2_only': sorted(set(map(key, new['fills'])) - set(map(key, old['fills']))),
                'v2_unfilled_events': [ev for ev in new['execution_events'] if ev['status'] != 'FILLED'],
                'v2_rows_excluded_missing_inputs': sum(x['excluded_missing_inputs'] for x in new['data_exclusions']),
                'v2_rows_seen': sum(x['rows'] for x in new['data_exclusions']),
                'legacy_rerun_equals_2026_10_21_history': sorted(map(key, old['fills'])) == sorted(map(key, hist)),
            }
    write(out('native_contract_replay.json') if out_dir is None else out_dir / 'native_contract_replay.json', result)
    print(json.dumps({sid: {k: v for k, v in r['diff'].items() if k in ('legacy_fills', 'v2_fills', 'v2_rows_excluded_missing_inputs', 'v2_rows_seen', 'legacy_rerun_equals_2026_10_21_history')}
                      if 'diff' in r else {c: len(r[c]['fills']) for c in contracts} for sid, r in result['strategies'].items()}))


CERTIFIED = ('cagr', 'sharpe', 'sortino', 'max_drawdown', 'win_rate', 'trade_count', 'turnover', 'gross_return', 'net_return',
             'stress_cost', 'benchmark_excess_return', 'is_oos', 'walk_forward')


def median(values):
    values = sorted(v for v in values if v is not None)
    if not values:
        return None
    mid = len(values) // 2
    return values[mid] if len(values) % 2 else (values[mid - 1] + values[mid]) / 2


def stdev(values):
    values = [v for v in values if v is not None]
    if len(values) < 2:
        return None
    mean = sum(values) / len(values)
    return math.sqrt(sum((v - mean) ** 2 for v in values) / (len(values) - 1))


def g2g3_comparison(params):
    """Same top_n / rebalance / weighting cells; dual factor vs each single factor (historical reference)."""
    rows = [p for p in params if p['group'] == 'A_FUNDAMENTAL']
    cells = {}
    for p in rows:
        cfg = json.loads(p['parameters'])
        cells.setdefault((cfg['top_n'], cfg['rebalance_days'], cfg['portfolio_weighting']), {})[cfg['factor_set_id']] = p
    pairs = []
    for cell, by in sorted(cells.items()):
        g2, g3, dual = by.get('G2'), by.get('G3'), by.get('G2_PLUS_G3')
        if not (g2 and g3 and dual):
            continue
        f = lambda p, k: num(p[k])
        pairs.append({'top_n': cell[0], 'rebalance_days': cell[1], 'portfolio_weighting': cell[2],
                      **{f'{name}_{k}': f(p, 'historical_' + k) for name, p in (('g2', g2), ('g3', g3), ('dual', dual))
                         for k in ('sharpe', 'max_drawdown', 'positive_fold_ratio', 'stress_sharpe')},
                      'dual_sharpe_above_both': f(dual, 'historical_sharpe') > max(f(g2, 'historical_sharpe'), f(g3, 'historical_sharpe')),
                      'dual_mdd_better_than_both': f(dual, 'historical_max_drawdown') > max(f(g2, 'historical_max_drawdown'), f(g3, 'historical_max_drawdown'))})
    summary = {name: {'median_sharpe': median([r[f'{name}_sharpe'] for r in pairs]), 'sharpe_stdev_across_cells': stdev([r[f'{name}_sharpe'] for r in pairs]),
                      'median_mdd': median([r[f'{name}_max_drawdown'] for r in pairs]), 'median_positive_fold_ratio': median([r[f'{name}_positive_fold_ratio'] for r in pairs])}
               for name in ('g2', 'g3', 'dual')}
    return {'scope': 'HISTORICAL_REFERENCE (development data, DATA_BLOCKED); not certified performance', 'cells': len(pairs), 'pairs': pairs,
            'summary': summary, 'dual_sharpe_above_both_count': sum(r['dual_sharpe_above_both'] for r in pairs),
            'dual_mdd_better_than_both_count': sum(r['dual_mdd_better_than_both'] for r in pairs)}


def day3_comparison(params):
    rows = [p for p in params if p['group'] == 'D_DAY3']
    single = {}
    for p in rows:
        cfg = json.loads(p['parameters'])
        if '+' not in cfg['factor_set']:
            single[(cfg['factor_set'], cfg['top_n'], cfg['rebalance_days'], cfg['portfolio_weighting'])] = num(p['historical_sharpe'])
    gains = []
    for p in rows:
        cfg = json.loads(p['parameters'])
        if '+' in cfg['factor_set']:
            parts = [single.get((f, cfg['top_n'], cfg['rebalance_days'], cfg['portfolio_weighting'])) for f in cfg['factor_set'].split('+')]
            best = max(v for v in parts if v is not None)
            gains.append({'id': p['id'], 'factor_weighting': cfg['factor_weighting'], 'sharpe': num(p['historical_sharpe']),
                          'best_single_sharpe': best, 'gain': num(p['historical_sharpe']) - best})
    by_method = {m: {'configs': len(g), 'positive_gain': sum(x['gain'] > 0 for x in g), 'median_gain': median([x['gain'] for x in g])}
                 for m in sorted({x['factor_weighting'] for x in gains}) for g in [[x for x in gains if x['factor_weighting'] == m]]}
    corr_path = ROOT / 'outputs/factor_validation/factor_validation_20260910_v1/factor_redundancy_matrix.csv'
    ids = {'A101_003', 'A191_088', 'A191_132', 'RS_004_NEAR_HIGH_120D'}
    corr = [{'pair': f"{r['factor_id']}~{r['other_factor_id']}", 'rank_correlation': num(r['rank_correlation'])}
            for r in csv_rows(corr_path) if r['factor_id'] in ids and r['other_factor_id'] in ids and r['factor_id'] < r['other_factor_id']]
    return {'scope': 'HISTORICAL_REFERENCE; best-of-grid Sharpe is a multiple-testing statistic, not evidence of effectiveness',
            'multi_factor_configs': len(gains), 'positive_gain_vs_best_constituent': sum(x['gain'] > 0 for x in gains),
            'median_gain': median([x['gain'] for x in gains]), 'by_factor_weighting': by_method,
            'factor_rank_correlations': corr or None,
            'factor_rank_correlations_reason': None if corr else 'Accepted Day3 factors absent from factor_validation_20260910_v1 redundancy matrix',
            'correlation_source': corr_path.relative_to(ROOT).as_posix(), 'gains': gains}


def d5_evidence():
    comparison = read(AUDIT / 'd5_repair_comparison.json')
    before, after = read(ROOT / comparison['before_path']), read(ROOT / comparison['after_path'])
    changed = []
    for r in comparison['records']:
        sid = r['id'].split(':', 1)[1]
        for track, t in r['tracks'].items():
            if t['ordered_fill_identity_equal'] and t['fill_amounts_equal'] and t['equity_equal']:
                continue
            key = lambda x: (x['date'], x['asset'], x['side'])
            old, new = set(map(key, before[sid][track]['fills'])), set(map(key, after[sid][track]['fills']))
            changed.append({'id': r['id'], 'track': track, 'before_orders': t['before_order_count'], 'after_orders': t['after_order_count'],
                            'removed_fills': sorted(old - new), 'added_fills': sorted(new - old),
                            'sharpe_before_after': [t['before_metrics']['sharpe']['value'], t['after_metrics']['sharpe']['value']]})
    return {'configurations': comparison['configurations'], 'cost_tracks': comparison['cost_tracks'],
            'unchanged_configurations': comparison['configurations'] - comparison['changed_configurations'],
            'changed_configurations': comparison['changed_configurations'], 'target_weights_unchanged': all(r['target_weights_unchanged'] for r in comparison['records']),
            'cause': 'Pre-fix replay carried the last observed price into sessions with a missing open, filling orders at a stale price (fixed fb39b99).',
            'changed': changed, 'source': 'outputs/strategy_revalidation/2026-10-21-audit/d5_repair_comparison.json',
            'scope': 'Corrected diagnostic replay; DATA_BLOCKED, not certified performance'}


def report():
    params = csv_rows(OUT / 'parameter_history_matrix.csv')
    history = csv_rows(OUT / 'family_history_matrix.csv')
    data_report = read(OUT / 'data_quality_report.json')
    engine_report = read(OUT / 'engine_test_results.json')
    native = read(OUT / 'native_contract_replay.json')
    frozen = read(AUDIT / 'frozen_gate_evidence.json')
    stocks = read(ROOT / HISTORY / 'stocks.json')
    window = frozen['documents']['promotion_review']['evaluation_window']
    minimum = frozen['documents']['FreshOOSAvailabilityReport'].get('minimum_requirement')
    from core.runtime.fundamental_shadow import FrozenStrategyLoader
    fingerprint = FrozenStrategyLoader().load().fingerprint
    assert fingerprint == frozen['documents']['promotion_review']['strategy_fingerprint'], 'FROZEN_FINGERPRINT_CHANGED'

    comparisons = {'g2g3': g2g3_comparison(params), 'day3': day3_comparison(params), 'd5': d5_evidence(),
                   'frozen_candidate': {'id': 'fundamental_g2g3_top5_reb60_score_weighted_v1', 'fingerprint': fingerprint,
                                        'fingerprint_unchanged': True, 'cutoff': window['cutoff'], 'window': window, 'minimum': minimum}}
    write(out('comparisons.json'), comparisons)

    blocked_reason = 'DATA_BLOCKED: no independently certified snapshot (see data_quality_report.json); certified metric undefined'
    certified_null = {f'certified_{k}': None for k in CERTIFIED}
    oos_reason = {'A_FUNDAMENTAL': f"Fresh OOS after cutoff {window['cutoff']}: {window['trading_days']} days / {window['calendar_months']} months / {window['rebalance_count']} rebalances < minimum {json.dumps(minimum)}; only the Frozen Candidate accrues Fresh OOS",
                  'C_D5': 'No Fresh OOS ledger; development splits cannot be relabelled OOS',
                  'D_DAY3': 'Strict OOS window 2026-01-01..2026-07-28 kept untouched by Day3 (strict_oos_rows_read=0); not opened while DATA_BLOCKED',
                  'B_NATIVE': 'No frozen untouched OOS; diagnostic window is not OOS'}

    def axes(group, family):
        return {'ENGINE': engine_report['families'][family]['ENGINE'], 'DATA': data_report['families'][family]['DATA'],
                'BACKTEST': 'BACKTEST_BLOCKED', 'OOS': 'OOS_BLOCKED', 'PRODUCTION_ELIGIBLE': 'NO'}

    param_rows, results = [], []
    for p in params:
        family = p['family_id']
        status = axes(p['group'], family)
        param_rows.append({**p, **status, 'backtest_reason': blocked_reason, 'oos_reason': oos_reason[p['group']]})
        results.append({'family_id': family, 'group': p['group'], 'id': p['id'], 'contract': CONTRACT[p['group']],
                        **certified_null, 'certified_reason': blocked_reason,
                        **{k: v for k, v in p.items() if k.startswith('historical_') and k not in ('historical_runs', 'historical_run_sha256')},
                        'historical_win_rate_reason': None if p['historical_win_rate'] else 'Not reported by the research scoreboard',
                        'historical_benchmark_excess_reason': 'No TAIEX benchmark in research scoreboards',
                        'd5_corrected_replay': p['corrected_replay'] or None})
    for sid in NATIVE:
        family = 'family:native:' + sid
        hist = stocks[sid]['metrics']
        v2 = native['strategies'][sid]['next_session_open_v2']['metrics']
        results.append({'family_id': family, 'group': 'B_NATIVE', 'id': sid, 'contract': CONTRACT['B_NATIVE'],
                        **certified_null, 'certified_reason': blocked_reason,
                        'historical_source': f'{HISTORY.as_posix()}/stocks.json#{sid}', 'historical_owner_gate': 'DIAGNOSTIC_ONLY',
                        **{f'historical_{k}': hist[k]['value'] for k in hist},
                        **{f'diagnostic_v2_{k}': v['value'] for k, v in v2.items()},
                        'diagnostic_v2_scope': 'next_session_open_v2 on DATA_BLOCKED MySQL (native_contract_replay.json); not performance'})
    emit_csv(out('parameter_validation_matrix.csv'), param_rows)
    emit_csv(out('backtest_results.csv'), results)

    families, blocked = [], []
    for row in history:
        group, family = row['group'], row['family_id']
        status = axes(group, family)
        engine_reasons = engine_report['families'][family].get('reasons') or engine_report['families'][family].get('caveats')
        native_diff = native['strategies'][family.split(':')[-1]]['diff'] if group == 'B_NATIVE' else None
        families.append({**row, **status, 'engine_detail': engine_reasons,
                         'native_contract_diff': {k: native_diff[k] for k in ('legacy_fills', 'v2_fills', 'v2_rows_excluded_missing_inputs', 'v2_rows_seen', 'legacy_rerun_equals_2026_10_21_history')} if native_diff else None,
                         'evidence': ['family_history_matrix.csv', 'data_quality_report.json', 'engine_test_results.json',
                                      'native_contract_replay.json' if native_diff else 'comparisons.json', 'oos_validation_results.json']})
        if status['ENGINE'] == 'ENGINE_FAIL':
            blocked += [{'family_id': family, 'axis': 'ENGINE', 'status': 'ENGINE_FAIL', 'reason': r} for r in engine_reasons]
        blocked += [{'family_id': family, 'axis': 'DATA', 'status': 'DATA_BLOCKED', 'reason': r} for r in data_report['families'][family]['reasons']]
        blocked += [{'family_id': family, 'axis': 'BACKTEST', 'status': 'BACKTEST_BLOCKED', 'reason': 'Requires DATA_PASS and a valid corrected-contract backtest'},
                    {'family_id': family, 'axis': 'OOS', 'status': 'OOS_BLOCKED', 'reason': oos_reason[group]}]
    emit_csv(out('family_validation_matrix.csv'), families)
    emit_csv(out('blocked_reasons.csv'), blocked)
    write(out('oos_validation_results.json'), {
        'frozen_candidate': {**comparisons['frozen_candidate'], 'status': 'OOS_BLOCKED', 'reason': oos_reason['A_FUNDAMENTAL'],
                             'ledger_source': frozen['source_root'], 'ledger_mutated': False},
        'families': {f: {'OOS': 'OOS_BLOCKED', 'reason': oos_reason[g]} for g, f in FAMILIES},
        'rules': 'No development sample relabelled OOS; original cutoffs and frozen minimums retained.',
        'certified_oos': 0})
    counts = {'families': len(families), 'engine_pass': sum(f['ENGINE'] == 'ENGINE_PASS' for f in families),
              'engine_fail': sum(f['ENGINE'] == 'ENGINE_FAIL' for f in families), 'data_pass': sum(f['DATA'] == 'DATA_PASS' for f in families),
              'valid_backtests': 0, 'backtest_pass': 0, 'backtest_fail': 0, 'valid_oos': 0,
              'still_blocked': sum(f['BACKTEST'] == 'BACKTEST_BLOCKED' for f in families), 'parameters': len(param_rows)}
    inputs = [AUDIT / n for n in ('identity_matrix_final.json', 'family_matrix.csv', 'd5_repair_comparison.json', 'data_validation.json',
                                  'frozen_gate_evidence.json', 'historical_hashes.json', 'manifest_verification_corrected.json')]
    inputs += [ROOT / HISTORY / 'stocks.json', ROOT / 'data/processed/fundamental_pit_v3/fundamental_records.parquet']
    write(out('reproduction_manifest.json'), {
        'git_head': git_head(), 'output_dir': OUT.relative_to(ROOT).as_posix(), 'counts': counts,
        'commands': ['python scripts/validate_native22.py ' + m for m in ('matrix', 'preservation', 'data', 'engine', 'native', 'report', 'verify')],
        'inputs': {p.relative_to(ROOT).as_posix(): digest(p) for p in inputs},
        'code': {p: digest(ROOT / p) for p in ('scripts/validate_native22.py', 'core/backtest/runner.py', 'test/test_native22_engine_contracts.py')},
        'outputs': {p.name: digest(p) for p in sorted(OUT.glob('*')) if p.is_file() and p.name != 'reproduction_manifest.json'},
        'BROKER_ORDER_SUBMISSION': 'DISABLED'})
    print(json.dumps(counts))


def verify():
    families = csv_rows(OUT / 'family_validation_matrix.csv')
    params = csv_rows(OUT / 'parameter_validation_matrix.csv')
    results = csv_rows(OUT / 'backtest_results.csv')
    assert len(families) == 22 and len({f['family_id'] for f in families}) == 22, 'FAMILY_COUNT'
    assert len(params) == 384 and len({p['id'] for p in params}) == 384, 'PARAMETER_COUNT'
    assert len(results) == 384 + len(NATIVE), 'RESULT_COUNT'
    for row in families + params:
        assert row['BACKTEST'] != 'BACKTEST_PASS' or row['DATA'] == 'DATA_PASS', 'BACKTEST_WITHOUT_DATA'
        assert row['OOS'] != 'OOS_PASS' or row['BACKTEST'] == 'BACKTEST_PASS', 'OOS_WITHOUT_BACKTEST'
        assert row['PRODUCTION_ELIGIBLE'] == 'NO', 'PRODUCTION'
    for row in results:
        assert all(row[f'certified_{k}'] == '' for k in CERTIFIED) and row['certified_reason'], 'CERTIFIED_METRIC_WITHOUT_DATA'
    review = read(AUDIT / 'frozen_gate_evidence.json')['documents']['promotion_review']
    assert review['BROKER_ORDER_SUBMISSION'] == 'DISABLED', 'BROKER'
    touched = subprocess.run(['git', 'diff', '--name-only', BASELINE_COMMIT, '--', 'execution', 'core/runtime', 'jobs/scheduler.py'],
                             cwd=ROOT, capture_output=True, text=True, check=True).stdout.split()
    assert not touched, 'BROKER_OR_RUNTIME_CHANGED: ' + str(touched)
    kept = preservation()
    assert kept['status'] == 'PASS', 'PRESERVATION'
    write(out('verification.json'), {'status': 'PASS', 'families': 22, 'parameters': 384, 'results': len(results),
                                     'preservation': kept, 'BROKER_ORDER_SUBMISSION': 'DISABLED'})
    print('VERIFY PASS')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode', choices=('matrix', 'preservation', 'data', 'engine', 'native', 'report', 'verify'))
    args = parser.parse_args()
    globals()[args.mode]()
