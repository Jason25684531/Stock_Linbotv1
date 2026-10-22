"""Native-7 recovery: official snapshot, model contracts, live parity, research replay, status matrix.

Every stage writes into one immutable output directory (an existing file is never overwritten).
Order: snapshot -> contracts -> engine -> parity -> replay -> report -> verify.
"""
import argparse
import contextlib
import csv
import json
import math
import platform
import subprocess
import sys
from collections import Counter
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.audit_strategy_history import digest, read, write
from scripts.revalidate_strategy_history import emit_csv
from scripts.validate_native22 import BASELINE_COMMIT, NATIVE, git_head, preservation

OUT = ROOT / 'outputs/native7_recovery/2026-10-22'
MYSQL = ROOT / 'outputs/strategy_validation/2026-10-21'
WINDOW = ('2026-05-21', '2026-07-28')  # after every model's training date, up to the last official session
MIN_SESSIONS = 252  # existing minimum_oos_sessions policy (outputs/strategy_validation validation_plan)
FLOW_INPUTS = {'foreign_ratio', 'trust_ratio', 'foreign_consec_days', 'trust_consec_days', 'chip_score', 'foreign_buy', 'trust_buy'}
FUNDAMENTAL_INPUTS = {'revenue_yoy', 'op_profit_margin', 'eps', 'rd_ratio'}
CERTIFIED = ('cagr', 'sharpe', 'sortino', 'max_drawdown', 'win_rate', 'trade_count', 'turnover', 'gross_return', 'net_return',
             'stress_cost', 'benchmark_excess_return')


def out(name):
    path = OUT / name
    if path.exists():
        raise FileExistsError(path)  # immutable evidence; rerun into a new directory
    return path


def forbidden_db(*_args, **_kwargs):
    raise RuntimeError('DB_ACCESS_FORBIDDEN: research replay must be reproducible from the snapshot')


def load_inputs(diagnostic):
    """diagnostic=True reproduces Daily's NULL -> 0 for never-populated sources (engineering evidence only, never certified)."""
    from core.research.native_frame import NativeResearchData

    snap = pd.read_parquet(OUT / 'research_snapshot.parquet')
    fin = pd.read_parquet(MYSQL / 'snapshot_financial_statements.parquet')
    rev = pd.read_parquet(MYSQL / 'snapshot_monthly_revenue.parquet')
    return NativeResearchData(snap, fin, rev, daily_equivalent_fill=diagnostic), snap


def strategy_of(key):
    from core.strategy_manager import StrategyManager

    return StrategyManager().get_strategy(key)


def snapshot():
    from sqlalchemy import text
    from core.db_helper import get_db_engine
    from core.research.native_snapshot import build_snapshot, corporate_action_check, load_official, reconcile_fundamentals

    OUT.mkdir(parents=True, exist_ok=True)
    official = load_official()
    snap, audit = build_snapshot(official, pd.read_parquet(MYSQL / 'snapshot_daily_market_data.parquet'))
    snap.to_parquet(out('research_snapshot.parquet'), index=False)
    official.taiex.rename_axis('trade_date').to_csv(out('taiex.csv'))
    audit['official_rows_without_trade_price'] = int(official.bars.close_price.isna().sum())
    fin = pd.read_parquet(MYSQL / 'snapshot_financial_statements.parquet')
    rev = pd.read_parquet(MYSQL / 'snapshot_monthly_revenue.parquet')
    pit = pd.read_parquet(ROOT / 'data/processed/fundamental_pit_v3/fundamental_records.parquet')
    with get_db_engine().connect() as connection:  # read-only SELECT of the thresholds Daily would read
        connection.exec_driver_sql('START TRANSACTION READ ONLY')
        settings = {k: v for k, v in connection.execute(text('SELECT setting_key, setting_value FROM user_settings'))}
        connection.rollback()
    write(out('user_settings.json'), settings)
    write(out('data_quality_report.json'), {
        'official': {'sessions': len(official.sessions), 'first': str(official.sessions.min().date()), 'last': str(official.sessions.max().date()),
                     'weekday_holidays': official.holidays, 'taiex_rows': len(official.taiex), 'files': official.file_count,
                     'aggregate_sha256': official.raw_sha256, 'source': 'artifacts/factors/d3_full_20230103_20260728/_raw/twse_rwd'},
        'mysql_inputs': {n: digest(MYSQL / n) for n in ('snapshot_daily_market_data.parquet', 'snapshot_financial_statements.parquet', 'snapshot_monthly_revenue.parquet')},
        'snapshot': {'path': 'research_snapshot.parquet', 'sha256': digest(OUT / 'research_snapshot.parquet'), **audit},
        'corporate_actions': corporate_action_check(official), 'fundamentals_vs_mops_pit_v3': reconcile_fundamentals(fin, rev, pit),
        'basis': 'TWSE-listed four-digit stocks, official sessions, raw prices (price return); TPEx and total return are out of certified scope',
        'user_settings_rows': len(settings)})
    print(json.dumps({k: audit[k] for k in ('price_match_rate', 'snapshot_rows', 'official_sessions_missing_in_mysql')}))


