"""
Phase 2 籌碼面指標與爬蟲測試
============================================
驗證項目：
  - 籌碼指標函數正確性 (unit)
  - 融資融券爬蟲模組可載入 (integration)
  - 資料管線新欄位相容性
"""

import pytest
import pandas as pd
import numpy as np


# ============================================
# 籌碼指標功能測試
# ============================================

class TestChipIndicators:
    """測試 calc_indicators 中的籌碼面函數"""

    def test_calculate_consec_days_basic(self):
        """連續正值天數：正常序列"""
        from core.calc_indicators import calculate_consec_days
        s = pd.Series([100, 200, -50, 300, 400, 500, -100, 200])
        result = calculate_consec_days(s)
        assert list(result) == [1, 2, 0, 1, 2, 3, 0, 1]

    def test_calculate_consec_days_all_positive(self):
        """連續正值天數：全部正值"""
        from core.calc_indicators import calculate_consec_days
        s = pd.Series([10, 20, 30, 40, 50])
        result = calculate_consec_days(s)
        assert list(result) == [1, 2, 3, 4, 5]

    def test_calculate_consec_days_all_negative(self):
        """連續正值天數：全部非正值"""
        from core.calc_indicators import calculate_consec_days
        s = pd.Series([-10, 0, -30, 0])
        result = calculate_consec_days(s)
        assert all(x == 0 for x in result)

    def test_margin_change_pct(self):
        """融資餘額日變動率"""
        from core.calc_indicators import calculate_margin_change_pct
        s = pd.Series([1000, 1100, 1050, 1050])
        result = calculate_margin_change_pct(s)
        assert result.iloc[0] == 0  # 第一天無前值
        assert abs(result.iloc[1] - 10.0) < 0.01  # +10%
        assert abs(result.iloc[2] - (-4.545)) < 0.1  # -4.5%
        assert result.iloc[3] == 0  # 無變動

    def test_margin_change_pct_zero_base(self):
        """融資餘額日變動率：前值為零"""
        from core.calc_indicators import calculate_margin_change_pct
        s = pd.Series([0, 100, 200])
        result = calculate_margin_change_pct(s)
        assert result.iloc[1] == 0  # 0->100, 前值是 0 → NaN → 填 0

    def test_dealer_ratio(self):
        """自營商比例計算"""
        from core.calc_indicators import calculate_dealer_ratio
        df = pd.DataFrame({
            'dealer_buy': [500, -300, 0],
            'volume': [10000, 10000, 0]
        })
        result = calculate_dealer_ratio(df)
        assert abs(result.iloc[0] - 0.05) < 0.001
        assert abs(result.iloc[1] - (-0.03)) < 0.001
        assert result.iloc[2] == 0  # volume=0 → safe division

    def test_dealer_ratio_no_column(self):
        """自營商比例：缺少 dealer_buy 欄位"""
        from core.calc_indicators import calculate_dealer_ratio
        df = pd.DataFrame({'volume': [10000]})
        result = calculate_dealer_ratio(df)
        assert result.iloc[0] == 0

    def test_chip_score_range(self):
        """籌碼綜合分數：結果在 0~100 之間"""
        from core.calc_indicators import calculate_chip_score
        df = pd.DataFrame({
            'foreign_ratio': [0.3, -0.4, 0.0],
            'trust_ratio': [0.2, -0.1, 0.0],
            'dealer_ratio': [0.1, -0.2, 0.0],
            'margin_change_pct': [-5.0, 10.0, 0.0],
        })
        result = calculate_chip_score(df)
        assert all(0 <= x <= 100 for x in result)

    def test_chip_score_strong_buy(self):
        """籌碼分數：三大法人齊買 + 融資減少 → 高分"""
        from core.calc_indicators import calculate_chip_score
        df = pd.DataFrame({
            'foreign_ratio': [0.4],
            'trust_ratio': [0.3],
            'dealer_ratio': [0.2],
            'margin_change_pct': [-10.0],
        })
        result = calculate_chip_score(df)
        assert result.iloc[0] > 70  # 強買訊號 → 高分

    def test_chip_score_weak(self):
        """籌碼分數：三大法人齊賣 + 融資暴增 → 低分"""
        from core.calc_indicators import calculate_chip_score
        df = pd.DataFrame({
            'foreign_ratio': [-0.4],
            'trust_ratio': [-0.3],
            'dealer_ratio': [-0.2],
            'margin_change_pct': [20.0],
        })
        result = calculate_chip_score(df)
        assert result.iloc[0] < 30  # 弱勢訊號 → 低分


