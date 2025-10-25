import core.Data as Data
import pandas as pd
import os
import finlab # 確保 finlab 已安裝
from dotenv import load_dotenv # 確保 python-dotenv 已安裝
import time # 導入 time 模組

# --- 0. 載入 API 金鑰 ---
load_dotenv()
# [修正] FinLab 預設讀取 FINLAB_API_TOKEN
# FINLAB_TOKEN = os.environ.get("FINLAB_API_TOKEN") 
# if not FINLAB_TOKEN:
#     print("[錯誤] 找不到 FinLab API Token。")
#     print("請檢查您的 .env 檔案中是否已設定 FINLAB_API_TOKEN。")
#     exit()
# try:
#     finlab.login(FINLAB_TOKEN) 
#     print("[成功] FinLab API 金鑰載入成功。")
# except Exception as e:
#     print(f"[錯誤] FinLab 登入失敗: {e}")
#     exit() 
# [暫時停用] 先註解掉 FinLab 登入，節省額度並專注 yfinance

# --- 1. 建立資料夾 ---
if not os.path.exists('data'):
    os.makedirs('data')
    print("已建立 'data' 資料夾，用於儲存快取檔案。")

# --- 2. [暫時停用 FinLab] 強制使用靜態股票池 ---
# print("正在從 FinLab 獲取「臺灣50」與「臺灣中型100」成分股...")
# try:
#     # 獲取臺灣50成分股
#     tw50_list = finlab.data.get('index_components', name='臺灣50')
#     tw50_stocks = tw50_list[tw50_list['is_component'] == True]['stock_id'].tolist()
#     print(f"    已獲取 {len(tw50_stocks)} 支臺灣50成分股。")

#     # 獲取臺灣中型100成分股
#     tw_mid100_list = finlab.data.get('index_components', name='臺灣中型100')
#     tw_mid100_stocks = tw_mid100_list[tw_mid100_list['is_component'] == True]['stock_id'].tolist()
#     print(f"    已獲取 {len(tw_mid100_stocks)} 支臺灣中型100成分股。")

#     # 合併為我們的 150 支股票池
#     STOCK_POOL = sorted(list(set(tw50_stocks + tw_mid100_stocks)))
#     print(f"[成功] 股票池已建立，總共 {len(STOCK_POOL)} 支股票。")
    
# except Exception as e:
#     print(f"[錯誤] 無法獲取成分股列表: {e}")
#     print("將使用一個靜態的備用列表進行測試...")
#     STOCK_POOL = ['0050', '2330', '2454', '2603', '2881'] # 備用方案

# [強制] 使用測試列表
STOCK_POOL = ['0050', '2330', '2454', '2603', '2881']
print(f"[測試模式] 使用靜態備用列表: {STOCK_POOL}")

# --- 3. 設定爬取時間 ---
START_DATE = "2019-01-01"
# [修正] 將結束日期改為昨天，避免 yfinance 抓取當天可能不完整的資料
import datetime
yesterday = datetime.date.today() - datetime.timedelta(days=1)
END_DATE = yesterday.strftime("%Y-%m-%d") 
print(f"資料區間設定為: {START_DATE} 到 {END_DATE}")

print(f"--- Day 1 任務：開始更新 {len(STOCK_POOL)} 支股票的數據 ---")

# --- 4. 執行爬蟲迴圈 ---
for stock_id in STOCK_POOL:
    print(f"正在處理: {stock_id} ({STOCK_POOL.index(stock_id) + 1} / {len(STOCK_POOL)})")
    
    # [增加延遲] 每次處理股票前稍微延遲，降低被封鎖風險
    time.sleep(1) 
    
    k_data_success = False # 標記 K 線是否下載成功
    
    try:
        # 4.1 下載 K 線 (來源: yfinance)
        print("    嘗試下載 K 線資料...")
        k_data = Data.getData(stock_id, START_DATE, END_DATE)
        if k_data is not None and not k_data.empty:
            print("    K 線資料下載/讀取成功。")
            k_data_success = True
        else:
            print(f"    [警告] {stock_id} K 線資料下載失敗或為空。")
            # 即使 K 線失敗，也繼續嘗試下載其他資料

        # 4.2 下載三大法人 (來源: FinMind)
        print("    嘗試下載三大法人資料...")
        Data.getFMInstitutionalInvestors(stock_id, START_DATE, END_DATE)
        print("    三大法人資料處理完畢。")
        time.sleep(0.5) # API 呼叫間隔

        # 4.3 下載融資融券 (來源: FinMind)
        print("    嘗試下載融資融券資料...")
        Data.getFMMarginTrading(stock_id, START_DATE, END_DATE)
        print("    融資融券資料處理完畢。")
        time.sleep(0.5) # API 呼叫間隔
        
        # 4.4 下載月營收 (來源: FinMind)
        print("    嘗試下載月營收資料...")
        Data.getFMMonthRevenue(stock_id, START_DATE, END_DATE)
        print("    月營收資料處理完畢。")
        time.sleep(0.5) # API 呼叫間隔

        # 4.5 [暫時停用 FinLab] 下載股權分散表
        # print("    嘗試下載股權分散表...")
        # if k_data_success: # 只有 K 線成功才執行
        #     Data.getPriceAndShareHolder(stock_id, START_DATE, END_DATE)
        #     print("    股權分散表處理完畢。")
        # else:
        #     print("    因 K 線下載失敗，跳過股權分散表處理。")

        print(f"    [成功] {stock_id} 資料處理流程完畢。")

    except Exception as e:
        print(f"    [嚴重錯誤] {stock_id} 處理過程中發生未預期錯誤: {e}")

print(f"--- Day 1 任務：數據更新流程結束 (共處理 {len(STOCK_POOL)} 支股票) ---")
