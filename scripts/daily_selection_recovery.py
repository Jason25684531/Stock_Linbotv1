"""Daily selection recovery: legacy baseline, official chip cache, shadow comparison, parity, report, verify.

Every stage writes into one immutable output directory (an existing file is never overwritten).
Daily runs are dry-run only (no DB writes, no LINE, no news API); MySQL is read, never modified.
Order: baseline -> chips -> shadow -> contracts -> parity -> tests -> report -> verify.
"""
import argparse
import contextlib
import json
import sys
from pathlib import Path
from unittest.mock import patch

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.audit_strategy_history import digest, read, write
from scripts.validate_native22 import NATIVE

OUT = ROOT / 'outputs/daily_selection_recovery/2026-10-22'
SNAPSHOT_DATES = ('2026-05-21', '2026-06-04', '2026-06-18', '2026-07-03', '2026-07-20')  # native7 parity dates
# dealer_buy / margin_balance exist in MySQL only from 2026-10-12; margin_change_pct needs the prior balance.
LIVE_DATES = ('2026-10-13', '2026-10-14', '2026-10-15', '2026-10-16', '2026-10-20')
FEATURES = ['stock_id', 'close_price', 'high_price', 'volume', 'volume_ratio', 'high_60', 'ma20', 'ma60', 'kd_k', 'rsi', 'natr',
            'foreign_buy', 'trust_buy', 'dealer_buy', 'margin_balance', 'dealer_ratio', 'margin_change_pct', 'chip_score',
            'foreign_consec_days', 'trust_consec_days', 'revenue_yoy', 'eps', 'op_profit_margin']


def out(name):
    path = OUT / name
    if path.exists():
        raise FileExistsError(path)  # immutable evidence; rerun into a new directory
    return path


def strategy_name(key):
    from core.strategy_manager import StrategyManager

    return StrategyManager().get_strategy(key).name


def daily(dates, subdir, **kwargs):
    """Dry-run run_daily per date; records the shared input frame and every strategy's ordered candidates."""
    from jobs import run_daily

    names = {strategy_name(k): k for k in NATIVE}
    target = OUT / subdir
    target.mkdir(parents=True, exist_ok=False)
    real = run_daily.run_strategy
    result = {}
    for day in dates:
        seen = {}

        def recording(strategy, df, date_str, engine, dry_run=False, **kw):
            seen.setdefault('frame', df)  # legacy: one shared frame
            picks = real(strategy, df, date_str, engine, dry_run=dry_run, **kw)
            seen[strategy.name] = picks
            return picks

        def provider(date_str, engine):
            seen['provider'] = real_loader(date_str, engine)
            return seen['provider']

        real_loader = run_daily.load_recovered_data
        with patch.object(run_daily, 'run_strategy', recording), patch.object(run_daily, 'load_recovered_data', provider), \
                open(target / f'{day}.log', 'w', encoding='utf-8') as log, contextlib.redirect_stdout(log):
            summary = run_daily.run_daily_for_date(day, dry_run=True, **kwargs)
        # recovered: every strategy gets its own filtered frame; features come from the full shared cross-section
        frame = seen['provider'].cross_section(pd.Timestamp(day)) if 'provider' in seen else seen.get('frame', pd.DataFrame())
        frame[[c for c in FEATURES if c in frame]].to_parquet(target / f'features_{day}.parquet', index=False)
        rows = {}
        for name, key in names.items():
            picks = seen.get(name, pd.DataFrame())
            rows[key] = {'candidates': len(picks), 'ranked': [] if picks.empty else list(picks.stock_id.astype(str)),
                         'ai_score': None if picks.empty or 'ai_score' not in picks else
                         [None if pd.isna(v) else float(v) for v in picks.ai_score],
                         'error': (summary or {}).get('strategy_errors', {}).get(name)}
        result[day] = {'rows': len(frame), 'strategies': rows,
                       **{k: summary[k] for k in ('feature_mode', 'exclusions', 'model_status') if summary and k in summary}}
    write(target / 'selection.json', result)
    print({d: {k: v['candidates'] for k, v in r['strategies'].items()} for d, r in result.items()})


def baseline():
    """Current (legacy) Daily on MySQL, before any code change: reproducibility baseline, not valid performance."""
    daily(SNAPSHOT_DATES + LIVE_DATES, 'before_legacy')


def shadow():
    """Recovered Daily on the same MySQL dates, dry-run (no DB writes, no LINE, no news API)."""
    daily(SNAPSHOT_DATES + LIVE_DATES, 'after_recovered', feature_mode='recovered')


NATIVE7 = ROOT / 'outputs/native7_recovery/2026-10-22'
CHIP_CACHE = ROOT / 'artifacts/official_chip_cache/twse'  # research cache, git-ignored; never written to MySQL
CHIP_URLS = {'T86': 'https://www.twse.com.tw/rwd/zh/fund/T86?date={d}&selectType=ALLBUT0999&response=json',
             'MI_MARGN': 'https://www.twse.com.tw/rwd/zh/marginTrading/MI_MARGN?date={d}&selectType=ALL&response=json'}
# T86 column positions (official field order): 4 foreign net excl. foreign dealers, 7 foreign dealers net, 10 trust net, 11 dealer net.
T86_COLUMNS = {'foreign_buy': 4, 'foreign_dealer_buy': 7, 'trust_buy': 10, 'dealer_buy': 11}
CHIP_WINDOW = ('2025-12-15', '2026-07-28')  # 150-day lookback before the first parity date through the official calendar end


