"""
每日資料庫統一更新入口 (V35 Pipeline)
============================================
功能：一鍵執行所有資料更新流程
更新順序：股價行情 → 月營收 → 季度財報
使用方式：python 1_update_database.py
"""
import requests
import pandas as pd
from sqlalchemy import text
import time
import random
from datetime import datetime, timedelta, date
from config import Config
from tool.db_helper import get_db_engine

# ============================================
# ⚙️ 設定區 (統一使用 Config)
# ============================================

# 偽裝瀏覽器 (全域 Header，會被函式內部的 update 覆蓋)
HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120.0.0.0 Safari/537.36',
    'Accept': 'application/json, text/javascript, */*; q=0.01',
    'Accept-Language': 'zh-TW,zh;q=0.9,en-US;q=0.8,en;q=0.7',
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

def fetch_twse_data(date_str, max_retries=3):
    """
    抓取證交所 (上市) 全市場（含重試機制）
    """
    print("  🔹 正在抓取上市 (TWSE) 資料...", end="")
    clean_date = date_str.replace('-', '')
    
    for attempt in range(max_retries):
        try:
            # 1. 股價 (MI_INDEX)
            price_df = pd.DataFrame()
            url = f"https://www.twse.com.tw/rwd/zh/afterTrading/MI_INDEX?date={clean_date}&type=ALL&response=json"
            res = requests.get(url, timeout=15)
            data = res.json()
            
            if data.get('stat') == 'OK':
                target_table = next((t for t in data['tables'] if "每日收盤行情" in t['title']), None)
                if target_table:
                    price_df = pd.DataFrame(target_table['data'], columns=target_table['fields'])
                    price_df = price_df.rename(columns={
                        '證券代號': 'stock_id', '開盤價': 'open_price', 
                        '最高價': 'high_price', '最低價': 'low_price', 
                        '收盤價': 'close_price', '成交股數': 'volume', '本益比': 'pe_ratio'
                    })
                    price_df = price_df[['stock_id', 'open_price', 'high_price', 'low_price', 'close_price', 'volume', 'pe_ratio']]

            if price_df.empty: 
                if attempt < max_retries - 1:
                    print(f" (第{attempt+1}次重試)", end="")
                    time.sleep(2 ** attempt)
                    continue
                else:
                    print(" (無資料)")
                    return None

            # 2. 籌碼 (T86)
            chips_df = pd.DataFrame()
            url = f"https://www.twse.com.tw/rwd/zh/fund/T86?date={clean_date}&selectType=ALL&response=json"
            res = requests.get(url, timeout=15)
            data = res.json()
            if data.get('stat') == 'OK':
                df = pd.DataFrame(data['data'], columns=data['fields'])
                f_col = next((c for c in df.columns if "外" in c and "買賣超股數" in c), None)
                t_col = next((c for c in df.columns if "投信" in c and "買賣超股數" in c), None)
                # Phase 2: 自營商買賣超（優先合計欄位，否則排除避險/自行子欄）
                d_col = next(
                    (c for c in df.columns if "自營商" in c and "買賣超" in c
                     and "自行" not in c and "避險" not in c),
                    None
                )
                
                if f_col and t_col:
                    cols_to_keep = ['證券代號', f_col, t_col]
                    col_names = ['stock_id', 'foreign_buy', 'trust_buy']
                    if d_col:
                        cols_to_keep.append(d_col)
                        col_names.append('dealer_buy')
                    chips_df = df[cols_to_keep].copy()
                    chips_df.columns = col_names

            if not chips_df.empty:
                merged = pd.merge(price_df, chips_df, on='stock_id', how='left')
            else:
                merged = price_df
                merged['foreign_buy'] = 0
                merged['trust_buy'] = 0

            # 確保 dealer_buy 欄位存在
            if 'dealer_buy' not in merged.columns:
                merged['dealer_buy'] = 0
                
            print(" ✅")
            return merged
            
        except Exception as e:
            if attempt < max_retries - 1:
                print(f" (重試...)", end="")
                time.sleep(2 ** attempt)
            else:
                print(f" ❌ 上市抓取失敗: {e}")
                return None
    return None

