# Tasks: V32 Upgrade Plan (Backtest Realism & Dashboard)

## Phase 1: Backtest Realism (回測擬真化) ✅
- [x] **Config Update**: Modify `config.py` to add `SLIPPAGE_RATE = 0.002` (0.2%) and `RISK_FREE_RATE = 0.01`.
- [x] **Slippage Logic**: Update `4_run_backtest.py` -> `buy()` and `sell()` functions to apply slippage (Buy high, Sell low).
- [x] **Metrics Upgrade**: Implement MDD (Max Drawdown) and Sharpe Ratio calculation in `4_run_backtest.py`.
- [x] **Data Export**: Ensure backtest results are saved to `ML_Data/backtest_result.csv` with a standard timestamp format for the Dashboard to read.

## Phase 2: Web Dashboard Infrastructure ✅
- [x] **Route Setup**: Add `/dashboard` route in `app.py`.
- [x] **Base Template**: Create `templates/base.html` with TailwindCSS and Alpine.js (CDN versions).
- [x] **Dashboard UI**: Create `templates/dashboard.html` extending `base.html`.
    - *Reference `openspec/specs/frontend-design.md` for styling.*
- [x] **API Endpoint**: Create `/api/performance` in `app.py` to serve the latest JSON data from `ML_Data/backtest_result.csv`.

## Phase 3: Data Visualization ✅
- [x] **Chart Integration**: Add Chart.js to `templates/dashboard.html`.
- [x] **Equity Curve**: Implement a line chart fetching data from `/api/performance` to show Asset Value over time.
- [x] **Stock List Table**: Render the daily stock selection list (V30/V31) in a responsive table using Alpine.js `x-for`.

## Phase 4: Integration & Verification ✅
- [x] **End-to-End Test**: Run a full backtest (`4_run_backtest.py`) -> Start Server (`app.py`) -> Check Dashboard charts.
- [x] **Compare**: Verify that the "Realism" ROI is lower (more realistic) than the old V30 ROI.