def chip_dates():
    snap = pd.read_parquet(NATIVE7 / 'research_snapshot.parquet', columns=['trade_date'])
    days = sorted(pd.Timestamp(d).strftime('%Y-%m-%d') for d in snap.trade_date.unique())
    live = [d.strftime('%Y-%m-%d') for d in pd.bdate_range('2026-09-28', '2026-10-21')]  # calendar not certified: weekdays, classified by response
    return [d for d in days if CHIP_WINDOW[0] <= d <= CHIP_WINDOW[1]] + live


def fetch_raw(dataset, day, gate):
    """Cached official response; downloads once at a polite rate and never overwrites a cached file."""
    import requests
    from core.runtime import public_data
    from datetime import datetime, timezone

    path = CHIP_CACHE / f'{dataset}_{day.replace("-", "")}.json'
    if not path.exists():
        gate.wait()
        url = CHIP_URLS[dataset].format(d=day.replace('-', ''))
        res = public_data.request('GET', url, headers={'User-Agent': 'StockLinbot-Research/1.0'}, timeout=30)
        res.raise_for_status()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(res.content)
        path.with_suffix('.meta.json').write_text(json.dumps({'url': url, 'retrieved_at': datetime.now(timezone.utc).isoformat()}), encoding='utf-8')
    return path


def parse_chip(dataset, path, day):
    from core.crawlers.chip_data_scraper import _clean_number, _resolve_twse_balance_indices

    data = json.loads(path.read_text(encoding='utf-8'))
    if data.get('stat') != 'OK':
        return None, data.get('stat')
    if data.get('date') != day.replace('-', ''):
        raise ValueError(f'SOURCE_DATE_MISMATCH: {dataset} {day} -> {data.get("date")}')
    if dataset == 'T86':
        rows = pd.DataFrame(data['data'])
        frame = pd.DataFrame({'stock_id': rows[0].str.strip(), **{c: rows[i].map(_clean_number) for c, i in T86_COLUMNS.items()}})
    else:
        table = next(t for t in data['tables'] if '代號' in t.get('fields', []))
        rows = pd.DataFrame(table['data'], columns=table['fields'])
        sid, bal, _short = _resolve_twse_balance_indices(list(rows.columns))
        frame = pd.DataFrame({'stock_id': rows.iloc[:, sid].astype(str).str.strip(), 'margin_balance': rows.iloc[:, bal].map(_clean_number)})
    return frame.assign(trade_date=day), 'OK'


def chips():
    """Official TWSE T86 (institutional net buy) and MI_MARGN (margin balance), cached and reconciled to MySQL."""
    from core.research.sources.twse import RequestGate

    gate = RequestGate(minimum_interval=3.0)  # TWSE throttles bursts; one request every 3 s
    frames, coverage, files = {'T86': [], 'MI_MARGN': []}, [], {}
    for day in chip_dates():
        for dataset in frames:
            path = fetch_raw(dataset, day, gate)
            frame, stat = parse_chip(dataset, path, day)
            files[path.name] = {'sha256': digest(path), **json.loads(path.with_suffix('.meta.json').read_text(encoding='utf-8'))}
            coverage.append({'dataset': dataset, 'date': day, 'stat': stat, 'rows': 0 if frame is None else len(frame)})
            if frame is not None:
                frames[dataset].append(frame)
    flows = pd.concat(frames['T86']).drop_duplicates(['stock_id', 'trade_date'])
    margin = pd.concat(frames['MI_MARGN']).drop_duplicates(['stock_id', 'trade_date'])
    official = flows.merge(margin, on=['stock_id', 'trade_date'], how='outer')
    official = official[official.stock_id.str.fullmatch(r'[1-9]\d{3}')]
    official.to_parquet(out('official_chips.parquet'), index=False)
    write(out('official_chip_manifest.json'), {
        'sources': CHIP_URLS, 'cache_dir': CHIP_CACHE.relative_to(ROOT).as_posix(), 'window': [chip_dates()[0], chip_dates()[-1]],
        'field_definitions': {'foreign_buy': 'T86 外陸資買賣超股數(不含外資自營商), shares', 'foreign_dealer_buy': 'T86 外資自營商買賣超股數, shares',
                              'trust_buy': 'T86 投信買賣超股數, shares', 'dealer_buy': 'T86 自營商買賣超股數 (自行買賣+避險), shares',
                              'margin_balance': 'MI_MARGN 融資今日餘額, lots (張)'},
        'coverage': coverage, 'files': files, 'official_chips_sha256': digest(OUT / 'official_chips.parquet')})
    print(pd.DataFrame(coverage).groupby(['dataset', 'stat']).size().to_dict())


CHIPS = ('foreign_buy', 'trust_buy', 'dealer_buy', 'margin_balance')


def match_table(left, right, keys=('stock_id', 'trade_date')):
    """Per-field exact agreement between MySQL (left) and official (right) on shared keys; whole-date failures listed."""
    merged = left.merge(right, on=list(keys), suffixes=('_db', '_off'))
    result = {}
    for c in CHIPS:
        both = merged.dropna(subset=[f'{c}_db', f'{c}_off'])
        equal = both[f'{c}_db'] == both[f'{c}_off']
        by_day = equal.groupby(both.trade_date).mean()
        result[c] = {'shared_keys': len(both), 'equal': int(equal.sum()), 'mismatch': int((~equal).sum()),
                     'match_rate': None if both.empty else round(float(equal.mean()), 6),
                     'dates_below_99pct': {d: round(float(v), 4) for d, v in by_day[by_day < .99].items()},
                     'db_null_where_official_present': int((merged[f'{c}_db'].isna() & merged[f'{c}_off'].notna()).sum())}
    return result


