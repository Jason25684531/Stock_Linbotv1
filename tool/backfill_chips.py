# tool/backfill_chips.py

import requests
import pandas as pd
from sqlalchemy import create_engine, text
import time
import random
from datetime import datetime, timedelta
import sys
import os

# 加入專案根目錄路徑，確保能 import config 或其他模組
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

# ============================================
# ⚙️ 設定區
# ============================================
DB_URL = "mysql+pymysql://root:my_secret_password@localhost:3306/stock_ai_db"

# 設定你要回補的區間 (例如過去 3 年) #專們補齊三大法人籌碼資料
START_DATE = "2025-01-01" 
END_DATE = datetime.today().strftime("%Y-%m-%d")   # 補到哪一天為止

# ============================================

def get_db_engine():
    return create_engine(DB_URL)

def get_local_price(engine, date_str):
    """
    🟢 [聰明點 1] 從「本地資料庫」讀取當天的股價資料
    不需去證交所重新下載，省時間、省頻寬
    """
    try:
        query = f"SELECT * FROM daily_market_data WHERE trade_date = '{date_str}'"
        df = pd.read_sql(query, engine)
        
        # 如果當天沒資料 (可能那天沒開盤，或資料庫漏了)
        if df.empty:
            return None
            
        # 移除原本可能存在的舊籌碼欄位 (如果有，避免重複)
        cols_to_drop = ['foreign_buy', 'trust_buy', 'foreign_ratio', 'trust_ratio']
        df = df.drop(columns=[c for c in cols_to_drop if c in df.columns])
        
        return df
    except Exception as e:
        print(f"⚠️ 讀取本地股價失敗 {date_str}: {e}")
        return None

def fetch_institutional_investors(date_str):
    """抓取三大法人買賣超 (T86)"""
    url = f"https://www.twse.com.tw/rwd/zh/fund/T86?date={date_str.replace('-','')}&selectType=ALL&response=json"
    try:
        res = requests.get(url)
        data = res.json()
        if data['stat'] != 'OK': return None
        
        df = pd.DataFrame(data['data'], columns=data['fields'])
        
        foreign_col = next((c for c in df.columns if "外陸資" in c and "買賣超股數" in c), None)
        trust_col = next((c for c in df.columns if "投信" in c and "買賣超股數" in c), None)
        
        if not foreign_col or not trust_col: return None
            
        keep_cols = ['證券代號', foreign_col, trust_col]
        df = df[keep_cols]
        df.columns = ['stock_id', 'foreign_buy', 'trust_buy']
        
        for col in ['foreign_buy', 'trust_buy']:
            df[col] = df[col].astype(str).str.replace(',', '')
            df[col] = pd.to_numeric(df[col], errors='coerce').fillna(0)
            
        return df
    except Exception as e:
        return None

def delete_existing_data(engine, date_str):
    """刪除當天舊資料"""
    try:
        with engine.connect() as conn:
            conn.execute(text(f"DELETE FROM daily_market_data WHERE trade_date = '{date_str}'"))
            conn.commit()
    except Exception as e:
        print(f"⚠️ 清除舊資料失敗: {e}")

# ============================================
# 🚀 主程式
# ============================================
if __name__ == "__main__":
    engine = get_db_engine()
    
    start_dt = datetime.strptime(START_DATE, "%Y-%m-%d").date()
    end_dt = datetime.strptime(END_DATE, "%Y-%m-%d").date()
    
    print(f"🔥 開始聰明回補籌碼資料：{start_dt} ~ {end_dt}")
    
    current_dt = start_dt
    while current_dt <= end_dt:
        date_str = current_dt.strftime("%Y-%m-%d")
        print(f"⏳ {date_str}: 讀取本地股價...", end="")
        
        # 1. 從本地讀股價 (不連網)
        price_df = get_local_price(engine, date_str)
        
        if price_df is not None and not price_df.empty:
            print(" 抓取雲端籌碼...", end="")
            
            # 2. 從證交所抓籌碼 (連網)
            chips_df = fetch_institutional_investors(date_str)
            
            # 3. 合併
            if chips_df is not None and not chips_df.empty:
                merged_df = pd.merge(price_df, chips_df, on='stock_id', how='left')
                merged_df['foreign_buy'] = merged_df['foreign_buy'].fillna(0)
                merged_df['trust_buy'] = merged_df['trust_buy'].fillna(0)
                status = "✅ 籌碼已補"
            else:
                # 只有股價沒有籌碼 (可能太久以前或是資料格式不同)
                merged_df = price_df
                merged_df['foreign_buy'] = 0
                merged_df['trust_buy'] = 0
                status = "⚠️ 無籌碼資料"

            # 4. 寫回資料庫
            # 注意：因為 price_df 是讀出來的，trade_date 已經是 datetime 物件，不需要再轉換
            # 但為了保險，我們重新賦值一次確保格式
            merged_df['trade_date'] = current_dt
            
            delete_existing_data(engine, date_str)
            
            try:
                merged_df.to_sql('daily_market_data', engine, if_exists='append', index=False)
                print(f" {status}")
            except Exception as e:
                print(f" ❌ 寫入失敗: {e}")
                
            # 休息一下 (因為我們還是有請求證交所一次)
            time.sleep(random.randint(2, 4))
        else:
            print(" 💤 本地無資料 (跳過)")
            # 本地沒資料不需要 sleep 太久
            
        current_dt += timedelta(days=1)

    print("🎉 全數回補完成！")