"""Legacy baseline runner for round 2026-11-06-msv (design D2, D3, D13): rules only, next-session open, base and 2x cost tracks.

Importable only. Every real call is a governed trial: the orchestrator registers and admits it first (scripts/research_workflow.py).
This module never touches the trial ledger, writes no file and computes no performance metric; it returns plain data
(NAV, trades, picks, checks) and the orchestrator computes the metrics.

Reuses scripts/native7_rules_only.py (build_snapshot, inputs, seed) and the engine settings of its run(), with three differences:
the window is refused after 2025-12-31 before any input is read, warm-up data starts no earlier than 2022-01-01, and financial
statements published after the end date are dropped before the provider is built.
"""
from __future__ import annotations

import contextlib
import functools
import hashlib
import json
import os
import random
import sys
from collections import Counter
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from core.research.strategy_signal import check_window, source_hash
from scripts import native7_rules_only as n7

ROUND = '2026-11-06-msv'
RECOVERY_ROUND = '2026-11-07-legacy-od29-recovery'
START, END = '2023-01-03', '2025-12-31'
RUNNABLE = ('hybrid_trend_rank', 'defensive_low_volatility', 'institutional_flow_confirmation', 'mean_reversion', 'quality_value_low_volatility')
BLOCKED = {'growth_momentum_breakout': 'MONTHLY_REVENUE_NO_ANNOUNCEMENT_TIME', 'quality_growth': 'MONTHLY_REVENUE_NO_ANNOUNCEMENT_TIME'}
# D3: doubled maximum holding days. V36/V37/V38 read V3x_MAX_HOLD_DAYS through BaseStrategy._get_float_setting, so runtime_overrides
# reach them. V31 (10) and V33 (12) hard-code the max_hold_days property: check_injectable returns a reason and the run is NOT_TESTED.
SENSITIVITY = {'hybrid_trend_rank': {'MAX_HOLD_DAYS': 20}, 'defensive_low_volatility': {'MAX_HOLD_DAYS': 24},
               'institutional_flow_confirmation': {'V36_MAX_HOLD_DAYS': 24}, 'mean_reversion': {'V37_MAX_HOLD_DAYS': 16},
               'quality_value_low_volatility': {'V38_MAX_HOLD_DAYS': 30}}
EXECUTION_CONTRACT = 'next_session_open_v2'
STRESS_MULTIPLE = 2
WARMUP_FLOOR = '2022-01-01'  # D2: warm-up data from 2022 only
INITIAL_CAPITAL = 1_000_000
FUNDAMENTAL_COLUMNS = frozenset({'eps', 'op_profit_margin', 'rd_ratio'})
CHIP_STRATEGY = 'institutional_flow_confirmation'
DEGENERATE_ZERO_SHARE = 0.95


def _costs(multiple=1):
    from config import Config
    from core.backtest.runner import FEE_RATE, MIN_FEE, TAX_RATE

    return {'fee_rate': FEE_RATE * multiple, 'tax_rate': TAX_RATE * multiple, 'minimum_fee': MIN_FEE * multiple,
            'slippage_max': float(getattr(Config, 'SLIPPAGE_MAX_PIPS', Config.SLIPPAGE_RATE)) * multiple}


def config_for(strategy_id, overrides=None):
    """Canonical JSON-able identity of one admission (base and 2x stress tracks share it)."""
    return {'round': ROUND, 'strategy': strategy_id, 'start': START, 'end': END, 'execution_contract': EXECUTION_CONTRACT, 'rules_only': True,
            'valuation_policy': 'finite_positive_stale',
            'seed': n7.SLIPPAGE_SEED, **_costs(), 'stress_multiple': STRESS_MULTIPLE, 'overrides': dict(sorted((overrides or {}).items()))}


def _strategy_file(strategy_id):
    from core.strategy_manager import StrategyManager

    return ROOT / (StrategyManager.CANONICAL_REGISTRY[strategy_id].rsplit('.', 1)[0].replace('.', '/') + '.py')


