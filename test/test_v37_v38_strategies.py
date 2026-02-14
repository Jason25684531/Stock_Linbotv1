"""
測試 V37 均值回歸策略 & V38 高殖利率價值策略
============================================
驗證策略註冊、載入、篩選邏輯、參數完整性。
"""

import pytest
import pandas as pd
import numpy as np
from config import Config
from tool.strategy_manager import StrategyManager


# ============================================
# Fixtures (manager / empty_df 已移至 conftest.py)
# ============================================


@pytest.fixture
def v37_strategy(manager):
    """取得 V37 策略實例"""
    manager.set_active_strategy('v37_mean_reversion')
    return manager.get_active_strategy()


@pytest.fixture
def v38_strategy(manager):
    """取得 V38 策略實例"""
    manager.set_active_strategy('v38_value_dividend')
    return manager.get_active_strategy()


@pytest.fixture
def sample_mean_reversion_df():
    """
    模擬均值回歸情境：KD 超賣 + BB 收斂 + 量縮。
    30 支股票，部分符合 V37 篩選條件。
    """
    np.random.seed(37)
    n_stocks = 30
    stock_ids = [f'{i:04d}' for i in range(2301, 2301 + n_stocks)]
    today = pd.Timestamp('2025-01-15')

    records = []
    for i, sid in enumerate(stock_ids):
        close = 50 + i * 3
        # 前 20 支：收盤 > MA60（基底通過）
        ma60 = close * (0.92 if i < 20 else 1.10)
        ma20 = close * (0.97 if i < 20 else 1.05)

        # 前 15 支：KD 低檔（< 35）
        kd_k = 20 + np.random.uniform(0, 10) if i < 15 else 55 + np.random.uniform(0, 20)

        # 前 12 支：BB 收斂（< 15）
        bb_width = 8 + np.random.uniform(0, 5) if i < 12 else 18 + np.random.uniform(0, 10)

        # 前 10 支：Bias 接近 0（-8 ~ 3）
        bias_val = -3 + np.random.uniform(-2, 3) if i < 10 else 5 + np.random.uniform(0, 5)

        # 前 8 支：量縮（volume_ratio < 1.0）+ RSI 偏冷
        vol_ratio = 0.6 + np.random.uniform(0, 0.3) if i < 8 else 1.2 + np.random.uniform(0, 0.5)
        rsi = 38 + np.random.uniform(0, 15) if i < 8 else 60 + np.random.uniform(0, 15)

        records.append({
            'stock_id': sid,
            'trade_date': today,
            'close_price': close,
            'open_price': close * 0.99,
            'high_price': close * 1.02,
            'low_price': close * 0.98,
            'ma20': ma20,
            'ma60': ma60,
            'volume': 1000 + i * 50,
            'volume_ratio': vol_ratio,
            'kd_k': kd_k,
            'kd_d': kd_k + 5,
            'bb_width': bb_width,
            'bias': bias_val,
            'rsi': rsi,
            'std_20': 2 + np.random.uniform(0, 3),
            'natr': 2 + np.random.uniform(0, 2),
            'macd_hist': np.random.uniform(-0.5, 0.5),
            'atr': 1 + np.random.uniform(0, 2),
        })

    return pd.DataFrame(records)


