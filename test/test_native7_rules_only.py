"""Native7 rules-only metrics: equity_performance must equal the engine formulas when the curve is finite and be undefined (never 0) otherwise."""
import math
from statistics import pstdev

import numpy as np
import pandas as pd
import pytest

from scripts import native7_rules_only as n7
from scripts import track_b_stats as st

DAYS = pd.bdate_range('2024-01-02', periods=60)
BENCH = pd.Series(100 * np.cumprod(1 + np.random.default_rng(3).normal(0, 0.01, 60)), index=DAYS)


def curve(seed=1, n=60):
    return pd.Series(1_000_000 * np.cumprod(1 + np.random.default_rng(seed).normal(0.0007, 0.01, n)), index=DAYS[:n])


def independent(equity):
    """Written from the definitions, not from the engine code."""
    v = equity.to_numpy()
    r = v[1:] / v[:-1] - 1
    peak = np.maximum.accumulate(v)
    return {'total_return': v[-1] / v[0] - 1, 'cagr': (v[-1] / v[0]) ** (252 / len(r)) - 1, 'sharpe': r.mean() * 252 / (r.std(ddof=0) * math.sqrt(252)),
            'annualized_volatility': r.std(ddof=0) * math.sqrt(252), 'max_drawdown': (v / peak - 1).min()}


def test_finite_curve_matches_independent_formulas():
    equity = curve()
    got = n7.equity_performance(equity, [], BENCH)
    assert got['metrics_status'] == 'COMPUTED'
    for name, expected in independent(equity).items():
        assert got['metrics'][name]['value'] == pytest.approx(expected, rel=1e-9, abs=1e-12), name


def test_finite_curve_matches_engine_and_track_b_stats():
    from core.backtest.metrics import calculate_metrics

    equity = curve(seed=2)
    trades = [{'profit_pct': 3.0}, {'profit_pct': -1.0}, {'profit_pct': 2.0}, {'profit_pct': -4.0}]
    got = n7.equity_performance(equity, trades, BENCH)['metrics']
    engine = calculate_metrics(list(equity), trades)
    for name in n7.METRICS:
        assert got[name]['value'] == engine.get(name).value
    r = equity.pct_change().dropna()
    assert got['win_rate']['value'] == 0.5
    assert got['max_drawdown']['value'] == pytest.approx(st.max_drawdown(r), rel=1e-9, abs=1e-12)
    assert got['cagr']['value'] == pytest.approx(st.cagr(r), rel=1e-9, abs=1e-12)
    # track_b_stats uses the sample std (ddof=1), the engine the population std: exact ratio sqrt((n-1)/n)
    assert st.sharpe(r) == pytest.approx(got['sharpe']['value'] * math.sqrt((len(r) - 1) / len(r)), rel=1e-9)


@pytest.mark.parametrize('bad', [float('nan'), float('inf'), -float('inf')])
def test_non_finite_equity_is_undefined_not_zero(bad):
    equity = curve()
    equity.iloc[10] = bad
    got = n7.equity_performance(equity, [], BENCH)
    assert got['metrics_status'] == 'UNDEFINED' and got['non_finite_sessions'] == 1
    for name, metric in got['metrics'].items():
        assert metric['value'] is None and 'NON_FINITE_EQUITY' in metric['reason'] and str(DAYS[10].date()) in metric['reason'], name


def test_engine_crash_root_cause_is_non_finite_returns():
    """statistics.pstdev fails with AttributeError on NaN/inf returns: the production crash, reproduced without touching production code."""
    from core.backtest.metrics import calculate_metrics

    equity = list(curve())
    equity[10] = float('nan')
    with pytest.raises(AttributeError, match='numerator'):
        calculate_metrics(equity, [])
    with pytest.raises(AttributeError, match='numerator'):
        pstdev([0.01, float('inf'), 0.02])


@pytest.mark.parametrize('n', [0, 1])
def test_too_short_curve_is_undefined(n):
    got = n7.equity_performance(curve(n=n) if n else pd.Series([], dtype=float, index=pd.DatetimeIndex([])), [], BENCH)
    assert got['metrics_status'] == 'UNDEFINED'
    assert all(m['value'] is None and m['reason'] for m in got['metrics'].values())


def test_zero_volatility_has_no_sharpe_and_no_trades_has_no_win_rate():
    flat = pd.Series(1_000_000.0, index=DAYS)
    got = n7.equity_performance(flat, [], BENCH)['metrics']
    assert got['sharpe']['value'] is None and got['sharpe']['reason']
    assert got['win_rate']['value'] is None and got['win_rate']['reason']
    assert got['total_return']['value'] == 0.0 and got['max_drawdown']['value'] == 0.0  # genuinely zero, not a failed calculation


def test_clean_turns_non_finite_into_none():
    assert n7.clean({'a': float('nan'), 'b': [float('inf'), 1.5], 'c': np.float64('-inf')}) == {'a': None, 'b': [None, 1.5], 'c': None}