def code_paths():
    return [Path(__file__).resolve(), Path(n7.__file__).resolve(), ROOT / 'core/backtest/runner.py', *(_strategy_file(s) for s in RUNNABLE),
            ROOT / 'core/strategies/base.py', ROOT / 'core/strategy/adapters/legacy.py', ROOT / 'core/research/native_frame.py',
            ROOT / 'scripts/native_recovery.py', ROOT / 'core/backtest/execution.py', ROOT / 'core/backtest/costs.py',
            ROOT / 'core/backtest/portfolio.py', ROOT / 'core/strategy_manager.py', ROOT / 'config.py', ROOT / 'core/research/benchmark_tri.py']


def input_paths():
    """Files a real run reads. The per-day T86 / MI_MARGN files are pinned by the chip manifest (one SHA256 per file)."""
    from core.research.native_frame import PIT_RECORDS

    return {'bars': n7.OUT / 'official_bars.parquet', 'chip_manifest': n7.odr.PREV_CHIPS, 'benchmarks': n7.OUT / 'benchmarks.csv',
            'delistings': n7.OUT / 'delistings.csv', 'user_settings': n7.NATIVE7 / 'user_settings.json',
            'financial_statements': n7.MYSQL / 'snapshot_financial_statements.parquet', 'monthly_revenue': n7.MYSQL / 'snapshot_monthly_revenue.parquet',
            'publication_dates': PIT_RECORDS, 'tri_source': ROOT / 'data/benchmark/MFI94U',
            'tri_calendar': ROOT / 'outputs/research_mode/prepared_v6/bars.json',
            'corporate_actions': ROOT / 'outputs/research_mode/prepared_v6/actions.json'}


# Version 2 evidence: which admitted inputs are the benchmark, the calendar and the data (core.backtest.run_store.evidence_status).
PINNED_INPUTS = {'benchmark': 'tri_source', 'calendar': 'tri_calendar',
                 'data': ['bars', 'financial_statements', 'monthly_revenue', 'publication_dates', 'corporate_actions']}


def check_injectable(strategy_id, overrides):
    """True when every override reaches the strategy through runtime_overrides (no source edit); otherwise the reason."""
    if strategy_id in BLOCKED:
        return f'DATA_BLOCKED: {BLOCKED[strategy_id]}'
    if strategy_id not in RUNNABLE:
        return f'UNKNOWN_STRATEGY: {strategy_id}'
    if not overrides:
        return True
    from core.strategy.adapters.legacy import _load_factory
    from core.strategy_manager import StrategyManager

    with patch('core.db_helper.get_setting', lambda key, default=None: default):  # no DB read
        strategy = _load_factory(StrategyManager.CANONICAL_REGISTRY[strategy_id])()
        strategy.set_runtime_overrides(dict(overrides))
        for key, value in overrides.items():
            if not key.endswith('MAX_HOLD_DAYS'):
                return f'UNSUPPORTED_OVERRIDE_KEY: {key} (only the pre-registered maximum holding days)'
            if strategy.max_hold_days != int(value):
                return (f'NOT_INJECTABLE: {type(strategy).__name__}.max_hold_days does not read {key} (stays {strategy.max_hold_days}); '
                        'runtime_overrides cannot change it without editing the strategy source')
    return True


def load_inputs(start, end):
    """Default builder: the native7 inputs. Window filters are applied afterwards by _prepare, for injected builders too."""
    from core.research import corporate_actions
    from core.research.native_frame import load_publication_dates

    paths = input_paths()
    return {'actions': corporate_actions.load(), 'snapshot': n7.build_snapshot(start, end), 'financials': pd.read_parquet(paths['financial_statements']),
            'revenue': pd.read_parquet(paths['monthly_revenue']), 'publications': load_publication_dates(), 'settings': n7.read(paths['user_settings']),
            'benchmark': pd.read_csv(paths['benchmarks'], parse_dates=['date']).set_index('date').taiex_price,
            'delistings': pd.read_csv(paths['delistings'], dtype={'stock_id': str}, encoding='utf-8-sig')}


