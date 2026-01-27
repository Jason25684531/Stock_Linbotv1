"""
技術指標計算模組 (合併版)
============================================
包含：
1. 技術指標計算函數 (RSI, MACD, KD, BB, Bias)
2. 資料庫批次更新功能

🔥 V33 Refactor:
- 所有魔術數字移至 Config
- 加入 Type Hints
- 改善 Docstrings
"""
from typing import Optional, Tuple
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
# 注意：請使用 tool.db_helper.get_db_engine() 取得資料庫連接


# ============================================
# 📊 技術指標計算函數
# ============================================

def calculate_rsi(series: pd.Series, period: Optional[int] = None) -> pd.Series:
    """
    計算 RSI (Relative Strength Index)
    
    Args:
        series: 收盤價序列
        period: 計算週期 (預設從 Config 讀取)
    
    Returns:
        RSI 數值序列 (0~100)
    """
    if period is None:
        period = Config.RSI_PERIOD
    
    delta = series.diff()
    gain = delta.where(delta > 0, 0).rolling(period).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(period).mean()
    rs = gain / loss
    return 100 - (100 / (1 + rs))


def calculate_macd(
    series: pd.Series, 
    fast: Optional[int] = None, 
    slow: Optional[int] = None, 
    signal: Optional[int] = None
) -> pd.Series:
    """
    計算 MACD Histogram
    
    Args:
        series: 收盤價序列
        fast: 快線週期 (預設從 Config 讀取)
        slow: 慢線週期 (預設從 Config 讀取)
        signal: 訊號線週期 (預設從 Config 讀取)
    
    Returns:
        MACD Histogram (MACD - Signal)
    """
    if fast is None:
        fast = Config.MACD_FAST
    if slow is None:
        slow = Config.MACD_SLOW
    if signal is None:
        signal = Config.MACD_SIGNAL
    
    exp_fast = series.ewm(span=fast, adjust=False).mean()
    exp_slow = series.ewm(span=slow, adjust=False).mean()
    macd_line = exp_fast - exp_slow
    signal_line = macd_line.ewm(span=signal, adjust=False).mean()
    return macd_line - signal_line


def calculate_atr(df: pd.DataFrame, period: Optional[int] = None) -> pd.Series:
    """
    計算 ATR (Average True Range) - 平均真實波幅
    
    🆕 V33 Phase 1+: 用於動態停損計算
    
    Args:
        df: 需包含 'high_price', 'low_price', 'close_price' 欄位
        period: 計算週期 (預設從 Config 讀取)
    
    Returns:
        ATR 數值序列
    """
    if period is None:
        period = Config.ATR_PERIOD
    
    high = df['high_price']
    low = df['low_price']
    close = df['close_price']
    
    # True Range = max(H-L, |H-Prev_C|, |L-Prev_C|)
    prev_close = close.shift(1)
    tr1 = high - low
    tr2 = (high - prev_close).abs()
    tr3 = (low - prev_close).abs()
    true_range = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    
    # ATR = EMA of True Range
    atr = true_range.ewm(span=period, adjust=False).mean()
    return atr


def calculate_kd(df: pd.DataFrame, period: Optional[int] = None) -> pd.Series:
    """
    計算 KD 指標的 K 值 (Stochastic Oscillator)
    
    Args:
        df: 需包含 'high_price', 'low_price', 'close_price' 欄位
        period: 計算週期 (預設從 Config 讀取)
    
    Returns:
        K 值序列 (0~100)
    """
    if period is None:
        period = Config.KD_PERIOD
    
    low_min = df['low_price'].rolling(period).min()
    high_max = df['high_price'].rolling(period).max()
    rsv = (df['close_price'] - low_min) / (high_max - low_min) * 100
    k = rsv.ewm(com=2, adjust=False).mean()
    return k


