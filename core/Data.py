# -----------------------------------------------------------------
# 檔名：Data.py (最終修正版 - FinMind 股票池 + FinLab 股權)
# -----------------------------------------------------------------
from FinMind.data import DataLoader
import finlab
from finlab import data as fdata
import pandas as pd
import numpy as np
import os
import time
import requests
import warnings
from requests.exceptions import HTTPError
from dotenv import load_dotenv
import scrape_tdcc
import datetime


warnings.filterwarnings("ignore")

# --- 0. 載入環境變數 ---
load_dotenv()
FINMIND_TOKEN = os.getenv("FINMIND_TOKEN")
FINLAB_TOKEN = os.getenv("FINLAB_TOKEN")

# --- 1. 初始化 FinMind ---
if not FINMIND_TOKEN:
    print("[警告] 未找到 FINMIND_TOKEN，FinMind API 將使用免費版限制。")
FM = DataLoader()
if FINMIND_TOKEN:
    try:
        FM.login_by_token(api_token=FINMIND_TOKEN)
        print("[成功] FinMind API Token 載入成功。")
    except Exception as e:
        print(f"[錯誤] FinMind 登入失敗: {e}，將使用免費版 API。")

# --- 2. 初始化 FinLab ---
if not FINLAB_TOKEN:
    print("[警告] 未找到 FINLAB_TOKEN，FinLab API 將無法登入 (部分功能可能受限)。")
else:
    try:
        finlab.login(api_token=FINLAB_TOKEN)
        print("[成功] FinLab API Token 載入成功。")
    except Exception as e:
        print(f"[錯誤] FinLab 登入失敗: {e}")

# --- 3. 資料路徑 ---
datapath = "data"  # 資料存放路徑

# ==========================================================
# 安全 API 呼叫函數 (僅用於 FinMind)
# ==========================================================
def safe_api_call(func, *args, **kwargs):
    retries = kwargs.pop('retries', 3)
    wait_time = kwargs.pop('wait_time', 3605) 

    for attempt in range(retries):
        try:
            if not callable(func):
                raise TypeError(f"{func} is not a callable method of DataLoader")
            result_df = func(*args, **kwargs)
            return result_df
        except HTTPError as e:
            if e.response.status_code == 429:
                if attempt < retries - 1:
                    print(f"    [API 限制] 達到 FinMind API 限制 (429)，等待 {wait_time/60:.1f} 分鐘後重試...")
                    time.sleep(wait_time)
                else:
                    print(f"    [API 限制] FinMind API 限制 (429)，已達最大重試次數。")
                    return pd.DataFrame()
            else:
                print(f"    [API HTTP 錯誤] 呼叫 FinMind API 失敗 ({e.response.status_code}): {e}")
                return pd.DataFrame() 
        except Exception as e:
            print(f"    [API 未知錯誤] 呼叫 FinMind API 失敗: {e}")
            if attempt < retries - 1:
                time.sleep(5)
            else:
                return pd.DataFrame()
    return pd.DataFrame()