def report_availability(financials, publications):
    """Date a filing becomes public: first MOPS publication when known, else the statutory deadline (native_frame.prepare_fundamentals)."""
    from core.research.native_frame import QUARTER_DEADLINES, _due

    deadline = financials.quarter.map(QUARTER_DEADLINES)
    due = _due(financials.year + deadline.str[2], deadline.str[0], deadline.str[1]).set_axis(financials.index)
    if publications is None:
        return due
    keys = financials[['stock_id', 'year', 'quarter']].astype({'stock_id': str, 'year': int, 'quarter': int})
    published = keys.merge(publications, on=['stock_id', 'year', 'quarter'], how='left').publication_date.set_axis(financials.index)
    return published.fillna(due)


def _digest(value):
    if value is None:
        return 'none'
    if isinstance(value, (pd.DataFrame, pd.Series)):
        head = json.dumps([str(c) for c in (value.columns if isinstance(value, pd.DataFrame) else [value.name])]).encode()
        return hashlib.sha256(head + pd.util.hash_pandas_object(value, index=isinstance(value, pd.Series)).to_numpy().tobytes()).hexdigest()
    return hashlib.sha256(json.dumps(value, sort_keys=True, default=str).encode()).hexdigest()


def _prepare(inputs, start, end):
    from core.research.native_frame import NativeResearchData, _due

    last = pd.Timestamp(end)
    first = max(pd.Timestamp(start) - pd.Timedelta(days=n7.LOOKBACK_DAYS), pd.Timestamp(WARMUP_FLOOR))
    snap = inputs['snapshot']
    snap = snap[(snap.trade_date >= first) & (snap.trade_date <= last)].reset_index(drop=True)
    fin = inputs['financials']
    fin = fin[fin.year >= 1911]
    fin = fin[report_availability(fin, inputs['publications']) <= last].reset_index(drop=True)  # nothing published after end
    rev = inputs['revenue']
    rev = rev[(_due(rev.year + (rev.month == 12), rev.month % 12 + 1, 10) <= last).to_numpy()].reset_index(drop=True)
    data = NativeResearchData(snap, fin, rev, publications=inputs['publications'])
    # ponytail: carry each filing's availability date, period and MOPS date into the frame rows so the PIT check reads the report actually
    # used. `keys` repeats prepare_fundamentals' own left merge, so its index labels are the ones _fin kept.
    keys = fin[['stock_id', 'year', 'quarter']].assign(stock_id=fin.stock_id.astype(str))
    pubs = inputs['publications']
    keys = keys.merge(pubs, on=['stock_id', 'year', 'quarter'], how='left') if pubs is not None else keys.assign(publication_date=pd.NaT)
    data._fin = data._fin.assign(fundamental_due=data._fin['due'],
                                 fundamental_period=(keys.year.astype(int).astype(str) + 'Q' + keys.quarter.astype(int).astype(str)).loc[data._fin.index],
                                 fundamental_publication_date=keys.publication_date.loc[data._fin.index])
    bench = inputs.get('benchmark')
    digests = {k: _digest(v) for k, v in (('snapshot', snap), ('financials', fin), ('revenue', rev), ('publications', inputs['publications']),
                                          ('settings', inputs['settings']), ('benchmark', bench), ('delistings', inputs.get('delistings')))}
    sessions = [d.strftime('%Y-%m-%d') for d in sorted(snap.trade_date.unique()) if start <= d.strftime('%Y-%m-%d') <= end]
    return SimpleNamespace(data=data, settings=inputs['settings'], sessions=sessions, start=start, end=end, digests=digests,
                           bench=None if bench is None else bench[(bench.index >= pd.Timestamp(start)) & (bench.index <= last)],
                           delistings=inputs.get('delistings'), actions=inputs.get('actions'))


@functools.lru_cache(maxsize=1)
def _default_prepared(start, end):
    # ponytail: one provider per window per process; its cross-section cache is what makes the 2nd..nth run take minutes, not hours
    return _prepare(load_inputs(start, end), start, end)


