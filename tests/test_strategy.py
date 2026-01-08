"""
測試策略模組

驗證：
- V30 篩選邏輯正確性
- V31 混合策略流程
- 市場趨勢檢查機制
- 格式化輸出函數
"""
import pytest
import pandas as pd
import numpy as np
from unittest.mock import Mock, patch
from tool.strategy import (
    get_v30_candidates,
    get_best_stocks_v31_hybrid,
    check_market_trend,
    get_v30_params_from_db,
)
from tool.calc_indicators import add_all_indicators


class TestMarketTrendCheck:
    """市場趨勢檢查測試"""
    
    @patch('tool.db_helper.get_market_trend')
    def test_check_market_trend_bull(self, mock_get_trend):
        """測試多頭市場檢查"""
        mock_get_trend.return_value = 'BULL'
        
        result = check_market_trend('2024-01-01')
        
        assert result == 'BULL'
        mock_get_trend.assert_called_once_with('2024-01-01')
    
    @patch('tool.db_helper.get_market_trend')
    def test_check_market_trend_bear(self, mock_get_trend):
        """測試空頭市場檢查"""
        mock_get_trend.return_value = 'BEAR'
        
        result = check_market_trend('2024-01-01')
        
        assert result == 'BEAR'
    
    @patch('tool.db_helper.get_market_trend')
    def test_check_market_trend_exception(self, mock_get_trend):
        """測試檢查失敗時返回 None"""
        mock_get_trend.side_effect = Exception("Database error")
        
        result = check_market_trend('2024-01-01')
        
        assert result is None


class TestV30Candidates:
    """V30 篩選邏輯測試"""
    
    @pytest.fixture
    def qualified_stocks(self):
        """生成符合 V30 條件的測試數據"""
        dates = pd.date_range('2024-01-01', periods=70, freq='D')
        
        df = pd.DataFrame({
            'trade_date': dates[-1],  # 最新日期
            'stock_id': ['2330', '2317', '2454'],
            'close_price': [120, 150, 200],
            'ma20': [115, 145, 195],
            'ma60': [110, 140, 190],
            'volume': [5000000, 6000000, 4000000],  # 都大於 300 萬
            'rsi': [55, 60, 50],  # 都在 40-70 之間
        })
        
        return df
    
    @pytest.fixture
    def unqualified_stocks(self):
        """生成不符合 V30 條件的測試數據"""
        dates = pd.date_range('2024-01-01', periods=70, freq='D')
        
        df = pd.DataFrame({
            'trade_date': dates[-1],
            'stock_id': ['1234', '5678', '9012'],
            'close_price': [100, 80, 110],
            'ma20': [105, 85, 115],  # 價格低於 MA20（空頭）
            'ma60': [110, 90, 120],
            'volume': [2000000, 500000, 1000000],  # 量能不足
            'rsi': [30, 75, 35],  # RSI 超買或超賣
        })
        
        return df
    
    @patch('tool.strategy.check_market_trend')
    def test_v30_candidates_bull_market(self, mock_trend, qualified_stocks):
        """測試在多頭市場的篩選"""
        mock_trend.return_value = 'BULL'
        
        result = get_v30_candidates(qualified_stocks)
        
        # 應該返回所有符合條件的股票
        assert len(result) == 3
        assert set(result['stock_id']) == {'2330', '2317', '2454'}
    
    @patch('tool.strategy.check_market_trend')
    def test_v30_candidates_bear_market(self, mock_trend, qualified_stocks):
        """測試在空頭市場應返回空 DataFrame"""
        mock_trend.return_value = 'BEAR'
        
        result = get_v30_candidates(qualified_stocks)
        
        # 空頭市場應該不選股
        assert result.empty
    
    @patch('tool.strategy.check_market_trend')
    def test_v30_candidates_unqualified(self, mock_trend, unqualified_stocks):
        """測試不符合條件的股票應被過濾"""
        mock_trend.return_value = 'BULL'
        
        result = get_v30_candidates(unqualified_stocks)
        
        # 沒有股票符合條件
        assert result.empty
    
    @patch('tool.strategy.check_market_trend')
    def test_v30_candidates_partial_qualified(self, mock_trend):
        """測試部分符合條件的情況"""
        mock_trend.return_value = 'BULL'
        
        df = pd.DataFrame({
            'trade_date': '2024-01-01',
            'stock_id': ['GOOD', 'BAD1', 'BAD2'],
            'close_price': [120, 80, 110],
            'ma20': [115, 85, 115],  # BAD1 空頭，BAD2 價格低於 MA20
            'ma60': [110, 90, 120],
            'volume': [5000000, 5000000, 2000000],  # BAD2 量能不足
            'rsi': [55, 60, 50],
        })
        
        result = get_v30_candidates(df)
        
        # 只有 GOOD 符合條件
        assert len(result) == 1
        assert result.iloc[0]['stock_id'] == 'GOOD'
    
    def test_v30_candidates_missing_columns(self):
        """測試缺少必要欄位時的行為"""
        df_incomplete = pd.DataFrame({
            'trade_date': '2024-01-01',
            'stock_id': ['2330'],
            'close_price': [120],
            # 缺少 ma20, ma60, volume, rsi
        })
        
        result = get_v30_candidates(df_incomplete)
        
        # 應該返回空 DataFrame 並印出警告
        assert result.empty