def fetch_tpex_data(date_str, max_retries=3):
    """
    抓取櫃買中心 (上櫃) 全市場 - 強健修復版
    """
    print("  🔹 正在抓取上櫃 (TPEx) 資料...", end="")
    dt = datetime.strptime(date_str, "%Y-%m-%d")
    minguo_date = f"{dt.year - 1911}/{dt.month:02d}/{dt.day:02d}"
    
    # ✅ 強化的 Headers，偽裝成真實瀏覽器
    current_headers = HEADERS.copy()
    current_headers.update({
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'Referer': f'https://www.tpex.org.tw/web/stock/aftertrading/daily_close_quotes/stk_quote_result.php?l=zh-tw&d={minguo_date}',
        'Origin': 'https://www.tpex.org.tw'
    })

    for attempt in range(max_retries):
        try:
            # ✅ 隨機延遲，降低被封鎖機率
            time.sleep(random.uniform(1.5, 3.0))
            
            price_df_stock = pd.DataFrame()
            price_df_etf = pd.DataFrame()
            
            # 1-A. 上櫃股票
            url = f"https://www.tpex.org.tw/web/stock/aftertrading/daily_close_quotes/stk_quote_result.php?l=zh-tw&d={minguo_date}&o=json"
            res = requests.get(url, headers=current_headers, timeout=20)
            
            if res.status_code != 200:
                raise Exception(f"HTTP {res.status_code}")

            try:
                data = res.json()
            except ValueError:
                # ✅ 捕獲 JSON 解析錯誤，避免程式崩潰
                print(f" (回應非 JSON: {res.text[:30]}...)", end="")
                if attempt < max_retries - 1: continue
                return None

            # 支援新舊兩種 API 回應格式
            stock_raw = data.get('aaData')  # 舊格式
            if not stock_raw and data.get('tables'):
                # 新格式：tables[0]['data']
                for tbl in data['tables']:
                    if isinstance(tbl, dict) and tbl.get('data'):
                        stock_raw = tbl['data']
                        break

            if stock_raw:
                df = pd.DataFrame(stock_raw)
                price_df_stock = df.iloc[:, [0, 4, 5, 6, 2, 8]].copy()
                price_df_stock.columns = ['stock_id', 'open_price', 'high_price', 'low_price', 'close_price', 'volume']
            
            # 1-B. 上櫃 ETF
            time.sleep(random.uniform(1, 2))
            url_etf = f"https://www.tpex.org.tw/web/etf/etf_daily_close_quotes/etf_quote_result.php?l=zh-tw&d={minguo_date}&o=json"
            res = requests.get(url_etf, headers=current_headers, timeout=20)
            
            try:
                data = res.json()
            except ValueError:
                data = {} # ETF 失敗不影響整體

            # 支援新舊兩種 API 回應格式
            etf_raw = data.get('aaData')
            if not etf_raw and data.get('tables'):
                for tbl in data['tables']:
                    if isinstance(tbl, dict) and tbl.get('data'):
                        etf_raw = tbl['data']
                        break

            if etf_raw:
                df = pd.DataFrame(etf_raw)
                price_df_etf = df.iloc[:, [0, 4, 5, 6, 2, 7]].copy()
                price_df_etf.columns = ['stock_id', 'open_price', 'high_price', 'low_price', 'close_price', 'volume']

            # 合併
            price_df = pd.concat([price_df_stock, price_df_etf], ignore_index=True)
            if price_df.empty:
                if attempt < max_retries - 1:
                    print(f" (空資料，第{attempt+1}次重試)", end="")
                    continue
                else:
                    print(" (無資料)")
                    return None
            
            price_df['pe_ratio'] = 0.0

            # 2. 籌碼
            time.sleep(random.uniform(1, 2))
            chips_df = pd.DataFrame()
            url = f"https://www.tpex.org.tw/web/stock/3insti/daily_trade/3itrade_hedge_result.php?l=zh-tw&se=AL&t=D&d={minguo_date}&o=json"
            res = requests.get(url, headers=current_headers, timeout=20)
            
            try:
                data = res.json()
                # 支援新舊兩種 API 回應格式
                chips_raw = data.get('aaData')
                if not chips_raw and data.get('tables'):
                    for tbl in data['tables']:
                        if isinstance(tbl, dict) and tbl.get('data'):
                            chips_raw = tbl['data']
                            break

                if chips_raw:
                    df = pd.DataFrame(chips_raw)
                    # TPEx 3itrade 欄位: [0]=代號, 外資相關..., 投信相關..., 自營商相關...
                    # 實測欄位位置: foreign_buy=col10, trust_buy=col13, dealer_buy=col16
                    try:
                        chips_df = df.iloc[:, [0, 10, 13, 16]].copy()
                        chips_df.columns = ['stock_id', 'foreign_buy', 'trust_buy', 'dealer_buy']
                    except (IndexError, KeyError):
                        # 若欄位不足，降級為不含 dealer
                        chips_df = df.iloc[:, [0, 10, 13]].copy()
                        chips_df.columns = ['stock_id', 'foreign_buy', 'trust_buy']
            except:
                pass

            if not chips_df.empty:
                price_df['stock_id'] = price_df['stock_id'].astype(str)
                chips_df['stock_id'] = chips_df['stock_id'].astype(str)
                merged = pd.merge(price_df, chips_df, on='stock_id', how='left')
            else:
                merged = price_df
                merged['foreign_buy'] = 0
                merged['trust_buy'] = 0

            # 確保 dealer_buy 欄位存在
            if 'dealer_buy' not in merged.columns:
                merged['dealer_buy'] = 0
                
            print(" ✅")
            return merged
            
        except Exception as e:
            if attempt < max_retries - 1:
                print(f" (錯誤: {str(e)[:20]}... 重試)", end="")
                time.sleep(5 * (attempt + 1))
            else:
                print(f" ❌ 上櫃抓取失敗: {e}")
                return None
    
    return None

