import requests
import pandas as pd
from sqlalchemy import create_engine, text
import time
import random
from datetime import datetime, timedelta

# ============================================
# ⚙️ 設定區
# ============================================
DB_URL = "mysql+pymysql://root:my_secret_password@localhost:3306/stock_ai_db"
START_DATE = "2022-01-01"  # 若資料庫全空，從這天開始抓

# ============================================
# 🛠️ 函數區
# ============================================

def get_db_engine():
    return create_engine(DB_URL)

def get_latest_date(engine):
    """查詢資料庫目前最新的日期"""
    try:
        with engine.connect() as conn:
            result = conn.execute(text("SELECT MAX(trade_date) FROM daily_market_data"))
            latest = result.scalar()
            return latest if latest else None
    except:
        return None

def fetch_daily_price(date_str):
    """抓取每日收盤行情 (MI_INDEX)"""
    url = f"https://www.twse.com.tw/rwd/zh/afterTrading/MI_INDEX?date={date_str.replace('-','')}&type=ALL&response=json"
    try:
        res = requests.get(url)
        data = res.json()
        if data['stat'] != 'OK': return None
        
        # 找到股價表 (通常是 tables9，但也可能是其他，依標題判斷)
        target_table = None
        for table in data['tables']:
            if "每日收盤行情" in table['title']:
                target_table = table
                break
        
        if not target_table: return None
        
        df = pd.DataFrame(target_table['data'], columns=target_table['fields'])
        
        # 整理欄位
        df = df[['證券代號', '開盤價', '最高價', '最低價', '收盤價', '成交股數', '本益比']]
        df.columns = ['stock_id', 'open_price', 'high_price', 'low_price', 'close_price', 'volume', 'pe_ratio']
        
        # 數值處理 (移除逗號，處理 --)
        for col in ['open_price', 'high_price', 'low_price', 'close_price', 'volume', 'pe_ratio']:
            df[col] = df[col].astype(str).str.replace(',', '').replace('--', '0')
            df[col] = pd.to_numeric(df[col], errors='coerce').fillna(0)
            
        return df
    except Exception as e:
        print(f"❌ 抓取股價失敗 {date_str}: {e}")
        return None

def fetch_institutional_investors(date_str):
    """
    🟢 [新增] 抓取三大法人買賣超 (T86)
    抓取 外資(Foreign) 與 投信(Trust) 的買賣超股數
    """
    url = f"https://www.twse.com.tw/rwd/zh/fund/T86?date={date_str.replace('-','')}&selectType=ALL&response=json"
    try:
        res = requests.get(url)
        data = res.json()
        if data['stat'] != 'OK': return None
        
        df = pd.DataFrame(data['data'], columns=data['fields'])
        
        # 確保欄位名稱正確 (證交所欄位名稱可能會變，這裡用關鍵字找)
        # 通常欄位：證券代號, ..., 外陸資買賣超股數(不含外資自營商), ..., 投信買賣超股數
        
        # 1. 找外資欄位 (包含 "外陸資" 和 "買賣超股數")
        foreign_col = next((c for c in df.columns if "外陸資" in c and "買賣超股數" in c), None)
        # 2. 找投信欄位
        trust_col = next((c for c in df.columns if "投信" in c and "買賣超股數" in c), None)
        
        if not foreign_col or not trust_col:
            return None
            
        keep_cols = ['證券代號', foreign_col, trust_col]
        df = df[keep_cols]
        df.columns = ['stock_id', 'foreign_buy', 'trust_buy']
        
        # 數值處理
        for col in ['foreign_buy', 'trust_buy']:
            df[col] = df[col].astype(str).str.replace(',', '')
            df[col] = pd.to_numeric(df[col], errors='coerce').fillna(0)
            
        return df
    except Exception as e:
        print(f"❌ 抓取籌碼失敗 {date_str}: {e}")
        return None

# ============================================
# 🚀 主程式
# ============================================
if __name__ == "__main__":
    engine = get_db_engine()
    
    # 決定開始日期
    latest_db_date = get_latest_date(engine)
    if latest_db_date:
        start_dt = latest_db_date + timedelta(days=1)
        print(f"📅 資料庫最新日期: {latest_db_date}，將從 {start_dt} 開始更新...")
    else:
        start_dt = datetime.strptime(START_DATE, "%Y-%m-%d").date()
        print(f"⚠️ 資料庫為空，將從 {start_dt} 開始更新...")

    end_dt = datetime.today().date()
    
    current_dt = start_dt
    while current_dt <= end_dt:
        date_str = current_dt.strftime("%Y-%m-%d")
        print(f"⏳ 正在處理: {date_str} ...", end="")
        
        # 1. 抓股價
        price_df = fetch_daily_price(date_str)
        
        if price_df is not None and not price_df.empty:
            # 2. 抓籌碼 (🟢 新增步驟)
            chips_df = fetch_institutional_investors(date_str)
            
            # 3. 合併資料 (Left Join: 以股價表為主)
            if chips_df is not None and not chips_df.empty:
                merged_df = pd.merge(price_df, chips_df, on='stock_id', how='left')
                # 沒抓到籌碼的補 0
                merged_df['foreign_buy'] = merged_df['foreign_buy'].fillna(0)
                merged_df['trust_buy'] = merged_df['trust_buy'].fillna(0)
            else:
                # 當天可能只有行情沒有籌碼資料 (罕見)
                merged_df = price_df
                merged_df['foreign_buy'] = 0
                merged_df['trust_buy'] = 0

            # 4. 寫入資料庫
            merged_df['trade_date'] = current_dt
            
            # 確保欄位順序與資料庫一致 (或使用 pandas to_sql 的彈性)
            # 這裡簡單處理，直接存
            try:
                merged_df.to_sql('daily_market_data', engine, if_exists='append', index=False)
                print(" ✅ 成功寫入 (含籌碼)！")
            except Exception as e:
                print(f" ❌ 寫入失敗: {e}")
        else:
            print(" 💤 假日或無資料")
            
        current_dt += timedelta(days=1)
        time.sleep(random.randint(3, 6)) # 稍微休息避免被擋

    print("🎉 資料庫更新完畢！")