class TestV31HybridStrategy:
    """V31 混合策略測試"""
    
    @pytest.fixture
    def mock_qualified_data(self):
        """生成符合 V30 的測試數據（含所有必要欄位）"""
        df = pd.DataFrame({
            'trade_date': '2024-01-01',
            'stock_id': ['2330', '2317', '2454', '2412', '3008'],
            'close_price': [120, 150, 200, 180, 90],
            'ma20': [115, 145, 195, 175, 88],
            'ma60': [110, 140, 190, 170, 85],
            'volume': [5000000, 6000000, 7000000, 4000000, 3500000],
            'rsi': [55, 60, 50, 65, 45],
            'bias': [4.3, 3.4, 2.6, 2.9, 2.3],
            'macd_hist': [0.5, 0.8, 0.3, 0.6, 0.2],
            'kd_k': [60, 70, 50, 65, 55],
            'bb_width': [0.05, 0.06, 0.04, 0.05, 0.04],
            'foreign_buy': [1000, -500, 2000, 500, -1000],
            'trust_buy': [500, 200, 1000, -200, 100],
        })
        
        return df
    
    @patch('tool.strategy.check_market_trend')
    @patch('tool.strategy._load_v31_model')
    def test_v31_hybrid_no_model(self, mock_load_model, mock_trend, mock_qualified_data):
        """測試沒有模型時應回退到 V30 篩選"""
        mock_trend.return_value = 'BULL'
        mock_load_model.return_value = (None, None)
        
        result = get_best_stocks_v31_hybrid(mock_qualified_data, top_n=3)
        
        # 應該有結果（V30 篩選）
        assert len(result) <= 5  # 所有股票都符合 V30
        assert 'ai_score' in result.columns
        # 沒有模型時 ai_score 應為 0.5
        assert (result['ai_score'] == 0.5).all()
    
    @patch('tool.strategy.check_market_trend')
    def test_v31_hybrid_bear_market(self, mock_trend, mock_qualified_data):
        """測試空頭市場應返回空結果"""
        mock_trend.return_value = 'BEAR'
        
        result = get_best_stocks_v31_hybrid(mock_qualified_data)
        
        assert result.empty
    
    @patch('tool.strategy.check_market_trend')
    @patch('tool.strategy._load_v31_model')
    def test_v31_hybrid_with_model(self, mock_load_model, mock_trend, mock_qualified_data):
        """測試有模型時的完整流程"""
        mock_trend.return_value = 'BULL'
        
        # Mock 模型與特徵列表
        mock_model = Mock()
        feature_list = ['rsi', 'bias', 'macd_hist', 'kd_k', 'bb_width', 
                       'volume_ratio', 'foreign_ratio', 'trust_ratio']
        mock_load_model.return_value = (mock_model, feature_list)
        
        # Mock 模型預測（返回機率）
        mock_model.predict_proba.return_value = np.array([
            [0.3, 0.7],  # 2330: 70% 機率
            [0.4, 0.6],  # 2317: 60%
            [0.5, 0.5],  # 2454: 50%
            [0.2, 0.8],  # 2412: 80%
            [0.6, 0.4],  # 3008: 40%
        ])
        
        result = get_best_stocks_v31_hybrid(mock_qualified_data, top_n=3)
        
        # 應該返回 Top 3（按 ai_score 排序）
        assert len(result) == 3
        assert 'ai_score' in result.columns
        
        # 檢查排序（應該是 2412, 2330, 2317）
        top_stocks = result['stock_id'].tolist()
        assert top_stocks[0] == '2412'  # 最高 80%
        assert top_stocks[1] == '2330'  # 第二 70%
        assert top_stocks[2] == '2317'  # 第三 60%