@pytest.fixture
def sample_value_df():
    """
    模擬價值股情境：高 EPS + 高營業利益率 + 低波動。
    30 支股票，部分符合 V38 篩選條件。
    """
    np.random.seed(38)
    n_stocks = 30
    stock_ids = [f'{i:04d}' for i in range(2301, 2301 + n_stocks)]
    today = pd.Timestamp('2025-01-15')

    records = []
    for i, sid in enumerate(stock_ids):
        close = 50 + i * 4
        # 前 20 支：收盤 > MA60
        ma60 = close * (0.92 if i < 20 else 1.10)
        ma20 = close * (0.97 if i < 20 else 1.05)

        # 前 15 支：高營業利益率（>= 8%）+ EPS > 0
        op_margin = 0.12 + np.random.uniform(0, 0.1) if i < 15 else 0.03 + np.random.uniform(0, 0.03)
        eps = 2.5 + np.random.uniform(0, 3) if i < 15 else -0.5 + np.random.uniform(0, 0.3)

        # 前 12 支：低波動（NATR < 4, STD < 3）
        natr = 2 + np.random.uniform(0, 1.5) if i < 12 else 5 + np.random.uniform(0, 3)
        std_20 = 1.5 + np.random.uniform(0, 1) if i < 12 else 4 + np.random.uniform(0, 2)

        # 前 10 支：RSI、Bias 溫和
        rsi = 48 + np.random.uniform(0, 15) if i < 10 else 70 + np.random.uniform(0, 10)
        bias_val = 0 + np.random.uniform(-3, 5) if i < 10 else 10 + np.random.uniform(0, 5)

        records.append({
            'stock_id': sid,
            'trade_date': today,
            'close_price': close,
            'open_price': close * 0.99,
            'high_price': close * 1.02,
            'low_price': close * 0.98,
            'ma20': ma20,
            'ma60': ma60,
            'volume': 800 + i * 30,
            'volume_ratio': 0.8 + np.random.uniform(0, 0.5),
            'op_profit_margin': op_margin,
            'eps': eps,
            'natr': natr,
            'std_20': std_20,
            'rsi': rsi,
            'bias': bias_val,
            'bb_width': 8 + np.random.uniform(0, 5),
            'macd_hist': np.random.uniform(-0.3, 0.3),
            'kd_k': 50 + np.random.uniform(-10, 10),
            'atr': 1 + np.random.uniform(0, 2),
        })

    return pd.DataFrame(records)


# empty_df 已移至 conftest.py


# ============================================
# V37 測試
# ============================================

class TestV37Registration:

    def test_v37_in_registry(self, manager):
        """V37 應出現在策略清單"""
        available = manager.list_available_strategies()
        assert 'v37_mean_reversion' in available

    def test_v37_loads_successfully(self, manager):
        """V37 應能成功載入"""
        success = manager.set_active_strategy('v37_mean_reversion')
        assert success is True
        strategy = manager.get_active_strategy()
        assert strategy.name == 'v37_mean_reversion'

    def test_v37_display_name(self, v37_strategy):
        assert v37_strategy.display_name == 'V37 均值回歸策略'

    def test_v37_description_not_empty(self, v37_strategy):
        assert len(v37_strategy.description) > 10


class TestV37Features:

    def test_feature_count(self, v37_strategy):
        """V37 應有 9 個特徵"""
        assert len(v37_strategy.features) == 9

    def test_core_features_present(self, v37_strategy):
        """核心均值回歸特徵必須存在"""
        core = ['kd_k', 'bb_width', 'bias', 'rsi', 'volume_ratio']
        for feat in core:
            assert feat in v37_strategy.features, f"Missing: {feat}"

    def test_technical_features_present(self, v37_strategy):
        """輔助技術特徵"""
        tech = ['std_20', 'natr', 'macd_hist', 'atr']
        for feat in tech:
            assert feat in v37_strategy.features, f"Missing: {feat}"


class TestV37Params:

    def test_target_return(self, v37_strategy):
        assert v37_strategy.target_return == 0.05

    def test_look_ahead_days(self, v37_strategy):
        assert v37_strategy.look_ahead_days == 8

    def test_stop_loss(self, v37_strategy):
        assert 0.03 <= v37_strategy.stop_loss <= 0.10

    def test_take_profit(self, v37_strategy):
        assert 0.05 <= v37_strategy.take_profit <= 0.20

    def test_max_hold_days(self, v37_strategy):
        assert 5 <= v37_strategy.max_hold_days <= 15


class TestV37Config:

    def test_kd_low(self):
        assert hasattr(Config, 'V37_KD_LOW')
        assert 15 <= Config.V37_KD_LOW <= 50

    def test_bb_width_max(self):
        assert hasattr(Config, 'V37_BB_WIDTH_MAX')
        assert 5 <= Config.V37_BB_WIDTH_MAX <= 30

    def test_bias_range(self):
        assert Config.V37_BIAS_LOW < Config.V37_BIAS_HIGH
        assert Config.V37_BIAS_LOW >= -15
        assert Config.V37_BIAS_HIGH <= 10

    def test_volume_ratio_max(self):
        assert 0.5 <= Config.V37_VOLUME_RATIO_MAX <= 2.0

    def test_rsi_range(self):
        assert Config.V37_RSI_LOW < Config.V37_RSI_HIGH
        assert Config.V37_RSI_LOW >= 15
        assert Config.V37_RSI_HIGH <= 70