def process_and_save(df, date_str, engine):
    """
    清洗股票行情資料並寫入資料庫
    
    將抓回的原始 DataFrame 進行數值清洗（去除逗號、處理無效值），
    然後透過 db_helper.upsert_stock_data 寫入 daily_market_data 表。
    
    Args:
        df: 原始行情 DataFrame（含 stock_id, open_price 等欄位）
        date_str: 交易日期字串 (YYYY-MM-DD)
        engine: SQLAlchemy 資料庫引擎
    
    Returns:
        int: 成功寫入的筆數
    """
    if df is None or df.empty: return 0
    
    cols = ['open_price', 'high_price', 'low_price', 'close_price', 'volume',
            'pe_ratio', 'foreign_buy', 'trust_buy', 'dealer_buy',
            'margin_balance', 'short_balance']
    for col in cols:
        if col in df.columns:
            df[col] = df[col].apply(clean_number)
        else:
            df[col] = 0
    
    df['trade_date'] = date_str
    df = df[df['close_price'] > 0]

    # 過濾權證（保留個股 + ETF + 債券ETF，只排除權證等衍生品）
    # 個股: 4碼 1xxx-9xxx | ETF/債券: 00 開頭 (0050, 006208, 00679B, 00631L...)
    before_filter = len(df)
    df['stock_id'] = df['stock_id'].astype(str).str.strip()
    df = df[df['stock_id'].str.match(r'^([1-9]\d{3}|00)')]
    filtered_out = before_filter - len(df)
    if filtered_out > 0:
        print(f"  📊 過濾權證: {before_filter} → {len(df)} 筆（排除 {filtered_out} 筆權證/衍生品）")
    
    try:
        from tool.db_helper import upsert_stock_data
        count = upsert_stock_data(df, 'daily_market_data')
        return count
    except ImportError:
        print("⚠️ 使用回退方案：REPLACE INTO")
        try:
            with engine.connect() as conn:
                conn.execute(text(f"DELETE FROM daily_market_data WHERE trade_date='{date_str}'"))
                conn.commit()
            df.to_sql('daily_market_data', engine, if_exists='append', index=False, chunksize=2000)
            return len(df)
        except Exception as e:
            print(f"❌ 寫入失敗: {e}")
            return 0

# ============================================
# 🚀 主程式 - V35 統一更新入口
# ============================================

