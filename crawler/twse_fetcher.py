import requests
import pandas as pd
import time
import random
import json

class TwseFetcher:
    def __init__(self):
        self.headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
        }

    def _clean_number(self, val):
        """清除數值中的逗號，並處理異常值"""
        if isinstance(val, str):
            val = val.replace(',', '').strip()
            if val == '-' or val == '--' or val == '':
                return None
        return val

    def fetch_daily_prices(self, date_str):
        """
        [V7 結構適應版] 抓取每日收盤行情
        支援舊版 (Root Keys) 與新版 (Tables List) 結構，自動鑽入尋找 2330
        """
        url = f'https://www.twse.com.tw/exchangeReport/MI_INDEX?response=json&date={date_str}&type=ALLBUT0999'
        print(f"   ☁️  抓取股價 (MI_INDEX) ...")
        
        try:
            res = requests.get(url, headers=self.headers, timeout=30)
            
            try:
                data = res.json()
            except json.JSONDecodeError:
                print(f"   ❌ {date_str} 回傳格式錯誤 (非 JSON)")
                return None
            
            if data['stat'] != 'OK':
                if '沒有符合條件的資料' in data['stat']:
                    print(f"   💤 {date_str} 休市或無資料")
                else:
                    print(f"   ⚠️ {date_str} API 狀態: {data['stat']}")
                return None

            # ==========================================
            # 🕵️‍♂️ 核心邏輯：建立「候選資料庫」
            # 無論資料藏在根目錄，還是藏在 tables 裡面，全部挖出來放在 potential_lists
            # ==========================================
            potential_lists = []
            
            # 1. 針對新格式：檢查 'tables' 欄位
            if 'tables' in data and isinstance(data['tables'], list):
                for table in data['tables']:
                    if 'data' in table:
                        potential_lists.append(table['data'])
            
            # 2. 針對舊格式：檢查根目錄的所有 values
            for value in data.values():
                if isinstance(value, list):
                    potential_lists.append(value)

            # ==========================================
            # 🔍 暴力搜索：誰裡面有台積電？
            # ==========================================
            target_data = None
            
            for lst in potential_lists:
                # 初步過濾：長度要夠長，且第一筆要是 list
                if len(lst) > 100: 
                    first_row = lst[0]
                    if isinstance(first_row, list) and len(first_row) > 10:
                        # 掃描內容尋找 2330
                        is_target = False
                        for row in lst:
                            # 檢查第一欄 (證券代號)
                            if len(row) > 0 and str(row[0]).strip() == '2330':
                                is_target = True
                                break
                        
                        if is_target:
                            target_data = lst
                            break

            if not target_data:
                print(f"   ⚠️ {date_str} 找不到股價清單 (已掃描 {len(potential_lists)} 個資料表)")
                # print(f"   Debug Keys: {list(data.keys())}") 
                return None

            # 3. 轉成 DataFrame
            df = pd.DataFrame(target_data)
            
            # 0:證券代號, 1:證券名稱, 2:成交股數, 5:開盤, 6:最高, 7:最低, 8:收盤
            required_cols = [0, 1, 2, 5, 6, 7, 8]
            
            if df.shape[1] < 9:
                print(f"   ❌ 資料欄位不足 (Cols: {df.shape[1]})")
                return None
                
            df = df.iloc[:, required_cols]
            df.columns = ['stock_id', 'stock_name', 'volume', 'open', 'high', 'low', 'close']
            
            return df
            
        except Exception as e:
            print(f"   ❌ 抓取股價失敗: {e}")
            return None

    def fetch_bwibbu(self, date_str):
        """
        抓取個股本益比、殖利率、股價淨值比
        URL: BWIBBU_ALL
        """
        url = f'https://www.twse.com.tw/exchangeReport/BWIBBU_ALL?response=json&date={date_str}&selectType=ALL'
        print(f"   ☁️  抓取基本面 (BWIBBU_ALL) ...")
        
        try:
            res = requests.get(url, headers=self.headers, timeout=15)
            data = res.json()
            
            if data['stat'] != 'OK':
                return None
                
            raw_list = data.get('data', [])
            if not raw_list:
                return None
            
            df = pd.DataFrame(raw_list)
            if df.shape[1] < 5:
                return None

            df = df.iloc[:, [0, 2, 3, 4]]
            df.columns = ['stock_id', 'pe_ratio', 'yield_percent', 'pb_ratio']
            
            return df
            
        except Exception as e:
            print(f"   ❌ 抓取基本面失敗: {e}")
            return None
    
    def fetch_daily_data(self, date_str):
        """
        [主入口] 取得整合後的每日資料
        """
        # 1. 抓股價
        price_df = self.fetch_daily_prices(date_str)
        if price_df is None:
            return None
        
        # 休息一下
        time.sleep(random.uniform(2, 4))
        
        # 2. 抓基本面
        fund_df = self.fetch_bwibbu(date_str)
        
        # 3. 合併
        if fund_df is not None:
            merged_df = pd.merge(price_df, fund_df, on='stock_id', how='left')
        else:
            print("   ⚠️ 無基本面資料，將補空值")
            merged_df = price_df
            merged_df['pe_ratio'] = None
            merged_df['pb_ratio'] = None
            merged_df['yield_percent'] = None

        # 4. 清洗
        merged_df['stock_id'] = merged_df['stock_id'].astype(str).str.strip()
        numeric_cols = ['volume', 'open', 'high', 'low', 'close', 'pe_ratio', 'pb_ratio', 'yield_percent']
        
        for col in numeric_cols:
            if col in merged_df.columns:
                merged_df[col] = merged_df[col].apply(self._clean_number)
                merged_df[col] = pd.to_numeric(merged_df[col], errors='coerce')

        merged_df['trade_date'] = pd.to_datetime(date_str, format='%Y%m%d').date()
        merged_df['foreign_buy_vol'] = 0
        merged_df['trust_buy_vol'] = 0
        merged_df['dealer_buy_vol'] = 0
        
        merged_df = merged_df[merged_df['stock_id'].apply(lambda x: len(x) == 4 and x.isdigit())]

        return merged_df

# ==========================================
# 🧪 測試區塊
# ==========================================
if __name__ == "__main__":
    print("🚀 V7 測試開始...")
    fetcher = TwseFetcher()
    test_date = "20241004" 
    
    print(f"\n📅 測試抓取 {test_date} ...")
    df = fetcher.fetch_daily_prices(test_date)
    
    if df is not None:
        print(f"🎉 測試成功！共 {len(df)} 筆")
        print(df.head(3))
        tsmc = df[df['stock_id'] == '2330']
        if not tsmc.empty:
            print("\n🔍 驗證台積電：")
            print(tsmc)
    else:
        print("❌ 測試失敗")