def gate(rec, c):
    if rec[c]['shared_keys'] == 0:
        return 'DATA_BLOCKED'
    return 'DATA_PASS' if rec[c]['mismatch'] == 0 else 'DATA_FAIL'


def reconcile():
    """MySQL chips vs official T86/MI_MARGN; research snapshot with official chips; per-source data gates."""
    from sqlalchemy import text

    from core.db_helper import get_db_engine
    from core.research.native_frame import load_publication_dates, prepare_fundamentals

    official = pd.read_parquet(OUT / 'official_chips.parquet')
    snap = pd.read_parquet(NATIVE7 / 'research_snapshot.parquet')
    snap_keys = snap.assign(trade_date=snap.trade_date.dt.strftime('%Y-%m-%d'))
    research_rec = match_table(snap_keys[snap_keys.trade_date >= CHIP_WINDOW[0]][['stock_id', 'trade_date', *CHIPS]], official)
    live = pd.read_sql(text("""SELECT stock_id, trade_date, foreign_buy, trust_buy, dealer_buy, margin_balance FROM daily_market_data
                               WHERE trade_date BETWEEN '2026-09-28' AND '2026-10-21'"""), get_db_engine())
    live = live.assign(stock_id=live.stock_id.astype(str).str.strip(), trade_date=pd.to_datetime(live.trade_date).dt.strftime('%Y-%m-%d'))
    live = live[live.stock_id.str.fullmatch(r'[1-9]\d{3}')]
    live_rec = match_table(live, official)

    # Research snapshot: official chips replace MySQL; outside the official window or absent from the official file -> NaN.
    joined = snap_keys.drop(columns=list(CHIPS)).merge(official[['stock_id', 'trade_date', *CHIPS]], on=['stock_id', 'trade_date'], how='left')
    joined['trade_date'] = pd.to_datetime(joined.trade_date)
    joined.to_parquet(out('research_snapshot_official_chips.parquet'), index=False)
    traded = joined[(joined.trade_date >= CHIP_WINDOW[0]) & joined.close_price.notna()]

    fin = pd.read_parquet(ROOT / 'outputs/strategy_validation/2026-10-21/snapshot_financial_statements.parquet')
    rev = pd.read_parquet(ROOT / 'outputs/strategy_validation/2026-10-21/snapshot_monthly_revenue.parquet')
    fin_table, _ = prepare_fundamentals(fin[fin.year >= 1911], rev, load_publication_dates())
    sources = fin_table.availability_source.value_counts().to_dict()
    gates = [
        {'gate': 'PRICES_TWSE', 'status': 'DATA_PASS', 'evidence': 'native7 official MI_INDEX snapshot (99.70% MySQL OHLC agreement); reused, not rebuilt'},
        {'gate': 'CALENDAR', 'status': 'DATA_PASS', 'scope': '2023-01-03..2026-07-28 official sessions',
         'note': 'After 2026-07-28 not certified; 2026-09-28..10-21 sessions only classified by T86/MI_MARGN responses'},
        {'gate': 'TAIEX_BENCHMARK', 'status': 'DATA_PASS', 'evidence': 'native7 taiex.csv (378 official closes)',
         'note': 'Benchmark only. Strategy market filter reads MARKET_SYMBOL=2330 (a strategy rule), not TAIEX'},
        {'gate': 'UNIVERSE_TWSE', 'status': 'DATA_PASS', 'evidence': 'native7 official daily lists'},
        {'gate': 'UNIVERSE_TPEX', 'status': 'DATA_BLOCKED', 'reason': 'No official TPEx price/calendar cache; research excludes TPEx. '
         'Live recovered Daily still sees TPEx rows from MySQL (uncertified, see owner_decisions_required.md OD-8)'},
        {'gate': 'INSTITUTIONAL_FLOWS_OFFICIAL', 'status': 'DATA_PASS', 'scope': f'{CHIP_WINDOW[0]}..{CHIP_WINDOW[1]}, 2026-09-28..2026-10-21',
         'evidence': 'official_chip_manifest.json (per-file SHA-256)', 'traded_rows_without_official_t86': int(traded.foreign_buy.isna().sum()),
         'note': 'A traded stock absent from T86 is kept NaN (absence is not treated as zero)'},
        {'gate': 'MARGIN_OFFICIAL', 'status': 'DATA_PASS', 'scope': 'same as T86', 'traded_rows_without_official_margin': int(traded.margin_balance.isna().sum())},
        *[{'gate': f'MYSQL_{c.upper()}_RESEARCH_WINDOW', 'status': gate(research_rec, c), 'reconciliation': research_rec[c]} for c in CHIPS],
        *[{'gate': f'MYSQL_{c.upper()}_LIVE_2026_10', 'status': gate(live_rec, c), 'reconciliation': live_rec[c]} for c in CHIPS],
        {'gate': 'FUNDAMENTALS', 'status': 'DATA_BLOCKED', 'availability_sources': {k: int(v) for k, v in sources.items()},
         'reason': 'Actual MOPS publication dates cover FY2025 and earlier only; later periods use the statutory deadline, which '
                   'cannot prove availability for late filers (OD-4: no DATA_PASS from deadlines)'},
        {'gate': 'MONTHLY_REVENUE', 'status': 'DATA_BLOCKED', 'reason': 'No official source or publication timestamp; statutory 10th-of-month estimate only'},
    ]
    write(out('data_quality_report.json'), {'gates': gates, 'native7_reference': 'outputs/native7_recovery/2026-10-22/data_quality_report.json',
                                            'research_snapshot_official_chips_sha256': digest(OUT / 'research_snapshot_official_chips.parquet'),
                                            'units': 'T86 values are shares, identical to MySQL foreign_buy/trust_buy/dealer_buy; margin_balance in lots'})
    print({g['gate']: g['status'] for g in gates})