# ==========================================================
# 函數 1: getData (使用 FinMind)
# ==========================================================
def getData(prod, st, en):
    bakfile = f'{datapath}/FM_{prod}_{st}_{en}_stock_daily.csv' 

    rename_map = {
        'trading_volume': 'volume',
        'max': 'high', 
        'min': 'low'   
    }
    required_cols_after_rename = ['open', 'high', 'low', 'close', 'volume', 'adj_close']

    if os.path.exists(bakfile):
        try:
            data = pd.read_csv(bakfile, index_col=None, parse_dates=['date']) 
            if data.empty:
                print(f"    [Cache Info] {prod} K-line cache empty. Re-downloading...")
                raise FileNotFoundError
            
            data = data.set_index('date') 
            data.columns = [str(i).lower() for i in data.columns]
            data = data.rename(columns=rename_map)
            
            if 'adj_close' not in data.columns:
                if 'close' in data.columns:
                    data['adj_close'] = data['close']
                else:
                    data['adj_close'] = np.nan
            
            if not all(col in data.columns for col in required_cols_after_rename):
                missing = [col for col in required_cols_after_rename if col not in data.columns]
                print(f"    [Cache Warning] {prod} cache missing required columns: {missing}. Re-downloading...")
                raise FileNotFoundError

            print(f"    [Cache Info] {prod} FinMind K-line data loaded from {bakfile}")
            return data
        
        except Exception as e:
            print(f"    [Cache Error] Failed loading K-line cache for {prod}: {e}. Re-downloading...")

    try:
        print(f"    [FinMind Info] Downloading K-line for {prod} from {st} to {en}...")
        data = safe_api_call(FM.taiwan_stock_daily, stock_id=prod, start_date=st, end_date=en)
        if data.empty:
            print(f"    [FinMind Info] taiwan_stock_daily failed for {prod}, trying taiwan_stock_price...")
            data = safe_api_call(FM.taiwan_stock_price, stock_id=prod, start_date=st, end_date=en)
            if data.empty:
                raise ValueError(f"{prod} K-line data is empty from both daily and price APIs.")

        print(f"    [FinMind Info] {prod} K-line Download successful.")
        data['date'] = pd.to_datetime(data['date'])
        
        data.to_csv(bakfile, index=False)
        print(f"    [Cache Info] {prod} K-line data saved to {bakfile}")
        
        data = data.set_index('date')
        data.columns = [str(i).lower() for i in data.columns] 
        data = data.rename(columns=rename_map)
        
        if 'adj_close' not in data.columns:
            if 'close' in data.columns:
                data['adj_close'] = data['close']
            else:
                data['adj_close'] = np.nan
        
        if not all(col in data.columns for col in required_cols_after_rename):
            missing = [col for col in required_cols_after_rename if col not in data.columns]
            raise ValueError(f"{prod} K-line data missing required columns after rename: {missing}")
        
        return data

    except Exception as e:
        print(f"    [FinMind Error] Failed processing K-line for {prod}: {e}")
        return pd.DataFrame()


# ==========================================================
# 函數 2: getStockPoolFromETF (新函數，使用 FinMind)
# ==========================================================
def getStockPoolFromETF():
    """從 FinMind 獲取 0050 和 0051 成分股作為股票池"""
    print("    [FinMind Info] Downloading ETF components (0050, 0051) for stock pool...")
    stock_pool = set()
    try:
        # 1. 獲取 0050 (台灣50)
        df_0050 = safe_api_call(FM.taiwan_stock_info_by_industry, industry_id="0050")
        if not df_0050.empty and 'stock_id' in df_0050.columns:
            stock_pool.update(df_0050['stock_id'].tolist())
            print(f"    [FinMind Info] Fetched {len(df_0050)} components from 0050.")
        else:
            print("    [FinMind Warning] Could not fetch 0050 components.")

        # 2. 獲取 0051 (中型100)
        df_0051 = safe_api_call(FM.taiwan_stock_info_by_industry, industry_id="0051")
        if not df_0051.empty and 'stock_id' in df_0051.columns:
            stock_pool.update(df_0051['stock_id'].tolist())
            print(f"    [FinMind Info] Fetched {len(df_0051)} components from 0051.")
        else:
            print("    [FinMind Warning] Could not fetch 0051 components.")
            
        # 3. 清理：移除 0050, 0051 本身 (如果它們在列表裡)
        stock_pool.discard('0050')
        stock_pool.discard('0051')
        
        # 4. 確保 ID 都是 4 碼數字普通股
        final_pool = sorted([
            s for s in stock_pool 
            if isinstance(s, str) and s.isdigit() and len(s) == 4 and not s.startswith('0')
        ])
        
        return final_pool

    except Exception as e:
        print(f"    [FinMind Error] Failed to get ETF components: {e}")
        return []

# ==========================================================
# 函數 3: getPriceAndShareHolder (使用 FinLab)
# ==========================================================
# def getPriceAndShareHolder(data1, prod, st, en):  # 接收 data1 (K線)
#     # [AI 修正] 直接返回 K 線資料，完全跳過股權處理
#     # print(f"    [注意] 股權分散表功能已暫時禁用。跳過 {prod} 的股權處理。") # 可選的提示訊息
#     return data1 # 直接返回 K 線資料
# def getPriceAndShareHolder(data1, prod, st, en):  # 接收 data1 (K線)
#     if data1 is None or data1.empty:
#         print(f"    [getPriceAndShareHolder Error] K-line data for {prod} is empty, skipping Shareholding.")
#         return data1 # 返回 K 線