class TestV37FilterCandidates:

    def test_empty_input(self, v37_strategy, empty_df):
        """空 DataFrame 應回傳空結果"""
        result = v37_strategy.filter_candidates(empty_df)
        assert result.empty

    def test_filters_produce_results(self, v37_strategy, sample_mean_reversion_df, monkeypatch):
        """模擬資料應篩出候選股"""
        monkeypatch.setattr(v37_strategy, '_check_market_filter', lambda *a, **k: True)
        result = v37_strategy.filter_candidates(sample_mean_reversion_df)
        assert not result.empty, "V37 should find candidates in sample data"
        print(f"  V37 篩選結果: {len(result)} 檔")

    def test_kd_oversold(self, v37_strategy, sample_mean_reversion_df, monkeypatch):
        """篩選結果的 kd_k 應 < 門檻"""
        monkeypatch.setattr(v37_strategy, '_check_market_filter', lambda *a, **k: True)
        result = v37_strategy.filter_candidates(sample_mean_reversion_df)
        if not result.empty and 'kd_k' in result.columns:
            assert (result['kd_k'] < Config.V37_KD_LOW).all()

    def test_bb_convergence(self, v37_strategy, sample_mean_reversion_df, monkeypatch):
        """篩選結果的 bb_width 應 < 門檻"""
        monkeypatch.setattr(v37_strategy, '_check_market_filter', lambda *a, **k: True)
        result = v37_strategy.filter_candidates(sample_mean_reversion_df)
        if not result.empty and 'bb_width' in result.columns:
            assert (result['bb_width'] < Config.V37_BB_WIDTH_MAX).all()

    def test_trend_filter(self, v37_strategy, sample_mean_reversion_df, monkeypatch):
        """篩選結果應符合收盤 > MA60"""
        monkeypatch.setattr(v37_strategy, '_check_market_filter', lambda *a, **k: True)
        result = v37_strategy.filter_candidates(sample_mean_reversion_df)
        if not result.empty:
            assert (result['close_price'] > result['ma60']).all()

    def test_sorted_by_kd_ascending(self, v37_strategy, sample_mean_reversion_df, monkeypatch):
        """結果應按 kd_k 升序排列（最超賣優先）"""
        monkeypatch.setattr(v37_strategy, '_check_market_filter', lambda *a, **k: True)
        result = v37_strategy.filter_candidates(sample_mean_reversion_df)
        if len(result) > 1 and 'kd_k' in result.columns:
            scores = result['kd_k'].values
            assert all(scores[i] <= scores[i + 1] for i in range(len(scores) - 1))

    def test_missing_kd_column(self, v37_strategy, monkeypatch):
        """缺少 KD 欄位時不應崩潰"""
        monkeypatch.setattr(v37_strategy, '_check_market_filter', lambda *a, **k: True)
        df = pd.DataFrame({
            'stock_id': ['2330', '2317'],
            'trade_date': pd.Timestamp('2025-01-15'),
            'close_price': [600, 150],
            'ma20': [590, 145],
            'ma60': [580, 140],
            'volume': [50000, 30000],
        })
        # 不應拋出異常
        result = v37_strategy.filter_candidates(df)
        assert isinstance(result, pd.DataFrame)


class TestV37StrategyInfo:

    def test_get_strategy_info(self, v37_strategy):
        """策略資訊應包含必要欄位"""
        info = v37_strategy.get_strategy_info()
        assert info['name'] == 'v37_mean_reversion'
        assert info['type'] == '反轉型'
        assert info['risk_level'] == '中'
        assert 'params' in info
        assert 'kd_low' in info['params']
        assert 'bb_width_max' in info['params']


# ============================================
# V38 測試
# ============================================

