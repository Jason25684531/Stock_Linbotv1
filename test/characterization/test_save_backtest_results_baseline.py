"""驗證 save_backtest_results() 對無效市場資料的拒絕邏輯。

2026-07-24: 一次意外的互動式驗證讓「查無市場資料」的無效結果（equity_df 攤平、
trades_df 為空）被當成正常結果整表覆寫。save_backtest_results() 現在在
market_data_available=False 時於觸及任何 DB 呼叫前即拒絕，且不影響
market_data_available=True（預設）的既有行為。

這裡只驗證閘門邏輯本身（是否呼叫到 DB 層），不驗證真實 MySQL 寫入語意
（RENAME TABLE 原子替換屬 Phase E，於隔離環境另行測試）。
"""

import pandas as pd
import pytest

import core.db_helper as db_helper


@pytest.mark.allow_real_backtest_persistence
def test_invalid_market_data_short_circuits_before_any_db_call(monkeypatch):
    def _fail_if_called(*args, **kwargs):
        raise AssertionError('ensure_backtest_tables() 不應在 market_data_available=False 時被呼叫')

    monkeypatch.setattr(db_helper, 'ensure_backtest_tables', _fail_if_called)

    result = db_helper.save_backtest_results(
        trades_df=pd.DataFrame(),
        equity_df=pd.DataFrame({'date': ['2026-01-02'], 'asset_value': [1000000.0], 'roi': [0.0]}),
        market_data_available=False,
    )

    assert result is False


@pytest.mark.allow_real_backtest_persistence
def test_valid_market_data_still_reaches_db_layer(monkeypatch):
    called = {}

    def _fake_ensure_backtest_tables():
        called['ensure_backtest_tables'] = True
        return True

    class _FakeConn:
        def __enter__(self):
            return self

        def __exit__(self, *exc_info):
            return False

        def execute(self, *args, **kwargs):
            called.setdefault('execute_count', 0)
            called['execute_count'] += 1
            return None

        def commit(self):
            called['committed'] = True

    class _FakeEngine:
        def connect(self):
            return _FakeConn()

    monkeypatch.setattr(db_helper, 'ensure_backtest_tables', _fake_ensure_backtest_tables)
    monkeypatch.setattr(db_helper, 'get_db_engine', lambda: _FakeEngine())

    result = db_helper.save_backtest_results(
        trades_df=pd.DataFrame(),
        equity_df=pd.DataFrame({'date': ['2026-01-02'], 'asset_value': [1000000.0], 'roi': [0.0]}),
        market_data_available=True,
    )

    assert result is True
    assert called['ensure_backtest_tables'] is True
    assert called['committed'] is True