#     try:
#         # --- [AI 修正] 決定要爬取的日期 ---
#         # 策略：爬取 data1 (K線) 中的 "最新日期" 對應的 TDCC 資料
#         # 注意：TDCC 資料通常是每周末更新，所以用 K 線最新日期去找 "可能" 會有資料
#         # 更穩健的做法是找到上週五的日期
#         latest_kline_date = data1.index.max()

#         # 找到上一個星期五 (如果今天或 K 線最新日是周末，則找上週五)
#         day_of_week = latest_kline_date.weekday()
#         if day_of_week == 5: # 星期六
#             last_friday = latest_kline_date - pd.Timedelta(days=1)
#         elif day_of_week == 6: # 星期日
#             last_friday = latest_kline_date - pd.Timedelta(days=2)
#         else: # 週一到週五
#             days_to_friday = (day_of_week - 4) if day_of_week >= 4 else (day_of_week + 3)
#             last_friday = latest_kline_date - pd.Timedelta(days=days_to_friday)

#         # 確保找到的是過去的日期 (如果 K 線資料很舊)
#         if last_friday > pd.Timestamp(datetime.date.today()):
#              days_from_today_to_friday = (datetime.date.today().weekday() - 4 + 7) % 7
#              last_friday = pd.Timestamp(datetime.date.today()) - pd.Timedelta(days=days_from_today_to_friday)


#         tdcc_date = last_friday.strftime('%Y%m%d')
#         print(f"    [TDCC Info] 嘗試獲取 {prod} 在 {tdcc_date} 的 TDCC 股權分散表資料...")

#         # 1. 呼叫新的爬蟲函式 (會自動處理快取)
#         cache_path = f'{datapath}/TDCC_{prod}_{tdcc_date}.csv'
    
#         if os.path.exists(cache_path):
#             tdcc_data = pd.read_csv(cache_path, index_col=0, parse_dates=True)
#             print(f"[Cache Info] {prod} @ {tdcc_date} TDCC 資料已從快取載入。")
#         else:
#             try:
#                 tdcc_data = scrape_tdcc.scrape_tdcc_single_stock_date(prod, sca_date=tdcc_date)
#                 if not tdcc_data.empty:
#                     tdcc_data.to_csv(cache_path)
#                     print(f"[TDCC Info] 已快取 {prod} @ {tdcc_date} 的 TDCC 資料。")
#                 else:
#                     print(f"[TDCC Warning] 無 TDCC 資料 for {prod} @ {tdcc_date}，跳過。")
#                     return data1  # 或繼續
#             except Exception as e:
#                 print(f"[Error] 刮取 TDCC 失敗 for {prod}: {e}")
#                 return data1

#         # 合併到 data1 (K線資料)
#         try:
#             # 重索引並 ffill 到日資料
#             tdcc_daily = tdcc_data.reindex(data1.index, method='ffill')
#             data1 = pd.concat([data1, tdcc_daily], axis=1)
#             print(f"[TDCC Info] TDCC 股權資料已合併 (使用 {tdcc_date} 資料 ffill) for {prod}.")
#             print(f"    [偵錯] 合併 (concat) 後 data1 的欄位 for {prod}:")
#             print(data1.columns.tolist())
#             print(f"    [偵錯] 預計要 ffill 的 TDCC 欄位 for {prod}: {tdcc_daily.columns.tolist()}")
#             print(f"    [偵錯] 向前填充 (ffill) 後 data1 的欄位 for {prod}:")
#             print(data1.columns.tolist())
#         except Exception as e:
#             print(f"[Error] 合併 TDCC 股權資料時發生錯誤 for {prod}: {e}")
#     except Exception as e:
#         print(f"[TDCC Error] 處理 TDCC 股權分散表時發生錯誤 for {prod}: {e}")

#     return data1

