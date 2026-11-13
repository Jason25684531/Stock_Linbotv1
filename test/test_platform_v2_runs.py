"""V2 run storage, identity and parameter bounds on synthetic results (no engine, no snapshot)."""
import json

import pytest

from scripts import platform_v2 as p2

ROWS = {'s': {'state': 'REUSE', 'reason': None, 'data_eligible': True, 'web_runnable': True, 'backtest_window': ['2016-07-11', '2025-12-31']},
        'blocked': {'state': 'DATA_BLOCKED', 'reason': 'X', 'data_eligible': False, 'backtest_window': None}}
COSTS = {'fee_rate': 0.001425, 'tax_rate': 0.003, 'minimum_fee': 20.0, 'slippage_max': 0.002}


def params(**changes):
    value = dict(strategy_id='s', start='2020-01-02', end='2020-12-31', capital=1_000_000, costs=COSTS, rule='LOT_ENGINE_V1')
    value.update(changes)
    return p2.check_params(**value, rows=ROWS)


@pytest.mark.parametrize('changes, reason', [
    ({'end': '2026-01-05'}, 'WINDOW_OUT_OF_RANGE'), ({'start': '2015-01-05'}, 'WINDOW_OUT_OF_RANGE'),
    ({'start': '2021-01-04'}, 'WINDOW_OUT_OF_RANGE'), ({'capital': 50_000}, 'OUT_OF_BOUNDS'), ({'capital': True}, 'OUT_OF_BOUNDS'),
    ({'costs': {**COSTS, 'fee_rate': -0.1}}, 'OUT_OF_BOUNDS'), ({'costs': {'fee_rate': 0.1}}, 'COSTS_KEYS'),
    ({'rule': 'LOT_MAGIC'}, 'UNKNOWN_RULE'), ({'strategy_id': 'blocked'}, 'NOT_RUNNABLE'), ({'strategy_id': 'zz'}, 'UNKNOWN_STRATEGY'),
    ({'start': 'not a date'}, 'INVALID_DATE'),
])
def test_parameters_out_of_range_are_refused(changes, reason):
    with pytest.raises(ValueError, match=reason):
        params(**changes)


def track(capital, fills=True):
    buy = {'fill_id': 'f', 'date': 'd2', 'side': 'BUY', 'stock_id': '2330', 'shares': 1000, 'price': 100, 'notional': 100000, 'fee': 0, 'tax': 0, 'cash_delta': -100_000.0, 'cash_after': capital - 100000}
    nav = [['2020-01-02', capital], ['2020-01-03', capital]]
    rows = [{'date': '2020-01-02', 'stock_id': 'CASH', 'shares': None, 'value': capital},
            {'date': '2020-01-03', 'stock_id': 'CASH', 'shares': None, 'value': capital}]
    return {'nav': nav, 'fills': [] if fills else [buy | {'date': '2020-01-03'}], 'corporate_action_events': [], 'positions_daily': rows,
            'open_positions': [], 'trades': [], 'cost_summary': {}, 'valuation_events': [], 'execution_events': [], 'accounting': 'LEDGER', 'engine_error': None}


@pytest.fixture
def engine(monkeypatch):
    calls = []

    def execute(strategy_id, snapshot_id, start, end, capital, costs, rule, use_cache=True):
        calls.append(capital)
        return {'tracks': {'base': track(capital), 'stress': track(capital)}, 'ledger_version': 'L', 'family': 'native',
                'feature_cache': {'key': None, 'attached': False, 'verified_dates': [], 'mismatches': []}}
    monkeypatch.setattr('scripts.platform_v2_engine.execute', execute)
    monkeypatch.setattr('scripts.historical_foundation.history_identity', lambda cap, snap, start, end: f'RID1:{snap}:{start}:{end}')
    return calls


