import pandas as pd
from sqlalchemy import create_engine, text
from crawler.twse_fetcher import TwseFetcher
from config import Config
from datetime import datetime, timedelta, date
import time

# 連線資料庫
engine = create_engine(Config.SQLALCHEMY_DATABASE_URI)

def save_to_db(df):
    """將資料寫入資料庫"""
    # 對應資料庫欄位
    rename_map = {
        'open': 'open_price', 
        'high': 'high_price', 
        'low': 'low_price', 
        'close': 'close_price'
    }
    df = df.rename(columns=rename_map)
    
    # 確保只寫入存在的欄位
    # 注意：這裡假設你已經執行過 ALTER TABLE 新增 pe_ratio 等欄位
    
    try:
        # 寫入資料庫 (append模式)
        df.to_sql('daily_market_data', con=engine, if_exists='append', index=False, chunksize=1000)
        print(f"   ✅ 成功寫入 {len(df)} 筆資料！")
    except Exception as e:
        if "Duplicate" in str(e):
            print("   ⚠️ 資料已存在 (Duplicate)，略過。")
        else:
            print(f"   ❌ 資料庫錯誤: {e}")

def get_all_existing_dates():
    """
    [補洞核心] 查詢資料庫中「所有」已經存在的日期
    回傳一個 set (集合)，方便快速比對
    """
    sql = text("SELECT DISTINCT trade_date FROM daily_market_data")
    existing_dates = set()
    
    try:
        with engine.connect() as conn:
            result = conn.execute(sql)
            for row in result:
                # 確保轉成 date 物件 (有些驅動回傳 datetime, 有些是 date)
                db_date = row[0]
                if isinstance(db_date, datetime):
                    existing_dates.add(db_date.date())
                else:
                    existing_dates.add(db_date)
        
        print(f"📊 資料庫目前已有 {len(existing_dates)} 個交易日的資料。")
        return existing_dates
    except Exception as e:
        print(f"查詢現有日期失敗 (可能是空表): {e}")
        return set()

def main():
    fetcher = TwseFetcher()
    
    # ==========================================
    # 🎯 設定回測起始點：這裡強制指定從 2022 年開始
    # ==========================================
    START_DATE = datetime(2025, 1, 1)
    END_DATE = datetime.now() # 到今天為止
    
    print(f"🚀 啟動「補洞模式」爬蟲...")
    print(f"📅 目標區間: {START_DATE.strftime('%Y-%m-%d')} ~ {END_DATE.strftime('%Y-%m-%d')}")
    
    # 1. 取得所有已存在的日期 (為了跳過不用爬的)
    existing_dates = get_all_existing_dates()

    # 2. 開始迴圈
    current = START_DATE
    
    # 連續失敗計數器 (如果連續失敗太多天，可能被鎖 IP，休息久一點)
    fail_count = 0 

    while current <= END_DATE:
        current_date_obj = current.date()
        date_str = current.strftime('%Y%m%d')
        
        # A. 週末判斷
        if current.weekday() in [5, 6]:
            # print(f"😴 {date_str} 是週末，跳過。") # 太吵可以註解掉
            current += timedelta(days=1)
            continue
            
        # B. [關鍵] 檢查資料庫是否已經有這天？
        if current_date_obj in existing_dates:
            print(f"⏩ {date_str} 資料庫已有，跳過 (Skip)。")
            current += timedelta(days=1)
            continue

        # C. 真的缺資料，開始爬蟲
        print(f"🔍 發現缺漏: {date_str}，開始爬取...")
        df = fetcher.fetch_daily_data(date_str)
        
        if df is not None and not df.empty:
            save_to_db(df)
            fail_count = 0 # 重置失敗計數
            
            # 爬蟲禮儀：休息 5~10 秒
            sleep_time = 5 
            print(f"--- 休息 {sleep_time} 秒 ---")
            time.sleep(sleep_time)
        else:
            # 可能是假日、颱風假、或被擋
            # 判斷是否為「無資料」還是「被擋」通常看 fetcher 內部的 print
            fail_count += 1
            if fail_count >= 5:
                print("⚠️ 連續失敗 5 次，可能被證交所暫時限制，強制休息 60 秒...")
                time.sleep(60)
                fail_count = 0
            else:
                time.sleep(3) 
            
        current += timedelta(days=1)

if __name__ == "__main__":
    main()