class FakeEngine:
    def __init__(self, mode, **kw):
        if mode == 'second':
            raise RuntimeError('boom')
        self.daily_assets, self.daily_dates = list(curve()), [d.strftime('%Y-%m-%d') for d in DAYS]
        self.trades, self.execution_events, self.required_inputs, self.data_exclusions = [], [{'status': 'FILLED'}], [], []
        self.mode = mode
        if mode == 'nan':
            self.daily_assets[7] = float('nan')

    def run(self):  # the real engine ends with calculate_metrics, which raises on a non-finite curve
        if self.mode == 'nan':
            raise AttributeError("'float' object has no attribute 'numerator'")
        if self.mode == 'other':
            raise AttributeError('something else')


@pytest.fixture
def stubbed_run(monkeypatch):
    import core.backtest.runner as runner
    import core.research.native_frame as native_frame
    import scripts.native_recovery as recovery

    saved = []
    snap = pd.DataFrame({'trade_date': DAYS})
    monkeypatch.setattr(n7, 'build_snapshot', lambda s, e: snap)
    monkeypatch.setattr(n7, 'read', lambda p: {})
    monkeypatch.setattr(n7, 'provenance', lambda tag: {'run_id': f'{tag}-test'})
    monkeypatch.setattr(n7, 'write', lambda path, payload: saved.append((path.name, payload)))
    monkeypatch.setattr(pd, 'read_parquet', lambda p: pd.DataFrame({'year': [2024]}))
    monkeypatch.setattr(pd, 'read_csv', lambda *a, **k: pd.DataFrame({'date': DAYS, 'taiex_price': BENCH.to_numpy()}))
    monkeypatch.setattr(native_frame, 'NativeResearchData', lambda *a, **k: object())
    monkeypatch.setattr(native_frame, 'load_publication_dates', lambda: {})
    monkeypatch.setattr(recovery, 'isolated', lambda data, settings: __import__('contextlib').nullcontext())
    monkeypatch.setattr('pathlib.Path.open', lambda self, *a, **k: __import__('io').StringIO(), raising=False)
    return saved, runner, monkeypatch


def test_engine_crash_on_non_finite_equity_becomes_blocked_not_zero(stubbed_run):
    saved, runner, monkeypatch = stubbed_run
    monkeypatch.setattr(runner, 'BacktestEngine', FakeEngine)
    n7.run(str(DAYS[0].date()), str(DAYS[-1].date()), ['nan', 'ok'], 'unit')
    strategies = saved[-1][1]['strategies']
    assert strategies['nan']['BACKTEST'] == 'BLOCKED' and strategies['nan']['metrics_status'] == 'UNDEFINED'
    assert 'numerator' in strategies['nan']['engine_run_error'] and str(DAYS[7].date()) in strategies['nan']['metrics']['sharpe']['reason']
    assert all(m['value'] is None for m in strategies['nan']['metrics'].values())
    assert strategies['ok']['BACKTEST'] == 'DIAGNOSTIC' and strategies['ok']['engine_run_error'] is None


def test_unrelated_engine_error_is_not_swallowed(stubbed_run):
    saved, runner, monkeypatch = stubbed_run
    monkeypatch.setattr(runner, 'BacktestEngine', FakeEngine)
    with pytest.raises(AttributeError, match='something else'):
        n7.run(str(DAYS[0].date()), str(DAYS[-1].date()), ['other'], 'unit')


def test_finished_strategies_are_saved_before_a_later_failure(stubbed_run):
    saved, runner, monkeypatch = stubbed_run
    monkeypatch.setattr(runner, 'BacktestEngine', FakeEngine)
    with pytest.raises(RuntimeError, match='boom'):
        n7.run(str(DAYS[0].date()), str(DAYS[-1].date()), ['first', 'second'], 'unit')
    assert saved and saved[-1][0] == 'native7_rules_only_unit.json'
    assert list(saved[-1][1]['strategies']) == ['first'] and saved[-1][1]['strategies']['first']['metrics_status'] == 'COMPUTED'


