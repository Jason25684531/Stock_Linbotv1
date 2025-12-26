import pandas as pd
import numpy as np
from sqlalchemy import create_engine
import os

# ============================================
# ⚙️ 設定區
# ============================================
DB_URL = "mysql+pymysql://root:my_secret_password@localhost:3306/stock_ai_db"
OUTPUT_DIR = os.path.join('ML_Data', 'feature_engineering')
TARGET_THRESHOLD = 0.02 

def get_db_engine():
    return create_engine(DB_URL)

def load_data_from_db():
    print("⏳ 從資料庫讀取數據中...")
    engine = get_db_engine()
    query = "SELECT * FROM daily_market_data ORDER BY stock_id, trade_date"
    df = pd.read_sql(query, engine)
    print(f"✅ 讀取完成！共 {len(df)} 筆數據")
    return df

def process_features(df):
    print("🛠️ 開始特徵工程 (V20 全能版)...")
    
    df['trade_date'] = pd.to_datetime(df['trade_date'])
    
    # 強制轉數值
    numeric_cols = ['open_price', 'high_price', 'low_price', 'close_price', 'volume', 'pe_ratio', 'foreign_buy', 'trust_buy']
    for col in numeric_cols:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors='coerce')
            
    df['foreign_buy'] = df['foreign_buy'].fillna(0)
    df['trust_buy'] = df['trust_buy'].fillna(0)
    df = df.sort_values(['stock_id', 'trade_date']).reset_index(drop=True)
    
    # ========================================
    # 1. 基礎技術指標 (MA, RSI)
    # ========================================
    df['MA5'] = df.groupby('stock_id')['close_price'].transform(lambda x: x.rolling(5).mean())
    df['MA20'] = df.groupby('stock_id')['close_price'].transform(lambda x: x.rolling(20).mean())
    df['MA60'] = df.groupby('stock_id')['close_price'].transform(lambda x: x.rolling(60).mean())
    
    def calculate_rsi(series, period=14):
        delta = series.diff()
        gain = (delta.where(delta > 0, 0)).rolling(period).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(period).mean()
        rs = gain / loss
        return 100 - (100 / (1 + rs))
    df['RSI'] = df.groupby('stock_id')['close_price'].transform(lambda x: calculate_rsi(x))

    # ========================================
    # 2. 進階技術指標 (MACD, KD, Bollinger)
    # ========================================
    
    # MACD
    def calculate_macd(series):
        exp12 = series.ewm(span=12, adjust=False).mean()
        exp26 = series.ewm(span=26, adjust=False).mean()
        macd = exp12 - exp26
        signal = macd.ewm(span=9, adjust=False).mean()
        return macd - signal # MACD Histogram
    df['MACD_hist'] = df.groupby('stock_id')['close_price'].transform(calculate_macd)
    
    # KD (Stochastic Oscillator)
    def calculate_k(df_grp):
        # 避免 rolling window 大於資料長度的警告
        if len(df_grp) < 9: return pd.Series([None]*len(df_grp), index=df_grp.index)
        
        low_min = df_grp['low_price'].rolling(9).min()
        high_max = df_grp['high_price'].rolling(9).max()
        rsv = (df_grp['close_price'] - low_min) / (high_max - low_min) * 100
        return rsv.ewm(com=2, adjust=False).mean() # K value
    
    # 使用 apply 時注意 index 對齊
    try:
        df['KD_K'] = df.groupby('stock_id').apply(calculate_k).reset_index(level=0, drop=True)
    except Exception as e:
        print(f"⚠️ KD計算警告: {e}")
        df['KD_K'] = 50 # 預設值
    
    # 布林通道 (Bollinger Bands)
    def calculate_bb_width(series):
        ma = series.rolling(20).mean()
        std = series.rolling(20).std()
        upper = ma + (2 * std)
        lower = ma - (2 * std)
        return (upper - lower) / ma # Band Width
    df['BB_width'] = df.groupby('stock_id')['close_price'].transform(calculate_bb_width)

    # ========================================
    # 3. 價值與籌碼
    # ========================================
    df['yield_percent'] = df.apply(lambda x: (1/x['pe_ratio'])*100 if x['pe_ratio']>0 else 0, axis=1)
    df['pb_ratio'] = 1.5 
    df['implied_roe'] = df.apply(lambda x: x['pb_ratio']/x['pe_ratio'] if x['pe_ratio']>0 else 0, axis=1)
    
    df['growth_proxy'] = (df['MA5'] - df['MA20']) / df['MA20'] * 100
    df['PEG'] = df.apply(lambda x: x['pe_ratio']/x['growth_proxy'] if x['growth_proxy']>0 else 999, axis=1)

    df['volume_safe'] = df['volume'].replace(0, 1) 
    df['foreign_ratio'] = df['foreign_buy'] / df['volume_safe']
    df['trust_ratio'] = df['trust_buy'] / df['volume_safe']
    df['trust_ma3'] = df.groupby('stock_id')['trust_ratio'].transform(lambda x: x.rolling(3).mean())

    # ========================================
    # 4. Target
    # ========================================
    # Shift -1 代表拿「明天」的漲跌當作今天的答案
    df['return_1d'] = df.groupby('stock_id')['close_price'].transform(lambda x: x.pct_change().shift(-1))
    df['target'] = (df['return_1d'] > TARGET_THRESHOLD).astype(int)
    
    print("✅ V20 特徵工程完成！")
    return df

def save_to_csv(df):
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    
    # 🟢 [關鍵修改] 這裡加入了 'foreign_buy', 'trust_buy'，這樣 CSV 才會存這兩欄！
    final_columns = [
        'trade_date', 'stock_id', 
        'open_price', 'high_price', 'low_price', 'close_price', 'volume',
        'pe_ratio', 'pb_ratio', 'yield_percent', 'implied_roe',
        'MA5', 'MA20', 'MA60', 'RSI',
        'MACD_hist', 'KD_K', 'BB_width',
        'PEG',
        'foreign_ratio', 'trust_ratio', 'trust_ma3',
        'foreign_buy', 'trust_buy', # 🟢 原始籌碼欄位 (給報告顯示用)
        'return_1d', 'target'
    ]
    cols = [c for c in final_columns if c in df.columns]
    
    # 1. 訓練集：嚴格刪除沒有 Target 的資料 (包含最新一天和假日斷點)
    train_path = os.path.join(OUTPUT_DIR, 'training_data.csv')
    train_df = df.dropna(subset=['target', 'return_1d'])
    train_df[cols].to_csv(train_path, index=False)
    print(f"💾 訓練資料已儲存: {train_path} (樣本數: {len(train_df)})")
    
    # 2. 預測集 (Inference)：保留最新一天
    # 只需要技術指標算得出來 (MA60, MACD等不為空) 即可，不需要 Target
    # 這份是給 debug_local.py 和 app.py 用的
    inference_path = os.path.join(OUTPUT_DIR, 'inference_data.csv')
    
    # 只濾除技術指標不足的前 60 天，保留最後一天
    tech_cols = ['MA60', 'MACD_hist'] 
    inference_df = df.dropna(subset=tech_cols)
    inference_df[cols].to_csv(inference_path, index=False)
    
    print(f"💾 預測資料已儲存: {inference_path} (含最新日期: {inference_df['trade_date'].max().date()})")

if __name__ == "__main__":
    raw_df = load_data_from_db()
    if not raw_df.empty:
        processed_df = process_features(raw_df)
        save_to_csv(processed_df)