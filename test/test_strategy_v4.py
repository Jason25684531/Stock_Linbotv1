"""Ranking counterexamples and original characterization; synthetic, never return evidence."""
import numpy as np
import pandas as pd
import pytest

from core.strategies import _tquant
from core.strategies.mad_distance import MadDistanceStrategy
from core.strategies.volatility_adjusted_momentum import VolatilityAdjustedMomentumStrategy


def history(kind):
    rows = []
    days = pd.bdate_range('2024-01-02', periods=200 if kind == 'mad_distance' else 30)
    for i in range(60 if kind == 'mad_distance' else 12):
        x = np.arange(len(days))
        if kind == 'mad_distance':
            close = np.full(len(days), 100.) if i < 54 else 100. + x * (.2 + (i - 54) * .1)
        else:
            close = 100. * np.exp((.001 + i * .0001) * x + .006 * np.sin(x * (1 + i * .03)))
        for j, day in enumerate(days):
            amplitude = 4. if j < len(days) - 5 else 1.
            rows.append({'stock_id': str(1000 + i), 'trade_date': day, 'close_price': close[j],
                         'high_price': close[j] + amplitude, 'low_price': close[j] - amplitude, 'volume': 10000.})
    return pd.DataFrame(rows)


@pytest.mark.parametrize('cls', [MadDistanceStrategy, VolatilityAdjustedMomentumStrategy])
def test_original_characterization_loses_rank(cls, monkeypatch):
    obj = cls(); h = history(obj.name)
    monkeypatch.setattr(_tquant, 'load_history', lambda day: h)
    frame = h[h.trade_date.eq(h.trade_date.max())].copy()
    selected = obj.select_history(h)
    filtered = obj.filter_candidates(frame).stock_id.tolist()
    assert filtered == sorted(selected)
    if obj.name == 'mad_distance':
        assert selected == ['1054', '1055', '1056', '1057', '1058', '1059']
    else:
        assert selected != filtered


@pytest.mark.parametrize('cls', [MadDistanceStrategy, VolatilityAdjustedMomentumStrategy])
def test_v4_rank_survives_filter_and_top_three(cls, monkeypatch):
    from scripts.strategy_v4_ranking import fixed_selection
    obj = cls(); h = history(obj.name)
    monkeypatch.setattr(_tquant, 'load_history', lambda day: h)
    frame = h[h.trade_date.eq(h.trade_date.max())].copy()
    old = obj.filter_candidates(frame).stock_id.tolist()
    with fixed_selection(obj.name):
        expected = obj.select_history(h)
        result = obj.filter_candidates(frame).stock_id.tolist()
        assert result == expected
        duplicate_index = frame.copy()
        duplicate_index.index = [0] * len(frame)
        assert obj.filter_candidates(duplicate_index).stock_id.tolist() == expected
        assert result[:3] != old[:3]
        if obj.name == 'mad_distance':
            assert result[:3] == ['1059', '1058', '1057']
    assert obj.filter_candidates(frame).stock_id.tolist() == old


@pytest.mark.parametrize('cls', [MadDistanceStrategy, VolatilityAdjustedMomentumStrategy])
def test_v4_future_missing_sparse_duplicate_and_tie(cls, monkeypatch):
    from scripts.strategy_v4_ranking import fixed_selection
    obj = cls(); h = history(obj.name)
    frame = h[h.trade_date.eq(h.trade_date.max())].copy()
    monkeypatch.setattr(_tquant, 'load_history', lambda day: h)
    with fixed_selection(obj.name):
        expected = obj.filter_candidates(frame).stock_id.tolist()
        future = h.iloc[-1:].copy(); future.trade_date += pd.Timedelta(days=30); future.close_price *= 100
        monkeypatch.setattr(_tquant, 'load_history', lambda day: pd.concat([h, future]))
        assert obj.filter_candidates(frame).stock_id.tolist() == expected
        assert obj.filter_candidates(frame[frame.stock_id.isin(expected[:2])]).stock_id.tolist() == expected[:2]
        with pytest.raises(ValueError, match='DUPLICATE'):
            obj.filter_candidates(pd.concat([frame, frame.iloc[:1]]))
        broken = h.copy(); broken.loc[broken.stock_id.eq(expected[0]), 'close_price'] = np.nan
        assert expected[0] not in obj.select_history(broken)
        tied = h.copy()
        for sid in expected:
            tied.loc[tied.stock_id.eq(sid), ['close_price', 'high_price', 'low_price', 'volume']] = h.loc[h.stock_id.eq(expected[0]), ['close_price', 'high_price', 'low_price', 'volume']].to_numpy()
        assert obj.select_history(tied) == sorted(obj.select_history(tied))


def test_v4_actual_engine_orders_and_fills(monkeypatch):
    from core.backtest import runner
    from scripts.strategy_v4_ranking import fixed_selection
    from test_platform_v2_engine import engine, order
    h = history('volatility_adjusted_momentum'); obj = VolatilityAdjustedMomentumStrategy()
    monkeypatch.setattr(_tquant, 'load_history', lambda day: h)
    e = engine(); e.strategy_obj = obj; e.model = None; e.rules_only = True
    e._load_legacy_frame = lambda day: h[h.trade_date.eq(h.trade_date.max())].copy()
    e.strategy_runner = None; e._selection_strategy_name = obj.name
    monkeypatch.setattr(runner, 'select_backtest_candidates', lambda strategy, platform, name, frame, day, overrides: strategy.filter_candidates(frame))
    with fixed_selection(obj.name):
        ranked = e.find_candidates('2024-02-12')
        assert ranked == obj.select_history(h)[:3]
        for sid in ranked:
            order(e, 'BUY', sid=sid)
        e.execute_pending_open('2026-01-06')
        assert [r['stock_id'] for r in e.fill_rows()] == ranked
        assert all(r['signal_date'] < r['date'] for r in e.fill_rows())