def test_merge_adopts_one_result_per_strategy_and_flags_conflicts(tmp_path, monkeypatch):
    import json

    from scripts import dual_track_matrix as matrix

    result = {'BACKTEST': 'DIAGNOSTIC', 'metrics': {'sharpe': {'value': 0.5, 'reason': None}}}
    other = {'BACKTEST': 'DIAGNOSTIC', 'metrics': {'sharpe': {'value': 0.7, 'reason': None}}}
    blocked = {'BACKTEST': 'BLOCKED', 'metrics': {'sharpe': {'value': None, 'reason': 'NON_FINITE_EQUITY'}}}
    files = {'native7_rules_only_a.json': {'same': result, 'clash': result, 'bad': blocked}, 'native7_rules_only_b.json': {'same': result, 'clash': other},
             'native7_rules_only_smoke_c.json': {'smoke_only': other}}
    for name, strategies in files.items():
        (tmp_path / name).write_text(json.dumps({'strategies': strategies}), encoding='utf-8')
    (tmp_path / 'backup_native7_a.json').write_text(json.dumps({'strategies': {'same': other}}), encoding='utf-8')  # outside the glob: never counted
    monkeypatch.setattr(matrix, 'RULES_DIR', tmp_path)
    adopted, conflicts = matrix.adopted_rules_results()
    assert adopted['same']['source']['run_id'] is None and 'UNCONFIRMED' in adopted['same']['source']['provenance']  # no run_id recorded: not invented
    adopted = {k: {f: v for f, v in r.items() if f != 'source'} for k, r in adopted.items()}
    assert adopted['same'] == result and 'same' not in conflicts  # identical content in two files counts once
    assert 'clash' in conflicts and 'clash' not in adopted  # contradictory results: neither is adopted
    assert adopted['bad']['metrics']['sharpe']['value'] is None and 'smoke_only' not in adopted


def test_stale_summary_sensitivity_is_exact_and_flags_structural_cases(tmp_path, monkeypatch):
    (tmp_path / 'official_events.csv').write_text('ex_date,stock_id\n2024-01-05,2002\n', encoding='utf-8')
    (tmp_path / 'delistings.csv').write_text('stock_id,last_traded\n3003,2024-01-08\n', encoding='utf-8')
    monkeypatch.setattr(n7, 'OUT', tmp_path)
    equity = curve()
    d = [str(x.date()) for x in DAYS]
    events = [{'date': d[4], 'stock_id': '1001', 'reason': 'NO_VALID_CLOSE_WITH_VOLUME', 'price': 50.0, 'price_source_date': d[3], 'stale_days': 1, 'shares': 1000, 'value': 80_000.0},
              {'date': d[5], 'stock_id': '1001', 'reason': 'NO_BAR_ROW', 'price': 50.0, 'price_source_date': d[3], 'stale_days': 2, 'shares': 1000, 'value': 80_000.0}]
    for e in events:
        e['equity_share'] = e['value'] / equity.loc[e['date']]
    got = n7.stale_summary(events, equity, [], BENCH, d)
    assert got['events'] == 2 and got['longest_carry_sessions'] == 2 and got['flags'] == ['STALE_VALUATION_MATERIAL']  # 80k of ~1M > 5%
    zero = equity.copy()
    zero.loc[DAYS[4]] -= 80_000.0
    zero.loc[DAYS[5]] -= 80_000.0
    assert got['sensitivity']['stale_positions_at_zero']['total_return'] == pytest.approx(n7.equity_performance(zero, [], BENCH)['metrics']['total_return']['value'], rel=1e-12)
    assert got['sensitivity']['carry_last_valid_close'] == {k: n7.equity_performance(equity, [], BENCH)['metrics'][k]['value'] for k in ('total_return', 'sharpe', 'max_drawdown')}
    down = equity.copy()
    down.loc[DAYS[4]] += 80_000.0 * (0.9 - 1)
    down.loc[DAYS[5]] += 80_000.0 * (0.81 - 1)
    assert got['sensitivity']['down_limit_each_stale_session']['max_drawdown'] == pytest.approx(n7.equity_performance(down, [], BENCH)['metrics']['max_drawdown']['value'], rel=1e-12)
    flagged = [dict(e, stock_id='2002', price_source_date=str(DAYS[0].date()), date=d[3], equity_share=0.001),
               dict(e, stock_id='3003', price_source_date=str(DAYS[0].date()), date=d[5], equity_share=0.001)]
    assert set(n7.stale_summary(flagged, equity, [], BENCH, d)['flags']) == {'EX_RIGHTS_DURING_CARRY', 'DELISTING_EXIT_UNVERIFIED'}


def test_zero_volume_day_of_an_active_stock_is_a_probable_suspension_not_a_plain_gap():
    from scripts.od29_valuation import zero_volume_classes

    days = pd.bdate_range('2025-06-02', periods=22)
    bars = pd.concat([pd.DataFrame({'trade_date': days, 'stock_id': '3036', 'volume': [3e6] * 21 + [0.0]}),
                      pd.DataFrame({'trade_date': days, 'stock_id': '9999', 'volume': [0.0, 100.0] * 10 + [0.0, 0.0]})])
    events = pd.DataFrame({'date': [str(days[-1].date())] * 2, 'stock_id': ['3036', '9999'], 'reason': ['NO_VALID_CLOSE_NO_VOLUME'] * 2, 'equity_share': [0.46, 0.01]})
    got = {z['stock_id']: z['class'] for z in zero_volume_classes(events, bars)}
    assert got == {'3036': 'PROBABLE_SUSPENSION', '9999': 'ILLIQUID_NO_TRADE'}
