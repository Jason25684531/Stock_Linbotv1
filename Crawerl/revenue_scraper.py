# -*- coding: utf-8 -*-
"""
台股月營收爬蟲 - 終極完整解決方案

整合3種方法：
1. 反爬蟲技術（fake-useragent + 隨機延遲）
2. Selenium 模擬真實瀏覽器
3. 備用：直接下載建議

使用方式：
    from revenue_scraper import RevenueScr​aper
    
    scraper = RevenueScraper()
    df = scraper.fetch(2024, 10)  # 爬取2024年10月資料
"""

import requests
import pandas as pd
from io import StringIO
import time
import random


class RevenueScraper:
    """台股月營收爬蟲"""
    
    def __init__(self, use_selenium=False):
        """
        初始化爬蟲
        
        Args:
            use_selenium (bool): 是否使用 Selenium（需安裝 ChromeDriver）
        """
        self.use_selenium = use_selenium
        self.session = requests.Session()
        
        # 嘗試載入 fake-useragent
        try:
            from fake_useragent import UserAgent
            self.ua = UserAgent()
            self.has_fake_ua = True
            print("[OK] 已載入 fake-useragent")
        except:
            self.has_fake_ua = False
            self.user_agents = [
                'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
                'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
                'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:121.0) Gecko/20100101 Firefox/121.0',
            ]
            print("[INFO] 使用預設 User-Agent 列表")
    
    def _get_random_ua(self):
        """取得隨機 User-Agent"""
        if self.has_fake_ua:
            return self.ua.random
        return random.choice(self.user_agents)
    
    def _random_delay(self, min_sec=1, max_sec=3):
        """隨機延遲"""
        time.sleep(random.uniform(min_sec, max_sec))
    
    def fetch(self, year, month):
        """
        爬取月營收資料
        
        Args:
            year (int): 西元年
            month (int): 月份
        
        Returns:
            pd.DataFrame or None
        """
        print(f"\n{'='*60}")
        print(f" 爬取 {year}年{month}月 台股月營收資料")
        print(f"{'='*60}\n")
        
        if self.use_selenium:
            return self._fetch_with_selenium(year, month)
        else:
            return self._fetch_with_requests(year, month)
    
    def _fetch_with_requests(self, year, month):
        """使用 requests 爬取（含反爬蟲技術）"""
        roc_year = year - 1911
        
        # 更新 headers
        headers = {
            'User-Agent': self._get_random_ua(),
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
            'Accept-Language': 'zh-TW,zh;q=0.9',
            'Connection': 'keep-alive',
            'Referer': 'https://mops.twse.com.tw/',
        }
        self.session.headers.update(headers)
        
        print(f"[User-Agent] {headers['User-Agent'][:70]}...")
        
        # 步驟1: 訪問首頁
        try:
            self.session.get('https://mops.twse.com.tw/', timeout=10)
            print("[步驟1] 訪問首頁 ✓")
            self._random_delay(1, 2)
        except:
            print("[步驟1] 訪問首頁失敗")
        
        # 步驟2: 訪問查詢頁
        try:
            self.session.get('https://mops.twse.com.tw/mops/web/t21sc03_1', timeout=10)
            print("[步驟2] 訪問查詢頁 ✓")
            self._random_delay(1, 2)
        except:
            print("[步驟2] 訪問查詢頁失敗")
        
        # 步驟3: 嘗試多種 URL
        print("\n[步驟3] 嘗試取得資料...")
        
        urls = [
            f'https://mops.twse.com.tw/nas/t21/sii/t21sc03_{roc_year}_{month}_0.html',
            f'https://mops.twse.com.tw/nas/t21/sii/t21sc03_{roc_year}_{month:02d}_0.html',
        ]
        
        for i, url in enumerate(urls, 1):
            print(f"\n  [{i}] {url}")
            try:
                resp = self.session.get(url, timeout=10)
                resp.encoding = 'big5'
                
                if resp.status_code == 200 and '<table' in resp.text:
                    print(f"      ✓ 成功！")
                    return self._parse_table(resp.text, year, month)
                else:
                    print(f"      ✗ Status {resp.status_code}")
            except Exception as e:
                print(f"      ✗ {e}")
            
            self._random_delay(0.5, 1)
        
        print("\n[失敗] 無法透過 requests 取得資料")
        print("      建議使用 Selenium 方法")
        return None
    
    def _fetch_with_selenium(self, year, month):
        """使用 Selenium 爬取（最穩定但較慢）"""
        try:
            from selenium import webdriver
            from selenium.webdriver.common.by import By
            from selenium.webdriver.support.ui import Select, WebDriverWait
            from selenium.webdriver.support import expected_conditions as EC
            from selenium.webdriver.chrome.options import Options
            
            print("[INFO] 啟動 Selenium...")
            
            # Chrome 選項
            options = Options()
            options.add_argument('--headless')  # 無頭模式
            options.add_argument('--no-sandbox')
            options.add_argument('--disable-dev-shm-usage')
            options.add_argument('--disable-blink-features=AutomationControlled')
            options.add_argument(f'user-agent={self._get_random_ua()}')
            
            driver = webdriver.Chrome(options=options)
            driver.set_page_load_timeout(30)
            
            try:
                roc_year = year - 1911
                
                # 使用正確的 URL（直接訪問查詢頁面）
                # MOPS 網站結構已變更，使用 hash 路由
                url = f'https://mops.twse.com.tw/mops/#/web/t21sc03?step=1&firstin=1&TYPEK=sii&year={roc_year}&month={month}'
                
                print(f"[步驟1] 直接訪問查詢URL...")
                print(f"        {url}")
                driver.get(url)
                
                # 等待頁面載入
                print(f"[步驟2] 等待結果載入...")
                time.sleep(5)  # AJAX 載入需要時間
                
                # 檢查是否需要點擊查詢按鈕
                try:
                    # 嘗試尋找並點擊查詢按鈕（如果存在）
                    wait = WebDriverWait(driver, 3)
                    search_btn = wait.until(
                        EC.element_to_be_clickable((By.XPATH, "//*[contains(text(),'查詢')]"))
                    )
                    search_btn.click()
                    print("        點擊查詢按鈕")
                    time.sleep(3)
                except:
                    # 沒有查詢按鈕也沒關係，可能已經載入資料
                    print("        無需點擊按鈕（資料已載入）")
                
                # 截圖結果
                driver.save_screenshot('selenium_result.png')
                print("        已截圖：selenium_result.png")
                
                # 取得 HTML
                print(f"[步驟3] 取得頁面內容...")
                html = driver.page_source
                
                # 檢查頁面標題
                print(f"        頁面標題：{driver.title}")
                print(f"        當前URL：{driver.current_url}")
                
                # 檢查是否有錯誤訊息
                if '查詢無資料' in html or '無符合' in html or '查無資料' in html:
                    print("        ✗ 查詢無資料")
                    return None
                
                # 保存HTML供檢查
                with open('selenium_result.html', 'w', encoding='utf-8') as f:
                    f.write(html)
                print("        已保存：selenium_result.html")
                
                # 解析表格
                print(f"[步驟4] 解析表格...")
                result = self._parse_table(html, year, month)
                
                if result is not None:
                    print("\n✓ Selenium 成功取得資料！")
                else:
                    print("\n✗ Selenium 無法解析表格")
                    print("   請檢查 selenium_result.html 和 selenium_result.png")
                
                return result
                
            finally:
                driver.quit()
                
        except ImportError:
            print("\n[錯誤] 請先安裝 Selenium:")
            print("       pip install selenium")
            print("       並下載 ChromeDriver")
            return None
        except Exception as e:
            print(f"\n[錯誤] Selenium 執行失敗: {e}")
            import traceback
            traceback.print_exc()
            return None
    
    def _parse_table(self, html, year, month):
        """解析 HTML 表格"""
        try:
            dfs = pd.read_html(StringIO(html))
            
            for df in dfs:
                col_str = ' '.join([str(c) for c in df.columns])
                
                if '公司代號' in col_str or '公司 代號' in col_str:
                    # 處理多層欄位
                    if isinstance(df.columns, pd.MultiIndex):
                        df.columns = df.columns.get_level_values(-1)
                    
                    # 清理欄位
                    df.columns = [str(c).strip() for c in df.columns]
                    
                    print(f"\n[結果] 取得 {len(df)} 筆資料")
                    print(f"       欄位: {', '.join(df.columns[:5].tolist())}...")
                    
                    # 存檔
                    filename = f'revenue_{year}_{month:02d}.csv'
                    df.to_csv(filename, index=False, encoding='utf-8-sig')
                    print(f"\n[儲存] {filename}")
                    
                    # 顯示範例
                    print(f"\n前3筆:")
                    print(df.head(3).to_string())
                    
                    return df
            
            print("[警告] 沒有找到營收表格")
            return None
            
        except Exception as e:
            print(f"[錯誤] 解析失敗: {e}")
            return None