def pit_check(rows, signal_date):
    """Per candidate row: the filing used must be public strictly before the signal date (date only, so strict <).

    checks: rows that used a filing; violations: availability date >= t (or missing); unverified: dated only by the statutory deadline.
    """
    t = pd.Timestamp(signal_date)
    if 'fundamental_availability_source' not in rows or 'fundamental_due' not in rows:
        return {'checks': len(rows), 'violations': 0, 'unverified': len(rows)}
    source = rows['fundamental_availability_source']
    used = source.notna()
    early = pd.to_datetime(rows['fundamental_due']) < t
    return {'checks': int(used.sum()), 'violations': int((used & ~early).sum()), 'unverified': int((used & early & source.ne('mops_publication')).sum())}


PIT_DETAIL_COLUMNS = ('ticker', 'signal_date', 'financial_period', 'statement_version', 'publication_date', 'source', 'source_hash',
                      'pit_result', 'pit_failure_reason')


def pit_detail(rows, signal_date, hashes):
    """One evidence row per row pit_check counts (same rules); publication_date only from MOPS, never a deadline or period end."""
    t = pd.Timestamp(signal_date)
    tickers = rows['stock_id'].astype(str) if 'stock_id' in rows else rows.index.astype(str)
    if 'fundamental_availability_source' not in rows or 'fundamental_due' not in rows:
        return [dict.fromkeys(PIT_DETAIL_COLUMNS) | {'ticker': sid, 'signal_date': signal_date, 'pit_result': 'PIT_UNVERIFIED',
                                                      'pit_failure_reason': 'AVAILABILITY_COLUMNS_MISSING'} for sid in tickers]
    out = []
    for sid, row in zip(tickers, rows.to_dict('records')):
        source = row['fundamental_availability_source']
        if not isinstance(source, str):
            continue  # no filing used: not a PIT check (pit_check counts only used rows)
        mops = source == 'mops_publication'
        early = pd.Timestamp(row['fundamental_due']) < t if pd.notna(row['fundamental_due']) else False
        published = row.get('fundamental_publication_date')
        out.append({'ticker': sid, 'signal_date': signal_date, 'financial_period': row.get('fundamental_period'),
                    'statement_version': (hashes.get('financials') or '')[:12] or None,
                    'publication_date': pd.Timestamp(published).strftime('%Y-%m-%d') if mops and pd.notna(published) else None,
                    'source': source, 'source_hash': hashes.get('publications' if mops else 'financials'),
                    'pit_result': 'PIT_VIOLATION' if not early else 'PIT_VERIFIED' if mops else 'PIT_UNVERIFIED',
                    'pit_failure_reason': 'PUBLISHED_ON_OR_AFTER_SIGNAL_DATE' if not early else None if mops else 'DEADLINE_DATED_NOT_PUBLICATION'})
    return out


def pit_totals(detail):
    return {'checks': len(detail), 'violations': sum(r['pit_result'] == 'PIT_VIOLATION' for r in detail),
            'unverified': sum(r['pit_result'] == 'PIT_UNVERIFIED' for r in detail)}


def pit_status(totals):
    if totals['violations']:
        return 'PIT_VIOLATION'
    return 'PIT_UNVERIFIED' if totals['unverified'] or not totals['checks'] else 'PIT_VERIFIED'


def chip_distribution(values):
    s = pd.to_numeric(pd.Series(values, dtype='float64'), errors='coerce').dropna()
    return {'n': len(s), 'distinct': int(s.nunique()), 'zero_share': float((s == 0).mean()) if len(s) else None,
            'min': float(s.min()) if len(s) else None, 'median': float(s.median()) if len(s) else None, 'max': float(s.max()) if len(s) else None}