# ==========================================================
# 函數 4: getFMInstitutionalInvestors (使用 FinMind)
# ==========================================================
def getFMInstitutionalInvestors(prod, st, en):
    bakfile = f"{datapath}/FM_{prod}_{st}_{en}_InstInvestors.csv" 
    if os.path.exists(bakfile):
        try:
            tmpdata = pd.read_csv(bakfile, index_col='date', parse_dates=['date'])
            if tmpdata.empty: raise FileNotFoundError
            print(f"    [Cache Info] FinMind Inst Investors loaded for {prod}.")
            return tmpdata
        except Exception as e:
            print(f"    [Cache Error] Failed loading Inst Investors cache for {prod}: {e}. Re-downloading...")
    try:
        print(f"    [FinMind Info] Downloading Inst Investors for {prod}...")
        tmpdata = safe_api_call(FM.taiwan_stock_institutional_investors, stock_id=prod, start_date=st, end_date=en)
        if tmpdata.empty: return pd.DataFrame()
        if 'date' not in tmpdata.columns: return pd.DataFrame()
        
        tmpdata['date'] = pd.to_datetime(tmpdata['date'])
        tmpdata.to_csv(bakfile, index=False) 
        print(f"    [Cache Info] FinMind Inst Investors saved for {prod}.")
        tmpdata = tmpdata.set_index('date')
        return tmpdata
    except Exception as e:
        print(f"    [FinMind Error] Failed downloading Inst Investors for {prod}: {e}")
        return pd.DataFrame()

# ==========================================================
# 函數 5: InstInvestorsDaily (格式轉換)
# ==========================================================
def InstInvestorsDaily(data):
    # (此函數保持不變)
    if data is None or data.empty: return pd.DataFrame()
    rs_sh, rs_dt = [], []
    unique_dates = data.index.unique()
    if not isinstance(unique_dates, pd.DatetimeIndex): return pd.DataFrame()
    for dt in unique_dates:
        data1 = data.loc[data.index == dt]
        if 'name' not in data1.columns: continue
        tmprow = []
        f_b = data1.loc[data1["name"] == "Foreign_Investor", "buy"].values
        f_s = data1.loc[data1["name"] == "Foreign_Investor", "sell"].values
        fd_b = data1.loc[data1["name"] == "Foreign_Dealer_Self", "buy"].values
        fd_s = data1.loc[data1["name"] == "Foreign_Dealer_Self", "sell"].values
        i_b = data1.loc[data1["name"] == "Investment_Trust", "buy"].values
        i_s = data1.loc[data1["name"] == "Investment_Trust", "sell"].values
        ds_b_options = ["Dealer_self", "Dealer_Proprietary", "Dealer"]
        ds_s_options = ["Dealer_self", "Dealer_Proprietary", "Dealer"]
        ds_b = data1.loc[data1["name"].isin(ds_b_options), "buy"].values
        ds_s = data1.loc[data1["name"].isin(ds_s_options), "sell"].values
        dh_b = data1.loc[data1["name"] == "Dealer_Hedging", "buy"].values
        dh_s = data1.loc[data1["name"] == "Dealer_Hedging", "sell"].values
        tmprow.append(f_b[0] if len(f_b) > 0 else 0)
        tmprow.append(f_s[0] if len(f_s) > 0 else 0)
        tmprow.append(fd_b[0] if len(fd_b) > 0 else 0)
        tmprow.append(fd_s[0] if len(fd_s) > 0 else 0)
        tmprow.append(i_b[0] if len(i_b) > 0 else 0)
        tmprow.append(i_s[0] if len(i_s) > 0 else 0)
        tmprow.append(np.sum(ds_b) if len(ds_b) > 0 else 0) 
        tmprow.append(np.sum(ds_s) if len(ds_s) > 0 else 0) 
        tmprow.append(dh_b[0] if len(dh_b) > 0 else 0)
        tmprow.append(dh_s[0] if len(dh_s) > 0 else 0)
        rs_sh.append(tmprow)
        rs_dt.append(dt)
    if not rs_sh: return pd.DataFrame()
    columns = ["外陸資買進股數(不含外資自營商)", "外陸資賣出股數(不含外資自營商)","外資自營商買進股數", "外資自營商賣出股數","投信買進股數", "投信賣出股數","自營商買進股數(自行買賣)", "自營商賣出股數(自行買賣)","自營商買進股數(避險)", "自營商賣出股數(避險)"]
    rs = pd.DataFrame(rs_sh, index=rs_dt, columns=columns)
    return rs