def parity():
    """Identical official inputs and dates: recovered Daily (run_daily code path) vs the research engine (find_candidates)."""
    from core.backtest.runner import AI_CONFIDENCE_THRESHOLD, BacktestEngine
    from core.research.native_frame import NativeResearchData, load_publication_dates
    from jobs import run_daily
    from scripts.native_recovery import isolated

    snap = pd.read_parquet(OUT / 'research_snapshot_official_chips.parquet')
    fin = pd.read_parquet(ROOT / 'outputs/strategy_validation/2026-10-21/snapshot_financial_statements.parquet')
    rev = pd.read_parquet(ROOT / 'outputs/strategy_validation/2026-10-21/snapshot_monthly_revenue.parquet')
    data = NativeResearchData(snap, fin[fin.year >= 1911], rev, publications=load_publication_dates())
    sessions = [d.strftime('%Y-%m-%d') for d in sorted(snap.trade_date.unique()) if SNAPSHOT_DATES[0] <= d.strftime('%Y-%m-%d') <= CHIP_WINDOW[1]]
    settings = read(NATIVE7 / 'user_settings.json')
    result = {'dates': SNAPSHOT_DATES, 'inputs': 'research_snapshot_official_chips.parquet + PIT fundamentals (actual publication date first)',
              'ai_confidence_threshold_backtest_only': AI_CONFIDENCE_THRESHOLD, 'strategies': {}}
    with isolated(data, settings), open(OUT / 'parity_stdout.log', 'w', encoding='utf-8') as log, contextlib.redirect_stdout(log):
        for key in NATIVE:
            engine = BacktestEngine(mode=key, start_date=SNAPSHOT_DATES[0], end_date=CHIP_WINDOW[1], initial_capital=1_000_000,
                                    persist_results=False, persist_to_db=False, execution_contract='next_session_open_v2',
                                    trading_sessions=sessions, research_data=data)
            strategy, rows = engine.strategy_obj, []
            for day in SNAPSHOT_DATES:
                picks = engine.find_candidates(day)
                frame, note, contract, status = run_daily.recovered_strategy_frame(strategy, data, day)
                live = run_daily.run_strategy(strategy, frame, day, None, dry_run=True, recovered=True, contract=contract, model_status=status) \
                    if not frame.empty else frame
                ranked = [] if live.empty else list(live.stock_id)
                if not live.empty and live.ai_score.notna().any():
                    confident = live[live.ai_score >= AI_CONFIDENCE_THRESHOLD]
                    ranked = list((confident if not confident.empty else live).stock_id)
                rows.append({'date': day, 'research_top3': picks, 'daily_top3': ranked[:3], 'daily_candidates': len(live),
                             'equal': picks == ranked[:3], 'same_required_inputs': sorted(engine.required_inputs) == sorted(
                                 run_daily.rule_inputs(strategy) | set(contract.required_raw if contract else ())),
                             'rows': note['rows'], 'excluded': note['excluded'], 'model_status': status})
            equal = all(r['equal'] and r['same_required_inputs'] for r in rows)
            non_empty = sum(bool(r['research_top3']) for r in rows)
            result['strategies'][key] = {'strategy_name': strategy.name, 'model_file': Path(engine.model_identity['path']).name,
                                         'PARITY': 'PARITY_FAIL' if not equal else 'PARITY_PASS' if non_empty else 'NOT_VERIFIABLE',
                                         'dates_with_candidates': non_empty, 'rows': rows}
    write(out('parity_results.json'), result)
    print({k: (v['PARITY'], v['dates_with_candidates']) for k, v in result['strategies'].items()})


