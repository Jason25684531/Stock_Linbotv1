import pandas as pd
from sqlalchemy import create_engine, text
from crawler.twse_fetcher import TwseFetcher
from config import Config
from datetime import datetime, timedelta
import time

# 連線資料庫
engine = create_engine(Config.SQLALCHEMY_DATABASE_URI)

def save_to_db(df):
    """
    將 DataFrame 寫入資料庫
    """
    # 欄位名稱對應 (DataFrame -> Database)
    # 我們的 fetcher 已經將 key 命名好了，這裡做最後檢查與排序
    target_columns = [
        'trade_date', 'stock_id', 'stock_name', 
        'open_price', 'high_price', 'low_price', 'close_price', 'volume',
        'foreign_buy_vol', 'trust_buy_vol', 'dealer_buy_vol',
        'pe_ratio', 'pb_ratio', 'yield_percent'  # ✨ V11 新增欄位
    ]
    
    # 重新命名以符合資料庫欄位 (fetcher 產出的名稱 -> DB 欄位名稱)
    rename_map = {
        'open': 'open_price', 
        'high': 'high_price', 
        'low': 'low_price', 
        'close': 'close_price'
        # pe_ratio, pb_ratio 等名稱在 fetcher 裡已經對了，不用改
    }
    df = df.rename(columns=rename_map)
    
    # 確保只有資料庫有的欄位才寫入 (避免報錯)
    # 如果資料庫尚未 ALTER TABLE 增加新欄位，這裡會自動過濾掉新欄位，防止程式崩潰
    # 但你必須執行 ALTER TABLE 才能存進去
    # 為了安全，我們先只取這些
    save_df = df.copy()
    
    # 寫入資料庫 (append模式)
    try:
        # chunksize 設定小一點，避免封包過大
        save_df.to_sql('daily_market_data', con=engine, if_exists='append', index=False, chunksize=1000)
        print(f"✅ 成功寫入 {len(save_df)} 筆資料 (含基本面)！")
    except Exception as e:
        if "Duplicate" in str(e):
            print("⚠️ 資料已存在，略過寫入。")
        elif "Unknown column" in str(e):
            print("❌ 錯誤：資料庫欄位不符！請確認是否已執行 ALTER TABLE 增加 pe_ratio 等欄位。")
            print(f"詳細錯誤: {e}")
        else:
            print(f"❌ 資料庫錯誤: {e}")

def get_last_date_from_db():
    """查詢資料庫中目前最新的日期"""
    sql = text("SELECT MAX(trade_date) FROM daily_market_data")
    try:
        with engine.connect() as conn:
            result = conn.execute(sql)
            last_date = result.scalar()
        return last_date
    except Exception as e:
        print(f"查詢最新日期失敗 (可能是空表): {e}")
        return None

def main():
    fetcher = TwseFetcher()
    
    # --- 自動判斷起始日期 (Smart Resume) ---
    last_date = get_last_date_from_db()
    
    if last_date:
        if isinstance(last_date, datetime):
            last_date = last_date.date()
        start_date = last_date + timedelta(days=1)
        # 轉換回 datetime
        start_date = datetime(start_date.year, start_date.month, start_date.day)
        print(f"🔄 偵測到斷點，將從 {start_date.strftime('%Y-%m-%d')} 開始續傳...")
    else:
        # 設定預設起始日 (例如今年初)
        start_date = datetime(2024, 1, 1) 
        print(f"🆕 資料庫為空，從預設起始日 {start_date.strftime('%Y-%m-%d')} 開始...")

    end_date = datetime.now()
    
    if start_date > end_date:
        print("🎉 資料庫已經是最新狀態，無需更新。")
        return

    # --- 開始爬蟲迴圈 ---
    current = start_date
    while current <= end_date:
        date_str = current.strftime('%Y%m%d')
        
        # 週末跳過
        if current.weekday() in [5, 6]:
            print(f"😴 {date_str} 是週末，跳過。")
            current += timedelta(days=1)
            continue
            
        # 呼叫新的爬蟲 (會同時抓價格 + 基本面)
        df = fetcher.fetch_daily_data(date_str)
        
        if df is not None and not df.empty:
            save_to_db(df)
            # 休息時間加長一點，因為我們現在發了兩個 Request
            sleep_time = 15 
            print(f"--- 休息 {sleep_time} 秒避免被鎖 IP ---")
            time.sleep(sleep_time)
        else:
            print(f"⚠️ {date_str} 無資料。")
            time.sleep(5) 
            
        current += timedelta(days=1)

if __name__ == "__main__":
    main()