# ==========================================================
# 函數 6: getFMMarginTrading (使用 FinMind)
# ==========================================================
def getFMMarginTrading(prod, st, en):
    bakfile = f"{datapath}/FM_{prod}_{st}_{en}_MarginTrading.csv" 
    if os.path.exists(bakfile):
        try:
            tmpdata = pd.read_csv(bakfile, index_col='date', parse_dates=['date'])
            if tmpdata.empty: raise FileNotFoundError
            print(f"    [Cache Info] FinMind Margin Trading loaded for {prod}.")
            return tmpdata
        except Exception as e:
            print(f"    [Cache Error] Failed loading Margin Trading cache for {prod}: {e}. Re-downloading...")
    try:
        print(f"    [FinMind Info] Downloading Margin Trading for {prod}...")
        tmpdata = safe_api_call(FM.taiwan_stock_margin_purchase_short_sale, stock_id=prod, start_date=st, end_date=en)
        if tmpdata.empty: return pd.DataFrame()
        if 'date' not in tmpdata.columns: return pd.DataFrame()
        
        tmpdata['date'] = pd.to_datetime(tmpdata['date'])
        tmpdata.to_csv(bakfile, index=False) 
        print(f"    [Cache Info] FinMind Margin Trading saved for {prod}.")
        tmpdata = tmpdata.set_index('date')
        return tmpdata
    except Exception as e:
        print(f"    [FinMind Error] Failed downloading Margin Trading for {prod}: {e}")
        return pd.DataFrame()

# ==========================================================
# 函數 7: getFMMonthRevenue (使用 FinMind)
# ==========================================================
def getFMMonthRevenue(prod, st, en):
    bakfile = f"{datapath}/FM_{prod}_{st}_{en}_MonthRevenue.csv" 
    if os.path.exists(bakfile):
        try:
            tmpdata = pd.read_csv(bakfile, index_col='date', parse_dates=['date'])
            if tmpdata.empty: raise FileNotFoundError
            print(f"    [Cache Info] FinMind Month Revenue loaded for {prod}.")
            return tmpdata
        except Exception as e:
            print(f"    [Cache Error] Failed loading Month Revenue cache for {prod}: {e}. Re-downloading...")
    try:
        print(f"    [FinMind Info] Downloading Month Revenue for {prod}...")
        tmpdata = safe_api_call(FM.taiwan_stock_month_revenue, stock_id=prod, start_date=st) 
        if tmpdata.empty: 
            if prod.startswith('00'):
                print(f"    [FinMind Info] No Month Revenue data for {prod} (ETF).")
            else:
                print(f"    [FinMind Warning] No Month Revenue data returned for {prod}.")
            return pd.DataFrame()
        if 'date' not in tmpdata.columns: return pd.DataFrame()
        
        tmpdata['date'] = pd.to_datetime(tmpdata['date'])
        tmpdata.to_csv(bakfile, index=False) 
        print(f"    [Cache Info] FinMind Month Revenue saved for {prod}.")
        tmpdata = tmpdata.set_index('date')
        return tmpdata
    except Exception as e:
        print(f"    [FinMind Error] Failed downloading Month Revenue for {prod}: {e}")
        return pd.DataFrame()

# ==========================================================
# 函數 8: discord_push
# ==========================================================
def discord_push(message):
    webhook_url = os.getenv("DISCORD_WEBHOOK_URL")
    if not webhook_url:
        print("[Discord Error] DISCORD_WEBHOOK_URL not found in .env file.")
        return
    data = { "content": message }
    try:
        response = requests.post(webhook_url, json=data, timeout=10)
        response.raise_for_status() 
        if response.status_code == 204: print("訊息已成功發送到 Discord！")
        else: print(f"訊息可能已發送，但狀態碼非預期: {response.status_code}")
    except requests.exceptions.Timeout: print("[Discord Error] 發送到 Discord 時超時。")
    except requests.exceptions.RequestException as e: print(f"[Discord Error] 發送到 Discord 時發生錯誤: {e}")
    except Exception as e: print(f"[Discord Error] 發送 Discord 訊息時發生未知錯誤: {e}")