# ============================================================================
# 簡易使用範例
# ============================================================================

def main():
    """簡易測試"""
    print("\n" + "="*60)
    print("  台股月營收爬蟲 - 簡易版".center(50))
    print("="*60)
    
    # 方法1: 使用 requests（快速但可能失敗）
    print("\n[方法1] 使用 requests + 反爬蟲技術\n")
    scraper1 = RevenueScraper(use_selenium=False)
    df1 = scraper1.fetch(2024, 10)
    
    if df1 is None:
        # 方法2: 使用 Selenium（較慢但成功率高）
        print("\n" + "="*60)
        print("\n[方法2] 使用 Selenium 模擬瀏覽器\n")
        
        try:
            scraper2 = RevenueScraper(use_selenium=True)
            df2 = scraper2.fetch(2024, 10)
            
            if df2 is not None:
                print("\n✓ 成功！")
            else:
                print("\n✗ Selenium 也失敗了")
                print("\n建議:")
                print("  1. 該月份資料可能尚未公布")
                print("  2. 請確認已安裝 ChromeDriver")
                print("  3. 或手動到 MOPS 網站下載")
        except:
            print("\n[INFO] Selenium 不可用，請參考 README 安裝")
    else:
        print("\n✓ 完成！")


if __name__ == "__main__":
    main()
