# 📅 Change Spec: V35 Financial Data Upgrade (財報數據升級計畫)

## 1. Context & Objective
* **Status**: Active
* **Owner**: Jason Ho
* **Goal**: 解決 MOPS 彙總報表缺少「研發費用」導致 V35 策略失效的問題。
* **Solution**:
    1.  改為抓取 **「營業費用 (Operating Expense)」** 與 **「營業利益 (Operating Profit)」**。
    2.  計算 **「營業利益率 (Operating Margin)」** 作為替代的基本面指標。
    3.  將爬蟲源切換至 `mopsov` (備援站) 以提升穩定性。

## 2. Task List

### Phase 1: Database Schema (資料庫架構)
- [x] **DB-01: Expand Schema**
    -   **File**: `tool/add_operating_columns.py`
    -   **Action**: Add `operating_expense` (BIGINT) and `operating_profit` (BIGINT) columns to `financial_statements` table.
    -   **Status**: ✅ Completed (欄位已存在).

### Phase 2: Crawler Core Refactoring (爬蟲核心重構)
- [x] **CRW-01: Switch to Backup Source**
    -   **File**: `tool/crawlers/quarterly_scraper.py`
    -   **Action**: Change target URL to `https://mopsov.twse.com.tw/mops/web/ajax_t163sb04`.
    -   **Status**: ✅ Completed.
- [x] **CRW-02: Robust HTML Parsing**
    -   **File**: `tool/crawlers/quarterly_scraper.py`
    -   **Action**: Implement error handling for "No tables found" and add random delay mechanism.
    -   **Status**: ✅ Completed (3-6秒隨機延遲).
- [x] **CRW-03: Column Mapping**
    -   **File**: `tool/crawlers/quarterly_scraper.py`
    -   **Action**: Update extraction logic to capture `營業費用` and `營業利益`, handling potential missing columns by defaulting to 0.
    -   **Status**: ✅ Completed (改進列名匹配邏輯).

### Phase 3: Update Tools (更新工具腳本)
- [x] **TOOL-01: Single Quarter Updater**
    -   **File**: `tool/update_financials_mops.py`
    -   **Action**: Create a standardized script to fetch and update financial data for a specific quarter.
    -   **Status**: ✅ Completed (支持命令行参数和 dry-run 模式).
- [x] **TOOL-02: Historical Batch Updater**
    -   **File**: `tool/update_history_financials.py`
    -   **Action**: Create a script to batch update historical data (e.g., Year 110-113) with delays to prevent IP ban.
    -   **Status**: ✅ Completed (10秒延遲，支持確認提示).

### Phase 4: System Integration (系統整合)
- [x] **INT-01: Data Merging Logic**
    -   **File**: `2_rundaily.py`
    -   **Action**: Update `merge_financial_data` function to query `operating_profit` and `operating_expense`.
    -   **Status**: ✅ Completed.
- [x] **INT-02: Metric Calculation**
    -   **File**: `2_rundaily.py`
    -   **Action**: Calculate `op_profit_margin = operating_profit / revenue`.
    -   **Status**: ✅ Completed (SQL 直接計算).
- [x] **INT-03: Output Optimization**
    -   **File**: `2_rundaily.py`
    -   **Action**: Update console output to display `OpMg` (Operating Margin) in the recommendation list.
    -   **Status**: ✅ Completed (Top 5 推薦顯示營業利益率).

## 3. Verification Plan
1.  Run `tool/update_financials_mops.py` and verify `financial_statements` table has data in new columns.
2.  Run `2_rundaily.py` and verify console output shows "OpMg: XX%".