def chip_degeneracy(per_date):
    """DEGENERATE_FACTOR when every signal date's cross-section is a single value, or >= 95% of all values are zero (none: degenerate)."""
    rows = [d for d in per_date if d['n']]
    n = sum(d['n'] for d in rows)
    if not n:
        return True, 'NO_CHIP_SCORE_VALUES'
    zeros = sum(d['zero_share'] * d['n'] for d in rows) / n
    if all(d['distinct'] <= 1 for d in rows):
        return True, 'SAME_VALUE_ON_EVERY_SIGNAL_DATE'
    if zeros >= DEGENERATE_ZERO_SHARE:
        return True, f'ZERO_SHARE_{zeros:.4f}'
    return False, None


class _CostLog:
    """Engine cost model that also totals what was charged (fees and tax), so each trade can carry its own cost."""

    def __init__(self, model):
        self.model, self.paid = model, 0.0

    def buy_cost(self, notional):
        cost = self.model.buy_cost(notional)
        self.paid += cost
        return cost

    def sell_cost(self, notional):
        cost = self.model.sell_cost(notional)
        self.paid += cost
        return cost


class _Observer:
    def __init__(self, chip, hashes=None):
        self.chip, self.pit, self.hashes = chip, False, hashes or {}
        self.picks, self.chip_dates, self.pit_rows = [], [], []
        self.pit_totals = {'checks': 0, 'violations': 0, 'unverified': 0}

    def selected(self, day, frame, candidates):
        if self.chip:
            self.chip_dates.append({'date': day, **chip_distribution(frame['chip_score'] if 'chip_score' in frame else [])})
        if self.pit and candidates is not None and len(candidates):
            for k, v in pit_check(candidates, day).items():
                self.pit_totals[k] += v
            self.pit_rows += pit_detail(candidates, day, self.hashes)

    def pick(self, day, ids):
        self.picks += [[day, str(sid), rank] for rank, sid in enumerate(ids, 1)]
        return ids


def _inject(engine, overrides):
    """runtime_overrides on the exit path (engine.strategy_obj) and on the selection path (platform adapter settings)."""
    merged = {**engine._strategy_runtime_overrides, **overrides}
    engine.strategy_obj.set_runtime_overrides(merged)
    engine._strategy_runtime_overrides = merged
    engine.max_hold_days = engine.strategy_obj.max_hold_days
    for key, value in overrides.items():
        if engine.strategy_obj.max_hold_days != int(value):
            raise ValueError(f'NOT_INJECTABLE: {key}')


def _record_trades(engine, costs):
    opened, buy, sell = {}, engine.buy, engine.sell

    def recorded_buy(sid, price, day):
        before = costs.paid
        buy(sid, price, day)
        if sid in engine.positions and sid not in opened:
            opened[sid] = {'shares': engine.positions[sid]['shares'], 'cost': costs.paid - before}

    def recorded_sell(sid, price, day, reason):
        count, before = len(engine.trades), costs.paid
        sell(sid, price, day, reason)
        if sid in opened:
            opened[sid]['cost'] += costs.paid - before
        if len(engine.trades) > count:
            engine.trades[-1].update(opened.pop(sid))

    engine.buy, engine.sell = recorded_buy, recorded_sell


CAPTURE = True  # read-only evidence wrappers (design D5); tests switch them off to prove they change nothing


def _record_positions(engine, rows):
    """After each session's marks and NAV, before the close signals: one row per held stock plus a CASH row (sum = that day's NAV)."""
    marks, mark, close = {}, engine._mark_position, engine.process_close_signals

    def recorded_mark(sid, info, date_str):
        value = mark(sid, info, date_str)
        marks[sid] = (info['shares'], value)
        return value

    def recorded_close(date_str, trend):
        rows.extend({'date': date_str, 'stock_id': sid, 'shares': shares, 'value': value}
                    for sid, (shares, value) in sorted(marks.items()) if sid in engine.positions)
        rows.append({'date': date_str, 'stock_id': 'CASH', 'shares': None, 'value': engine.capital})
        marks.clear()
        return close(date_str, trend)

    engine._mark_position, engine.process_close_signals = recorded_mark, recorded_close


