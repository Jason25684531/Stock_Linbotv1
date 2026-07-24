"""
共用測試 Fixtures
============================================
提供 StrategyManager、空 DataFrame 等跨測試共用 fixture。
避免各測試檔重複定義相同的 setup 邏輯。
"""

import pytest
import pandas as pd
from core.strategy_manager import StrategyManager


@pytest.fixture
def manager():
    """每次測試前重設 StrategyManager Singleton，確保測試隔離"""
    StrategyManager._instance = None
    return StrategyManager()


@pytest.fixture
def empty_df():
    """空 DataFrame，供篩選邏輯邊界測試"""
    return pd.DataFrame()


@pytest.fixture(autouse=True)
def _forbid_real_backtest_persistence(monkeypatch, request):
    """預設攔截真實 save_backtest_results()，避免測試意外覆寫正式回測資料。

    2026-07-24: 一次互動式驗證意外觸發真實回測落庫，整表覆寫了
    backtest_trades/backtest_equity_curve。需要真正落庫語意的測試請標記
    @pytest.mark.allow_real_backtest_persistence 並自行指向隔離的測試目標。
    """
    if 'allow_real_backtest_persistence' in request.keywords:
        return

    def _blocked(*args, **kwargs):
        raise AssertionError(
            'save_backtest_results() 被呼叫，但測試未標記 '
            '@pytest.mark.allow_real_backtest_persistence'
        )

    monkeypatch.setattr('core.db_helper.save_backtest_results', _blocked)
    monkeypatch.setattr('core.backtest.runner.save_backtest_results', _blocked)