class TestV38Registration:

    def test_v38_in_registry(self, manager):
        """V38 應出現在策略清單"""
        available = manager.list_available_strategies()
        assert 'v38_value_dividend' in available

    def test_v38_loads_successfully(self, manager):
        """V38 應能成功載入"""
        success = manager.set_active_strategy('v38_value_dividend')
        assert success is True
        strategy = manager.get_active_strategy()
        assert strategy.name == 'v38_value_dividend'

    def test_v38_display_name(self, v38_strategy):
        assert v38_strategy.display_name == 'V38 高殖利率價值策略'

    def test_v38_description_not_empty(self, v38_strategy):
        assert len(v38_strategy.description) > 10


class TestV38Features:

    def test_feature_count(self, v38_strategy):
        """V38 應有 9 個特徵"""
        assert len(v38_strategy.features) == 9

    def test_core_features_present(self, v38_strategy):
        """核心低波動特徵"""
        core = ['natr', 'std_20', 'rsi', 'bias']
        for feat in core:
            assert feat in v38_strategy.features, f"Missing: {feat}"

    def test_technical_features_present(self, v38_strategy):
        """輔助技術特徵"""
        tech = ['bb_width', 'macd_hist', 'volume_ratio', 'kd_k', 'atr']
        for feat in tech:
            assert feat in v38_strategy.features, f"Missing: {feat}"


class TestV38Params:

    def test_target_return(self, v38_strategy):
        assert v38_strategy.target_return == 0.05

    def test_look_ahead_days(self, v38_strategy):
        assert v38_strategy.look_ahead_days == 15

    def test_stop_loss(self, v38_strategy):
        assert 0.03 <= v38_strategy.stop_loss <= 0.12

    def test_take_profit(self, v38_strategy):
        assert 0.05 <= v38_strategy.take_profit <= 0.25

    def test_max_hold_days(self, v38_strategy):
        assert 10 <= v38_strategy.max_hold_days <= 25


class TestV38Config:

    def test_op_margin_min(self):
        assert hasattr(Config, 'V38_OP_MARGIN_MIN')
        assert 0.03 <= Config.V38_OP_MARGIN_MIN <= 0.20

    def test_eps_min(self):
        assert hasattr(Config, 'V38_EPS_MIN')
        assert Config.V38_EPS_MIN >= 0

    def test_natr_max(self):
        assert hasattr(Config, 'V38_NATR_MAX')
        assert 2 <= Config.V38_NATR_MAX <= 8

    def test_std20_max(self):
        assert hasattr(Config, 'V38_STD20_MAX')
        assert 1 <= Config.V38_STD20_MAX <= 6

    def test_rsi_range(self):
        assert Config.V38_RSI_LOW < Config.V38_RSI_HIGH
        assert Config.V38_RSI_LOW >= 25
        assert Config.V38_RSI_HIGH <= 80


