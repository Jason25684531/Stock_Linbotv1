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
import sys
import os

# 加入專案根目錄以便 import config
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import Config

# ============================================
# Multi-factor Z-Score matrix constants
# ============================================

MULTI_FACTOR_COLUMNS = [
    'rsi',
    'bias',
    'macd_hist',
    'kd_k',
    'bb_width',
    'volume_ratio',
    'natr',
    'std_20',
    'foreign_ratio',
    'trust_ratio',
    'dealer_ratio',
    'foreign_consec_days',
    'trust_consec_days',
    'dealer_consec_days',
    'institutional_net_buy',
    'institutional_consec_buy_days',
    'chip_score',
    'large_holder_ratio',
    'large_holder_ratio_change',
    'news_sentiment_score',
    'revenue_yoy',
    'rd_ratio',
    'op_profit_margin',
    'eps',
]

Z_SCORE_FEATURE_COLUMNS = [f'{column}_z' for column in MULTI_FACTOR_COLUMNS]

LARGE_HOLDER_RATIO_ALIASES = [
    'large_holder_ratio',
    'large_holder_holding_ratio',
    'holder_400_ratio',
    'holder_400_holding_ratio',
    'holding_400_ratio',
    'major_holder_ratio',
    'major_holder_holding_ratio',
    'big_holder_ratio',
]

LARGE_HOLDER_CHANGE_ALIASES = [
    'large_holder_ratio_change',
    'large_holder_holding_ratio_change',
    'holder_400_ratio_change',
    'holder_400_holding_ratio_change',
    'holding_400_ratio_change',
    'major_holder_ratio_change',
    'big_holder_ratio_change',
]

# ============================================
# ⚙️ 設定區
# ============================================
# 注意：請使用 core.db_helper.get_db_engine() 取得資料庫連接


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


def calculate_natr(df: pd.DataFrame, period: Optional[int] = None) -> pd.Series:
    """
    計算 NATR (Normalized ATR) - 標準化真實波幅
    
    🆕 V33 Phase 1: NATR = (ATR / Close) * 100
    用於 V33 Low Volatility 策略的波動度篩選
    
    Args:
        df: 需包含 'high_price', 'low_price', 'close_price' 欄位
        period: 計算週期 (預設從 Config 讀取)
    
    Returns:
        NATR 百分比序列 (通常 0~10%)
    """
    atr = calculate_atr(df, period)
    close = df['close_price']
    
    # NATR = (ATR / Close) * 100
    natr = (atr / close) * 100
    
    # 避免極端值
    return natr.fillna(0).clip(0, 50)


def calculate_std_20(series: pd.Series) -> pd.Series:
    """
    計算 20 日標準差 (Standard Deviation)
    
    🆕 V33 Phase 1: 用於低波動策略
    衡量價格波動的離散程度
    
    Args:
        series: 收盤價序列
    
    Returns:
        20 日標準差序列
    """
    return series.rolling(window=20, min_periods=10).std().fillna(0)


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
    
    🔄 V33 Refactor: 從 jobs/train_model.py 相依邏輯抽出供共用
    
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


# ============================================
# 📊 籌碼面進階指標 (Phase 2)
# ============================================

def encode_news_sentiment(sentiment) -> int:
    """Map daily news sentiment to a neutral-safe numeric factor."""
    if sentiment is None:
        return 0

    if isinstance(sentiment, dict):
        sentiment = sentiment.get('sentiment')

    text = str(sentiment).strip().lower()
    if not text:
        return 0

    bullish_values = {
        'bullish', 'positive', 'beneficial', 'upbeat', '1', '+1',
        '偏多', '利多', '看多',
    }
    neutral_values = {'neutral', 'flat', 'mixed', '0', '中性', '普通'}
    bearish_values = {
        'bearish', 'negative', 'risk', '-1',
        '偏空', '利空', '看空',
    }

    if text in bullish_values:
        return 1
    if text in bearish_values:
        return -1
    if text in neutral_values:
        return 0
    return 0


def _numeric_series(df: pd.DataFrame, column: str, default: float = 0.0) -> pd.Series:
    if column not in df.columns:
        return pd.Series(default, index=df.index, dtype='float64')
    values = pd.to_numeric(df[column], errors='coerce')
    values = values.replace([np.inf, -np.inf], np.nan)
    return values.fillna(default).astype('float64')


def _first_numeric_alias(df: pd.DataFrame, aliases: list[str]) -> pd.Series:
    for column in aliases:
        if column in df.columns:
            return _numeric_series(df, column)
    return pd.Series(0.0, index=df.index, dtype='float64')


