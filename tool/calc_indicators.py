import pandas as pd
from sqlalchemy import create_engine

# ============================================
# ⚙️ 設定區
# ============================================
DB_URL = "mysql+pymysql://root:my_secret_password@localhost:3306/stock_ai_db"

def fix_database_indicators():
    print("🚑 [計算模組] 正在修復並計算指標 (MA20, MA60, Bias, RSI)...")
    engine = create_engine(DB_URL)
    
    # 1. 把所有資料撈出來
    print("📥 讀取原始資料...")
    try:
        df = pd.read_sql("SELECT * FROM daily_market_data", engine)
    except Exception as e:
        print(f"❌ 資料庫讀取失敗: {e}")
        return

    if df.empty:
        print("⚠️ 資料庫為空，無法計算。")
        return
    
    # ============================================
    # 🧼 強力清洗區
    # ============================================
    print("🧼 正在清洗資料格式...")
    
    numeric_cols = ['open_price', 'high_price', 'low_price', 'close_price', 'volume']
    
    for col in numeric_cols:
        if col in df.columns:
            if df[col].dtype == 'object':
                df[col] = df[col].astype(str).str.replace(',', '').str.replace('---', 'NaN').replace('--', 'NaN')
            df[col] = pd.to_numeric(df[col], errors='coerce')
    
    df = df.dropna(subset=['close_price'])
    
    # ============================================
    
    # 2. 確保日期排序正確
    df['trade_date'] = pd.to_datetime(df['trade_date'])
    df = df.sort_values(['stock_id', 'trade_date'])
    
    # 3. 補算指標
    print("📊 計算技術指標...")
    
    # 🟢 [新增] MA20 (月線) - V26 策略必需品！
    df['ma20'] = df.groupby('stock_id')['close_price'].transform(lambda x: x.rolling(20).mean())

    # MA60 (季線)
    df['ma60'] = df.groupby('stock_id')['close_price'].transform(lambda x: x.rolling(60).mean())
    
    # 乖離率 (Bias)
    df['bias'] = (df['close_price'] - df['ma60']) / df['ma60'] * 100

    # RSI
    def calc_rsi(series, period=14):
        delta = series.diff()
        gain = (delta.where(delta > 0, 0)).rolling(period).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(period).mean()
        rs = gain / loss
        return 100 - (100 / (1 + rs))
    
    df['rsi'] = df.groupby('stock_id')['close_price'].transform(calc_rsi)
    
    # 填補空值
    df = df.fillna(0)
    
    # 4. 寫回資料庫
    print("💾 正在寫入資料庫 (這會覆蓋舊表，請稍候)...")
    try:
        df.to_sql('daily_market_data', engine, if_exists='replace', index=False, chunksize=5000)
        print("✅ 計算完成！MA20 已補齊。")
    except Exception as e:
        print(f"❌ 寫入失敗: {e}")

if __name__ == "__main__":
    fix_database_indicators()