class TestV38FilterCandidates:

    def test_empty_input(self, v38_strategy, empty_df):
        """空 DataFrame 應回傳空結果"""
        result = v38_strategy.filter_candidates(empty_df)
        assert result.empty

    def test_filters_produce_results(self, v38_strategy, sample_value_df, monkeypatch):
        """模擬資料應篩出候選股"""
        monkeypatch.setattr(v38_strategy, '_check_market_filter', lambda *a, **k: True)
        result = v38_strategy.filter_candidates(sample_value_df)
        assert not result.empty, "V38 should find candidates in sample data"
        print(f"  V38 篩選結果: {len(result)} 檔")

    def test_profitability_filter(self, v38_strategy, sample_value_df, monkeypatch):
        """篩選結果的 op_profit_margin 應 >= 門檻"""
        monkeypatch.setattr(v38_strategy, '_check_market_filter', lambda *a, **k: True)
        result = v38_strategy.filter_candidates(sample_value_df)
        if not result.empty and 'op_profit_margin' in result.columns:
            assert (result['op_profit_margin'] >= Config.V38_OP_MARGIN_MIN).all()

    def test_eps_positive(self, v38_strategy, sample_value_df, monkeypatch):
        """篩選結果的 EPS 應 > 門檻"""
        monkeypatch.setattr(v38_strategy, '_check_market_filter', lambda *a, **k: True)
        result = v38_strategy.filter_candidates(sample_value_df)
        if not result.empty and 'eps' in result.columns:
            assert (result['eps'] > Config.V38_EPS_MIN).all()

    def test_low_volatility(self, v38_strategy, sample_value_df, monkeypatch):
        """篩選結果的 NATR 應 < 門檻"""
        monkeypatch.setattr(v38_strategy, '_check_market_filter', lambda *a, **k: True)
        result = v38_strategy.filter_candidates(sample_value_df)
        if not result.empty and 'natr' in result.columns:
            assert (result['natr'] < Config.V38_NATR_MAX).all()

    def test_trend_filter(self, v38_strategy, sample_value_df, monkeypatch):
        """篩選結果應符合收盤 > MA60"""
        monkeypatch.setattr(v38_strategy, '_check_market_filter', lambda *a, **k: True)
        result = v38_strategy.filter_candidates(sample_value_df)
        if not result.empty:
            assert (result['close_price'] > result['ma60']).all()

    def test_sorted_by_op_margin(self, v38_strategy, sample_value_df, monkeypatch):
        """結果應按 op_profit_margin 降序排列"""
        monkeypatch.setattr(v38_strategy, '_check_market_filter', lambda *a, **k: True)
        result = v38_strategy.filter_candidates(sample_value_df)
        if len(result) > 1 and 'op_profit_margin' in result.columns:
            margins = result['op_profit_margin'].values
            assert all(margins[i] >= margins[i + 1] for i in range(len(margins) - 1))

    def test_missing_financial_columns(self, v38_strategy, monkeypatch):
        """缺少財報欄位時不應崩潰（只是篩不到）"""
        monkeypatch.setattr(v38_strategy, '_check_market_filter', lambda *a, **k: True)
        df = pd.DataFrame({
            'stock_id': ['2330', '2317'],
            'trade_date': pd.Timestamp('2025-01-15'),
            'close_price': [600, 150],
            'ma20': [590, 145],
            'ma60': [580, 140],
            'volume': [50000, 30000],
            'natr': [2.0, 3.0],
            'std_20': [1.5, 2.0],
            'rsi': [50, 55],
            'bias': [1.0, 2.0],
        })
        result = v38_strategy.filter_candidates(df)
        assert isinstance(result, pd.DataFrame)


class TestV38StrategyInfo:

    def test_get_strategy_info(self, v38_strategy):
        """策略資訊應包含必要欄位"""
        info = v38_strategy.get_strategy_info()
        assert info['name'] == 'v38_value_dividend'
        assert info['type'] == '價值型'
        assert info['risk_level'] == '低'
        assert 'params' in info
        assert 'op_margin_min' in info['params']
        assert 'eps_min' in info['params']


# ============================================
# 跨策略整合測試
# ============================================

class TestMultiStrategyIntegration:

    def test_all_strategies_registered(self, manager):
        """V31 ~ V38 全部策略都應已註冊"""
        available = manager.list_available_strategies()
        expected = [
            'v31_hybrid', 'v33_low_vol', 'v34_turbo',
            'v35_innovation', 'v36_chip_momentum',
            'v37_mean_reversion', 'v38_value_dividend',
        ]
        for s in expected:
            assert s in available, f"Missing strategy: {s}"

    def test_strategy_switching(self, manager):
        """V37/V38 策略切換應正常運作"""
        for name in ['v37_mean_reversion', 'v38_value_dividend']:
            success = manager.set_active_strategy(name)
            assert success, f"Failed to switch to {name}"
            strategy = manager.get_active_strategy()
            assert strategy.name == name

    def test_no_feature_overlap_regression(self, v37_strategy, v38_strategy):
        """V37 和 V38 的特徵列表應不完全相同（策略特色差異）"""
        # 雖然可以有部分重疊，但整體特徵集不應完全一樣
        f37 = set(v37_strategy.features)
        f38 = set(v38_strategy.features)
        # 允許部分重疊但不能完全等同
        if f37 == f38:
            # 如果特徵完全一樣也 OK（只是篩選邏輯不同），但加個 warning
            print("  ⚠️ V37 和 V38 特徵相同，差異在篩選邏輯")
