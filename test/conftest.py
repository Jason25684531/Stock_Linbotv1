"""
共用測試 Fixtures
============================================
提供 StrategyManager、空 DataFrame 等跨測試共用 fixture。
避免各測試檔重複定義相同的 setup 邏輯。
"""

import pytest
import pandas as pd
from tool.strategy_manager import StrategyManager


@pytest.fixture
def manager():
    """每次測試前重設 StrategyManager Singleton，確保測試隔離"""
    StrategyManager._instance = None
    return StrategyManager()


@pytest.fixture
def empty_df():
    """空 DataFrame，供篩選邏輯邊界測試"""
    return pd.DataFrame()
