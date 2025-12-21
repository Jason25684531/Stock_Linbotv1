import pandas as pd
import numpy as np
from sqlalchemy import create_engine
from config import Config
import os

# 1. 連線資料庫
engine = create_engine(Config.SQLALCHEMY_DATABASE_URI)

def calculate_technical_indicators(df):
    """計算 V11 策略所需的所有指標"""
    df = df.sort_values('trade_date')
    
    # ----------------------------------------------------
    # A. 基礎整理
    # ----------------------------------------------------
    # 確保數值型態
    cols = ['open_price', 'high_price', 'low_price', 'close_price', 'volume', 'pe_ratio', 'pb_ratio', 'yield_percent']
    for col in cols:
        df[col] = pd.to_numeric(df[col], errors='coerce')
    
    # 填補基本面缺失值 (避免當機)
    df['pe_ratio'] = df['pe_ratio'].fillna(0)
    df['pb_ratio'] = df['pb_ratio'].fillna(0)
    df['yield_percent'] = df['yield_percent'].fillna(0)

    # ----------------------------------------------------
    # B. V11 核心：基本面特徵 (Fundamental)
    # ----------------------------------------------------
    # 1. 隱含 ROE = (P/B) / (P/E)
    # 防呆：如果 PE 是 0 或負的，ROE 就設為 0
    df['implied_roe'] = df.apply(lambda row: (row['pb_ratio'] / row['pe_ratio']) if row['pe_ratio'] > 0 else 0, axis=1)
    
    # ----------------------------------------------------
    # C. 技術指標 (Technical) - 使用 Pandas 實作 (免安裝 TA-Lib)
    # ----------------------------------------------------
    close = df['close_price']
    
    # 1. 移動平均線 (MA)
    df['MA5'] = close.rolling(window=5).mean()
    df['MA20'] = close.rolling(window=20).mean()
    df['MA60'] = close.rolling(window=60).mean()
    
    # 2. 相對強弱指標 (RSI 14)
    delta = close.diff()
    gain = (delta.where(delta > 0, 0)).rolling(window=14).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(window=14).mean()
    rs = gain / loss
    df['RSI'] = 100 - (100 / (1 + rs))
    
        # ==========================================
    # 🟢 [新增] 基本面因子計算 (PEG & 隱含 EPS)
    # ==========================================
    print("📊 正在計算基本面數據 (PEG)...")

    # 1. 反推 EPS (每股盈餘)
    # 邏輯：因為 PE = 股價 / EPS，所以 EPS = 股價 / PE
    # 防呆：如果 PE 是 0 (虧錢) 或 NaN，EPS 就設為 0
    df['implied_eps'] = df.apply(lambda x: x['close_price'] / x['pe_ratio'] if (pd.notnull(x['pe_ratio']) and x['pe_ratio'] > 0) else 0, axis=1)

    # 2. 計算 EPS 成長率 (跟一季前/60天前相比)
    # 我們想找的是「獲利正在加速」的公司
    df['eps_growth_q'] = df.groupby('stock_id')['implied_eps'].pct_change(60, fill_method=None)

    # 3. 計算 PEG (本益成長比)
    # 公式：PE / (EPS成長率 * 100)
    def calculate_peg(row):
        pe = row['pe_ratio']
        growth = row['eps_growth_q']
        
        # 過濾無效數據
        if pd.isna(pe) or pe <= 0: return 999       # 虧錢公司給高分(爛)
        if pd.isna(growth) or growth <= 0.01: return 999 # 沒成長或衰退給高分(爛)
        
        # 正常計算
        return pe / (growth * 100)

    df['PEG'] = df.apply(calculate_peg, axis=1)

    # 4. 去除極端值 (讓數據乖一點，方便 AI 學習)
    # 我們把 PEG 限制在 0 ~ 5 之間，超過 5 當作 5
    df['PEG'] = df['PEG'].clip(0, 5)

    print("✅ PEG 計算完成！")
    
    # 3. 標記未來漲跌 (AI 的標準答案)
    # 如果「明天收盤」比「今天收盤」高，就標記為 1 (漲)，否則 0
    df['Target'] = (df['close_price'].shift(-1) > df['close_price']).astype(int)
    
    # 刪除因為計算指標產生的 NaN (前 60 天會變空值)
    df = df.dropna()
    
    return df

def main():
    print("🚀 Day 2: 開始特徵工程 (V11 ROE 版)...")
    
    # 1. 從資料庫讀取所有資料
    print("📂 從 MySQL 讀取歷史資料 (這可能需要一點時間)...")
    df = pd.read_sql("SELECT * FROM daily_market_data", engine)
    print(f"   ✅ 讀取完成，共 {len(df)} 筆原始數據")

    # 2. 針對每一檔股票單獨計算指標
    # (不能混在一起算，台積電的 MA 不能跟聯電混算)
    result_list = []
    
    # 依照 stock_id 分組
    grouped = df.groupby('stock_id')
    
    total_stocks = len(grouped)
    process_count = 0
    
    print("⚙️  正在計算技術指標與 ROE...")
    for stock_id, group in grouped:
        # 只有資料夠長才算 (少於 60 天算不出 MA60，跳過)
        if len(group) > 60:
            processed_group = calculate_technical_indicators(group.copy())
            result_list.append(processed_group)
        
        process_count += 1
        if process_count % 100 == 0:
            print(f"   已處理 {process_count}/{total_stocks} 檔股票...")

    # 3. 合併結果
    if not result_list:
        print("❌ 錯誤：沒有產生任何資料 (可能是資料庫數據太少)")
        return

    final_df = pd.concat(result_list)
    
    # 4. 選擇要用來訓練的欄位
    features = [
        'stock_id', 'trade_date', 
        'open_price', 'high_price', 'low_price', 'close_price', 'volume', 
        'pe_ratio', 'pb_ratio', 'yield_percent', 'implied_roe',  # V11 新增
        'MA5', 'MA20', 'MA60', 'RSI', 'PEG',
        'Target' # 答案
    ]
    final_df = final_df[features]


    # ==========================================
    # 📂 存檔到 ML_Data/feature_engineering
    # ==========================================
    output_dir = os.path.join('ML_Data', 'feature_engineering')
    os.makedirs(output_dir, exist_ok=True)
    output_file = os.path.join(output_dir, 'training_data.csv')
    
    final_df.to_csv(output_file, index=False)
    
    print(f"✅ 特徵工程完成！檔案已儲存至: {output_file}")
    print(f"📊 總資料筆數: {len(final_df)}")

if __name__ == "__main__":
    main()