def test_identical_identity_is_reused_and_any_change_is_a_new_run(tmp_path, engine):
    p = params()
    p['strategy_id'] = 'mean_reversion'  # a real catalog id for the identity's capability lookup
    first = p2.run_one(p, 'SNAP', root=tmp_path)
    assert p2.run_one(p, 'SNAP', root=tmp_path) == first and engine == [1_000_000.0]
    other = p2.run_one(p | {'capital': 2_000_000.0}, 'SNAP', root=tmp_path)
    assert other != first and engine == [1_000_000.0, 2_000_000.0]
    assert p2.identity(p | {'rule': 'LOT_ODD'}, 'SNAP') != p2.identity(p, 'SNAP') != p2.identity(p, 'SNAP2')
    record = json.loads((tmp_path / first / 'run.json').read_text(encoding='utf-8'))
    assert record['publishable'] is True and record['platform_evidence_version'] == 2
    assert record['comparison_group'].startswith('PLATFORM_V2:') and record['evidence']['run_id'] == first
    assert {'fills.csv', 'daily_ledger.csv', 'positions_daily.csv', 'trades_stress.csv'} <= {f.name for f in (tmp_path / first).iterdir()}


def test_unreconciled_run_is_stored_not_publishable(tmp_path):
    p = params()
    result = {'tracks': {'base': track(1_000_000.0, fills=False), 'stress': track(1_000_000.0)}, 'ledger_version': 'L', 'family': 'native',
              'feature_cache': {'key': None, 'attached': False, 'verified_dates': [], 'mismatches': []}}
    record, frames = p2.build_record(p, 'RID2-x', '20260101T000000Z-00000000', 'SNAP', result)
    assert record['publishable'] is False and record['not_publishable_reasons'][0].startswith('base: CASH')
    result['tracks']['base'] = track(1_000_000.0)
    result['feature_cache']['mismatches'] = ['2020-01-02: differs']
    assert p2.build_record(p, 'RID2-x', '20260101T000000Z-00000000', 'SNAP', result)[0]['not_publishable_reasons'] == ['FEATURE_CACHE_MISMATCH']


def test_compare_reports_differences():
    import pandas as pd
    nav = pd.DataFrame({'date': ['d1'], 'nav_base': [1.0], 'nav_stress': [1.0]})
    trades = pd.DataFrame({'stock_id': ['2330'], 'profit_pct': [1.0]})
    same = {'nav': nav, 'trades': trades, 'trades_stress': trades}
    assert p2.compare(same, same) == []
    moved = {'nav': nav.assign(nav_base=1.0 + 1e-6), 'trades': trades.assign(stock_id='2317'), 'trades_stress': trades.iloc[:0]}
    assert p2.compare(same, moved) == ['NAV_VALUES:nav_base', 'TRADES_VALUES:stock_id', 'TRADES_STRESS_COUNT 1 != 0']


def test_stored_frames_keep_complete_fill_and_trade_fields():
    base = track(1_000_000.0, fills=False)
    base['trades'] = [{'stock_id': '2330', 'buy_date': '2020-01-03', 'sell_date': '2020-01-03', 'shares': 1000}]
    result = {'tracks': {'base': base, 'stress': track(1_000_000.0)}, 'ledger_version': 'L', 'family': 'native',
              'feature_cache': {'key': None, 'attached': False, 'verified_dates': [], 'mismatches': []}}
    _, frames = p2.build_record(params(), 'RID2-x', '20260101T000000Z-00000000', 'SNAP', result)
    assert {'fill_id', 'date', 'side', 'stock_id', 'shares', 'cash_delta'} <= set(frames['fills'].columns)
    assert frames['trades'].iloc[0]['shares'] == 1000


def test_model_version_binds_snapshot_spec_and_implementation():
    first = p2.model_version(20, 'spec', 'snapshot', {'prediction.py': 'v1'})
    assert first != p2.model_version(20, 'spec', 'other', {'prediction.py': 'v1'})
    assert first != p2.model_version(20, 'spec', 'snapshot', {'prediction.py': 'v2'})
    assert first != p2.model_version(20, 'other', 'snapshot', {'prediction.py': 'v1'})