def tpex():
    """Root-cause evidence: weekend and duplicate rows come from four-digit ids that are not on the official TWSE list."""
    daily = pd.read_parquet(MYSQL / 'snapshot_daily_market_data.parquet', columns=['stock_id', 'trade_date', 'close_price'])
    official = set(pd.read_parquet(OUT / 'research_snapshot.parquet', columns=['stock_id']).stock_id)
    daily['trade_date'] = pd.to_datetime(daily.trade_date)
    four = daily[daily.stock_id.str.fullmatch(r'[1-9]\d{3}')]
    result = {}
    for name, frame in (('twse_official_ids', four[four.stock_id.isin(official)]), ('non_twse_four_digit_ids', four[~four.stock_id.isin(official)])):
        weekend = frame.trade_date.dt.dayofweek >= 5
        monthly = frame[~weekend].groupby(frame.trade_date.dt.to_period('M')).stock_id.nunique()
        result[name] = {'ids': int(frame.stock_id.nunique()), 'rows': len(frame), 'weekend_rows': int(weekend.sum()),
                        'weekday_duplicate_keys': int(frame[~weekend].duplicated(['stock_id', 'trade_date']).sum()),
                        'first_date': str(frame.trade_date.min().date()), 'weekday_ids_per_month': {str(k): int(v) for k, v in monthly.items()}}
    write(out('tpex_audit.json'), result)
    print({k: (v['ids'], v['weekend_rows'], v['weekday_duplicate_keys'], v['first_date']) for k, v in result.items()})


def contracts():
    import joblib
    from config import Config
    from core.model_utils import get_model_dir
    from core.research.native_frame import FRAME_INPUTS, rule_inputs
    from core.research.native_models import ModelContractError, load_model_contract

    strict_data, _ = load_inputs(diagnostic=False)
    unavailable = strict_data.unavailable_inputs(*WINDOW)
    generic = Path(Config.MODEL_PATH)
    generic_data = joblib.load(generic)
    result = {'window_start': WINDOW[0], 'generic_model_used_by_legacy_backtest': {
        'path': generic.as_posix(), 'sha256': digest(generic), 'features': generic_data['features'], 'training_date': str(generic_data['training_date'])},
        'inputs_null_at_source_in_window': sorted(unavailable), 'strategies': {}}
    for key in NATIVE:
        strategy = strategy_of(key)
        entry = {'strategy_name': strategy.name, 'rule_inputs': sorted(rule_inputs(strategy)),
                 'legacy_backtest_lookup': f'stock_ai_model_{key}.pkl', 'legacy_lookup_exists': (Path(get_model_dir()) / f'stock_ai_model_{key}.pkl').is_file()}
        try:  # strict: what a certified research backtest needs
            entry['contract'] = {'status': 'PASS', **load_model_contract(strategy.name, FRAME_INPUTS, WINDOW[0], unavailable).identity}
        except ModelContractError as exc:
            entry['contract'] = {'status': 'BLOCKED', 'reason': str(exc)}
        # diagnostic: Daily's NULL -> 0 accepted, so parity and execution evidence can still be produced
        entry['diagnostic_contract'] = {'status': 'PASS', **load_model_contract(strategy.name, FRAME_INPUTS, WINDOW[0]).identity}
        try:  # the same model with an earlier window must be refused
            load_model_contract(strategy.name, FRAME_INPUTS, '2026-05-01')
            entry['earlier_window_check'] = 'NOT_REFUSED'
        except ModelContractError as exc:
            entry['earlier_window_check'] = str(exc).split(':')[0]
        result['strategies'][key] = entry
    write(out('model_contract.json'), result)
    print({k: v['contract']['status'] for k, v in result['strategies'].items()})