class TestV30ParamsFromDB:
    """V30 參數讀取測試"""
    
    @patch('sqlalchemy.create_engine')
    def test_get_v30_params_from_db_success(self, mock_engine):
        """測試成功從資料庫讀取參數"""
        # Mock 資料庫連線與結果
        mock_conn = Mock()
        mock_engine.return_value.connect.return_value.__enter__.return_value = mock_conn
        
        mock_result = Mock()
        mock_result.fetchall.return_value = [
            ('v30_stop_loss', '0.08'),
            ('v30_take_profit', '0.25'),
            ('v30_max_hold_days', '15'),
        ]
        mock_conn.execute.return_value = mock_result
        
        params = get_v30_params_from_db()
        
        # 檢查參數是否正確更新
        assert params['STOP_LOSS'] == 0.08
        assert params['TAKE_PROFIT'] == 0.25
        assert params['MAX_HOLD_DAYS'] == 15
    
    @patch('sqlalchemy.create_engine')
    def test_get_v30_params_from_db_failure(self, mock_engine):
        """測試資料庫讀取失敗時使用預設值"""
        # Mock 資料庫連線失敗
        mock_engine.side_effect = Exception("Connection error")
        
        params = get_v30_params_from_db()
        
        # 應該返回 Config 的預設值
        from config import Config
        assert params['VOLUME_THRESHOLD'] == Config.V30_VOLUME_THRESHOLD
        assert params['STOP_LOSS'] == Config.V30_STOP_LOSS


class TestEdgeCasesStrategy:
    """策略邊界情況測試"""
    
    def test_empty_dataframe_v30(self):
        """測試空 DataFrame"""
        empty_df = pd.DataFrame()
        
        result = get_v30_candidates(empty_df)
        
        assert result.empty
    
    def test_empty_dataframe_v31(self):
        """測試空 DataFrame 給 V31"""
        empty_df = pd.DataFrame()
        
        result = get_best_stocks_v31_hybrid(empty_df)
        
        assert result.empty
    
    @patch('tool.strategy.check_market_trend')
    def test_single_stock_v30(self, mock_trend):
        """測試只有一檔股票的情況"""
        mock_trend.return_value = 'BULL'
        
        df = pd.DataFrame({
            'trade_date': '2024-01-01',
            'stock_id': ['2330'],
            'close_price': [120],
            'ma20': [115],
            'ma60': [110],
            'volume': [5000000],
            'rsi': [55],
        })
        
        result = get_v30_candidates(df)
        
        assert len(result) == 1
        assert result.iloc[0]['stock_id'] == '2330'
