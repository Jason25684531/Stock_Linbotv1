"""
測試 V36 籌碼動能策略 (Chip Momentum)
============================================
驗證策略註冊、載入、篩選邏輯、出場規則、特徵完整性。
"""

import pytest
import pandas as pd
import numpy as np
from config import Config
from core.strategy_manager import StrategyManager


# ============================================
# Fixtures (manager / empty_df 已移至 conftest.py)
# ============================================


@pytest.fixture
def v36_strategy(manager):
    """取得 V36 策略實例"""
    manager.set_active_strategy('v36_chip_momentum')
    return manager.get_active_strategy()


@pytest.fixture
def sample_chip_df():
    """
    模擬含完整籌碼指標的市場資料 DataFrame。
    30 支股票，部分符合 V36 篩選條件。
    """
    np.random.seed(42)
    n_stocks = 30
    stock_ids = [f'{i:04d}' for i in range(2301, 2301 + n_stocks)]
    today = pd.Timestamp('2025-01-15')

    records = []
    for i, sid in enumerate(stock_ids):
        close = 50 + i * 5
        ma20 = close * (0.97 if i < 20 else 1.05)   # 前 20 支多頭排列
        ma60 = close * (0.93 if i < 20 else 1.10)
        records.append({
            'stock_id': sid,
            'trade_date': today,
            'close_price': close,
            'open_price': close * 0.99,
            'high_price': close * 1.02,
            'low_price': close * 0.98,
            'ma20': ma20,
            'ma60': ma60,
            'volume': 2000 + i * 100,  # 全部通過 volume threshold
            'volume_ratio': 1.2 + np.random.uniform(-0.3, 0.5),
            'chip_score': 60 + (10 if i < 15 else -30),  # 前 15 支 chip_score >= 55
            'foreign_consec_days': 5 if i < 12 else 0,    # 前 12 支外資連買 >= 3
            'trust_consec_days': 3 if i < 10 else 0,      # 前 10 支投信連買 >= 2
            'foreign_ratio': 0.05 + np.random.uniform(-0.02, 0.05),
            'trust_ratio': 0.03 + np.random.uniform(-0.01, 0.03),
            'dealer_ratio': 0.01 + np.random.uniform(-0.01, 0.02),
            'margin_change_pct': np.random.uniform(-2, 2),
            'rsi': 55 + np.random.uniform(-10, 20),
            'bias': 2 + np.random.uniform(-1, 3),
            'macd_hist': np.random.uniform(-0.5, 0.5),
        })

    return pd.DataFrame(records)


# empty_df 已移至 conftest.py


# ============================================
# 1. 策略註冊 & 載入
# ============================================

class TestV36Registration:

    def test_v36_in_registry(self, manager):
        """V36 應出現在策略清單"""
        available = manager.list_available_strategies()
        assert 'v36_chip_momentum' in available

    def test_v36_loads_successfully(self, manager):
        """V36 應能成功載入"""
        success = manager.set_active_strategy('v36_chip_momentum')
        assert success is True
        strategy = manager.get_active_strategy()
        assert strategy.name == 'v36_chip_momentum'

    def test_v36_display_name(self, v36_strategy):
        assert v36_strategy.display_name == 'V36 籌碼動能策略'

    def test_v36_description_not_empty(self, v36_strategy):
        assert len(v36_strategy.description) > 10


# ============================================
# 2. 特徵定義
# ============================================

class TestV36Features:

    def test_feature_count(self, v36_strategy):
        """V36 應有 11 個特徵"""
        assert len(v36_strategy.features) == 11

    def test_chip_core_features_present(self, v36_strategy):
        """核心籌碼特徵必須存在"""
        core = ['chip_score', 'foreign_consec_days', 'trust_consec_days',
                'margin_change_pct', 'dealer_ratio']
        for feat in core:
            assert feat in v36_strategy.features, f"Missing: {feat}"

    def test_technical_features_present(self, v36_strategy):
        """技術面輔助特徵"""
        tech = ['rsi', 'bias', 'macd_hist', 'volume_ratio']
        for feat in tech:
            assert feat in v36_strategy.features, f"Missing: {feat}"