def calculate_cross_sectional_zscore(
    df: pd.DataFrame,
    columns: list[str],
    date_col: str = 'trade_date',
) -> pd.DataFrame:
    """Add same-date cross-sectional Z-Score columns with neutral fallback."""
    result = df.copy()
    if result.empty:
        for column in columns:
            result[f'{column}_z'] = pd.Series(dtype='float64')
        return result

    group_key = date_col if date_col in result.columns else None

    for column in columns:
        z_col = f'{column}_z'
        numeric = (
            pd.to_numeric(result[column], errors='coerce')
            if column in result.columns
            else pd.Series(np.nan, index=result.index)
        )
        numeric = numeric.replace([np.inf, -np.inf], np.nan)
        result[column] = numeric
        result[z_col] = 0.0

        grouped = result.groupby(group_key, dropna=False).groups if group_key else {None: result.index}
        for _, index in grouped.items():
            values = numeric.loc[index]
            valid = values.dropna()
            if valid.empty:
                continue
            std = float(valid.std(ddof=0))
            if not np.isfinite(std) or std == 0:
                continue
            mean = float(valid.mean())
            result.loc[index, z_col] = ((values - mean) / std).fillna(0.0)

        result[z_col] = (
            pd.to_numeric(result[z_col], errors='coerce')
            .replace([np.inf, -np.inf], np.nan)
            .fillna(0.0)
        )
        result[column] = result[column].fillna(0.0)

    return result


def build_multi_factor_matrix(
    df: pd.DataFrame,
    trade_date=None,
    news_sentiment=None,
) -> pd.DataFrame:
    """Build raw multi-factor inputs and canonical Z-Score model features."""
    result = df.copy()
    if result.empty:
        for column in MULTI_FACTOR_COLUMNS:
            result[column] = pd.Series(dtype='float64')
        return calculate_cross_sectional_zscore(result, MULTI_FACTOR_COLUMNS)

    if 'trade_date' not in result.columns and trade_date is not None:
        result['trade_date'] = trade_date

    result['volume'] = _numeric_series(result, 'volume', default=1.0).replace(0, 1)
    if 'volume_ratio' not in result.columns and 'stock_id' in result.columns:
        result = calculate_ratio_features(result)
    elif 'volume_ratio' not in result.columns:
        result['volume_ratio'] = 1.0

    for column in ['foreign_buy', 'trust_buy', 'dealer_buy']:
        result[column] = _numeric_series(result, column)

    vol_safe = _numeric_series(result, 'volume', default=1.0).replace(0, 1)
    result['foreign_ratio'] = (
        _numeric_series(result, 'foreign_ratio')
        if 'foreign_ratio' in result.columns
        else (result['foreign_buy'] / vol_safe).clip(-0.5, 0.5)
    )
    result['trust_ratio'] = (
        _numeric_series(result, 'trust_ratio')
        if 'trust_ratio' in result.columns
        else (result['trust_buy'] / vol_safe).clip(-0.5, 0.5)
    )
    result['dealer_ratio'] = (
        _numeric_series(result, 'dealer_ratio')
        if 'dealer_ratio' in result.columns
        else (result['dealer_buy'] / vol_safe).clip(-0.5, 0.5)
    )

    for buy_col, consec_col in [
        ('foreign_buy', 'foreign_consec_days'),
        ('trust_buy', 'trust_consec_days'),
        ('dealer_buy', 'dealer_consec_days'),
    ]:
        if consec_col not in result.columns:
            if 'stock_id' in result.columns and 'trade_date' in result.columns:
                ordered = result.sort_values(['stock_id', 'trade_date'])
                result.loc[ordered.index, consec_col] = (
                    ordered.groupby('stock_id')[buy_col].transform(calculate_consec_days)
                )
            else:
                result[consec_col] = (result[buy_col] > 0).astype(int)
        result[consec_col] = _numeric_series(result, consec_col)

    result['institutional_net_buy'] = result['foreign_buy'] + result['trust_buy'] + result['dealer_buy']
    result['institutional_consec_buy_days'] = (
        result['foreign_consec_days'] + result['trust_consec_days'] + result['dealer_consec_days']
    )
    result.loc[result['institutional_net_buy'] <= 0, 'institutional_consec_buy_days'] = 0
    result['large_holder_ratio'] = _first_numeric_alias(result, LARGE_HOLDER_RATIO_ALIASES)
    result['large_holder_ratio_change'] = _first_numeric_alias(result, LARGE_HOLDER_CHANGE_ALIASES)
    result['news_sentiment_score'] = float(encode_news_sentiment(news_sentiment))

    for column in MULTI_FACTOR_COLUMNS:
        if column not in result.columns:
            result[column] = 0.0
        result[column] = _numeric_series(result, column)

    return calculate_cross_sectional_zscore(result, MULTI_FACTOR_COLUMNS)


