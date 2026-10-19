import numpy as np
import pandas as pd
import pytest
from core.strategies import _tquant
from test_tquant_strategies import history_frame

@pytest.mark.parametrize('module,class_name,close', [
    ('kd_oversold', 'KdOversoldStrategy', [100] * 9 + [80]),
    ('bias_reversion', 'BiasReversionStrategy', np.linspace(110, 90, 8)),
    ('bollinger_reversion', 'BollingerReversionStrategy', [100] * 19 + [80]),
    ('volume_surge', 'VolumeSurgeStrategy', [100] * 4),
    ('aroon_trend', 'AroonTrendStrategy', np.arange(100, 125)),
    ('trend_following', 'TrendFollowingStrategy', np.linspace(100, 200, 81)),
    ('contrarian_pullback', 'ContrarianPullbackStrategy', [*np.linspace(100, 200, 20), 180]),
    ('long_term_trend', 'LongTermTrendStrategy', np.linspace(100, 200, 251)),
    ('regression_momentum', 'RegressionMomentumStrategy', np.linspace(100, 200, 252)),
    ('volatility_adjusted_momentum', 'VolatilityAdjustedMomentumStrategy', np.linspace(100, 200, 30)),
])
def test_technical_rule_fixture_and_warmup(monkeypatch, module, class_name, close):
    from importlib import import_module
    strategy = getattr(import_module('core.strategies.' + module), class_name)()
    history = history_frame(close)
    if module == 'kd_oversold':
        history['high_price'], history['low_price'] = 120., 70.
    if module == 'volume_surge':
        history.loc[history.index[-1], 'volume'] = 10000.
    snapshot = history.tail(1).copy()
    monkeypatch.setattr(_tquant, 'load_history', lambda date: history)
    assert strategy.filter_candidates(snapshot).stock_id.tolist() == ['2330']
    monkeypatch.setattr(_tquant, 'load_history', lambda date: history.iloc[1:])
    assert strategy.filter_candidates(snapshot).empty
    history.loc[history.index[-1], 'close_price'] = np.nan
    monkeypatch.setattr(_tquant, 'load_history', lambda date: history)
    assert strategy.filter_candidates(snapshot).empty


def test_mad_keeps_cross_section_and_all_price_action_filters(monkeypatch):
    from core.strategies.mad_distance import MadDistanceStrategy
    candidate = history_frame([100] * 180 + list(np.linspace(101, 130, 20)))
    candidate.loc[candidate.index[-5:], 'high_price'] = candidate.close_price.tail(5) + .25
    candidate.loc[candidate.index[-5:], 'low_price'] = candidate.close_price.tail(5) - .25
    peers = [history_frame([100] * 200, str(2000 + i)) for i in range(49)]
    history = pd.concat([candidate, *peers], ignore_index=True)
    snapshot = history.groupby('stock_id').tail(1).copy()
    monkeypatch.setattr(_tquant, 'load_history', lambda date: history)
    strategy = MadDistanceStrategy()
    assert strategy.filter_candidates(snapshot).stock_id.tolist() == ['2330']
    history.loc[history.stock_id.eq('2330') & history.trade_date.eq(history.trade_date.max()), 'high_price'] = 300
    assert strategy.filter_candidates(snapshot).empty
    monkeypatch.setattr(_tquant, 'load_history', lambda date: candidate)
    assert strategy.filter_candidates(snapshot).empty


@pytest.mark.parametrize('module,class_name,window', [
    ('volatility_adjusted_momentum', 'VolatilityAdjustedMomentumStrategy', 30),
])
def test_undefined_momentum_is_not_selected(monkeypatch, module, class_name, window):
    from importlib import import_module
    history = history_frame([100] * window)
    monkeypatch.setattr(_tquant, 'load_history', lambda date: history)
    strategy = getattr(import_module('core.strategies.' + module), class_name)()
    assert strategy.filter_candidates(history.tail(1)).empty