def engine():
    import xml.etree.ElementTree as ET

    tests = ['test/test_native_recovery.py', 'test/test_native22_engine_contracts.py', 'test/test_next_session_execution.py',
             'test/test_research_execution_price.py', 'test/characterization']
    junit = out('engine_junit.xml')
    proc = subprocess.run([sys.executable, '-m', 'pytest', *tests, '-q', '-p', 'no:cacheprovider', f'--junitxml={junit}'], cwd=ROOT, capture_output=True, text=True)
    cases = [{'test': f"{c.get('classname')}::{c.get('name')}",
              'status': 'FAIL' if c.find('failure') is not None or c.find('error') is not None else 'SKIP' if c.find('skipped') is not None else 'PASS'}
             for c in ET.parse(junit).getroot().iter('testcase')]
    write(out('engine_test_results.json'), {'command': 'python -m pytest ' + ' '.join(tests), 'returncode': proc.returncode,
                                            'counts': dict(Counter(c['status'] for c in cases)), 'cases': cases})
    print(proc.returncode, dict(Counter(c['status'] for c in cases)))


def research_engine(key, data, sessions):
    from core.backtest.runner import BacktestEngine

    class Recording(BacktestEngine):
        def find_candidates(self, date_str):
            picks = super().find_candidates(date_str)
            self.signals.append({'date': date_str, 'picks': picks})
            return picks

    instance = Recording(mode=key, start_date=WINDOW[0], end_date=WINDOW[1], initial_capital=1_000_000, persist_results=False,
                         persist_to_db=False, execution_contract='next_session_open_v2', trading_sessions=sessions, research_data=data)
    instance.signals = []
    return instance


@contextlib.contextmanager
def isolated(data, settings):
    """No DB, no settings write: reads are served from the snapshot and the recorded user_settings."""
    from core.strategy_manager import StrategyManager

    sentinel = SimpleNamespace(connect=forbidden_db)
    with patch('core.db_helper.get_stock_data', data.stock_data), patch('core.db_helper.get_setting', lambda k, d=None: settings.get(k, d)), \
            patch('core.db_helper.get_db_engine', lambda: sentinel), patch('core.backtest.runner.get_db_engine', lambda: sentinel), \
            patch.object(StrategyManager, '_instance', None):
        yield


def session_list(snap):
    days = sorted(snap.trade_date.unique())
    return [pd.Timestamp(d).strftime('%Y-%m-%d') for d in days if WINDOW[0] <= pd.Timestamp(d).strftime('%Y-%m-%d') <= WINDOW[1]]


