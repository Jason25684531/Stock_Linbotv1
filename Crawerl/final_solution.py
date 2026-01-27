# -*- coding: utf-8 -*-
"""
最終解決方案：手動操作 + 直接下載

由於 MOPS 網站已改版為 SPA（單頁應用），
Selenium 難以穩定抓取。

提供兩個實際可行的方案：
"""

import requests
import pandas as pd
from io import StringIO
import webbrowser

print("="*70)
print("  台股月營收 - 實際解決方案")
print("="*70)

# 方案1：直接開啟MOPS網站手動下載
print("\n【方案1】手動下載（最穩定）")
print("-"*70)
print("步驟：")
print("  1. 點擊下方連結開啟 MOPS 網站")
print("  2. 輸入年份和月份")
print("  3. 點擊「查詢」")
print("  4. 點擊「彙總報表」->「下載CSV」")
print("\n正在開啟瀏覽器...")

# 開啟MOPS網站
mops_url = "https://mops.twse.com.tw/mops/web/t21sc03_q5"
webbrowser.open(mops_url)
print(f"✓ 已開啟：{mops_url}")

input("\n按 Enter 繼續查看其他方案...")

# 方案2：說明為什麼爬蟲失敗
print("\n\n【方案2】為什麼自動爬蟲失敗？")
print("-"*70)
print("""
原因分析：

1. ❌ MOPS 網站已改版
   • 舊版：server-side rendering（容易爬）
   • 新版：Single Page Application（難爬）
   • URL會自動redirect到首頁

2. ❌ 靜態檔案可能已移除
   • 測試的 URL 全部返回 404
   • 2024年10月的資料應該已公布
   • 但靜態HTML檔案不存在

3. ❌ 需要 JavaScript 渲染
   • 資料透過 AJAX動態載入
   • Selenium 會被重定向
   • 需要複雜的等待和互動邏輯

4. ✅ 你的程式碼和技術都是正確的！
   • User-Agent 設定：正確
   • 隨機延遲：正確
   • Session管理：正確
   • 只是網站結構變了
""")

# 方案3：使用政府開放資料
print("\n【方案3】使用政府開放資料API")
print("-"*70)

print("\n嘗試從證交所開放資料平台取得...")
try:
    # 嘗試證交所公開API
    url = "https://openapi.twse.com.tw/v1/opendata/t187ap03_L"
    
    print(f"請求：{url}")
    resp = requests.get(url, timeout=10)
    
    if resp.status_code == 200:
        data = resp.json()
        if data:
            print(f"✓ 成功！取得 {len(data)} 筆資料")
            df = pd.DataFrame(data)
            print(f"\n欄位：{df.columns.tolist()}")
            print(f"\n前3筆：")
            print(df.head(3))
            
            # 儲存
            df.to_csv('opendata_stock.csv', index=False, encoding='utf-8-sig')
            print(f"\n已儲存：opendata_stock.csv")
        else:
            print("✗ 無資料")
    else:
        print(f"✗ HTTP {resp.status_code}")
        
except Exception as e:
    print(f"✗ 錯誤：{e}")

# 總結
print("\n\n" + "="*70)
print("  總結與建議")
print("="*70)

print("""
實際可行的方案：

✅ 【最推薦】手動下載
   • 到 MOPS 網站手動查詢並下載 CSV
   • 網址：https://mops.twse.com.tw/mops/web/t21sc03_q5
   • 優點：100%成功，資料最完整
   • 缺點：需要人工操作

✅ 【進階】使用 FinMind API
   • 需註冊：https://finmindtrade.com/
   • 優點：穩定的API，資料齊全
   • 缺點：需要註冊，有請求限制

✅ 【替代】證交所開放資料
   • 剛才測試的方式
   • 優點：不需註冊，免費
   • 缺點：資料可能不是月營收

⚠️  【不推薦】繼續爬 MOPS
   • 網站已改版為SPA
   • 需要非常複雜的 Selenium 邏輯
   • 不穩定，容易失敗
   • 可能違反使用條款

你的反爬蟲技術學習目標已達成！
真實專案中，遇到這種狀況就改用 API 或手動下載。
""")

print("="*70)
