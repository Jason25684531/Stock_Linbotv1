# V33 Phase 2+ Hotfix Summary

## 🐛 問題修復完成 (2026-01-09)

### 錯誤原因
在 V33 Phase 2+ 重構時，將所有參數統一移至 `Config` 類別，但以下檔案仍使用舊的導入方式：
- `5_push_to_line.py` - Line 日報推播
- `4_run_backtest.py` - 回測引擎
- `tool/strategy.py` - 策略模組內部引用

### 修復內容

#### 1. tool/strategy.py (line 456)
```python
# Before
"max_hold_days": V30_PARAMS['MAX_HOLD_DAYS'],

# After
"max_hold_days": params['MAX_HOLD_DAYS'],
```

#### 2. 5_push_to_line.py (line 6)
```python
# Before
from tool.strategy import get_v30_candidates, V30_PARAMS, calculate_v30_signal

# After
from tool.strategy import get_v30_candidates, get_v30_params_from_db, calculate_v30_signal
```

#### 3. 4_run_backtest.py (line 19)
```python
# Before
from tool.strategy import get_v30_candidates, get_v30_params_from_db, V30_PARAMS

# After
from tool.strategy import get_v30_candidates, get_v30_params_from_db
```

### 驗證結果

✅ 所有檔案語法檢查通過
✅ VSCode 無錯誤提示
✅ 文檔已更新（UpdateList.md + README.md）

### 文檔更新

#### UpdateList.md
- ✅ 新增 Hotfix 章節
- ✅ 詳細記錄錯誤原因與修復步驟
- ✅ 新增完整的「更新流程與測試啟動方式」
- ✅ 包含快速檢查清單
- ✅ 常見問題排查指南

#### README.md
- ✅ 更新「每日更新流程」章節
- ✅ 修正指令範例（使用 `python -c` 執行 calc_indicators）
- ✅ 新增「快速檢查清單」表格
- ✅ 新增「語法檢查」章節
- ✅ 優化指令說明與預期結果

### 測試指令速查

```powershell
# 每日更新（推薦）
python 2_rundaily.py

# 本地測試
python debug_local.py

# 回測驗證
python 4_run_backtest.py          # V31 (預設)
python 4_run_backtest.py --v30    # V30

# 重新訓練（含情緒特徵）
python 3_train_model.py

# 語法檢查
python -m py_compile <filename>.py
```

### 核心改進點

1. **統一參數管理**：所有參數統一使用 `Config` 或 `get_v30_params_from_db()`
2. **向後相容**：舊的 `get_v30_params_from_db()` 函數保持可用
3. **清晰文檔**：UpdateList.md 提供完整的更新與測試流程
4. **快速驗證**：README.md 提供檢查清單，方便開發者驗證

---

**修復完成時間**: 2026-01-09  
**影響範圍**: 3 個核心檔案  
**向後相容性**: ✅ 完全相容