def run_price_update(engine):
    """
    步驟一：更新每日股價行情
    
    從 TWSE/TPEx 抓取最新的日線行情（含籌碼），
    自動偵測資料庫最新日期並補齊至今日。
    
    Args:
        engine: SQLAlchemy 資料庫引擎
    
    Returns:
        int: 本次更新的總筆數
    """
    print(f"\n{'='*60}")
    print("📈 步驟 1/3：更新每日股價行情")
    print(f"{'='*60}")
    
    latest_date = get_latest_date_from_db(engine)
    if latest_date:
        start_dt = latest_date + timedelta(days=1)
        print(f"📅 資料庫最新: {latest_date}，將從 {start_dt} 開始更新...")
    else:
        start_dt = datetime.strptime("2024-01-01", "%Y-%m-%d").date()
        print(f"⚠️ 資料庫為空，將從 {start_dt} 開始更新...")

    end_dt = datetime.now().date()
    
    if start_dt > end_dt:
        print("✅ 股價資料已是最新，無需更新。")
        return 0

    total_count = 0
    current_dt = start_dt
    while current_dt <= end_dt:
        date_str = current_dt.strftime("%Y-%m-%d")
        print(f"\n📅 正在處理: {date_str}")
        
        twse_data = fetch_twse_data(date_str)
        tpex_data = fetch_tpex_data(date_str)
        
        final_df = pd.DataFrame()
        if twse_data is not None: final_df = pd.concat([final_df, twse_data])
        if tpex_data is not None: final_df = pd.concat([final_df, tpex_data])

        # Phase 2: 融資融券餘額
        if not final_df.empty:
            try:
                from tool.crawlers.chip_data_scraper import fetch_margin_balance
                margin_df = fetch_margin_balance(date_str)
                if not margin_df.empty:
                    final_df['stock_id'] = final_df['stock_id'].astype(str).str.strip()
                    margin_df['stock_id'] = margin_df['stock_id'].astype(str).str.strip()
                    final_df = pd.merge(final_df, margin_df, on='stock_id', how='left')
            except Exception as e:
                print(f"  ⚠️ 融資融券合併失敗（不中斷）: {e}")
        
        count = process_and_save(final_df, date_str, engine)
        if count > 0:
            print(f"💾 成功寫入 {count} 筆資料！")
            total_count += count
        else:
            print("💤 假日或無資料")

        current_dt += timedelta(days=1)
        time.sleep(random.randint(3, 5))

    return total_count


def run_monthly_revenue_update():
    """
    步驟二：更新月營收資料
    
    呼叫 tool/update_monthly_revenue.py 中的爬蟲邏輯，
    爬取 MOPS 靜態 HTML 以取得最新月營收與 YoY 資料，
    寫入 monthly_revenue 資料表。
    
    Returns:
        bool: 是否成功
    """
    print(f"\n{'='*60}")
    print("💰 步驟 2/3：更新月營收資料")
    print(f"{'='*60}")
    
    try:
        from tool.update_monthly_revenue import fetch_mops_static_revenue, process_and_save as save_revenue
        
        now = datetime.now()
        # 營收通常在次月 10 日後公布，抓取上個月的
        if now.day < 12:
            target_year = now.year if now.month > 1 else now.year - 1
            target_month = now.month - 1 if now.month > 1 else 12
        else:
            target_year = now.year
            target_month = now.month
        
        # 嘗試最新月份 + 上個月（確保補齊）
        months_to_try = []
        for offset in range(2):
            m = target_month - offset
            y = target_year
            if m <= 0:
                m += 12
                y -= 1
            months_to_try.append((y, m))
        
        success = False
        for y, m in months_to_try:
            print(f"\n📅 嘗試更新 {y}年{m}月 月營收...")
            res_df = fetch_mops_static_revenue(y, m)
            if res_df is not None and not res_df.empty:
                save_revenue(res_df)
                success = True
            time.sleep(random.uniform(2, 4))
        
        return success
        
    except Exception as e:
        print(f"⚠️ 月營收更新失敗（不中斷流程）: {e}")
        return False


