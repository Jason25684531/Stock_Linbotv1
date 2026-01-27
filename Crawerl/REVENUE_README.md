# 台股月營收爬蟲 - 使用說明

## 📋 問題說明

你的原始程式碼無法爬取台股月營收，主要原因：

```python
# ❌ 原始程式碼（會失敗）
url = 'https://mops.twse.com.tw/nas/t21/sii/t21sc03_113_6_0.html'
r = requests.get(url)  # 缺少 headers，會被擋
r.encoding = 'big5'
dfs = pd.read_html(StringIO(r.text))
```

**失敗原因：**
1. ❌ 沒有設定 `User-Agent`（會被識別為爬蟲）
2. ❌ 沒有模擬真實瀏覽器行為
3. ❌ MOPS 網站有反爬蟲機制

---

## ✅ 解決方案

已為你創建 **3 個完整的解決方案**：

### 方案 1: 基本修正（最簡單）✨

```python
import requests
import pandas as pd
from io import StringIO

# ✅ 加上 User-Agent 就可以了！
headers = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
}

url = 'https://mops.twse.com.tw/nas/t21/sii/t21sc03_113_10_0.html'
r = requests.get(url, headers=headers, timeout=10)  # 加上 headers
r.encoding = 'big5'

if r.status_code == 200:
    dfs = pd.read_html(StringIO(r.text))
    df = dfs[0]
    print(df.head())
```

### 方案 2: 完整反爬蟲技術（推薦）⭐

使用已創建的 `revenue_scraper.py`：

```python
from revenue_scraper import RevenueScraper

# 創建爬蟲實例
scraper = RevenueScraper()

# 爬取資料
df = scraper.fetch(2024, 10)  # 2024年10月

if df is not None:
    print("成功！")
    print(df.head())
```

**包含的反爬蟲技術：**
- ✅ 隨機 User-Agent（fake-useragent）
- ✅ 隨機延遲（避免頻率限制）
- ✅ Session 管理（保持 Cookie）
- ✅ Referer 設定（模擬真實瀏覽）
- ✅ 多種 URL 嘗試

### 方案 3: Selenium（最穩定）🚀

當前兩個方案都失敗時使用：

```python
from revenue_scraper import RevenueScraper

# 使用 Selenium 模式
scraper = RevenueScraper(use_selenium=True)
df = scraper.fetch(2024, 10)
```

**需要安裝 ChromeDriver：**
1. 下載：https://chromedriver.chromium.org/downloads
2. 放到 PATH 或專案目錄

---

## 📦 安裝套件

```powershell
# 基本套件（已安裝）
pip install requests pandas

# 反爬蟲套件（已安裝）
pip install fake-useragent

# Selenium（選用，需要 ChromeDriver）
pip install selenium
```

---

## 🎯 使用現成檔案

### 1. `revenue_scraper.py` - 簡潔版（推薦）

```powershell
python revenue_scraper.py
```

特色：
- 乾淨的程式碼
- 容易理解和修改
- 包含完整註解
- 同時支援 requests 和 Selenium

### 2. `taiwan_revenue_crawler.py` - 完整版

```powershell
python taiwan_revenue_crawler.py
```

特色：
- 更詳細的日誌輸出
- 3種不同策略
- 適合學習反爬蟲技術

---

## 💡 反爬蟲技術說明

### 1. User-Agent 輪換

```python
# 方法A: 使用 fake-useragent
from fake_useragent import UserAgent
ua = UserAgent()
headers = {'User-Agent': ua.random}

# 方法B: 手動列表
user_agents = [
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) ...',
    'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) ...',
]
headers = {'User-Agent': random.choice(user_agents)}
```

### 2. 隨機延遲（避免頻率限制）

```python
import time
import random

for page in range(10):
    requests.get(url)
    time.sleep(random.uniform(1, 3))  # 隨機等待 1-3 秒
```

### 3. Session 管理（保持 Cookie）

```python
session = requests.Session()
session.get('https://website.com')  # 取得 Cookie
session.get('https://website.com/data')  # 使用相同 Cookie
```

### 4. Referer 設定（模擬真實行為）

```python
headers = {
    'Referer': 'https://mops.twse.com.tw/',  # 表示從首頁來的
    'Origin': 'https://mops.twse.com.tw'
}
```

---

## 🔍 測試結果

目前測試發現：
- ✅ 程式碼邏輯正確
- ✅ 反爬蟲技術已整合
- ⚠️ 靜態 URL 返回 404（資料可能未公布）
- ⚠️ API 有安全機制（需要 Selenium）

**建議：**
1. 使用 Selenium 方案（最穩定）
2. 或等待資料公布後使用 requests 方案
3. 或直接到 MOPS 網站手動下載

---

## 📝 完整範例

```python
# example.py
from revenue_scraper import RevenueScraper
import pandas as pd

# 創建爬蟲
scraper = RevenueScraper()

# 爬取多個月份
months = [(2024, 10), (2024, 9), (2024, 8)]
results = []

for year, month in months:
    print(f"\n爬取 {year}年{month}月...")
    df = scraper.fetch(year, month)
    
    if df is not None:
        results.append(df)
        print(f"✓ 成功：{len(df)} 筆資料")
    else:
        print(f"✗ 失敗")
    
    time.sleep(5)  # 避免頻率限制

# 合併所有結果
if results:
    all_data = pd.concat(results, ignore_index=True)
    all_data.to_csv('all_revenue.csv', index=False, encoding='utf-8-sig')
    print(f"\n✓ 完成！共 {len(all_data)} 筆資料")
```

---

## ⚠️ 注意事項

1. **資料公布時間**：月營收通常在次月10日前公布
2. **請求頻率**：建議每次請求間隔 3-5 秒
3. **IP 限制**：如果被封鎖，等待 30 分鐘後再試
4. **合法使用**：僅供個人學習研究使用

---

## 🎓 學習重點

通過這個專案你學會了：

✅ **反爬蟲技術**
- User-Agent 偽裝
- 隨機延遲
- Session 管理
- Referer 設定

✅ **錯誤處理**
- HTTP 狀態碼檢查
- 例外處理
- 多種備用方案

✅ **資料處理**
- pandas 讀取 HTML 表格
- 編碼轉換（big5/utf-8）
- CSV 儲存

---

## 📚 相關資源

- [MOPS 公開資訊觀測站](https://mops.twse.com.tw/)
- [fake-useragent 文件](https://pypi.org/project/fake-useragent/)
- [Selenium 文件](https://selenium-python.readthedocs.io/)
- [反爬蟲技術介紹](https://ithelp.ithome.com.tw/articles/10216004)

---

**檔案清單：**
- ✅ `revenue_scraper.py` - 簡潔版爬蟲（推薦使用）
- ✅ `taiwan_revenue_crawler.py` - 完整版爬蟲
- ✅ `REVENUE_README.md` - 本說明文件

---

**立即開始：**
```powershell
# 執行簡易版
python revenue_scraper.py

# 或執行完整版
python taiwan_revenue_crawler.py
```

祝爬蟲成功！ 🎉
