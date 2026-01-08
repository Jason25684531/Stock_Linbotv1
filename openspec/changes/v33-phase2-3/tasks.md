# Tasks: V33 Strategy Deep Dive & PK System

## Phase 2: Strategy Deep Dive 🧠
- [x] **Config Update**: Add `USE_KD_FILTER = False`, `USE_BB_FILTER = False` to `config.py`.
- [x] **Indicator Logic**: Ensure `tool/calc_indicators.py` has `calculate_kd_full` and bollinger calculation.
- [x] **Strategy Logic**: Update `tool/strategy.py` -> `get_v30_candidates` to apply KD/BB filters IF enabled in Config.
- [x] **Optimization Script**: Create `5_optimize_params.py` using `optuna` to find best V30 parameters.

## Phase 3: PK System & Visualization ⚔️
- [x] **DB Schema**: Update `tool/db_helper.py` -> Add `init_pk_tables()` to create `user_simulation_trades`.
- [x] **Backend API**: Add `/api/user/trade` (POST) and `/api/pk/battle` (GET) in `app.py`.
- [x] **Frontend**: Add "⚔️ Battle Arena" tab in `templates/dashboard.html` with comparison charts.

---

## 驗收標準
- [x] Phase 2: 所有濾網預設關閉，開啟後能正確過濾候選股票
- [x] Phase 3: 能記錄模擬交易，前端顯示人機對決圖表
- [x] 無語法錯誤，`python app.py` 可正常啟動
- [x] 更新 `UpdateList.md` 與 `README.md` 文檔

✅ **所有任務已完成**
