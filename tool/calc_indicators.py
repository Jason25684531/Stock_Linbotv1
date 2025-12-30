"""
技術指標計算模組 (合併版)
============================================
包含：
1. 技術指標計算函數 (RSI, MACD, KD, BB)
2. 資料庫批次更新功能
"""
import pandas as pd
import numpy as np
from sqlalchemy import create_engine
import sys
import os

# 加入專案根目錄以便 import config
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import Config

# ============================================
# ⚙️ 設定區
# ============================================
DB_URL = Config.SQLALCHEMY_DATABASE_URI


# ============================================
# 📊 技術指標計算函數
# ============================================

def calculate_rsi(series: pd.Series, period: int = 14) -> pd.Series:
    """
    計算 RSI (Relative Strength Index)
    
    Args:
        series: 收盤價序列
        period: 計算週期 (預設 14)
    
    Returns:
        RSI 數值序列 (0~100)
    """
    delta = series.diff()
    gain = delta.where(delta > 0, 0).rolling(period).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(period).mean()
    rs = gain / loss
    return 100 - (100 / (1 + rs))


def calculate_macd(series: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9) -> pd.Series:
    """
    計算 MACD Histogram
    
    Args:
        series: 收盤價序列
        fast: 快線週期 (預設 12)
        slow: 慢線週期 (預設 26)
        signal: 訊號線週期 (預設 9)
    
    Returns:
        MACD Histogram (MACD - Signal)
    """
    exp_fast = series.ewm(span=fast, adjust=False).mean()
    exp_slow = series.ewm(span=slow, adjust=False).mean()
    macd_line = exp_fast - exp_slow
    signal_line = macd_line.ewm(span=signal, adjust=False).mean()
    return macd_line - signal_line


def calculate_kd(df: pd.DataFrame, period: int = 9) -> pd.Series:
    """
    計算 KD 指標的 K 值 (Stochastic Oscillator)
    
    Args:
        df: 需包含 'high_price', 'low_price', 'close_price' 欄位
        period: 計算週期 (預設 9)
    
    Returns:
        K 值序列 (0~100)
    """
    low_min = df['low_price'].rolling(period).min()
    high_max = df['high_price'].rolling(period).max()
    rsv = (df['close_price'] - low_min) / (high_max - low_min) * 100
    k = rsv.ewm(com=2, adjust=False).mean()
    return k


def calculate_bb_width(series: pd.Series, period: int = 20, std_mult: float = 2.0) -> pd.Series:
    """
    計算布林通道寬度 (Bollinger Band Width)
    
    Args:
        series: 收盤價序列
        period: MA 週期 (預設 20)
        std_mult: 標準差倍數 (預設 2)
    
    Returns:
        帶寬百分比
    """
    ma = series.rolling(period).mean()
    std = series.rolling(period).std()
    upper = ma + (std_mult * std)
    lower = ma - (std_mult * std)
    return (upper - lower) / ma


def calculate_bias(close: pd.Series, ma: pd.Series) -> pd.Series:
    """
    計算乖離率 (Bias)
    """
    return (close - ma) / ma * 100


def add_all_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """
    一次性計算所有技術指標 (便捷函數，用於單一股票)
    
    Args:
        df: 需包含 'close_price', 'high_price', 'low_price' 欄位
        
    Returns:
        加入所有指標欄位的 DataFrame
    """
    df = df.copy()
    
    df['ma5'] = df['close_price'].rolling(5).mean()
    df['ma20'] = df['close_price'].rolling(20).mean()
    df['ma60'] = df['close_price'].rolling(60).mean()
    df['bias'] = calculate_bias(df['close_price'], df['ma20'])
    df['rsi'] = calculate_rsi(df['close_price'])
    df['macd_hist'] = calculate_macd(df['close_price'])
    df['kd_k'] = calculate_kd(df)
    df['bb_width'] = calculate_bb_width(df['close_price'])
    
    return df


# ============================================
# 🗄️ 資料庫批次更新功能
# ============================================

def fix_database_indicators():
    """計算全市場技術指標並寫回資料庫"""
    print("🚑 [AI工程] 正在計算全套技術指標 (MA, RSI, MACD, KD, BB)...")
    engine = create_engine(DB_URL)
    
    try:
        df = pd.read_sql("SELECT * FROM daily_market_data", engine)
    except Exception as e:
        print(f"❌ 讀取失敗: {e}")
        return

    if df.empty: return
    
    # 1. 清洗數據
    print("🧼 清洗數據中...")
    cols = ['open_price', 'high_price', 'low_price', 'close_price', 'volume']
    for col in cols:
        if col in df.columns and df[col].dtype == 'object':
            df[col] = df[col].astype(str).str.replace(',', '').str.replace('--', 'NaN')
            df[col] = pd.to_numeric(df[col], errors='coerce')
            
    df = df.dropna(subset=['close_price'])
    df['trade_date'] = pd.to_datetime(df['trade_date'])
    df = df.sort_values(['stock_id', 'trade_date'])
    
    # 2. 計算指標 (使用共用模組)
    print("📊 計算特徵工程 (這需要一點時間)...")
    
    # 基礎指標
    df['ma5'] = df.groupby('stock_id')['close_price'].transform(lambda x: x.rolling(5).mean())
    df['ma20'] = df.groupby('stock_id')['close_price'].transform(lambda x: x.rolling(20).mean())
    df['ma60'] = df.groupby('stock_id')['close_price'].transform(lambda x: x.rolling(60).mean())
    df['bias'] = (df['close_price'] - df['ma20']) / df['ma20'] * 100
    
    # 進階指標 (使用共用模組的函數)
    df['macd_hist'] = df.groupby('stock_id')['close_price'].transform(calculate_macd)
    df['kd_k'] = df.groupby('stock_id').apply(calculate_kd).reset_index(level=0, drop=True)
    df['bb_width'] = df.groupby('stock_id')['close_price'].transform(calculate_bb_width)
    
    # RSI (使用共用模組)
    df['rsi'] = df.groupby('stock_id')['close_price'].transform(calculate_rsi)

    df = df.fillna(0)
    
    # 3. 存回資料庫
    print("💾 正在升級資料庫 (寫入新特徵)...")
    df.to_sql('daily_market_data', engine, if_exists='replace', index=False, chunksize=5000)
    print("✅ 資料庫升級完成！現在裡面有 MACD 和 KD 了。")

if __name__ == "__main__":
    fix_database_indicators()