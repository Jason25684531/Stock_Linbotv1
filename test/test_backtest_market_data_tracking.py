"""驗證 BacktestEngine.get_market_trend() 正確記錄市場資料讀取成敗次數。"""

import pandas as pd

from core.backtest.runner import BacktestEngine


def _make_engine():
    engine = BacktestEngine.__new__(BacktestEngine)
    engine.market_data_reads = 0
    engine.market_data_read_failures = 0
    return engine


def test_successful_market_data_read_increments_reads(monkeypatch):
    engine = _make_engine()
    monkeypatch.setattr(
        'core.db_helper.get_stock_data',
        lambda symbol, date_str: (pd.DataFrame({'close_price': [100.0]}), date_str),
    )
    monkeypatch.setattr('core.backtest.runner.db_get_market_trend', lambda date_str: 'BULL')

    engine.get_market_trend('2026-01-02')

    assert engine.market_data_reads == 1
    assert engine.market_data_read_failures == 0


def test_failed_market_data_read_increments_failures(monkeypatch):
    engine = _make_engine()
    monkeypatch.setattr(
        'core.db_helper.get_stock_data',
        lambda symbol, date_str: (pd.DataFrame(), None),
    )
    monkeypatch.setattr('core.backtest.runner.db_get_market_trend', lambda date_str: 'BEAR')

    engine.get_market_trend('2026-01-02')

    assert engine.market_data_reads == 0
    assert engine.market_data_read_failures == 1


def test_legitimate_empty_data_for_a_date_is_not_counted_as_failure(monkeypatch):
    """當日確實無資料（如假日）時 date_str 仍非 None，不應計為失敗。"""
    engine = _make_engine()
    monkeypatch.setattr(
        'core.db_helper.get_stock_data',
        lambda symbol, date_str: (pd.DataFrame(), date_str),
    )
    monkeypatch.setattr('core.backtest.runner.db_get_market_trend', lambda date_str: 'BEAR')

    engine.get_market_trend('2026-01-02')

    assert engine.market_data_reads == 1
    assert engine.market_data_read_failures == 0