def run_financial_update():
    """
    步驟三：更新最新一季財報
    
    呼叫 tool/update_financials_mops.py 中的季報爬蟲邏輯，
    從 mopsov 備援站抓取最新一季的綜合損益表，
    寫入 financial_statements 資料表（含營業利益率計算）。
    
    Returns:
        bool: 是否成功
    """
    print(f"\n{'='*60}")
    print("📊 步驟 3/3：更新季度財報")
    print(f"{'='*60}")
    
    try:
        from tool.update_financials_mops import update_quarter
        
        now = datetime.now()
        roc_year = now.year - 1911
        
        # 推算最新可取得的季度
        # Q1 (5月後可取得), Q2 (8月後), Q3 (11月後), Q4 (次年3月後)
        if now.month >= 11:
            target_year, target_quarter = roc_year, 3
        elif now.month >= 8:
            target_year, target_quarter = roc_year, 2
        elif now.month >= 5:
            target_year, target_quarter = roc_year, 1
        elif now.month >= 3:
            target_year, target_quarter = roc_year - 1, 4
        else:
            target_year, target_quarter = roc_year - 1, 3
        
        print(f"📅 目標季度: 民國 {target_year} Q{target_quarter}")
        result = update_quarter(target_year, target_quarter)
        return result
        
    except Exception as e:
        print(f"⚠️ 季度財報更新失敗（不中斷流程）: {e}")
        return False


def print_summary_report(price_count, revenue_ok, financial_ok, elapsed):
    """
    印出更新流程的統計摘要
    
    Args:
        price_count: 股價更新筆數
        revenue_ok: 月營收是否成功
        financial_ok: 季度財報是否成功
        elapsed: 總耗時秒數
    """
    engine = get_db_engine()
    
    print(f"\n{'='*60}")
    print("📊 V35 每日更新流程 - 統計摘要")
    print(f"{'='*60}")
    
    # 各表筆數統計
    try:
        with engine.connect() as conn:
            price_total = conn.execute(text("SELECT COUNT(*) FROM daily_market_data")).scalar() or 0
            financial_total = conn.execute(text("SELECT COUNT(*) FROM financial_statements")).scalar() or 0
            
            try:
                revenue_total = conn.execute(text("SELECT COUNT(*) FROM monthly_revenue")).scalar() or 0
            except Exception:
                revenue_total = 0
            
        print(f"\n📋 資料表統計:")
        print(f"  {'daily_market_data':<25} {price_total:>10,} 筆（本次新增 {price_count}）")
        print(f"  {'monthly_revenue':<25} {revenue_total:>10,} 筆")
        print(f"  {'financial_statements':<25} {financial_total:>10,} 筆")
    except Exception as e:
        print(f"  ⚠️ 統計查詢失敗: {e}")
    
    print(f"\n📋 更新狀態:")
    print(f"  {'📈 股價行情':<15} {'✅ 完成' if price_count > 0 else '💤 已是最新'}")
    print(f"  {'💰 月營收':<15} {'✅ 完成' if revenue_ok else '⚠️ 跳過/失敗'}")
    print(f"  {'📊 季度財報':<15} {'✅ 完成' if financial_ok else '⚠️ 跳過/失敗'}")
    print(f"\n⏱️ 總耗時: {elapsed // 60:.0f} 分 {elapsed % 60:.0f} 秒")
    print(f"{'='*60}\n")


if __name__ == "__main__":
    print(f"\n{'='*60}")
    print("🚀 Stock Linbot V35 - 每日資料庫統一更新")
    print(f"📅 執行時間: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"{'='*60}")
    
    start_time = datetime.now()
    
    print(f"\n🔗 連線資料庫...")
    engine = get_db_engine()
    
    # 步驟 1: 股價行情
    price_count = run_price_update(engine)
    
    # 步驟 2: 月營收
    revenue_ok = run_monthly_revenue_update()
    
    # 步驟 3: 季度財報
    financial_ok = run_financial_update()
    
    # 總結報告
    elapsed = (datetime.now() - start_time).total_seconds()
    print_summary_report(price_count, revenue_ok, financial_ok, elapsed)
    
    print("🎉 每日更新流程完成！")