def _evidence(engine, positions, costs, sessions):
    """Open positions at the end and cost/turnover totals that include them (closed-trade figures alone omit open entries)."""
    last = {r['stock_id']: r['value'] for r in positions if positions and r['date'] == positions[-1]['date']}
    open_rows = [{'stock_id': sid, 'shares': info['shares'], 'entry_price': info.get('cost'), 'last_mark_value': last.get(sid)}
                 for sid, info in sorted(engine.positions.items())]
    traded = sum((t['buy_price'] + t['sell_price']) * t['shares'] for t in engine.trades) + sum(r['shares'] * (r['entry_price'] or 0) for r in open_rows)
    nav = pd.Series(engine.daily_assets, dtype='float64')
    years = len(sessions) / 252
    return open_rows, {'fees_and_tax_paid': costs.paid, 'includes_open_positions': True, 'traded_value': traded,
                       'turnover_annual': traded / nav.mean() / years if len(nav) and nav.mean() > 0 and years else None,
                       'slippage': 'applied inside fill prices (seeded); not separable from the fill price'}


def _run_track(strategy_id, overrides, prep, multiple):
    """One engine run with native7's settings; costs and slippage cap scaled by `multiple`, same slippage seed."""
    import core.backtest.runner as runner
    from core.backtest.costs import CostModel
    from core.research import corporate_actions
    from scripts.native_recovery import isolated

    costs = _costs(multiple)
    observer = _Observer(strategy_id == CHIP_STRATEGY, prep.digests)
    positions = []
    select = runner.select_backtest_candidates

    def observed_select(strategy, platform, name, frame, asof_date, runtime_overrides=None):
        candidates = select(strategy, platform, name, frame, asof_date, runtime_overrides)
        observer.selected(asof_date, frame, candidates)
        return candidates

    engine_error = None
    with isolated(prep.data, prep.settings), open(os.devnull, 'w', encoding='utf-8') as sink, contextlib.redirect_stdout(sink), \
            patch.object(runner, 'select_backtest_candidates', observed_select), patch.object(runner.Config, 'SLIPPAGE_MAX_PIPS', costs['slippage_max']):
        engine = runner.BacktestEngine(mode=strategy_id, start_date=prep.start, end_date=prep.end, initial_capital=INITIAL_CAPITAL, persist_results=False,
                                       persist_to_db=False, execution_contract=EXECUTION_CONTRACT, trading_sessions=prep.sessions,
                                       research_data=prep.data, rules_only=True, valuation_policy='finite_positive_stale')
        if overrides:
            _inject(engine, overrides)
        engine.costs = _CostLog(CostModel(costs['fee_rate'], costs['tax_rate'], costs['minimum_fee']))
        _record_trades(engine, engine.costs)
        if CAPTURE:
            _record_positions(engine, positions)
        observer.pit = bool(FUNDAMENTAL_COLUMNS & set(engine.required_inputs))
        find = engine.find_candidates
        engine.find_candidates = lambda day: observer.pick(day, find(day))
        random.seed(n7.SLIPPAGE_SEED)
        try:
            engine.run()
        except AttributeError as error:  # calculate_metrics after the session loop raises on a non-finite curve (native7_rules_only.run)
            if 'numerator' not in str(error) or len(engine.daily_assets) != len(prep.sessions):
                raise
            engine_error = f'{type(error).__name__}: {error}'
    trades = [{k: t.get(k) for k in ('stock_id', 'buy_date', 'buy_price', 'sell_date', 'sell_price', 'shares', 'cost', 'profit_pct', 'reason')}
              for t in engine.trades]
    track = {'nav': [[d, float(v)] for d, v in zip(engine.daily_dates, engine.daily_assets)], 'trades': trades, 'engine_error': engine_error, 'costs': costs,
             'valuation_policy': engine.valuation_policy, 'valuation_events': list(engine.valuation_events),
             'valuation': corporate_actions.verdict(engine.valuation_events, getattr(prep, 'actions', None))}
    if CAPTURE:
        track['positions_daily'] = positions
        track['open_positions'], track['cost_summary'] = _evidence(engine, positions, engine.costs, prep.sessions)
    return track, engine, observer