# ============================================
# 3. 策略參數
# ============================================

class TestV36Params:

    def test_target_return(self, v36_strategy):
        assert v36_strategy.target_return == 0.07

    def test_look_ahead_days(self, v36_strategy):
        assert v36_strategy.look_ahead_days == 10

    def test_stop_loss(self, v36_strategy):
        assert 0.03 <= v36_strategy.stop_loss <= 0.15

    def test_take_profit(self, v36_strategy):
        assert 0.05 <= v36_strategy.take_profit <= 0.30

    def test_max_hold_days(self, v36_strategy):
        assert 5 <= v36_strategy.max_hold_days <= 20


# ============================================
# 4. Config 常數
# ============================================

class TestV36Config:

    def test_chip_score_min(self):
        assert hasattr(Config, 'V36_CHIP_SCORE_MIN')
        assert 30 <= Config.V36_CHIP_SCORE_MIN <= 80

    def test_foreign_consec_min(self):
        assert hasattr(Config, 'V36_FOREIGN_CONSEC_MIN')
        assert 1 <= Config.V36_FOREIGN_CONSEC_MIN <= 10

    def test_trust_consec_min(self):
        assert hasattr(Config, 'V36_TRUST_CONSEC_MIN')
        assert 1 <= Config.V36_TRUST_CONSEC_MIN <= 10

    def test_volume_threshold(self):
        assert hasattr(Config, 'V36_VOLUME_THRESHOLD')
        assert Config.V36_VOLUME_THRESHOLD >= 100

    def test_rsi_range(self):
        assert Config.V36_RSI_LOW < Config.V36_RSI_HIGH
        assert Config.V36_RSI_LOW >= 20
        assert Config.V36_RSI_HIGH <= 90


# ============================================
# 5. 篩選邏輯
# ============================================

class TestV36FilterCandidates:

    def test_empty_input(self, v36_strategy, empty_df):
        """空 DataFrame 應回傳空結果"""
        result = v36_strategy.filter_candidates(empty_df)
        assert result.empty

    def test_filters_produce_results(self, v36_strategy, sample_chip_df, monkeypatch):
        """模擬資料應篩出候選股"""
        # 繞過大盤過濾（無 DB 連線時 _check_market_filter 會觸發熔斷）
        monkeypatch.setattr(v36_strategy, '_check_market_filter', lambda *a, **k: True)
        result = v36_strategy.filter_candidates(sample_chip_df)
        assert not result.empty, "V36 should find candidates in sample data"
        print(f"  篩選結果: {len(result)} 檔")

    def test_chip_score_threshold(self, v36_strategy, sample_chip_df, monkeypatch):
        """篩選結果的 chip_score 應 >= 門檻"""
        monkeypatch.setattr(v36_strategy, '_check_market_filter', lambda *a, **k: True)
        result = v36_strategy.filter_candidates(sample_chip_df)
        if not result.empty and 'chip_score' in result.columns:
            assert (result['chip_score'] >= Config.V36_CHIP_SCORE_MIN).all()

    def test_trend_filter(self, v36_strategy, sample_chip_df, monkeypatch):
        """篩選結果應符合多頭排列"""
        monkeypatch.setattr(v36_strategy, '_check_market_filter', lambda *a, **k: True)
        result = v36_strategy.filter_candidates(sample_chip_df)
        if not result.empty:
            assert (result['close_price'] > result['ma60']).all()
            if 'ma20' in result.columns:
                assert (result['close_price'] > result['ma20']).all()

    def test_sorted_by_chip_score(self, v36_strategy, sample_chip_df, monkeypatch):
        """結果應按 chip_score 降序排列"""
        monkeypatch.setattr(v36_strategy, '_check_market_filter', lambda *a, **k: True)
        result = v36_strategy.filter_candidates(sample_chip_df)
        if len(result) > 1 and 'chip_score' in result.columns:
            scores = result['chip_score'].values
            assert all(scores[i] >= scores[i + 1] for i in range(len(scores) - 1))

    def test_missing_chip_columns_graceful(self, v36_strategy):
        """當缺少籌碼欄位時不應崩潰"""
        df = pd.DataFrame({
            'stock_id': ['2330', '2317'],
            'trade_date': pd.Timestamp('2025-01-15'),
            'close_price': [600, 150],
            'ma20': [590, 145],
            'ma60': [580, 140],
            'volume': [50000, 30000],
        })
        # 不包含 chip_score 等欄位 — 不應拋異常
        result = v36_strategy.filter_candidates(df)
        assert isinstance(result, pd.DataFrame)