def calculate_dealer_ratio(df: pd.DataFrame) -> pd.Series:
    """
    計算自營商買超佔成交量比例

    Args:
        df: 需包含 dealer_buy, volume 欄位

    Returns:
        dealer_ratio 序列 (-0.5 ~ 0.5)
    """
    if 'dealer_buy' not in df.columns:
        return pd.Series(0, index=df.index)

    vol = df['volume'].replace(0, 1)
    ratio = df['dealer_buy'] / vol
    return ratio.clip(-0.5, 0.5)


def calculate_consec_days(series: pd.Series) -> pd.Series:
    """
    計算連續正值天數（用於外資/投信/自營商連買天數）

    邏輯：
      - 值 > 0 時連續累計 +1
      - 值 <= 0 時重置為 0

    Args:
        series: 買賣超序列（已按時間排序）

    Returns:
        連續正值天數序列
    """
    positive = (series > 0).astype(int)
    groups = (positive != positive.shift()).cumsum()
    consec = positive.groupby(groups).cumsum()
    return consec


def calculate_margin_change_pct(series: pd.Series) -> pd.Series:
    """
    計算融資餘額日變動率 (%)

    公式: (今日餘額 - 昨日餘額) / 昨日餘額 * 100

    Args:
        series: 融資餘額序列（已按時間排序）

    Returns:
        變動率序列 (%)
    """
    prev = series.shift(1).replace(0, float('nan'))
    pct = (series - series.shift(1)) / prev * 100
    return pct.fillna(0).clip(-50, 50)


def calculate_chip_score(df: pd.DataFrame) -> pd.Series:
    """
    計算籌碼綜合分數 (0~100)

    根據 Config 中定義的權重，綜合：
    - 外資買超信號 (foreign_ratio 標準化)
    - 投信買超信號 (trust_ratio 標準化)
    - 自營商買超信號 (dealer_ratio 標準化)
    - 融資減少信號 (margin_change_pct < 0 為正面)

    Args:
        df: 需包含 foreign_ratio, trust_ratio, dealer_ratio,
            margin_change_pct 欄位（或使用 0 兜底）

    Returns:
        chip_score 序列 (0~100)
    """
    w_f = Config.CHIP_WEIGHT_FOREIGN
    w_t = Config.CHIP_WEIGHT_TRUST
    w_d = Config.CHIP_WEIGHT_DEALER
    w_m = Config.CHIP_WEIGHT_MARGIN

    def _norm_ratio(s: pd.Series) -> pd.Series:
        """將 ratio (-0.5~0.5) 映射至 (0~100)"""
        return ((s + 0.5) / 1.0 * 100).clip(0, 100)

    # 各分量分數（0~100）
    f_score = _norm_ratio(df.get('foreign_ratio', pd.Series(0, index=df.index)))
    t_score = _norm_ratio(df.get('trust_ratio', pd.Series(0, index=df.index)))
    d_score = _norm_ratio(df.get('dealer_ratio', pd.Series(0, index=df.index)))

    # 融資信號：融資減少（margin_change_pct < 0）→ 正面信號
    margin_pct = df.get('margin_change_pct', pd.Series(0, index=df.index))
    m_score = ((-margin_pct).clip(-50, 50) + 50) / 100 * 100  # 映射 -50~50 → 0~100

    score = (w_f * f_score + w_t * t_score + w_d * d_score + w_m * m_score)
    return score.clip(0, 100).round(2)


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
    df['natr'] = calculate_natr(df)  # 🆕 V33 Phase 1: 標準化波動度
    df['std_20'] = calculate_std_20(df['close_price'])  # 🆕 V33 Phase 1: 20日標準差
    
    # 🆕 Phase 2: 籌碼面指標
    df['dealer_ratio'] = calculate_dealer_ratio(df)
    
    return df


# ============================================
# 🗄️ 資料庫批次更新功能
# ============================================