def calculate_kd_full(
    df: pd.DataFrame, 
    period: Optional[int] = None
) -> Tuple[pd.Series, pd.Series]:
    """
    計算完整 KD 指標 (K 值與 D 值)
    
    🆕 V33 Phase 2: 用於 KD 黃金交叉濾網
    
    Args:
        df: 需包含 'high_price', 'low_price', 'close_price' 欄位
        period: 計算週期 (預設從 Config 讀取)
    
    Returns:
        Tuple[K 值序列, D 值序列] - 均為 0~100 範圍
    """
    if period is None:
        period = Config.KD_PERIOD
    
    low_min = df['low_price'].rolling(period).min()
    high_max = df['high_price'].rolling(period).max()
    rsv = (df['close_price'] - low_min) / (high_max - low_min) * 100
    
    # K 值：RSV 的 3 期平滑
    k = rsv.ewm(com=2, adjust=False).mean()
    
    # D 值：K 值的 3 期平滑
    d = k.ewm(com=2, adjust=False).mean()
    
    return k, d


def calculate_bb_width(
    series: pd.Series, 
    period: Optional[int] = None, 
    std_mult: Optional[float] = None
) -> pd.Series:
    """
    計算布林通道寬度 (Bollinger Band Width)
    
    Args:
        series: 收盤價序列
        period: MA 週期 (預設從 Config 讀取)
        std_mult: 標準差倍數 (預設從 Config 讀取)
    
    Returns:
        帶寬百分比
    """
    if period is None:
        period = Config.BB_PERIOD
    if std_mult is None:
        std_mult = Config.BB_STD_MULT
    
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


def calculate_ratio_features(df: pd.DataFrame) -> pd.DataFrame:
    """
    計算比例特徵（籌碼面標準化）
    
    🔄 V33 Refactor: 從 3_train_model.py 移至此模組供共用
    
    Args:
        df: DataFrame，包含股票資料
    
    Returns:
        DataFrame: 添加了比例特徵的數據
    """
    print("📊 計算比例特徵（籌碼面標準化）...")
    
    df = df.copy()
    
    # 避免除以零
    df['volume'] = df['volume'].replace(0, 1)
    
    # 計算成交量相對於 20 日均量的比例（量能強度）
    df['volume_ma20'] = df.groupby('stock_id')['volume'].transform(
        lambda x: x.rolling(20, min_periods=1).mean()
    )
    df['volume_ratio'] = df['volume'] / df['volume_ma20'].replace(0, 1)
    
    # 籌碼面比例（外資/投信 參與度）
    if 'foreign_buy' in df.columns:
        df['foreign_ratio'] = df['foreign_buy'] / df['volume']
        df['foreign_ratio'] = df['foreign_ratio'].clip(-0.5, 0.5)
    else:
        df['foreign_ratio'] = 0
        
    if 'trust_buy' in df.columns:
        df['trust_ratio'] = df['trust_buy'] / df['volume']
        df['trust_ratio'] = df['trust_ratio'].clip(-0.5, 0.5)
    else:
        df['trust_ratio'] = 0
    
    # 限制極端值（避免異常數據影響模型）
    df['volume_ratio'] = df['volume_ratio'].clip(0, 5)
    
    return df


def add_all_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """
    一次性計算所有技術指標 (便捷函數，用於單一股票)
    
    Args:
        df: 需包含 'close_price', 'high_price', 'low_price' 欄位
        
    Returns:
        加入所有指標欄位的 DataFrame
    """
    df = df.copy()
    
    # 移動平均
    df['ma5'] = df['close_price'].rolling(5).mean()
    df['ma20'] = df['close_price'].rolling(20).mean()
    df['ma60'] = df['close_price'].rolling(60).mean()
    
    # 技術指標
    df['bias'] = calculate_bias(df['close_price'], df['ma20'])
    df['rsi'] = calculate_rsi(df['close_price'])
    df['macd_hist'] = calculate_macd(df['close_price'])
    df['kd_k'] = calculate_kd(df)
    df['bb_width'] = calculate_bb_width(df['close_price'])
    df['atr'] = calculate_atr(df)  # 🆕 V33 Phase 1+: ATR 動態停損
    
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
    df['kd_k'] = df.groupby('stock_id').apply(calculate_kd, include_groups=False).reset_index(level=0, drop=True)
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