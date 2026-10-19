"""Notebook-rule fixtures and public integration contracts for TQuant ports."""
import numpy as np
import pandas as pd
import pytest

from core.strategies import _tquant
from core.strategy_manager import StrategyManager


def history_frame(close, stock_id='2330'):
    close = np.asarray(close, dtype=float)
    return pd.DataFrame({'stock_id': stock_id,
                         'trade_date': pd.bdate_range('2025-01-02', periods=len(close)),
                         'close_price': close, 'high_price': close + 1,
                         'low_price': close - 1, 'volume': np.full(len(close), 1000.)})


def test_macd_source_cross_and_no_input_mutation(monkeypatch):
    from core.strategies.macd_cross import MacdCrossStrategy
    history = history_frame([100] * 26 + [99, 98, 97, 96, 95, 94, 93, 92, 110])
    history = pd.concat([history, history.assign(stock_id='2317')], ignore_index=True)
    monkeypatch.setattr(_tquant, 'load_history', lambda date: history)
    snapshot = history.groupby('stock_id').tail(1).copy()
    original = snapshot.copy(deep=True)
    strategy = MacdCrossStrategy()
    assert strategy.filter_candidates(snapshot).stock_id.tolist() == ['2330']
    pd.testing.assert_frame_equal(snapshot, original)
    history.loc[history.stock_id.eq('2330') & history.trade_date.eq(history.trade_date.max()), 'close_price'] = 90
    assert strategy.filter_candidates(snapshot).empty


def test_macd_requires_history_and_rejects_future(monkeypatch):
    from core.strategies.macd_cross import MacdCrossStrategy
    history = history_frame(np.linspace(100, 110, 34))
    snapshot = history.tail(1).copy()
    monkeypatch.setattr(_tquant, 'load_history', lambda date: history)
    assert MacdCrossStrategy().filter_candidates(snapshot).empty
    with pytest.raises(ValueError, match='one as-of date'):
        MacdCrossStrategy().filter_candidates(history)
    history.loc[history.index[-1], 'trade_date'] += pd.Timedelta(days=10)
    assert MacdCrossStrategy().filter_candidates(snapshot).empty
    assert MacdCrossStrategy().filter_candidates(snapshot.iloc[:0]).empty


def test_registry_aliases_line_and_cli_contracts(manager):
    from app import line_flows
    from core.backtest.runner import get_registered_strategy_names, parse_cli_args, resolve_backtest_plan
    metadata = manager.STRATEGY_METADATA
    assert manager.list_strategies() == list(metadata)
    assert get_registered_strategy_names(manager) == list(metadata)
    assert resolve_backtest_plan(parse_cli_args(['--strategies', 'all']))['strategy_names'] == list(metadata)
    registry = manager.get_platform_registry()
    for key, item in metadata.items():
        assert registry.resolve(key) == key
        for alias in item.legacy_ids:
            assert manager.resolve(alias, warn_legacy=False) == key
        if item.source == 'tquant':
            assert item.legacy_ids == ()
            assert item.source_notebook.endswith('.ipynb')
            strategy = manager.get_strategy(key)
            assert strategy is not None
            assert strategy.source_notebook == item.source_notebook
            assert _tquant.ENGINE_NOTICE in strategy.description
    native = [key for key, item in metadata.items() if item.source == 'native']
    assert len(native) == 7
    assert [row['key'] for row in line_flows._list_strategy_picker_options()] == native


def test_catalog_source_capabilities_and_page():
    from app import app
    from app.web_server import _build_strategy_catalog
    catalog = _build_strategy_catalog()
    for item in catalog:
        if item.get('source') == 'tquant':
            assert item['category'] == 'Technical'
            assert item['source_notebook'].endswith('.ipynb')
            assert item['available_for_daily'] is True
            assert item['available_for_backtest'] is True
    client = app.test_client()
    response = client.get('/strategies')
    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert '來源：' in html and _tquant.ENGINE_NOTICE in html
    assert ':aria-pressed=' in html
    assert "{key: 'Technical', label: '技術'}" in html
    payload = client.get('/api/strategies').get_json()
    assert set(StrategyManager.CANONICAL_REGISTRY) <= {row['name'] for row in payload['strategies']}