def fix_database_indicators():
    """計算全市場技術指標並寫回資料庫"""
    print("🚑 [AI工程] 正在計算全套技術指標 (MA, RSI, MACD, KD, BB, NATR, STD_20)...")
    from core.db_helper import get_db_engine
    engine = get_db_engine()
    
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
    
    # 🆕 V33 Phase 1: NATR 與 STD_20
    print("📊 計算 V33 新指標 (NATR, STD_20)...")
    df['natr'] = df.groupby('stock_id').apply(
        lambda x: calculate_natr(x)
    ).reset_index(level=0, drop=True)
    
    df['std_20'] = df.groupby('stock_id')['close_price'].transform(calculate_std_20)

    # 🆕 Phase 2: 籌碼面進階指標
    print("📊 計算籌碼面指標 (dealer_ratio, consec_days, margin_change, chip_score)...")

    # 自營商比例
    vol_safe = df['volume'].replace(0, 1)
    if 'dealer_buy' in df.columns:
        df['dealer_ratio'] = (df['dealer_buy'] / vol_safe).clip(-0.5, 0.5)
    else:
        df['dealer_ratio'] = 0

    # 外資/投信/自營商比例（用於 chip_score）
    if 'foreign_buy' in df.columns:
        df['foreign_ratio'] = (df['foreign_buy'] / vol_safe).clip(-0.5, 0.5)
    else:
        df['foreign_ratio'] = 0

    if 'trust_buy' in df.columns:
        df['trust_ratio'] = (df['trust_buy'] / vol_safe).clip(-0.5, 0.5)
    else:
        df['trust_ratio'] = 0

    # 連續買超天數（每支股票獨立計算）
    if 'foreign_buy' in df.columns:
        df['foreign_consec_days'] = df.groupby('stock_id')['foreign_buy'].transform(calculate_consec_days)
    else:
        df['foreign_consec_days'] = 0

    if 'trust_buy' in df.columns:
        df['trust_consec_days'] = df.groupby('stock_id')['trust_buy'].transform(calculate_consec_days)
    else:
        df['trust_consec_days'] = 0

    # 融資餘額日變動率（%）
    if 'margin_balance' in df.columns:
        df['margin_change_pct'] = df.groupby('stock_id')['margin_balance'].transform(calculate_margin_change_pct)
    else:
        df['margin_change_pct'] = 0

    # 籌碼綜合分數
    df['chip_score'] = calculate_chip_score(df)

    df = df.fillna(0)
    
    # 3. 安全寫回資料庫（使用 batch UPDATE，不 DROP 表）
    print("💾 正在升級資料庫 (批次更新指標欄位)...")
    from core.db_helper import get_db_engine as _get_engine, ensure_indicator_columns
    from sqlalchemy import text as _text
    
    # 確保欄位存在（自動 ALTER TABLE ADD COLUMN）
    indicator_cols = [
        'ma5', 'ma20', 'ma60', 'bias', 'macd_hist', 'kd_k', 'bb_width', 'rsi', 'natr', 'std_20',
        # Phase 2: 籌碼面指標
        'dealer_ratio', 'foreign_ratio', 'trust_ratio',
        'foreign_consec_days', 'trust_consec_days',
        'margin_change_pct', 'chip_score',
    ]
    ensure_indicator_columns(indicator_cols)
    
    # 批次 UPDATE
    update_sql = _text("""
        UPDATE daily_market_data
        SET ma5 = :ma5, ma20 = :ma20, ma60 = :ma60,
            bias = :bias, macd_hist = :macd_hist, kd_k = :kd_k,
            bb_width = :bb_width, rsi = :rsi, natr = :natr, std_20 = :std_20,
            dealer_ratio = :dealer_ratio, foreign_ratio = :foreign_ratio,
            trust_ratio = :trust_ratio,
            foreign_consec_days = :foreign_consec_days,
            trust_consec_days = :trust_consec_days,
            margin_change_pct = :margin_change_pct, chip_score = :chip_score
        WHERE stock_id = :stock_id AND trade_date = :trade_date
    """)
    
    batch_size = 5000
    total_updated = 0
    with engine.connect() as conn:
        batch = []
        for _, row in df.iterrows():
            batch.append({
                'stock_id': row['stock_id'],
                'trade_date': row['trade_date'],
                'ma5': float(row.get('ma5', 0)),
                'ma20': float(row.get('ma20', 0)),
                'ma60': float(row.get('ma60', 0)),
                'bias': float(row.get('bias', 0)),
                'macd_hist': float(row.get('macd_hist', 0)),
                'kd_k': float(row.get('kd_k', 0)),
                'bb_width': float(row.get('bb_width', 0)),
                'rsi': float(row.get('rsi', 0)),
                'natr': float(row.get('natr', 0)),
                'std_20': float(row.get('std_20', 0)),
                'dealer_ratio': float(row.get('dealer_ratio', 0)),
                'foreign_ratio': float(row.get('foreign_ratio', 0)),
                'trust_ratio': float(row.get('trust_ratio', 0)),
                'foreign_consec_days': float(row.get('foreign_consec_days', 0)),
                'trust_consec_days': float(row.get('trust_consec_days', 0)),
                'margin_change_pct': float(row.get('margin_change_pct', 0)),
                'chip_score': float(row.get('chip_score', 0)),
            })
            if len(batch) >= batch_size:
                conn.execute(update_sql, batch)
                conn.commit()
                total_updated += len(batch)
                print(f"   進度: {total_updated:,} / {len(df):,}")
                batch = []
        if batch:
            conn.execute(update_sql, batch)
            conn.commit()
            total_updated += len(batch)
    
    print(f"✅ 資料庫升級完成！更新 {total_updated:,} 筆（含 MACD, KD, NATR, STD_20）")

if __name__ == "__main__":
    fix_database_indicators()