def contracts():
    """Model identity, feature order, inputs per strategy, and how chip_score looked in the data the models were trained on (OD-3)."""
    import joblib
    from sqlalchemy import text

    from core.db_helper import get_db_engine
    from core.model_utils import get_model_path
    from core.research.native_frame import FRAME_INPUTS, rule_inputs
    from core.research.native_models import ModelContractError, load_model_contract
    from core.strategy_manager import StrategyManager

    manager, rows = StrategyManager(), {}
    for key in NATIVE:
        strategy = manager.get_strategy(key)
        data = joblib.load(get_model_path(strategy.name))
        try:
            identity = load_model_contract(strategy.name, FRAME_INPUTS, SNAPSHOT_DATES[0]).identity
            contract_error = None
        except ModelContractError as exc:
            identity, contract_error = None, str(exc)
        rows[key] = {'strategy_name': strategy.name, 'contract_error': contract_error, 'identity': identity,
                     'metadata_keys': sorted(data), 'look_ahead_days': data.get('look_ahead_days'), 'target_return': data.get('target_return'),
                     'rule_inputs': sorted(rule_inputs(strategy)),
                     'required_inputs': sorted(rule_inputs(strategy) | set((identity or {}).get('raw_inputs_required', []))),
                     'MODEL_STATUS': 'MODEL_BLOCKED' if contract_error or not data.get('training_data_end') else 'MODEL_PASS',
                     'model_blocked_reasons': [r for r in (contract_error, None if data.get('training_data_end') else
                                               'TRAINING_DATA_END_MISSING: only training_date is recorded; train/test boundary cannot be proven') if r]}
    with get_db_engine().connect() as conn:  # read-only aggregate
        columns = [r[0] for r in conn.execute(text('SHOW COLUMNS FROM daily_market_data'))]
        trained = conn.execute(text("""
            SELECT COUNT(*) n, SUM(foreign_buy IS NOT NULL) foreign_non_null, SUM(trust_buy IS NOT NULL) trust_non_null,
                   SUM(dealer_buy IS NOT NULL) dealer_non_null, SUM(margin_balance IS NOT NULL) margin_non_null,
                   SUM(dealer_buy_vol <> 0) dealer_buy_vol_non_zero, MIN(trade_date) first_date, MAX(trade_date) last_date
            FROM daily_market_data WHERE trade_date <= '2026-05-20'""")).mappings().one()
    trace = {k: (str(v) if k.endswith('_date') else int(v or 0)) for k, v in trained.items()}
    importance = {key: {f: r['identity']['feature_order'] and float(dict(zip(r['identity']['feature_order'],
                                                                          joblib.load(get_model_path(r['strategy_name']))['model'].feature_importances_))[f])
                        for f in ('chip_score_z', 'dealer_ratio_z', 'dealer_consec_days_z', 'institutional_net_buy_z')}
                  for key, r in rows.items() if r['identity']}
    write(out('model_contract_report.json'), {
        'scope': 'Seven native strategy models; read-only. No model is retrained (OD-3).', 'strategies': rows,
        'chip_score_training_trace': {
            'code': 'jobs/train_model.py:prepare_training_data: dealer_buy defaults to 0 when the column is absent (:165); dealer_ratio '
                    'and margin_change_pct are recomputed only when absent or all 0 (:174-185; margin_change_pct = 0 when '
                    'margin_balance is absent); chip_score = calculate_chip_score (:190); build_multi_factor_matrix fills NaN with 0',
            'daily_market_data_columns_now': columns, 'chip_score_column_in_db': 'chip_score' in columns,
            'rows_up_to_training_date': trace, 'feature_importance': importance,
            'finding': 'Up to the training date every dealer_buy and margin_balance is NULL (dealer_buy_vol is always 0), and all seven '
                       'models give dealer_ratio_z and dealer_consec_days_z zero importance while chip_score_z is used. That matches '
                       'training with the dealer and margin components held at their neutral value (score 50), i.e. chip_score '
                       'varied only through foreign_ratio and trust_ratio. The documented formula with real dealer and margin '
                       'inputs (available from 2026-10-12 in MySQL) is a different feature. The schema at training time is not '
                       'recorded, so this is an inference from code and importances, not proof.',
            'status': 'MODEL_BLOCKED: training-time chip_score definition unproven (OD-3); no retraining, no zero filling'},
        'training_data_end': {k: r['identity'] and r['identity'].get('training_data_end') for k, r in rows.items()}})
    print({k: (r['MODEL_STATUS'], r['contract_error']) for k, r in rows.items()}, trace)


TESTS = ['test/test_daily_selection_recovery.py', 'test/test_native_recovery.py', 'test/characterization', 'test/test_run_daily.py',
         'test/test_run_daily_persistence.py', 'test/test_daily_platform_parity.py', 'test/test_daily_pipeline_consolidation_contract.py',
         'test/test_native22_engine_contracts.py', 'test/test_next_session_execution.py', 'test/test_research_execution_price.py',
         'test/test_strategy_center.py', 'test/test_strategy_search.py']


def tests():
    import subprocess
    import xml.etree.ElementTree as ET
    from collections import Counter

    junit = out('test_junit.xml')
    proc = subprocess.run([sys.executable, '-m', 'pytest', *TESTS, '-q', '-p', 'no:cacheprovider', f'--junitxml={junit}'],
                          cwd=ROOT, capture_output=True, text=True)
    cases = [{'test': f"{c.get('classname')}::{c.get('name')}",
              'status': 'FAIL' if c.find('failure') is not None or c.find('error') is not None else 'SKIP' if c.find('skipped') is not None else 'PASS'}
             for c in ET.parse(junit).getroot().iter('testcase')]
    write(out('test_results.json'), {'command': 'python -m pytest ' + ' '.join(TESTS), 'returncode': proc.returncode,
                                     'summary': proc.stdout.strip().splitlines()[-1], 'counts': dict(Counter(c['status'] for c in cases)), 'cases': cases})
    print(proc.returncode, proc.stdout.strip().splitlines()[-1])


COMPARED = ('volume_ratio', 'high_60', 'chip_score', 'ma60', 'kd_k', 'margin_change_pct', 'revenue_yoy', 'eps', 'op_profit_margin')
# Which gates an input depends on, research window (official chips) and live October (MySQL).
FUNDAMENTAL_GATES = {'revenue_yoy': 'MONTHLY_REVENUE', 'eps': 'FUNDAMENTALS', 'op_profit_margin': 'FUNDAMENTALS'}