def _delisted(prep, held):
    method = 'delistings.csv rows with last_traded inside [start, end]'
    if prep.delistings is None:
        return {'count': None, 'method': method, 'reason': 'DELISTINGS_UNAVAILABLE'}
    gone = prep.delistings
    inside = gone[(pd.to_datetime(gone.last_traded) >= pd.Timestamp(prep.start)) & (pd.to_datetime(gone.last_traded) <= pd.Timestamp(prep.end))]
    return {'count': len(inside), 'method': method, 'traded_by_strategy': len(set(inside.stock_id.astype(str)) & held)}


def run_legacy(strategy_id, overrides=None, *, start=START, end=END, snapshot_builder=None):
    """Base and 2x cost tracks for one legacy strategy. snapshot_builder(start, end) -> load_inputs-shaped dict (tests inject one)."""
    check_window(start, end)  # D13: before any input is read
    if strategy_id in BLOCKED:
        raise ValueError(f'DATA_BLOCKED {strategy_id}: {BLOCKED[strategy_id]}')
    if strategy_id not in RUNNABLE:
        raise ValueError(f'UNKNOWN_STRATEGY: {strategy_id}')
    overrides = dict(overrides or {})
    if overrides and (reason := check_injectable(strategy_id, overrides)) is not True:
        raise ValueError(reason)
    prep = _default_prepared(start, end) if snapshot_builder is None else _prepare(snapshot_builder(start, end), start, end)
    if not prep.sessions:
        raise ValueError('NO_SESSIONS_IN_WINDOW')
    base, engine, observer = _run_track(strategy_id, overrides, prep, 1)
    stress, _, _ = _run_track(strategy_id, overrides, prep, STRESS_MULTIPLE)
    events = engine.execution_events
    unfilled = [e for e in events if e.get('status') != 'FILLED']
    shares = [e['same_day_amount_share'] for e in events if e.get('same_day_amount_share') is not None]
    chip = None
    if observer.chip:
        degenerate, reason = chip_degeneracy(observer.chip_dates)
        chip = {'per_date': observer.chip_dates, 'pooled_n': sum(d['n'] for d in observer.chip_dates), 'degenerate': degenerate, 'reason': reason}
    held = {t['stock_id'] for t in base['trades']} | set(engine.positions)
    checks = {'trade_count': len(base['trades']), 'delisted_in_window': _delisted(prep, held),
              'pit_status': pit_status(observer.pit_totals) if observer.pit else 'NOT_APPLICABLE', 'pit_violations': observer.pit_totals['violations'],
              'pit_checks': observer.pit_totals['checks'], 'pit_unverified': observer.pit_totals['unverified'],
              'lookahead': 'signal t, fill t+1', 'lookahead_violations': sum(e['execution_date'] <= e['signal_date'] for e in events),
              'unfillable_orders': {'total': sum(e.get('status') == 'UNFILLED' for e in events),
                                    'by_reason': dict(Counter(f"{e.get('status')}:{e.get('reason')}" for e in unfilled))},
              'max_volume_share': max(shares) if shares else None, 'chip_score': chip, 'degenerate': bool(chip and chip['degenerate']),
              'max_hold_days': engine.max_hold_days, 'required_inputs': list(engine.required_inputs), 'fill_rule': engine.fill_rule}
    bench = prep.bench
    return {'strategy_id': strategy_id, 'overrides': dict(sorted(overrides.items())), 'config': config_for(strategy_id, overrides) | {'start': start, 'end': end},
            'tracks': {'base': base, 'stress': stress}, 'picks': observer.picks, 'checks': checks, 'pit_detail': observer.pit_rows,
            'data_version': source_hash(prep.digests), 'input_digests': prep.digests,
            'benchmark': None if bench is None or bench.empty else [[d.strftime('%Y-%m-%d'), float(v)] for d, v in bench.items()],
            'benchmark_note': ('TAIEX_PRICE_SOURCE_UNAVAILABLE' if bench is None or bench.empty else 'TAIEX price index (price return)')
                              + '; total-return index: TOTAL_RETURN_INDEX_UNAVAILABLE'}