def parity():
    """Same complete input frame: the research find_candidates equals Daily's run_strategy ranking."""
    from config import Config
    from core.backtest.runner import AI_CONFIDENCE_THRESHOLD
    from core.calc_indicators import calculate_ratio_features
    from core.model_utils import load_model
    from jobs import run_daily

    data, snap = load_inputs(diagnostic=True)
    sessions = session_list(snap)
    settings = read(OUT / 'user_settings.json')
    dates = sessions[::10]
    result = {'dates': dates, 'ai_confidence_threshold_backtest_only': AI_CONFIDENCE_THRESHOLD, 'strategies': {}}
    with isolated(data, settings), open(OUT / 'parity_stdout.log', 'w', encoding='utf-8') as log, contextlib.redirect_stdout(log):
        for key in NATIVE:
            e = research_engine(key, data, sessions)
            strategy = e.strategy_obj
            model, _features, path, used_fallback = load_model(strategy_name=strategy.name, allow_fallback=True, require_predict_proba=True)
            rows = []
            for day in dates:
                picks = e.find_candidates(day)
                frame, note = data.frame(day, e.required_inputs)
                live = run_daily.run_strategy(strategy, frame, day, None, dry_run=True) if not frame.empty else frame
                ranked = [] if live.empty else list(live.stock_id)
                if not live.empty and 'ai_score' in live:
                    confident = live[live.ai_score >= AI_CONFIDENCE_THRESHOLD]
                    ranked = list((confident if not confident.empty else live).stock_id)
                rows.append({'date': day, 'research_top3': picks, 'daily_ranked_top3': ranked[:3], 'daily_candidates': len(live),
                             'equal': picks == ranked[:3], 'rows': note['rows'], 'excluded': note['excluded']})
            result['strategies'][key] = {'strategy_name': strategy.name, 'daily_model_file': Path(path).name, 'daily_used_fallback': used_fallback,
                                         'research_model_file': Path(e.model_identity['path']).name, 'same_model_file': Path(path).name == Path(e.model_identity['path']).name,
                                         'all_equal': all(r['equal'] for r in rows), 'dates_with_candidates': sum(bool(r['research_top3']) for r in rows), 'rows': rows}
        day = dates[len(dates) // 2]
        full = data.cross_section(pd.Timestamp(day))
        one_day = full.drop(columns=['volume_ratio'])
        high60 = one_day.groupby('stock_id')['high_price'].transform(lambda x: x.rolling(60, min_periods=1).max())
        required = ['ma20', 'ma60', 'kd_k', 'natr', 'rsi', 'macd_hist']
        result['daily_pipeline_defects'] = {
            'evidence_date': day,
            'volume_ratio_constant_one_in_daily': bool((calculate_ratio_features(one_day).volume_ratio == 1.0).all()),
            'volume_ratio_research_distinct_values': int(full.volume_ratio.nunique()),
            'v34_high_60_equals_todays_high_on_one_day_frame': bool(((high60 == one_day.high_price) | (high60.isna() & one_day.high_price.isna())).all()),
            'rows_daily_would_zero_fill_indicators': int(full[required].isna().any(axis=1).sum()), 'rows_total': len(full),
            'note': 'Daily fills NaN indicators and financials with 0 and strategies coerce NaN to 0; research excludes those rows instead. '
                    'Daily and the strategies are intentionally not modified.'}
    write(out('parity.json'), result)
    print({k: (v['all_equal'], v['same_model_file'], v['dates_with_candidates']) for k, v in result['strategies'].items()})


def replay():
    from core.backtest.metrics import calculate_metrics

    data, snap = load_inputs(diagnostic=True)
    strict_data, _ = load_inputs(diagnostic=False)
    sessions = session_list(snap)
    settings = read(OUT / 'user_settings.json')
    taiex = pd.read_csv(OUT / 'taiex.csv', index_col=0, parse_dates=True).iloc[:, 0]
    bench = taiex.loc[WINDOW[0]:WINDOW[1]]
    result = {'window': WINDOW, 'sessions': len(sessions), 'seed': 'none (no randomness in research execution)',
              'benchmark': {'name': 'TAIEX price index (official)', 'return': float(bench.iloc[-1] / bench.iloc[0] - 1)}, 'strategies': {}}
    with isolated(strict_data, settings):
        for key in NATIVE:  # the certified-grade attempt must be refused before any trade can be simulated
            try:
                research_engine(key, strict_data, sessions)
                result['strategies'][key] = {'strict_attempt': {'status': 'NOT_REFUSED'}}
            except Exception as exc:
                result['strategies'][key] = {'strict_attempt': {'status': 'BLOCKED', 'reason': str(exc)}}
    with isolated(data, settings):
        for key in NATIVE:
            with (OUT / f'replay_{key}.log').open('w', encoding='utf-8') as log, contextlib.redirect_stdout(log):
                e = research_engine(key, data, sessions)
                e.run(return_metrics=True)
            metrics = calculate_metrics(e.daily_assets, e.trades)
            result['strategies'][key].update({
                'scope': 'DIAGNOSTIC_DAILY_EQUIVALENT (never-populated sources filled with 0 exactly as Daily does); not a valid backtest',
                'model': e.model_identity, 'required_inputs': e.required_inputs, 'signals': e.signals,
                'execution_events': e.execution_events, 'unexecuted_end_of_window_signals': e.pending_orders,
                'closed_trades': e.trades, 'dates': e.daily_dates, 'equity': e.daily_assets, 'data_exclusions': e.data_exclusions,
                'diagnostic_metrics': {k: {'value': (m.value if m.value is None or math.isfinite(m.value) else None), 'reason': m.reason}
                                       for k in ('cagr', 'sharpe', 'sortino', 'max_drawdown', 'win_rate', 'trade_count')
                                       for m in [metrics.get(k)]},
                'final_equity': e.daily_assets[-1] if e.daily_assets else None})
    write(out('replay.json'), result)
    print({k: (sum(x['status'] == 'FILLED' for x in v['execution_events']), len(v['closed_trades'])) for k, v in result['strategies'].items()})


def gates(report, entry):
    audit, official = report['snapshot'], report['official']
    fundamentals = report['fundamentals_vs_mops_pit_v3']
    flows = {'foreign_buy', 'trust_buy'}
    g = {'CALENDAR': ('PASS', f"{official['sessions']} official sessions {official['first']}..{official['last']} from TWSE responses; later dates unverified"),
         'PRICES_TWSE': ('PASS', f"official TWSE bars are the price source; MySQL rows agree on {audit['price_match_rate']:.2%} of {audit['price_keys_compared']} keys, "
                                 f"mismatches quarantined (misdated {audit['price_mismatch_rows_by_date_top10']})"),
         'TAIEX': ('PASS' if official['taiex_rows'] == official['sessions'] else 'BLOCKED', f"{official['taiex_rows']} official index closes"),
         'CORPORATE_ACTIONS': ('PASS', f"{report['corporate_actions']['events_on_official_sessions']} official ex-rights events consistent with official previous close; "
                                       'price-return basis only (cash dividends not credited, total return unavailable)'),
         'UNIVERSE': ('PASS' if not audit['official_stock_ids_never_in_mysql'] else 'BLOCKED',
                      f"point-in-time list = official daily table ({audit['snapshot_stocks']} stocks incl. later-delisted); none missing from MySQL; TPEx out of scope"),
         'MODEL_CONTRACT': (entry['contract']['status'], entry['contract'].get('reason', 'dedicated model, every feature with importance > 0 is populated, window after training'))}
    if set(entry['rule_inputs']) & FUNDAMENTAL_INPUTS:
        g['FUNDAMENTALS'] = ('BLOCKED', f"timing = statutory deadline (conservative); MySQL values equal MOPS PIT v3 on overlap (operating_profit {fundamentals['operating_profit_matches_cumulative']:.0%}, "
                                        f"eps {fundamentals['eps_matches_cumulative']:.2%}) but PIT v3 ends FY{fundamentals['pit_fiscal_year_max']}; window needs 2026Q1; "
                                        f"monthly revenue_yoy self-consistent {fundamentals['monthly_revenue_yoy_self_consistent']:.2%} with no official source")
    g['INSTITUTIONAL_FLOWS'] = ('BLOCKED', 'foreign_buy/trust_buy come only from MySQL and no official 三大法人 file is cached to reconcile them; '
                                           f"{audit['snapshot_rows_in_mysql_span_without_trusted_flows']} stock-days lack trusted flows; dealer_buy/margin_balance never populated "
                                           '(ignored by the models, importance 0)')
    return g


def report():
    quality = read(OUT / 'data_quality_report.json')
    models = read(OUT / 'model_contract.json')
    par = read(OUT / 'parity.json')
    rep = read(OUT / 'replay.json')
    tests = read(OUT / 'engine_test_results.json')
    before = read(OUT / 'before_legacy/native_contract_replay.json')
    prev = {r['family_id'].split(':')[-1]: r for r in csv.DictReader(open(ROOT / 'outputs/native22_validation/2026-10-22/family_validation_matrix.csv', encoding='utf-8-sig'))}
    rows, gate_rows, results, blocked, comparison = [], [], [], [], {}
    for key in NATIVE:
        entry = models['strategies'][key]
        g = gates(quality, entry)
        data_status = 'DATA_PASS' if all(v[0] == 'PASS' for v in g.values()) else 'DATA_BLOCKED'
        p = par['strategies'][key]
        engine_ok = (tests['returncode'] == 0 and entry['diagnostic_contract']['status'] == 'PASS' and p['all_equal'] and p['same_model_file'] and not p['daily_used_fallback'])
        engine_status = 'ENGINE_PASS' if engine_ok else 'ENGINE_FAIL'
        sample = rep['sessions']
        reasons = [f"{n}: {v[1]}" for n, v in g.items() if v[0] != 'PASS']
        if sample < MIN_SESSIONS:
            reasons.append(f'INSUFFICIENT_SAMPLE: {sample} certified sessions after model training < {MIN_SESSIONS}')
        r = rep['strategies'][key]
        filled = [x for x in r['execution_events'] if x['status'] == 'FILLED']
        old = before['strategies'][key]['legacy_same_close']
        weekend = sum(pd.Timestamp(f['date']).dayofweek >= 5 for f in old['fills'])
        comparison[key] = {
            'before_legacy_raw_mysql': {'fills': len(old['fills']), 'closed_trades': len(old['closed_trades']), 'fills_on_weekend_dates': weekend,
                                        'same_close_fills': sum(f['date'] == f['signal_date'] for f in old['fills']),
                                        'model': 'generic stock_ai_model.pkl (9 features, sentiment_score zero-filled)',
                                        'first_fill': min((f['date'] for f in old['fills']), default=None)},
            'after_research_official_snapshot': {'filled_orders': len(filled), 'unfilled_orders': sum(x['status'] != 'FILLED' for x in r['execution_events']),
                                                 'closed_trades': len(r['closed_trades']), 'signals_days_with_picks': sum(bool(s['picks']) for s in r['signals']),
                                                 'fills_before_signal_next_session': all(f['execution_date'] > f['signal_date'] for f in filled),
                                                 'rows_excluded_missing_inputs': sum(x['excluded'] for x in r['data_exclusions']),
                                                 'rows_seen': sum(x['rows'] for x in r['data_exclusions']), 'model': Path(r['model']['path']).name,
                                                 'first_fill': min((f['execution_date'] for f in filled), default=None)}}
        defects = ['volume_ratio is constant 1.0 in Daily (one-day frame); research uses the 20-day definition used in training',
                   'chip_score is constant 0 in Daily because dealer_buy/margin_balance are NULL; the model was trained with varying chip_score']
        if key == 'growth_momentum_breakout':
            defects.append('V34 60-day high is computed on a one-day frame, so the breakout rule degenerates to close >= 0.93 x today high (Daily and backtest alike)')
        if key == 'institutional_flow_confirmation':
            defects.append('Stage 2 (chip_score) can never pass in Daily while chip_score is 0: Daily selects nothing')
        rows.append({'strategy': key, 'engine_before': prev[key]['ENGINE'], 'engine_after': engine_status, 'DATA': data_status, 'BACKTEST': 'BACKTEST_BLOCKED',
                     'OOS': 'OOS_BLOCKED', 'PRODUCTION_ELIGIBLE': 'NO', 'blocked_by': ';'.join(n for n, v in g.items() if v[0] != 'PASS'),
                     'parity_dates_with_candidates': f"{p['dates_with_candidates']}/{len(p['rows'])}",
                     'parity_evidence': 'NON_VACUOUS' if p['dates_with_candidates'] else 'VACUOUS_ZERO_CANDIDATES_ON_BOTH_PATHS',
                     'daily_pipeline_defects_not_fixed': ' | '.join(defects)})
        gate_rows += [{'strategy': key, 'gate': n, 'status': v[0], 'evidence': v[1]} for n, v in g.items()]
        blocked += [{'strategy': key, 'axis': 'DATA' if not x.startswith('INSUFFICIENT') else 'BACKTEST', 'reason': x} for x in reasons]
        diag = r['diagnostic_metrics']
        results.append({'strategy': key, **{f'certified_{k}': None for k in CERTIFIED}, 'certified_reason': '; '.join(reasons),
                        **{f'diagnostic_{k}': diag[k]['value'] for k in diag}, 'diagnostic_final_equity': r['final_equity'],
                        'diagnostic_taiex_return': rep['benchmark']['return'], 'diagnostic_scope': f"{sample} sessions {WINDOW[0]}..{WINDOW[1]}; DATA_BLOCKED; engineering evidence only"})
    emit_csv(out('native7_status_matrix.csv'), rows)
    emit_csv(out('data_gates.csv'), gate_rows)
    emit_csv(out('blocked_reasons.csv'), blocked)
    emit_csv(out('backtest_results.csv'), results)
    write(out('before_after.json'), comparison)
    write(out('oos_validation_results.json'), {'status': 'OOS_BLOCKED', 'certified_oos': 0,
                                               'reason': 'No frozen untouched OOS exists for the native strategies; the replay window is development data after model training, not Fresh OOS'})
    inputs = {n: digest(OUT / n) for n in ('research_snapshot.parquet', 'user_settings.json', 'taiex.csv')}
    inputs.update({p.relative_to(ROOT).as_posix(): digest(p) for p in sorted((ROOT / 'ML_Data/pkl').glob('stock_ai_model*.pkl'))})
    write(out('reproduction_manifest.json'), {
        'git_head': git_head(), 'baseline_commit': BASELINE_COMMIT, 'output_dir': OUT.relative_to(ROOT).as_posix(), 'window': WINDOW,
        'commands': ['python scripts/native_recovery.py ' + m for m in ('snapshot', 'contracts', 'engine', 'parity', 'replay', 'report', 'verify')],
        'before_replay_command': "python -c \"import scripts.validate_native22 as v; v.native('2026-05-21','2026-07-28',('legacy_same_close',), out_dir=Path('<out>/before_legacy'))\"",
        'official_aggregate_sha256': quality['official']['aggregate_sha256'], 'mysql_inputs': quality['mysql_inputs'], 'inputs': inputs,
        'code': {p: digest(ROOT / p) for p in ('core/backtest/runner.py', 'core/research/native_snapshot.py', 'core/research/native_frame.py',
                                                'core/research/native_models.py', 'scripts/native_recovery.py', 'test/test_native_recovery.py')},
        'outputs': {p.name: digest(p) for p in sorted(OUT.glob('*')) if p.is_file() and p.name != 'reproduction_manifest.json'},
        'environment': {'python': platform.python_version(), 'pandas': pd.__version__, 'platform': platform.platform()},
        'BROKER_ORDER_SUBMISSION': 'DISABLED'})
    print(Counter(r['DATA'] for r in rows), Counter(r['engine_after'] for r in rows))


def verify():
    rows = list(csv.DictReader(open(OUT / 'native7_status_matrix.csv', encoding='utf-8-sig')))
    results = list(csv.DictReader(open(OUT / 'backtest_results.csv', encoding='utf-8-sig')))
    assert [r['strategy'] for r in rows] == list(NATIVE) and len(results) == 7, 'STRATEGY_SET'
    for r in rows:
        assert r['BACKTEST'] != 'BACKTEST_PASS' or r['DATA'] == 'DATA_PASS', 'BACKTEST_WITHOUT_DATA'
        assert r['engine_after'] != 'ENGINE_PASS' or r['DATA'] in ('DATA_PASS', 'DATA_BLOCKED'), 'AXIS'
        assert r['PRODUCTION_ELIGIBLE'] == 'NO', 'PRODUCTION'
    for r, s in zip(results, rows):
        assert s['DATA'] == 'DATA_PASS' or all(r[f'certified_{k}'] == '' for k in CERTIFIED), 'CERTIFIED_METRIC_WITHOUT_DATA'
    untouched = subprocess.run(['git', 'diff', '--name-only', BASELINE_COMMIT, '--', 'execution', 'core/runtime', 'jobs/scheduler.py', 'jobs/run_daily.py',
                                'core/strategies', 'core/calc_indicators.py', 'config', 'strategy_settings.json', '4_run_backtest.py', '5_push_to_line.py'],
                               cwd=ROOT, capture_output=True, text=True, check=True).stdout.split()
    assert not untouched, 'PROTECTED_CODE_CHANGED: ' + str(untouched)
    kept = preservation()
    assert kept['status'] == 'PASS', 'PRESERVATION'
    write(out('verification.json'), {'status': 'PASS', 'strategies': 7, 'protected_code_unchanged': True, 'preservation': kept, 'BROKER_ORDER_SUBMISSION': 'DISABLED'})
    print('VERIFY PASS')


if __name__ == '__main__':
    sys.stdout.reconfigure(encoding='utf-8')  # strategies print emoji; the Windows console defaults to cp950
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('stage', choices=('snapshot', 'tpex', 'contracts', 'engine', 'parity', 'replay', 'report', 'verify'))
    globals()[parser.parse_args().stage]()
