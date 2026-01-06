# Project Context: Stock Linbot V1

## Project Overview
Stock Linbot V1 是一個針對台股市場的自動化量化交易與分析系統。它結合了傳統技術指標 (V30) 與機器學習模型 (V31 XGBoost) 來產生交易訊號，並透過 Line Bot 提供即時互動，目前正朝向 V32 (Web Dashboard & 擬真回測) 邁進。

## Tech Stack
- **Language**: Python 3.10+
- **Web Framework**: Flask (Line Bot Webhook & Dashboard Backend)
- **Database**: MySQL 8.0 (Dockerized), SQLAlchemy ORM
- **Data Analysis**: Pandas, NumPy, TA-Lib (Technical Indicators)
- **Machine Learning**: XGBoost, Scikit-learn, Joblib
- **Frontend (V32+)**: HTML5, TailwindCSS (CDN), Alpine.js (CDN), Chart.js (CDN)
- **Scheduling**: Windows Task Scheduler

## Architecture
1.  **ETL Layer**: `1_update_database.py` (Daily crawl), `calc_indicators.py`.
2.  **Strategy Layer**: `tool/Strategy.py` (Logic core), `3_train_model.py` (AI training).
3.  **Backtest Engine**: `4_run_backtest.py` (Simulation).
4.  **Application Layer**: `app.py` (Line Bot interface & Web Dashboard).

## Directory Structure
- `tool/`: 核心邏輯 (db_helper, strategy, indicators).
- `ML_Model/`: 存放訓練好的 XGBoost 模型 (.json/.pkl).
- `ML_Data/`: 存放回測結果與訓練資料 (.csv).
- `templates/`: Flask HTML 模板 (V32 Dashboard).
- `openspec/`: 開發規範與變更管理.