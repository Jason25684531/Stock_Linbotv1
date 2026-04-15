# Dashboard 前端修復測試指南

## 修復內容

### 問題 1：即時選股訊號標籤無法切換
**原因分析：**
- 前端 JavaScript 邏輯正常，但缺少調試信息無法診斷問題
- 可能是用戶端瀏覽器快取或 Alpine.js 響應式更新問題

**修復方案：**
1. ✅ 添加了詳細的 Console 日誌輸出
   - 點擊策略標籤時會記錄：`🖱️ 點擊策略: V35 經營效益 v35_innovation`
   - API 請求時會記錄：`🔍 載入即時訊號 - 策略: v35_innovation TopN: 5`
   - 成功載入時會記錄：`✅ 即時訊號載入成功: V35 經營效益策略 數量: 5`

2. ✅ 增強了標籤按鈕的視覺回饋
   - 選中的策略標籤會加粗顯示（`font-bold`）
   - 確保 Alpine.js 的響應式更新正確觸發

3. ✅ 添加了 `$nextTick()` 強制 DOM 更新

### 問題 2：交易歷史只顯示 V37
**原因分析：**
- 數據庫實際上有混合策略的記錄（V35: 16筆, V36: 21筆, V37: 13筆）
- API 返回數據正確
- 前端顯示邏輯正常

**根本原因：** 
- 用戶看到的可能是瀏覽器快取的舊數據
- 或者是在數據更新前的截圖

**修復方案：**
1. ✅ 添加了策略分布統計日誌
   - 載入交易數據時會在 Console 顯示：`📊 策略分布: {v35_innovation: 16, v36_chip_momentum: 21, v37_mean_reversion: 13}`

2. ✅ 改進了策略標籤的渲染邏輯
   - 正確處理 null/undefined 策略值
   - 優化了策略名稱映射

---

## 測試步驟

### 1. 清除瀏覽器快取
```
Chrome/Edge: Ctrl + Shift + Delete
Firefox: Ctrl + Shift + Delete
或使用無痕模式: Ctrl + Shift + N
```

### 2. 啟動 Flask 應用
```powershell
cd D:\01_Project\Stocke\Stock_Linbotv1
python app.py
```

說明：根目錄 `app.py` 目前是 compatibility facade，正式 Web route 由 `app/web_server.py` 註冊，LINE webhook 由 `app/line_bot.py` 註冊。

### 3. 打開瀏覽器開發者工具
1. 訪問: http://localhost:1688/dashboard
2. 登入 (如果需要)
3. 按 F12 打開開發者工具
4. 切換到 Console 標籤

### 4. 測試即時選股訊號切換
1. **觀察初始狀態**
   - Console 應該顯示：`✅ 交易數據載入成功: 50 筆`
   - Console 應該顯示：`📊 策略分布: {...}`
   - Console 應該顯示：`✅ 即時訊號載入成功: XXX 策略 數量: X`

2. **點擊不同策略標籤**
   - 點擊 "V35 經營效益"
   - Console 應該顯示：
     ```
     🖱️ 點擊策略: V35 經營效益 v35_innovation
     🔍 載入即時訊號 - 策略: v35_innovation TopN: 5
     ✅ 即時訊號載入成功: V35 經營效益策略 數量: 5
     📊 DOM 更新完成
     ```
   - 畫面應該更新顯示 V35 策略的選股結果

3. **切換多個策略測試**
   - 依次點擊 V31、V33、V34、V35、V36、V37、V38
   - 每次點擊都應該：
     - 標籤樣式改變（藍色背景+加粗）
     - Console 顯示相應日誌
     - 選股列表更新

### 5. 測試交易歷史篩選
1. **檢查初始數據**
   - Console 應該顯示：`📊 策略分布: {v35_innovation: 16, v36_chip_momentum: 21, v37_mean_reversion: 13}`
   - 頁面應該顯示混合策略的交易記錄（V35、V36、V37 標籤）

2. **使用策略篩選下拉選單**
   - 選擇 "V35 經營效益"
   - 只顯示 V35 的交易記錄（顯示 16 筆）
   - 選擇 "V36 籌碼動能"  
   - 只顯示 V36 的交易記錄（顯示 21 筆）
   - 選擇 "🎯 全部策略"
   - 顯示所有交易記錄（顯示 50 筆）

---

## 預期結果

### ✅ 即時選股訊號正常工作
- 點擊任何策略標籤，選股列表立即更新
- Console 顯示完整的日誌流程
- 選中的標籤有視覺反饋（藍色+加粗）

### ✅ 交易歷史正確顯示
- 交易記錄包含 V35、V36、V37 三種策略
- 策略標籤顏色正確（V35紫色、V36青色、V37粉色）
- 篩選功能正常工作

---

## 如果仍有問題

### 問題 A：點擊標籤沒有反應
**可能原因：**
1. JavaScript 載入失敗
2. Alpine.js 初始化錯誤

**診斷步驟：**
```javascript
// 在 Console 中執行
console.log('Alpine.js 版本:', typeof Alpine !== 'undefined' ? 'OK' : 'MISSING');
console.log('當前策略:', document.querySelector('[x-data]').__x);
```

### 問題 B：交易記錄全部顯示同一策略
**可能原因：**
1. 數據庫只有單一策略的數據（需要執行組合回測）

**解決方案：**
```powershell
# 執行組合回測，生成混合策略的交易記錄
python 4_run_backtest.py --portfolio --strategies v35_innovation,v36_chip_momentum,v37_mean_reversion --weights 3,3,4
```

---

## 驗證 API 數據

可以直接訪問 API 端點驗證數據：

### 交易歷史 API
```
http://localhost:1688/api/trades
```
應該返回包含多種策略的 JSON 數據

### 即時選股 API  
```
http://localhost:1688/api/daily-signals?strategy=v35&top_n=5
http://localhost:1688/api/daily-signals?strategy=v36&top_n=5
http://localhost:1688/api/daily-signals?strategy=v37&top_n=5
```
每個端點應該返回不同策略的選股結果

---

## 技術細節

### 修改的文件
- `templates/dashboard.html`
  - `loadLiveSignals()` 函數：添加日誌和強制 DOM 更新
  - `loadTrades()` 函數：添加策略分布統計
  - 策略標籤按鈕：添加點擊日誌和加粗樣式
  - 交易記錄表格：改進策略名稱映射

### 新增的調試文件
- `check_trades.py` - 檢查數據庫交易記錄
- `test_api.py` - 測試 API 端點
- `test_frontend_fix.py` - 綜合測試腳本

---

## 聯絡與支援

如果測試後仍有問題，請提供：
1. 瀏覽器 Console 的完整日誌
2. Network 標籤中 API 請求的 Response
3. 當前啟用的策略設定

修復完成日期：2026-02-15