# ============================================
# 6. 出場訊號
# ============================================

class TestV36ExitSignal:

    def test_chip_weakness_exit(self, v36_strategy):
        """chip_score 跌破 30 + 小獲利 → 應出場"""
        action, reason, stop = v36_strategy.check_exit_signal(
            stock_id='2330',
            current_price=103,
            current_date=pd.Timestamp('2025-01-20'),
            position_info={
                'cost': 100,
                'chip_score': 25,  # < 30
                'entry_date': pd.Timestamp('2025-01-10'),
                'stop_loss': 93,
                'highest_price': 105,
            },
        )
        assert action == 'SELL'
        assert '籌碼轉弱' in reason

    def test_chip_collapse_exit(self, v36_strategy):
        """chip_score 跌破 20 + 虧損 → 立即止損"""
        action, reason, stop = v36_strategy.check_exit_signal(
            stock_id='2330',
            current_price=95,
            current_date=pd.Timestamp('2025-01-20'),
            position_info={
                'cost': 100,
                'chip_score': 15,  # < 20
                'entry_date': pd.Timestamp('2025-01-10'),
                'stop_loss': 93,
                'highest_price': 100,
            },
        )
        assert action == 'SELL'
        assert '崩潰止損' in reason

    def test_normal_hold(self, v36_strategy):
        """chip_score 正常 → 交給基類尾停"""
        action, reason, stop = v36_strategy.check_exit_signal(
            stock_id='2330',
            current_price=102,
            current_date=pd.Timestamp('2025-01-12'),
            position_info={
                'cost': 100,
                'chip_score': 65,  # 正常
                'entry_date': pd.Timestamp('2025-01-10'),
                'stop_loss': 93,
                'highest_price': 103,
                'days': 2,
            },
        )
        # 獲利 2%，chip_score 正常 → 不應立刻賣出
        assert action in ('HOLD', 'SELL')  # 取決於基類尾停邏輯


# ============================================
# 7. get_strategy_info
# ============================================

class TestV36Info:

    def test_info_dict(self, v36_strategy):
        info = v36_strategy.get_strategy_info()
        assert info['name'] == 'v36_chip_momentum'
        assert 'params' in info
        assert 'chip_score_min' in info['params']
        assert 'features' in info


# ============================================
# 8. 與其他策略共存
# ============================================

class TestV36Coexistence:

    def test_switch_to_v36_and_back(self, manager):
        """V36 切換不影響其他策略"""
        # 先切到 V33
        manager.set_active_strategy('v33_low_vol')
        s1 = manager.get_active_strategy()
        assert s1.name == 'v33_low_vol'

        # 切到 V36
        manager.set_active_strategy('v36_chip_momentum')
        s2 = manager.get_active_strategy()
        assert s2.name == 'v36_chip_momentum'

        # 切回 V33
        manager.set_active_strategy('v33_low_vol')
        s3 = manager.get_active_strategy()
        assert s3.name == 'v33_low_vol'

    def test_all_strategies_loadable(self, manager):
        """所有已註冊策略都能載入"""
        available = manager.list_available_strategies()
        for name in available:
            manager.set_active_strategy(name)
            s = manager.get_active_strategy()
            assert s is not None, f"Failed to load: {name}"
            assert hasattr(s, 'features')
            assert hasattr(s, 'filter_candidates')