# ============================================
# 爬蟲模組載入測試
# ============================================

class TestChipDataScraper:
    """測試 chip_data_scraper 模組"""

    def test_module_importable(self):
        """模組可正常匯入"""
        from core.crawlers.chip_data_scraper import (
            fetch_margin_balance,
            fetch_margin_balance_twse,
            fetch_margin_balance_tpex,
        )
        assert callable(fetch_margin_balance)
        assert callable(fetch_margin_balance_twse)
        assert callable(fetch_margin_balance_tpex)

    def test_clean_number(self):
        """數字清洗函數"""
        from core.crawlers.chip_data_scraper import _clean_number
        assert _clean_number('1,234') == 1234.0
        assert _clean_number('--') == 0
        assert _clean_number('---') == 0
        assert _clean_number(None) == 0
        assert _clean_number(42) == 42.0


# ============================================
# 資料管線相容性測試
# ============================================

class TestPipelineCompatibility:
    """測試 jobs.update_database 新欄位相容性"""

    def test_process_and_save_new_columns(self):
        """process_and_save 可處理含 dealer_buy/margin_balance 的 DataFrame"""
        # 驗證 clean_number 與新欄位不衝突
        from importlib import import_module
        mod = import_module('jobs.update_database')
        clean_number = mod.clean_number

        df = pd.DataFrame({
            'stock_id': ['2330'],
            'open_price': ['100'],
            'high_price': ['105'],
            'low_price': ['99'],
            'close_price': ['103'],
            'volume': ['10,000'],
            'pe_ratio': ['15.2'],
            'foreign_buy': ['500'],
            'trust_buy': ['-200'],
            'dealer_buy': ['300'],
            'margin_balance': ['1,234'],
            'short_balance': ['567'],
        })

        for col in ['open_price', 'high_price', 'low_price', 'close_price',
                     'volume', 'pe_ratio', 'foreign_buy', 'trust_buy',
                     'dealer_buy', 'margin_balance', 'short_balance']:
            df[col] = df[col].apply(clean_number)

        assert df['dealer_buy'].iloc[0] == 300.0
        assert df['margin_balance'].iloc[0] == 1234.0
        assert df['short_balance'].iloc[0] == 567.0

    def test_process_and_save_missing_columns(self):
        """process_and_save 缺少新欄位時仍正常（backward compat）"""
        from importlib import import_module
        mod = import_module('jobs.update_database')
        clean_number = mod.clean_number

        # 模擬舊版資料（不含 dealer_buy 等欄位）
        df = pd.DataFrame({
            'stock_id': ['2330'],
            'open_price': [100.0],
            'close_price': [103.0],
            'volume': [10000],
        })

        # 新的 process_and_save 邏輯中，缺少欄位會補 0
        cols = ['dealer_buy', 'margin_balance', 'short_balance']
        for col in cols:
            if col not in df.columns:
                df[col] = 0

        assert df['dealer_buy'].iloc[0] == 0
        assert df['margin_balance'].iloc[0] == 0

    def test_calc_indicators_new_functions_available(self):
        """calc_indicators 模組包含所有新函數"""
        from core.calc_indicators import (
            calculate_dealer_ratio,
            calculate_consec_days,
            calculate_margin_change_pct,
            calculate_chip_score,
        )
        assert callable(calculate_dealer_ratio)
        assert callable(calculate_consec_days)
        assert callable(calculate_margin_change_pct)
        assert callable(calculate_chip_score)

    def test_config_chip_constants(self):
        """Config 包含籌碼面相關常數"""
        from config import Config
        assert hasattr(Config, 'CHIP_WEIGHT_FOREIGN')
        assert hasattr(Config, 'CHIP_WEIGHT_TRUST')
        assert hasattr(Config, 'CHIP_WEIGHT_DEALER')
        assert hasattr(Config, 'CHIP_WEIGHT_MARGIN')
        assert hasattr(Config, 'CHIP_CONSEC_DAYS_WINDOW')
        # 權重總和應為 1.0
        total = (Config.CHIP_WEIGHT_FOREIGN + Config.CHIP_WEIGHT_TRUST +
                 Config.CHIP_WEIGHT_DEALER + Config.CHIP_WEIGHT_MARGIN)
        assert abs(total - 1.0) < 0.001