def report():
    """Before/after tables, owner decisions and the reproduction manifest from the stage outputs (no new computation)."""
    import csv
    import platform
    import subprocess

    before, after = read(OUT / 'before_legacy/selection.json'), read(OUT / 'after_recovered/selection.json')
    contracts_report, parity_report = read(OUT / 'model_contract_report.json'), read(OUT / 'parity_results.json')
    gates = {g['gate']: g['status'] for g in read(OUT / 'data_quality_report.json')['gates']}
    feature_rows, summary = [], []
    for day in before:
        old = pd.read_parquet(OUT / f'before_legacy/features_{day}.parquet').set_index('stock_id')
        new = pd.read_parquet(OUT / f'after_recovered/features_{day}.parquet').set_index('stock_id')
        old['high_60'] = old.high_price  # legacy V34 value: a one-day rolling max is today's high
        old, new = old.apply(pd.to_numeric, errors='coerce'), new.apply(pd.to_numeric, errors='coerce')
        shared = old.index.intersection(new.index)
        frame = pd.DataFrame({'date': day, 'stock_id': shared})
        for c in COMPARED:
            frame[f'{c}_before'], frame[f'{c}_after'] = old.loc[shared, c].to_numpy(), new.loc[shared, c].to_numpy()
            b, a = old.loc[shared, c], new.loc[shared, c]
            summary.append({'date': day, 'feature': c, 'stocks': len(shared), 'before_distinct': int(b.nunique()), 'after_distinct': int(a.nunique()),
                            'before_null': int(b.isna().sum()), 'after_null': int(a.isna().sum()),
                            'before_zero': int((b == 0).sum()), 'after_zero': int((a == 0).sum()),
                            'before_equals_1': int((b == 1).sum()) if c == 'volume_ratio' else None,
                            'changed': int(((b - a).abs() > 1e-12 + 1e-9 * a.abs()).sum() + (b.isna() ^ a.isna()).sum())})
        feature_rows.append(frame)
    pd.concat(feature_rows).to_csv(out('feature_comparison.csv'), index=False, encoding='utf-8-sig')
    pd.DataFrame(summary).to_csv(out('feature_comparison_summary.csv'), index=False, encoding='utf-8-sig')

    selection = []
    for key in NATIVE:
        info = contracts_report['strategies'][key]
        name = info['strategy_name']
        fundamental = {FUNDAMENTAL_GATES[i] for i in info['required_inputs'] if i in FUNDAMENTAL_GATES}
        for day in before:
            b, a = before[day]['strategies'][key], after[day]['strategies'][key]
            note = after[day]['exclusions'][name]
            # These runs read MySQL (dry-run), so the MySQL gates apply; official-input evidence is in parity_results.json.
            window = 'RESEARCH_WINDOW' if day in SNAPSHOT_DATES else 'LIVE_2026_10'
            used = sorted({f'MYSQL_{c.upper()}_{window}' for c in CHIPS} | {'UNIVERSE_TPEX'} | fundamental)
            scored = a['ai_score'] is not None and any(v is not None for v in a['ai_score'])
            data_status = 'DATA_PASS' if all(gates.get(g) == 'DATA_PASS' for g in used) else \
                'DATA_FAIL' if any(gates.get(g) == 'DATA_FAIL' for g in used) else 'DATA_BLOCKED'
            selection.append({
                'strategy': key, 'strategy_name': name, 'date': day, 'source': 'mysql_dry_run',
                'before_candidates': b['candidates'], 'after_candidates': a['candidates'],
                'before_missing_excluded': 0, 'after_missing_excluded': note['excluded'], 'after_rows': note['rows'],
                'after_missing_by_reason': json.dumps(pd.Series([r['reason'] for r in note['excluded_rows']], dtype=object).value_counts().to_dict()),
                'before_top3': ' '.join(b['ranked'][:3]),
                'after_top3': ' '.join(a['ranked'][:3]) + ' (diagnostic AI score; model blocked)' if scored and a['ranked'] else
                'NOT_VERIFIABLE (no candidates)' if not a['ranked'] else 'NOT_VERIFIABLE (unscored)',
                'after_rule_order_top3': ' '.join(a['ranked'][:3]),
                'before_model_features': 'INVALID: features zero-filled, volume_ratio 1.0, chip_score from zero-filled inputs',
                'after_model_features': after[day]['model_status'][name] or 'MODEL_VALID',
                'before_conditions': 'DEGRADED: volume_ratio constant, V34 high_60 = today high, NULL -> 0 inputs pass conditions',
                'after_conditions': 'COMPLETE: every rule input present; rows missing any are excluded before the strategy',
                'engineering_before': 'ENGINE_FAIL (P0-1..P0-4)',
                'engineering_after': 'ENGINE_PASS' if parity_report['strategies'][key]['PARITY'] == 'PARITY_PASS' else parity_report['strategies'][key]['PARITY'],
                'data_after': data_status, 'data_gates_used': ' '.join(used),
                'publishable_after': 'NO (OD-5/OD-7: recovered is shadow-only; model blocked)'})
    with out('strategy_selection_comparison.csv').open('w', newline='', encoding='utf-8-sig') as handle:
        writer = csv.DictWriter(handle, fieldnames=list(selection[0]))
        writer.writeheader()
        writer.writerows(selection)

    (out('owner_decisions_required.md')).write_text(OWNER_DECISIONS, encoding='utf-8')
    outputs = {p.relative_to(OUT).as_posix(): digest(p) for p in sorted(OUT.rglob('*')) if p.is_file() and '_aborted' not in p.parts}
    chip_manifest = read(OUT / 'official_chip_manifest.json')
    write(out('reproduction_manifest.json'), {
        'change': 'openspec/changes/2026-10-22-recover-daily-selection-pipeline', 'output_dir': OUT.relative_to(ROOT).as_posix(),
        'git_head': subprocess.run(['git', 'rev-parse', 'HEAD'], cwd=ROOT, capture_output=True, text=True).stdout.strip(),
        'baseline_head': '1e43f1d773f33d6643a082a783a8138fd10712a1',
        'commands': [f'python scripts/daily_selection_recovery.py {s}' for s in
                     ('baseline  # run at 1e43f1d before the code change', 'chips', 'reconcile', 'shadow', 'contracts', 'parity', 'tests', 'report', 'verify')],
        'inputs': {'native7_research_snapshot': digest(NATIVE7 / 'research_snapshot.parquet'),
                   'mysql_financial_snapshot': digest(ROOT / 'outputs/strategy_validation/2026-10-21/snapshot_financial_statements.parquet'),
                   'mysql_revenue_snapshot': digest(ROOT / 'outputs/strategy_validation/2026-10-21/snapshot_monthly_revenue.parquet'),
                   'fundamental_pit_v3_records': digest(ROOT / 'data/processed/fundamental_pit_v3/fundamental_records.parquet'),
                   'official_chip_files': len(chip_manifest['files']), 'official_chips': chip_manifest['official_chips_sha256'],
                   'user_settings': digest(NATIVE7 / 'user_settings.json'),
                   'mysql_live': 'read-only queries at run time; live Daily dry-runs are not byte-reproducible after the DB changes'},
        'code': {p: digest(ROOT / p) for p in ('scripts/daily_selection_recovery.py', 'jobs/run_daily.py', 'core/calc_indicators.py',
                                                'core/research/native_frame.py', 'core/research/native_models.py',
                                                'core/strategies/growth_momentum_breakout.py', 'config/settings.py',
                                                'test/test_daily_selection_recovery.py')},
        'models': {k: v['identity'] and v['identity']['sha256'] for k, v in contracts_report['strategies'].items()},
        'environment': {'python': platform.python_version(), 'pandas': pd.__version__, 'platform': platform.platform()},
        'outputs': outputs, 'BROKER_ORDER_SUBMISSION': 'DISABLED', 'DAILY_FEATURE_MODE_default': 'legacy'})
    print(pd.DataFrame(selection).groupby('strategy')[['before_candidates', 'after_candidates', 'after_missing_excluded']].sum().to_dict())


