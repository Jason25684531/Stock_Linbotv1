import pandas as pd
import numpy as np
from sqlalchemy import create_engine
from config import Config
import talib
from tqdm import tqdm
import warnings
import os  # ✨ 新增這行

# 忽略警告
warnings.filterwarnings('ignore')

# --- 設定參數 ---
PREDICT_DAYS = 20       # 預測未來 20 天
TARGET_RETURN = 0.10    # 目標漲幅 10%

def calculate_technical_indicators(df):
    """計算技術指標 (含 V11 所需的 ROE 與基本面處理)"""
    # 確保是數值
    close = df['close_price']
    high = df['high_price']
    low = df['low_price']
    volume = df['volume']
    
    # --- 技術面 ---
    # 1. 均線
    df['MA5'] = talib.SMA(close, timeperiod=5)
    df['MA20'] = talib.SMA(close, timeperiod=20)
    df['MA60'] = talib.SMA(close, timeperiod=60)
    
    # 2. 動能
    df['RSI'] = talib.RSI(close, timeperiod=14)
    df['MACD'], df['MACD_signal'], _ = talib.MACD(close)
    
    # 3. KD 指標
    df['slowk'], df['slowd'] = talib.STOCH(
        high, low, close,
        fastk_period=9, slowk_period=3, slowk_matype=0,
        slowd_period=3, slowd_matype=0
    )
    df['KD_diff'] = df['slowk'] - df['slowd']
    
    # 4. 布林通道
    df['upper'], df['middle'], df['lower'] = talib.BBANDS(close, timeperiod=20)
    df['BB_width'] = (df['upper'] - df['lower']) / df['middle']
    
    # 5. 乖離率
    df['Bias_20'] = (close - df['MA20']) / df['MA20']

    # 6. 成交量與 ATR
    df['vol_ma5'] = talib.SMA(volume, timeperiod=5)
    df['vol_ratio'] = volume / df['vol_ma5'].replace(0, 1)

    df['ATR'] = talib.ATR(high, low, close, timeperiod=14)
    df['ATR_pct'] = df['ATR'] / close 

    # --- ✨ V11 新增：基本面計算 ---
    # 確保欄位存在且為數值
    if 'pe_ratio' in df.columns and 'pb_ratio' in df.columns:
        # 隱含 ROE = PB / PE (防止除以0)
        df['implied_roe'] = df.apply(
            lambda row: (row['pb_ratio'] / row['pe_ratio']) 
            if (pd.notnull(row['pe_ratio']) and row['pe_ratio'] > 0) else 0, 
            axis=1
        )
    else:
        df['implied_roe'] = 0
    
    return df

def calculate_chip_factors(df):
    """計算籌碼因子"""
    df['trust_buy_vol'] = df['trust_buy_vol'].fillna(0)
    is_trust_buy = (df['trust_buy_vol'] > 0).astype(int)
    grouper = (is_trust_buy != is_trust_buy.shift()).cumsum()
    df['trust_streak'] = is_trust_buy.groupby(grouper).cumsum()
    
    total_buy = df['foreign_buy_vol'] + df['trust_buy_vol'] + df['dealer_buy_vol']
    vol = df['volume'].replace(0, 1) 
    df['institutions_ratio'] = total_buy / vol
    
    df['foreign_5d_sum'] = df['foreign_buy_vol'].rolling(5).sum()
    return df

def define_label(df):
    """定義學習目標"""
    indexer = pd.api.indexers.FixedForwardWindowIndexer(window_size=PREDICT_DAYS)
    future_high = df['high_price'].rolling(window=indexer).max()
    
    df['future_max_return'] = (future_high / df['close_price']) - 1
    df['Target'] = (df['future_max_return'] >= TARGET_RETURN).astype(int)
    return df

def main():
    print("🔌 連接資料庫...")
    engine = create_engine(Config.SQLALCHEMY_DATABASE_URI)
    
    print("📥 讀取資料庫中 (含基本面資料)...")
    # ✨ 這裡要選取新欄位
    sql = """
    SELECT trade_date, stock_id, 
           open_price, high_price, low_price, close_price, volume,
           foreign_buy_vol, trust_buy_vol, dealer_buy_vol,
           pe_ratio, pb_ratio, yield_percent
    FROM daily_market_data
    """
    try:
        df_all = pd.read_sql(sql, engine)
    except Exception as e:
        print(f"❌ 資料庫讀取失敗: {e}")
        return

    if df_all.empty:
        print("❌ 資料庫是空的！請先執行 Day 1 爬蟲。")
        return

    # 強制轉數值
    numeric_cols = [
        'open_price', 'high_price', 'low_price', 'close_price', 'volume',
        'foreign_buy_vol', 'trust_buy_vol', 'dealer_buy_vol',
        'pe_ratio', 'pb_ratio', 'yield_percent'
    ]
    for col in numeric_cols:
        if col in df_all.columns:
            df_all[col] = pd.to_numeric(df_all[col], errors='coerce')
    
    df_all.fillna(0, inplace=True) 
    df_all['trade_date'] = pd.to_datetime(df_all['trade_date'])
    
    print("🚀 開始特徵工程計算 (V11)...")
    result_list = []
    
    groups = df_all.groupby('stock_id')
    
    for stock_id, group in tqdm(groups):
        group = group.sort_values('trade_date')
        if len(group) < 60: continue
            
        try:
            group = calculate_technical_indicators(group)
            group = calculate_chip_factors(group)
            group = define_label(group)
            
            # 刪除無法計算指標的列
            group = group.dropna(subset=['MA60', 'RSI', 'ATR_pct'])
            result_list.append(group)
        except Exception as e:
            continue

    if not result_list:
        print("❌ 計算後沒有剩餘資料。")
        return

    final_df = pd.concat(result_list)
    
    # 存檔清單 (加入基本面與 ROE)
    features = [
        'trade_date', 'stock_id', 'close_price', 
        'MA5', 'MA20', 'MA60', 'RSI', 'MACD', 'BB_width', 'Bias_20',
        'trust_streak', 'institutions_ratio', 'foreign_5d_sum',
        'slowk', 'KD_diff', 'vol_ratio', 'ATR_pct',
        'pe_ratio', 'pb_ratio', 'yield_percent', 'implied_roe', # ✨ 新增
        'Target'
    ]
    
    available_cols = [c for c in features if c in final_df.columns]
    final_df = final_df[available_cols]
    
    # ==========================================
    # 📂 修改存檔路徑：Root / ML_Data / feature_engineering
    # ==========================================
    
    # 1. 定義資料夾路徑
    output_dir = os.path.join('ML_Data', 'feature_engineering')
    
    # 2. 如果資料夾不存在，就自動建立 (包含父資料夾)
    os.makedirs(output_dir, exist_ok=True)
    
    # 3. 組合完整的檔案路徑
    output_file = os.path.join(output_dir, 'training_data.csv')
    
    # 4. 存檔
    final_df.to_csv(output_file, index=False)
    
    print(f"✅ V11 訓練檔已產出: {output_file}")
    print(f"✨ 包含新特徵: implied_roe, pe_ratio, pb_ratio")

if __name__ == "__main__":
    main()