import requests
import pandas as pd
from sqlalchemy import create_engine, text
import time
import random
from datetime import datetime, timedelta, date
from config import Config

# ============================================
# ⚙️ 設定區 (統一使用 Config)
# ============================================
DB_URL = Config.SQLALCHEMY_DATABASE_URI

# 偽裝瀏覽器，避免被擋
HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120.0.0.0 Safari/537.36',
    'Referer': 'https://www.tpex.org.tw/',
    'X-Requested-With': 'XMLHttpRequest'
}

# ============================================
# 🛠️ 核心功能：抓取全市場資料
# ============================================

def get_latest_date_from_db(engine):
    """查詢資料庫目前最新的日期"""
    try:
        with engine.connect() as conn:
            result = conn.execute(text("SELECT MAX(trade_date) FROM daily_market_data"))
            latest = result.scalar()
            if isinstance(latest, datetime):
                return latest.date()
            return latest
    except:
        return None

def clean_number(x):
    """清洗數字格式 (去除逗號, 處理 --)"""
    if pd.isna(x): return 0
    s = str(x).replace(',', '').replace('---', '0').replace('--', '0')
    try:
        return float(s)
    except:
        return 0

def fetch_twse_data(date_str):
    """抓取證交所 (上市) 全市場"""
    print("  🔹 正在抓取上市 (TWSE) 資料...", end="")
    clean_date = date_str.replace('-', '')
    
    # 1. 股價 (MI_INDEX)
    price_df = pd.DataFrame()
    try:
        url = f"https://www.twse.com.tw/rwd/zh/afterTrading/MI_INDEX?date={clean_date}&type=ALL&response=json"
        res = requests.get(url, timeout=15) # 官方每日更新通常不用 headers 也能抓，但慢
        data = res.json()
        if data.get('stat') == 'OK':
            # 找「每日收盤行情」表
            target_table = next((t for t in data['tables'] if "每日收盤行情" in t['title']), None)
            if target_table:
                price_df = pd.DataFrame(target_table['data'], columns=target_table['fields'])
                # 欄位映射
                price_df = price_df.rename(columns={
                    '證券代號': 'stock_id', '開盤價': 'open_price', 
                    '最高價': 'high_price', '最低價': 'low_price', 
                    '收盤價': 'close_price', '成交股數': 'volume', '本益比': 'pe_ratio'
                })
                price_df = price_df[['stock_id', 'open_price', 'high_price', 'low_price', 'close_price', 'volume', 'pe_ratio']]
    except: pass

    if price_df.empty: 
        print(" (無資料)")
        return None

    # 2. 籌碼 (T86)
    chips_df = pd.DataFrame()
    try:
        url = f"https://www.twse.com.tw/rwd/zh/fund/T86?date={clean_date}&selectType=ALL&response=json"
        res = requests.get(url, timeout=15)
        data = res.json()
        if data.get('stat') == 'OK':
            df = pd.DataFrame(data['data'], columns=data['fields'])
            # 模糊搜尋欄位
            f_col = next((c for c in df.columns if "外" in c and "買賣超股數" in c), None)
            t_col = next((c for c in df.columns if "投信" in c and "買賣超股數" in c), None)
            
            if f_col and t_col:
                chips_df = df[['證券代號', f_col, t_col]]
                chips_df.columns = ['stock_id', 'foreign_buy', 'trust_buy']
    except: pass

    # 3. 合併
    if not chips_df.empty:
        merged = pd.merge(price_df, chips_df, on='stock_id', how='left')
    else:
        merged = price_df
        merged['foreign_buy'] = 0
        merged['trust_buy'] = 0
        
    print(" ✅")
    return merged

