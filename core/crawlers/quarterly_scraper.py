"""
MOPS 季報爬蟲模組 (mopsov 版本)
============================================
功能：從 MOPS 舊版網站抓取綜合損益表 (含營業費用)
資料來源：mopsov.twse.com.tw (ajax_t163sb04) - 備援站以提升穩定性
"""
import time
import random
import requests
import pandas as pd
from io import StringIO
from typing import Dict, Optional

class QuarterlyScraper:
    # [V35 升級] 改用備援站提升穩定性
    MOPS_URL = 'https://mopsov.twse.com.tw/mops/web/ajax_t163sb04'
    
    def __init__(self):
        self.session = requests.Session()
        # 偽裝成瀏覽器
        self.headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Content-Type': 'application/x-www-form-urlencoded',
            'Origin': 'https://mopsov.twse.com.tw',
            'Referer': 'https://mopsov.twse.com.tw/mops/web/t163sb04',
        }

    def fetch_all_markets(self, year: int, quarter: int) -> pd.DataFrame:
        """抓取上市(sii)與上櫃(otc)並合併"""
        print(f"🕷️ 開始爬取 民國{year}年 Q{quarter}...")
        
        df_sii = self._fetch_data(year, quarter, 'sii')
        
        # [V35 升級] 隨機延遲 3-6 秒避免被封鎖
        delay = random.uniform(3, 6)
        print(f"   ⏳ 休息 {delay:.1f} 秒避免被鎖...")
        time.sleep(delay)
        
        df_otc = self._fetch_data(year, quarter, 'otc')
        
        # 合併
        frames = [d for d in [df_sii, df_otc] if d is not None and not d.empty]
        if not frames:
            return pd.DataFrame()
            
        result = pd.concat(frames, ignore_index=True)
        # 去重
        result.drop_duplicates(subset=['stock_id'], inplace=True)
        return result

    def _fetch_data(self, year: int, quarter: int, typek: str) -> Optional[pd.DataFrame]:
        market_name = "上市" if typek == 'sii' else "上櫃"
        print(f"   👉 正在抓取 {market_name} (TYPEK={typek})...")
        
        payload = {
            'encodeURIComponent': '1', 'step': '1', 'firstin': '1', 'off': '1',
            'TYPEK': typek, 
            'year': str(year), 
            'season': f"{quarter:02d}"
        }
        
        try:
            res = self.session.post(self.MOPS_URL, headers=self.headers, data=payload, timeout=45)
            res.encoding = 'utf-8' # mopsov 使用 UTF-8 編碼
            
            if "查詢過於頻繁" in res.text:
                print("      ❌ 失敗: IP 被限制，請稍後再試")
                return None

            # [V35 升級] 加強錯誤處理：檢查是否有表格
            try:
                dfs = pd.read_html(StringIO(res.text))
            except ValueError as e:
                if "No tables found" in str(e):
                    print(f"      ⚠️ {market_name} 無資料表格 (可能該季尚未公告)")
                else:
                    print(f"      ❌ HTML 解析失敗: {e}")
                return None
            
            # 尋找含有數據的表格 (特徵：有 '營業收入' 和 '費用')
            target_df = None
            for df in dfs:
                cols_str = str(df.columns)
                # 寬鬆匹配，因為欄位名稱可能會變
                if '營業收入' in cols_str and '費用' in cols_str:
                    target_df = df
                    break
            
            if target_df is None:
                print(f"      ⚠️ {market_name} 無資料或表格結構改變")
                return None

            # --- 資料清洗 ---
            df = target_df.copy()
            # 處理 MultiIndex (如果有的話)
            if isinstance(df.columns, pd.MultiIndex):
                df.columns = df.columns.get_level_values(-1)
            
            # 清理列名（去除前後空格，但保留內部空格）
            df.columns = [str(c).strip() for c in df.columns]
            
            # [V35 升級] 改進欄位對應邏輯（更寬鬆匹配，忽略內部空格）
            col_map = {}
            for col in df.columns:
                col_clean = col.replace(' ', '')  # 移除所有空格用於匹配
                if '公司代號' in col_clean or '代號' in col_clean: 
                    col_map[col] = 'stock_id'
                elif '營業收入' in col_clean: 
                    col_map[col] = 'revenue'
                elif '營業費用' in col_clean: 
                    col_map[col] = 'operating_expense'
                elif '營業利益' in col_clean or '營業利益(損失)' in col_clean: 
                    col_map[col] = 'operating_profit'
                elif '基本每股盈餘' in col_clean or 'EPS' in col.upper(): 
                    col_map[col] = 'eps'
            
            df.rename(columns=col_map, inplace=True)
            
            # 確保必要欄位存在
            required = ['stock_id', 'revenue', 'operating_expense']
            if not all(c in df.columns for c in required):
                print(f"      ⚠️ 欄位缺失 (缺少: {[c for c in required if c not in df.columns]})")
                return None

            # 數值清理
            cols_to_clean = ['revenue', 'operating_expense', 'eps']
            if 'operating_profit' in df.columns: 
                cols_to_clean.append('operating_profit')
            else:
                # 如果沒有營業利益欄位，自己算 (簡化版：假設 毛利-費用? 不太準，先填 0)
                # 不過通常表4和表6都有營業利益
                df['operating_profit'] = 0

            for col in cols_to_clean:
                df[col] = pd.to_numeric(df[col].astype(str).str.replace(',', '').replace('--', '0'), errors='coerce').fillna(0)

            # [V35 升級] 單位調整：MOPS 數據為千元，乘以 1000 轉為「元」儲存
            # 註：資料庫欄位定義為 BIGINT，可容納大數值
            df['revenue'] = df['revenue'] * 1000
            df['operating_expense'] = df['operating_expense'] * 1000
            if 'operating_profit' in df.columns:
                df['operating_profit'] = df['operating_profit'] * 1000
            
            # 補上時間與研發(0)
            df['year'] = year + 1911
            df['quarter'] = quarter
            df['rd_expense'] = 0 # 既然抓不到，就填 0
            
            # 過濾無效代號
            df = df[df['stock_id'].astype(str).str.match(r'^\d{4}$')]
            
            print(f"      ✅ 成功解析 {len(df)} 筆資料")
            return df[['stock_id', 'year', 'quarter', 'revenue', 'operating_expense', 'operating_profit', 'rd_expense', 'eps']]

        except Exception as e:
            print(f"      ❌ 抓取錯誤: {e}")
            return None