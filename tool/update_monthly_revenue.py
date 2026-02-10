"""
月營收歷史回補工具 (MOPS 靜態 HTML 版)
============================================
功能：爬取歷史月營收 (補完 OpenAPI 無法提供的 2023-2025 數據)
使用方式：直接執行 python tool/backfill_monthly_revenue.py
"""
import sys
import os
import requests
import pandas as pd
import time
import random
from io import StringIO
from sqlalchemy import text
from datetime import datetime

# 將專案根目錄加入路徑
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from tool.db_helper import get_db_engine

def fetch_mops_static_revenue(year, month):
    """
    從 MOPS 靜態 HTML 爬取指定月份的月營收資料
    
    同時抓取上市 (sii) 與上櫃 (otc) 市場的月營收，
    包含當月營收金額與去年同月增減率 (YoY%)。
    
    Args:
        year: 西元年（如 2024）
        month: 月份（1-12）
    
    Returns:
        DataFrame: 含公司代號、營收、YoY 等欄位；若失敗回傳 None
    """
    roc_year = year - 1911
    print(f"\n📅 正在爬取 {year}年{month}月 (民國 {roc_year} 年)...")
    
    all_data = []
    markets = [('sii', '上市'), ('otc', '上櫃')]
    
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36'
    }

    for market_code, market_name in markets:
        # MOPS 網址慣例：有時候月份是 1, 有時候是 01
        urls = [
            f"https://mopsov.twse.com.tw/nas/t21/{market_code}/t21sc03_{roc_year}_{month}_0.html",
            f"https://mopsov.twse.com.tw/nas/t21/{market_code}/t21sc03_{roc_year}_{month:02d}_0.html"
        ]
        
        success = False
        for url in urls:
            try:
                time.sleep(random.uniform(2, 4)) # 保護伺服器延遲
                resp = requests.get(url, headers=headers, timeout=20)
                resp.encoding = 'big5' # 重要：MOPS 靜態檔通常是 Big5
                
                if resp.status_code != 200:
                    continue

                # 解析 HTML
                dfs = pd.read_html(StringIO(resp.text))
                
                for df in dfs:
                    # 辨識特徵：欄位中包含 '公司代號' 且資料量夠大
                    # 這種 HTML 格式通常會有合併儲存格，需特別處理
                    df_str = str(df.columns)
                    if '公司代號' in df_str or '公司 代號' in df_str:
                        # 清理多層索引與空格
                        if isinstance(df.columns, pd.MultiIndex):
                            df.columns = df.columns.get_level_values(-1)
                        
                        df.columns = [str(c).replace(' ', '').replace('　', '') for c in df.columns]
                        
                        # 篩選有效列
                        temp_df = df[df['公司代號'].astype(str).str.match(r'^\d+$')].copy()
                        if not temp_df.empty:
                            temp_df['year'] = year
                            temp_df['month'] = month
                            all_data.append(temp_df)
                            success = True
                
                if success:
                    print(f"   ✅ {market_name}: 成功取得資料")
                    break
            except Exception:
                continue
                
        if not success:
            print(f"   ❌ {market_name}: 無法取得資料")

    if not all_data:
        return None
        
    return pd.concat(all_data, ignore_index=True)

def process_and_save(df):
    """
    清洗月營收資料並寫入 monthly_revenue 資料表
    
    自動建表（若不存在），清洗 stock_id 格式，
    轉換營收金額單位（千元→元），並以 UPSERT 方式存入資料庫。
    
    Args:
        df: 爬取回來的原始 DataFrame
    """
    if df is None or df.empty:
        return
        
    engine = get_db_engine()
    
    # 欄位映射邏輯 (適應靜態 HTML 的欄位名)
    clean_df = pd.DataFrame()
    
    # 找出正確的欄位名
    col_stock = next((c for c in df.columns if '公司代號' in c), None)
    col_rev = next((c for c in df.columns if '當月營收' in c and '去年' not in c), None)
    col_yoy = next((c for c in df.columns if '去年同月增減' in c and '%' in c), None)

    if not col_stock or not col_rev:
        print("   ❌ 找不到關鍵欄位，跳過此月")
        return

    try:
        clean_df['stock_id'] = df[col_stock].astype(str).str.replace('.0', '', regex=False).str.strip()
        clean_df['year'] = df['year']
        clean_df['month'] = df['month']
        
        # 轉換數值
        clean_df['revenue'] = pd.to_numeric(df[col_rev].astype(str).str.replace(',', '', regex=False), errors='coerce').fillna(0) * 1000
        clean_df['revenue_yoy'] = pd.to_numeric(df[col_yoy], errors='coerce').fillna(0) if col_yoy else 0.0
        
        # 寫入資料庫
        year_val = int(clean_df.iloc[0]['year'])
        month_val = int(clean_df.iloc[0]['month'])
        
        with engine.begin() as conn:
            conn.execute(text("""
                CREATE TABLE IF NOT EXISTS monthly_revenue (
                    stock_id VARCHAR(10),
                    year INT,
                    month INT,
                    revenue BIGINT,
                    revenue_yoy FLOAT,
                    PRIMARY KEY (stock_id, year, month)
                )
            """))
            conn.execute(text("DELETE FROM monthly_revenue WHERE year = :y AND month = :m"), {"y": year_val, "m": month_val})
            clean_df.to_sql('monthly_revenue', conn, if_exists='append', index=False, chunksize=2000)
            print(f"      ✅ 寫入成功 ({len(clean_df)} 筆)")
            
    except Exception as e:
        print(f"   ❌ 處理失敗: {e}")

def main():
    # 設定補完範圍
    start_date = '2023-01'
    end_date = datetime.now().strftime('%Y-%m')

    print(f"🚀 開始執行歷史月營收回補: {start_date} ~ {end_date}")
    
    date_range = pd.date_range(start=start_date, end=end_date, freq='MS')
    
    for dt in date_range:
        if dt > datetime.now(): break
        
        res_df = fetch_mops_static_revenue(dt.year, dt.month)
        process_and_save(res_df)
        
        print("   ☕ 休息一下...")
        time.sleep(random.uniform(2, 5))

if __name__ == "__main__":
    main()