def fetch_tpex_data(date_str):
    """抓取櫃買中心 (上櫃) 全市場 - 含債券ETF"""
    print("  🔹 正在抓取上櫃 (TPEx) 資料...", end="")
    dt = datetime.strptime(date_str, "%Y-%m-%d")
    minguo_date = f"{dt.year - 1911}/{dt.month:02d}/{dt.day:02d}"
    
    price_df_stock = pd.DataFrame()
    price_df_etf = pd.DataFrame()
    
    # 1-A. 上櫃股票
    try:
        url = f"https://www.tpex.org.tw/web/stock/aftertrading/daily_close_quotes/stk_quote_result.php?l=zh-tw&d={minguo_date}&o=json"
        res = requests.get(url, headers=HEADERS, timeout=15)
        data = res.json()
        if data.get('aaData'):
            df = pd.DataFrame(data['aaData'])
            # Index: 0代號, 2收盤, 4開盤, 5最高, 6最低, 8成交量
            price_df_stock = df.iloc[:, [0, 4, 5, 6, 2, 8]].copy()
            price_df_stock.columns = ['stock_id', 'open_price', 'high_price', 'low_price', 'close_price', 'volume']
    except: pass
    
    # 1-B. 上櫃 ETF (關鍵！為了 00679B)
    try:
        url_etf = f"https://www.tpex.org.tw/web/etf/etf_daily_close_quotes/etf_quote_result.php?l=zh-tw&d={minguo_date}&o=json"
        res = requests.get(url_etf, headers=HEADERS, timeout=15)
        data = res.json()
        if data.get('aaData'):
            df = pd.DataFrame(data['aaData'])
            # Index: 0代號, 2收盤, 4開盤, 5最高, 6最低, 7成交量 (ETF少一欄)
            price_df_etf = df.iloc[:, [0, 4, 5, 6, 2, 7]].copy()
            price_df_etf.columns = ['stock_id', 'open_price', 'high_price', 'low_price', 'close_price', 'volume']
    except: pass

    # 合併價格
    price_df = pd.concat([price_df_stock, price_df_etf], ignore_index=True)
    if price_df.empty: 
        print(" (無資料)")
        return None
    price_df['pe_ratio'] = 0.0

    # 2. 籌碼 (se=AL 包含全部)
    chips_df = pd.DataFrame()
    try:
        url = f"https://www.tpex.org.tw/web/stock/3insti/daily_trade/3itrade_hedge_result.php?l=zh-tw&se=AL&t=D&d={minguo_date}&o=json"
        res = requests.get(url, headers=HEADERS, timeout=15)
        data = res.json()
        if data.get('aaData'):
            df = pd.DataFrame(data['aaData'])
            chips_df = df.iloc[:, [0, 10, 13]].copy()
            chips_df.columns = ['stock_id', 'foreign_buy', 'trust_buy']
    except: pass

    # 3. 合併
    if not chips_df.empty:
        # 轉字串對齊
        price_df['stock_id'] = price_df['stock_id'].astype(str)
        chips_df['stock_id'] = chips_df['stock_id'].astype(str)
        merged = pd.merge(price_df, chips_df, on='stock_id', how='left')
    else:
        merged = price_df
        merged['foreign_buy'] = 0
        merged['trust_buy'] = 0
        
    print(" ✅")
    return merged

def process_and_save(df, date_str, engine):
    """清洗並寫入資料庫"""
    if df is None or df.empty: return 0
    
    # 清洗數字
    cols = ['open_price', 'high_price', 'low_price', 'close_price', 'volume', 'pe_ratio', 'foreign_buy', 'trust_buy']
    for col in cols:
        df[col] = df[col].apply(clean_number)
    
    df['trade_date'] = date_str
    
    # 過濾掉沒有收盤價的
    df = df[df['close_price'] > 0]
    
    # 寫入
    try:
        # 為了效能，先刪除當天所有資料 (避免重複)
        with engine.connect() as conn:
            conn.execute(text(f"DELETE FROM daily_market_data WHERE trade_date='{date_str}'"))
            conn.commit()
            
        df.to_sql('daily_market_data', engine, if_exists='append', index=False, chunksize=2000)
        return len(df)
    except Exception as e:
        print(f"❌ 寫入失敗: {e}")
        return 0

# ============================================
# 🚀 主程式
# ============================================
if __name__ == "__main__":
    print(f"🔗 連線資料庫: {DB_URL}")
    engine = create_engine(DB_URL)
    
    latest_date = get_latest_date_from_db(engine)
    if latest_date:
        start_dt = latest_date + timedelta(days=1)
        print(f"📅 資料庫最新: {latest_date}，將從 {start_dt} 開始更新...")
    else:
        start_dt = datetime.strptime("2024-01-01", "%Y-%m-%d").date()
        print(f"⚠️ 資料庫為空，將從 {start_dt} 開始更新...")

    end_dt = datetime.now().date()
    
    if start_dt > end_dt:
        print("✅ 資料庫已是最新，無需更新。")
        exit(0)

    current_dt = start_dt
    while current_dt <= end_dt:
        date_str = current_dt.strftime("%Y-%m-%d")
        print(f"\n📅 正在處理: {date_str}")
        
        twse_data = fetch_twse_data(date_str)
        tpex_data = fetch_tpex_data(date_str)
        
        final_df = pd.DataFrame()
        if twse_data is not None: final_df = pd.concat([final_df, twse_data])
        if tpex_data is not None: final_df = pd.concat([final_df, tpex_data])
        
        count = process_and_save(final_df, date_str, engine)
        if count > 0:
            print(f"💾 成功寫入 {count} 筆資料！")
        else:
            print("💤 假日或無資料")

        current_dt += timedelta(days=1)
        time.sleep(random.randint(3, 5))

    print("\n🎉 每日更新完成！")