OWNER_DECISIONS = """# Owner Decisions Required — 2026-10-22 Daily Selection Recovery

Decided this round (OD-1..OD-7) are recorded in `openspec/changes/2026-10-22-recover-daily-selection-pipeline/design.md` §9.
Open items below need an explicit owner decision; nothing here was changed without one.

| ID | Item | Evidence | Options | Current state |
|---|---|---|---|---|
| OD-3a | Training-time `chip_score` differs from the formula with real dealer/margin data | `model_contract_report.json` `chip_score_training_trace`: dealer/margin NULL in every training row; `dealer_ratio_z` importance 0, `chip_score_z` > 0 in all 7 models | (a) retrain on certified chips with `training_data_end` recorded; (b) keep models blocked | MODEL_BLOCKED, not retrained |
| OD-3b | Models lack `training_data_end` | all 7 pickles: keys without `training_data_end` | re-export metadata from the training run, or retrain with it recorded | MODEL_BLOCKED |
| OD-5a | Recovered candidates without valid AI scores | every native model is MODEL_BLOCKED, so recovered Daily has no publishable ranking | publish by rule order, wait for models, or keep shadow | shadow only |
| OD-7a | Switch production to `DAILY_FEATURE_MODE=recovered` | parity and tests in this directory | separate acceptance run, then approval | legacy; recovered refuses non-dry-run |
| OD-8 | TPEx rows in live recovered Daily | research excludes TPEx (no official TPEx cache); live MySQL still carries TPEx stocks (weekend/duplicate history, native7 `tpex_audit.json`) | restrict live recovered to TWSE, or fetch and certify TPEx official data | uncertified, reported |
| OD-9 | MySQL institutional flows stored as 0 on whole dates | `data_quality_report.json` MYSQL_FOREIGN_BUY/TRUST_BUY_RESEARCH_WINDOW: 19 dates with < 99% agreement, MySQL 0 vs official non-zero; Oct trust 53 mismatches | repair the crawler/DB from official T86 (DB write needs approval) | research uses official T86; MySQL untouched |
| OD-10 | V36 relaxed mode drops the consecutive-buy condition | `core/strategies/institutional_flow_confirmation.py` relaxed branch keeps stage-1 rows with chip_score >= 27.5; with real chips the neutral score is ~50, so the relaxed branch admits nearly every stage-1 row when stage 2 is empty | keep, tighten, or remove (strategy rule change) | unchanged; test documents it |
| OD-11 | Ex-dividend bias in raw-price `high_60` (OD-2) | system has raw prices only; after ex-rights `high_60` stays above an adjusted high for up to 60 sessions | keep raw (stricter), or add adjusted prices | raw, reported |
| OD-12 | Monthly revenue and post-FY2025 filings have no publication timestamp | `data_quality_report.json` FUNDAMENTALS / MONTHLY_REVENUE | add MOPS publication capture for revenue and recent quarters | DATA_BLOCKED, deadline estimate flagged |
| OD-13 | Model-only inputs also exclude rule candidates | when the model contract loads (2026-10-13..16) stocks missing a model input (mostly no margin data) are excluded, e.g. 448 of 458 exclusions for mean_reversion on 2026-10-15; when the contract is refused no model exclusion applies, so candidate counts depend on model availability | keep (research engine does the same; parity relies on it), or exclude by rule inputs only and leave those stocks unscored | kept, identical in research and Daily |
"""


V34_SOURCE = 'core/strategies/growth_momentum_breakout.py'
V34_GUARD_COMMENT = "# Recovered Daily and research provide high_60 from each stock's history; a one-day legacy frame cannot."


