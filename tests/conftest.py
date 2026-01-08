"""
pytest 配置文件與共用 fixtures

此文件提供：
- 測試用資料庫連線
- Mock 數據生成器
- 共用的 fixtures
"""
import pytest
import pandas as pd
import numpy as np
from datetime import datetime, timedelta


@pytest.fixture
def sample_stock_data():
    """生成測試用股價數據"""
    dates = pd.date_range(start='2024-01-01', periods=100, freq='D')
    
    # 生成模擬股價（簡單的上升趨勢 + 隨機波動）
    base_price = 100
    trend = np.linspace(0, 20, 100)  # 20% 上升趨勢
    noise = np.random.randn(100) * 2  # 隨機波動
    close_prices = base_price + trend + noise
    
    # 生成 OHLC
    high_prices = close_prices + np.random.rand(100) * 2
    low_prices = close_prices - np.random.rand(100) * 2
    open_prices = close_prices + np.random.randn(100) * 1
    
    # 成交量
    volumes = np.random.randint(1000000, 10000000, 100)
    
    df = pd.DataFrame({
        'trade_date': dates,
        'stock_id': '2330',
        'open_price': open_prices,
        'high_price': high_prices,
        'low_price': low_prices,
        'close_price': close_prices,
        'volume': volumes,
    })
    
    return df


@pytest.fixture
def sample_market_data():
    """生成測試用市場數據（多檔股票）"""
    stock_ids = ['2330', '2317', '2454', '2412', '3008']
    dates = pd.date_range(start='2024-01-01', periods=60, freq='D')
    
    data_frames = []
    for stock_id in stock_ids:
        base_price = np.random.randint(50, 200)
        trend = np.linspace(0, 10, 60)
        noise = np.random.randn(60) * 3
        close_prices = base_price + trend + noise
        
        df = pd.DataFrame({
            'trade_date': dates,
            'stock_id': stock_id,
            'close_price': close_prices,
            'high_price': close_prices + np.random.rand(60) * 2,
            'low_price': close_prices - np.random.rand(60) * 2,
            'volume': np.random.randint(2000000, 8000000, 60),
        })
        data_frames.append(df)
    
    return pd.concat(data_frames, ignore_index=True)


@pytest.fixture
def known_rsi_data():
    """已知 RSI 值的測試數據（用於驗證計算準確度）"""
    # 簡單的價格序列，方便手動驗證 RSI
    prices = pd.Series([
        44, 44.34, 44.09, 43.61, 44.33, 44.83, 45.10, 45.42, 45.84, 46.08,
        45.89, 46.03, 45.61, 46.28, 46.28, 46.00, 46.03, 46.41, 46.22, 45.64
    ])
    
    # 預期的 RSI (14 期) 約為 70.46（使用標準公式計算）
    expected_rsi = 70.46
    
    return {'prices': prices, 'expected_rsi': expected_rsi}


@pytest.fixture
def config_mock(monkeypatch):
    """Mock Config 設定，避免依賴真實資料庫"""
    from config import Config
    
    monkeypatch.setattr(Config, 'V30_VOLUME_THRESHOLD', 3000000)
    monkeypatch.setattr(Config, 'V30_RSI_LOW', 40)
    monkeypatch.setattr(Config, 'V30_RSI_HIGH', 70)
    monkeypatch.setattr(Config, 'V30_STOP_LOSS', 0.10)
    monkeypatch.setattr(Config, 'V30_TAKE_PROFIT', 0.20)
    
    return Config
