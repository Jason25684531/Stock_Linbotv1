"""
MOPS 季報爬蟲模組
============================================
功能：從公開資訊觀測站抓取綜合損益表數據
資料來源：MOPS T13SB01 (上市) / T163SB09 (上櫃)
反爬蟲對策：User-Agent 輪替 + 隨機延遲 + 錯誤重試
"""
import time
import random
import requests
import pandas as pd
from io import StringIO
from typing import Dict, List, Optional, Tuple
from datetime import datetime


class QuarterlyScraper:
    """季報爬蟲類別"""
    
    # User-Agent 池 (模擬不同瀏覽器)
    USER_AGENTS = [
        'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:121.0) Gecko/20100101 Firefox/121.0',
        'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.1 Safari/605.1.15',
        'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
    ]
    
    # MOPS 網址模板
    MOPS_URL_SII = 'https://mops.twse.com.tw/mops/web/ajax_t163sb05'  # 上市
    MOPS_URL_OTC = 'https://mops.twse.com.tw/mops/web/ajax_t163sb09'  # 上櫃
    
    def __init__(self, retry_count: int = 3, delay_range: Tuple[float, float] = (2.0, 5.0)):
        """
        初始化爬蟲
        
        Args:
            retry_count: 失敗重試次數
            delay_range: 延遲範圍 (秒)
        """
        self.retry_count = retry_count
        self.delay_range = delay_range
        self.session = requests.Session()
    
    def _get_random_headers(self) -> Dict[str, str]:
        """產生隨機請求標頭 (反爬蟲對策)"""
        return {
            'User-Agent': random.choice(self.USER_AGENTS),
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
            'Accept-Language': 'zh-TW,zh;q=0.9,en-US;q=0.8,en;q=0.7',
            'Accept-Encoding': 'gzip, deflate, br',
            'Connection': 'keep-alive',
            'Referer': 'https://mops.twse.com.tw/',
        }
    
    def _random_delay(self):
        """隨機延遲 (避免被封鎖)"""
        delay = random.uniform(*self.delay_range)
        time.sleep(delay)
    
    def fetch_quarterly_data(self, year: int, quarter: int, market: str = 'sii') -> Optional[pd.DataFrame]:
        """
        抓取單季財報數據
        
        Args:
            year: 民國年 (例: 112)
            quarter: 季度 (1-4)
            market: 市場類別 ('sii'=上市, 'otc'=上櫃)
        
        Returns:
            DataFrame 或 None (失敗時)
        """
        # 驗證參數
        if not (1 <= quarter <= 4):
            raise ValueError(f"季度必須介於 1-4，得到: {quarter}")
        
        # 選擇網址
        url = self.MOPS_URL_SII if market == 'sii' else self.MOPS_URL_OTC
        
        # 準備 POST 參數
        payload = {
            'encodeURIComponent': 1,
            'step': 1,
            'firstin': 1,
            'off': 1,
            'keyword4': '',
            'code1': '',
            'TYPEK2': '',
            'checkbtn': '',
            'queryName': 'co_id',
            'inpuType': 'co_id',
            'TYPEK': 'all',
            'isnew': 'false',
            'co_id': '',
            'year': str(year),
            'season': str(quarter).zfill(2),  # 01, 02, 03, 04
        }
        
        # 重試邏輯
        for attempt in range(1, self.retry_count + 1):
            try:
                print(f"🔄 [{market.upper()}] 抓取 {year}年Q{quarter} (嘗試 {attempt}/{self.retry_count})...")
                
                # 發送請求
                response = self.session.post(
                    url,
                    data=payload,
                    headers=self._get_random_headers(),
                    timeout=30
                )
                response.raise_for_status()
                
                # 解析 HTML 表格
                df_list = pd.read_html(StringIO(response.text), encoding='utf-8')
                
                if not df_list:
                    print("⚠️ 未找到表格")
                    continue
                
                # 通常第一個表格是我們要的綜合損益表
                df = df_list[0]
                
                # 基本清理
                df = self._clean_dataframe(df, year, quarter, market)
                
                if df is not None and not df.empty:
                    print(f"✅ 成功抓取 {len(df)} 筆資料")
                    self._random_delay()  # 成功後也要延遲
                    return df
                
            except requests.Timeout:
                print(f"⏱️ 請求逾時 (第 {attempt} 次)")
            except requests.RequestException as e:
                print(f"❌ 請求錯誤: {e}")
            except Exception as e:
                print(f"❌ 解析錯誤: {e}")
            
            # 失敗延遲 (指數退避)
            if attempt < self.retry_count:
                backoff = self.delay_range[1] * (2 ** (attempt - 1))
                print(f"⏳ 等待 {backoff:.1f} 秒後重試...")
                time.sleep(backoff)
        
        print(f"❌ 抓取失敗 (已重試 {self.retry_count} 次)")
        return None
    
    def _clean_dataframe(self, df: pd.DataFrame, year: int, quarter: int, market: str) -> Optional[pd.DataFrame]:
        """
        清理資料框
        
        Args:
            df: 原始資料框
            year: 年度
            quarter: 季度
            market: 市場
        
        Returns:
            清理後的資料框
        """
        try:
            # 尋找關鍵欄位 (MOPS 表格結構可能變動)
            # 預期欄位：公司代號、公司名稱、營業收入、研究發展費用、基本每股盈餘
            
            # 如果第一列是標題，設為欄位名
            if '公司代號' not in df.columns and '公司代號' in df.iloc[0].values:
                df.columns = df.iloc[0]
                df = df[1:].reset_index(drop=True)
            
            # 必要欄位檢查
            required_cols = ['公司代號', '營業收入', '基本每股盈餘']
            missing = [col for col in required_cols if col not in df.columns]
            
            if missing:
                print(f"⚠️ 缺少必要欄位: {missing}")
                print(f"📋 現有欄位: {df.columns.tolist()}")
                return None
            
            # 選取並重命名欄位
            result = pd.DataFrame({
                'stock_id': df['公司代號'].astype(str).str.strip(),
                'year': year,
                'quarter': quarter,
                'revenue': pd.to_numeric(df['營業收入'], errors='coerce') * 1000,  # 轉為千元
                'rd_expense': pd.to_numeric(
                    df.get('研究發展費用', 0), 
                    errors='coerce'
                ).fillna(0) * 1000,
                'eps': pd.to_numeric(df['基本每股盈餘'], errors='coerce'),
            })
            
            # 過濾無效數據
            result = result[result['stock_id'].str.match(r'^\d{4}$', na=False)]  # 只保留4位數股票代號
            result = result.dropna(subset=['revenue', 'eps'])
            
            return result
            
        except Exception as e:
            print(f"❌ 清理資料失敗: {e}")
            return None
    
    def fetch_all_markets(self, year: int, quarter: int) -> pd.DataFrame:
        """
        抓取上市 + 上櫃所有資料
        
        Args:
            year: 民國年
            quarter: 季度
        
        Returns:
            合併後的 DataFrame
        """
        all_data = []
        
        # 上市
        df_sii = self.fetch_quarterly_data(year, quarter, market='sii')
        if df_sii is not None:
            all_data.append(df_sii)
        
        # 上櫃
        df_otc = self.fetch_quarterly_data(year, quarter, market='otc')
        if df_otc is not None:
            all_data.append(df_otc)
        
        if all_data:
            result = pd.concat(all_data, ignore_index=True)
            # 去重 (以防上市上櫃重複)
            result = result.drop_duplicates(subset=['stock_id', 'year', 'quarter'])
            return result
        else:
            return pd.DataFrame()
    
    def calculate_q4_from_annual(self, annual_data: pd.DataFrame, q1_q3_data: pd.DataFrame) -> pd.DataFrame:
        """
        計算 Q4 數據 (年度 - Q1~Q3)
        
        注意：MOPS 有時只提供累計數據，需自行計算單季
        
        Args:
            annual_data: 年度累計數據
            q1_q3_data: Q1~Q3 累計數據
        
        Returns:
            Q4 單季數據
        """
        # 合併
        merged = annual_data.merge(
            q1_q3_data[['stock_id', 'revenue', 'rd_expense']],
            on='stock_id',
            suffixes=('_annual', '_q3')
        )
        
        # 計算 Q4
        merged['revenue'] = merged['revenue_annual'] - merged['revenue_q3']
        merged['rd_expense'] = merged['rd_expense_annual'] - merged['rd_expense_q3']
        merged['quarter'] = 4
        
        return merged[['stock_id', 'year', 'quarter', 'revenue', 'rd_expense', 'eps']]


# ===========================================
# 測試與範例
# ===========================================
if __name__ == '__main__':
    print("🧪 測試季報爬蟲...")
    
    scraper = QuarterlyScraper()
    
    # 測試：抓取 2024 Q3 (民國 113 年)
    test_year = 113
    test_quarter = 3
    
    print(f"\n📊 測試抓取 {test_year} 年 Q{test_quarter}")
    df = scraper.fetch_all_markets(test_year, test_quarter)
    
    if not df.empty:
        print(f"\n✅ 成功抓取 {len(df)} 檔股票")
        print(f"\n前 5 筆資料：")
        print(df.head())
        
        # 儲存測試結果
        output_file = f'test_financial_{test_year}Q{test_quarter}.csv'
        df.to_csv(output_file, index=False, encoding='utf-8-sig')
        print(f"\n💾 已儲存：{output_file}")
    else:
        print("\n❌ 未能抓取任何資料")