def verify():
    """Historical results, frozen candidate, archived evidence and production defaults unchanged."""
    import csv
    import hashlib
    import subprocess

    from scripts.validate_native22 import preservation

    kept = preservation()
    # The only protected identity source changed on purpose is V34 (P0-3): the 60-day-high block now runs only when the
    # input has no high_60. Legacy frames (one-day Daily frame, DB backtest rows) never carry high_60, so legacy results
    # are behavior-identical; the diff must be exactly the guard and the DB must not hold a high_60 column.
    assert not kept['historical_changed'] and kept['inventory_identities_equal'], 'PRESERVATION'
    assert set(kept['protected_changed']) <= {V34_SOURCE}, 'PROTECTED_SOURCE_CHANGED: ' + str(kept['protected_changed'])
    v34 = [l for l in subprocess.run(['git', 'diff', '1e43f1d', '--', V34_SOURCE], cwd=ROOT, capture_output=True, text=True, encoding='utf-8',
                                     check=True).stdout.splitlines() if l.startswith(('+', '-')) and not l.startswith(('+++', '---'))]
    removed = [l[1:].strip() for l in v34 if l.startswith('-')]
    added = [l[1:].strip() for l in v34 if l.startswith('+')]
    assert added[:2] == [V34_GUARD_COMMENT, "if 'high_60' not in df_sorted.columns:"] and added[2:] == removed, 'V34_DIFF_NOT_GUARD_ONLY'
    assert 'high_60' not in read(OUT / 'model_contract_report.json')['chip_score_training_trace']['daily_market_data_columns_now'], 'DB_HIGH_60'
    summary = read(ROOT / 'outputs/strategy_revalidation/2026-10-21-audit/identity_matrix_final.json')['summary']
    families = list(csv.DictReader(open(ROOT / 'outputs/native22_validation/2026-10-22/family_validation_matrix.csv', encoding='utf-8-sig')))
    params = list(csv.DictReader(open(ROOT / 'outputs/native22_validation/2026-10-22/parameter_validation_matrix.csv', encoding='utf-8-sig')))
    counts = {'families': len(families), 'parameters': len(params), 'identities': summary['historical_validation_identities'],
              'source_records': summary['source_records']}
    assert counts == {'families': 22, 'parameters': 384, 'identities': 453, 'source_records': 461}, counts
    review = read(ROOT / 'outputs/strategy_revalidation/2026-10-21-audit/frozen_gate_evidence.json')['documents']['promotion_review']
    from core.runtime.fundamental_shadow import FrozenStrategyLoader
    assert FrozenStrategyLoader().load().fingerprint == review['strategy_fingerprint'], 'FROZEN_FINGERPRINT_CHANGED'
    assert review['evaluation_window']['cutoff'] == '2026-07-28' and review['BROKER_ORDER_SUBMISSION'] == 'DISABLED', 'FROZEN_OR_BROKER'
    archived = {}
    for folder in ('outputs/native7_recovery/2026-10-22', 'outputs/native22_validation/2026-10-22'):
        recorded = read(ROOT / folder / 'reproduction_manifest.json')['outputs']
        changed = [n for n, h in recorded.items() if hashlib.sha256((ROOT / folder / n).read_bytes()).hexdigest() != h]
        assert not changed, f'ARCHIVED_EVIDENCE_CHANGED: {folder} {changed}'
        archived[folder] = len(recorded)
    touched = subprocess.run(['git', 'diff', '--name-only', '1e43f1d', '--', 'execution', 'core/runtime', 'jobs/scheduler.py', 'strategy_settings.json',
                              '4_run_backtest.py', '5_push_to_line.py', 'core/backtest', 'app', 'services'],
                             cwd=ROOT, capture_output=True, text=True, check=True).stdout.split()
    assert not touched, 'PRODUCTION_SURFACE_CHANGED: ' + str(touched)
    from config import Config
    assert Config.DAILY_FEATURE_MODE == 'legacy', 'DEFAULT_MODE'
    params_changed = subprocess.run(['git', 'diff', '1e43f1d', '--', 'config/settings.py'], cwd=ROOT, capture_output=True, text=True, encoding='utf-8').stdout
    assert [l for l in params_changed.splitlines() if l.startswith(('+', '-')) and not l.startswith(('+++', '---'))
            and 'DAILY_FEATURE_MODE' not in l and 'dry-run/shadow' not in l] == [], 'STRATEGY_PARAMETERS_CHANGED'
    write(out('verification.json'), {'status': 'PASS', 'preservation': kept, 'intentional_protected_source_change': {
                                         V34_SOURCE: 'P0-3 guard only; legacy behavior identical (verified diff)'} if kept['protected_changed'] else {},
                                     'counts': counts, 'frozen_cutoff': '2026-07-28',
                                     'frozen_fingerprint_unchanged': True, 'archived_outputs_verified': archived,
                                     'production_surface_unchanged_since_1e43f1d': True, 'DAILY_FEATURE_MODE_default': 'legacy',
                                     'strategy_parameters_unchanged': True, 'BROKER_ORDER_SUBMISSION': 'DISABLED'})
    print('VERIFY PASS', counts, archived)


if __name__ == '__main__':
    sys.stdout.reconfigure(encoding='utf-8')  # strategies print emoji; the Windows console defaults to cp950
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('stage', choices=('baseline', 'chips', 'reconcile', 'shadow', 'contracts', 'parity', 'tests', 'report', 'verify'))
    globals()[parser.parse_args().stage]()
