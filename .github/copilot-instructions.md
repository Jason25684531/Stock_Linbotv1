# AI Coding Instructions: Stock Linbot V35

## Project Overview
Taiwan stock trading system combining ML (XGBoost) with technical analysis. Delivers real-time stock recommendations via Line Bot and Web Dashboard, with multi-strategy backtesting engine supporting 4+ parallel strategies (V31/V33/V34/V35).

## Critical Architecture Patterns

### 1. Layered Dependency Flow (Top-Down)
```
Application (app.py, 1-7_*.py) 
  ↓ 
Backtest Layer (4_run_backtest.py) 
  ↓ 
Strategy Layer (tool/strategy_manager.py → tool/strategies/*) 
  ↓ 
Indicators (tool/calc_indicators.py) 
  ↓ 
Data Layer (tool/db_helper.py - SINGLE entry point)
  ↓ 
Config (.env + config.py)
```

**NEVER bypass this flow**: All DB operations MUST go through `tool.db_helper` to prevent SQL injection and maintain testability. Do NOT write raw SQL in application files.

### 2. Strategy Factory Pattern (Core Design)
All strategies inherit from `BaseStrategy` (abstract class) and implement:
- `filter_candidates(df)`: Technical screening logic
- Properties: `name`, `features`, `target_return`, `look_ahead_days`, `stop_loss`

**Creating new strategies**: Copy `tool/strategies/v33_low_vol.py` as template, register in `StrategyManager.STRATEGY_REGISTRY`, update `strategy_settings.json`.

**Multi-strategy parallelism** (V2): `StrategyManager` supports running V33+V34 simultaneously via `active_strategies: List[str]` in settings file.

### 3. Singleton Settings Management
- `StrategyManager`: Singleton managing `strategy_settings.json` 
- `tool.db_helper`: Database settings via `user_settings` table
- `config.py`: Environment-based configuration (DB_URL, LINE tokens from `.env`)

**Settings priority**: `.env` > `strategy_settings.json` > `user_settings` table > hardcoded defaults

## Essential Developer Workflows

### Daily Execution Pipeline (Production)
```powershell
# Complete daily workflow
python 1_update_database.py    # Fetch TWSE/TPEX price data
python 7_update_financials.py  # Update quarterly financials from OpenAPI
python 3_train_model.py        # Retrain XGBoost model
python 2_rundaily.py           # Calculate indicators for all stocks
python 5_push_to_line.py       # Send recommendations to Line Bot

# Start web dashboard
python app.py                  # Access at http://localhost:5000
```

### Backtesting & Optimization
```powershell
# Single strategy backtest
python 4_run_backtest.py --v31

# Multi-strategy portfolio backtest (default)
python 4_run_backtest.py       # Uses active_strategies from settings

# Parameter optimization
python 6_optimize_params.py    # Grid search for best MA/RSI combinations
```

### Testing (pytest)
```powershell
pytest test/test_strategy_factory.py -v     # Strategy loading & filtering
pytest test/test_phase3_integration.py -v   # V35 financial data integration
pytest -m unit                              # Unit tests only
pytest -m integration                       # Integration tests only
```

### Database Setup
```powershell
# Start MySQL via Docker
docker-compose up -d

# Initialize settings table
python init_settings.py

# Manual schema fixes (if needed)
python tool/fix_db_schema.py
```

## Project-Specific Conventions

### Import Rules
```python
# ✅ CORRECT: Use absolute imports from project root
from config import Config
from tool.db_helper import get_db_engine, get_stock_data
from tool.strategy_manager import StrategyManager
from tool.calc_indicators import calculate_rsi

# ❌ WRONG: Relative imports or duplicate implementations
from ..config import Config  # Don't use relative
# Don't reimplement DB connections locally
```

### Configuration Access
```python
# ✅ Always use Config class and db_helper
from config import Config
from tool.db_helper import get_setting, update_setting

db_url = Config.SQLALCHEMY_DATABASE_URI  # Reads from .env
fee_rate = Config.FEE_RATE
ma_period = int(get_setting('V30_MA_FAST', 5))  # DB settings

# ❌ Never hardcode or duplicate
engine = create_engine('mysql+pymysql://...')  # Wrong!
FEE_RATE = 0.001425  # Should use Config.FEE_RATE
```

### Type Hints & Docstrings
```python
# ✅ Expected style (V33+ refactor standard)
from typing import Optional, List
import pandas as pd

def calculate_rsi(series: pd.Series, period: Optional[int] = None) -> pd.Series:
    """Calculate RSI indicator.
    
    Args:
        series: Close price series
        period: Calculation period (default from Config)
    
    Returns:
        RSI series (0-100)
    """
```

### Error Handling for Data Operations
```python
# ✅ Pattern used throughout codebase
try:
    df = get_stock_data(stock_id)
    if df.empty:
        print(f"⚠️ 查無資料: {stock_id}")
        return None
    # Process data...
except Exception as e:
    print(f"❌ 錯誤: {e}")
    return None
```

## Frontend Development (Web Dashboard)

### Tech Stack Constraints
- **NO build tools**: Use CDN links (Tailwind CSS, Alpine.js, Chart.js, Plotly.js)
- **NO npm/webpack/node_modules**: Pure HTML + Jinja2 templates
- **Aesthetic**: Dark mode quant dashboard with high contrast (see `openspec/specs/frontend-design.md`)

### Flask Route Patterns
```python
# ✅ Standard pattern with login protection
from flask_login import login_required

@app.route('/dashboard')
@login_required  # Phase 5 security requirement
def dashboard():
    # Use tool.db_helper for queries
    from tool.db_helper import get_db_engine
    # Render with Jinja2
    return render_template('dashboard.html', data=data)
```

## Key Integration Points

### Line Bot Messaging (SDK v3)
```python
# Updated 2024 SDK pattern
from linebot.v3.messaging import MessagingApi, TextMessage
from linebot.v3.webhooks import MessageEvent

@handler.add(MessageEvent, message=TextMessageContent)
def handle_message(event):
    user_text = event.message.text
    # Process with strategy...
    reply = format_v31_recommendation(candidates)
```

### XGBoost Model Interface
```python
# Model expects exact feature columns from strategy
strategy = StrategyManager().get_active_strategy()
features = strategy.features  # e.g., ['rsi', 'bias', 'macd_hist', ...]
X = df[features]
df['ai_score'] = model.predict_proba(X)[:, 1]
```

### Financial Data Scraping
- **Primary source**: `https://openapi.twse.com.tw/v1/opendata/t187ap06_X_ci` (quarterly consolidated income statements)
- **Fallback**: Manual CSV import via batch script when API returns empty (see [7_update_financials.py](7_update_financials.py) warning messages)
- **Critical columns**: `revenue`, `revenue_yoy`, `net_profit_margin` (used by V35 strategy)

## Common Pitfalls & Solutions

### Issue: Circular Import Errors
**Symptom**: `ImportError: cannot import name 'Config' from partially initialized module`
**Solution**: Use lazy loading in `config.py` for strategy-dependent features:
```python
@classmethod
def get_active_features(cls):
    from tool.strategy_manager import get_active_strategy  # Lazy import
    return get_active_strategy().features
```

### Issue: Settings Not Persisting
**Check order**:
1. Verify `strategy_settings.json` format (V2 requires `active_strategies: List`)
2. Confirm `user_settings` table exists (`python init_settings.py`)
3. Validate .env file encoding (UTF-8, no BOM)

### Issue: Backtest Returns Zero Trades
**Debug steps**:
1. Check market filter: `收盤價 < MA60` triggers market-wide stop ([4_run_backtest.py](4_run_backtest.py#L520))
2. Verify indicator columns exist in `stock_data` table (run `python 2_rundaily.py`)
3. Print `filter_candidates()` output to see filtering stages

## Key Files Reference

| Purpose | File | Notes |
|---------|------|-------|
| All DB operations | [tool/db_helper.py](tool/db_helper.py) | ONLY entry point, uses parameterized queries |
| Technical indicators | [tool/calc_indicators.py](tool/calc_indicators.py) | RSI, MACD, KD, BB, ATR, volume ratios |
| Strategy factory | [tool/strategy_manager.py](tool/strategy_manager.py) | Singleton, reads strategy_settings.json |
| Base strategy class | [tool/strategies/base.py](tool/strategies/base.py) | Abstract class defining strategy interface |
| Backtest engine | [4_run_backtest.py](4_run_backtest.py) | Supports single/portfolio mode |
| Configuration hub | [config.py](config.py) | Loads from .env, defines all constants |
| Web application | [app.py](app.py) | Flask + Line Bot webhook + dashboard routes |

## Version History Context
- **V30**: Pure technical analysis (MA + volume)
- **V31**: V30 filtering + XGBoost ranking (current hybrid baseline)
- **V33**: Low volatility (NATR < 4%, low STD_20)
- **V34**: Turbo strategy (revenue YoY > 30%, 60-day high)
- **V35**: Innovation strategy (R&D expenditure + revenue growth)

**Current state (V35 Phase 5)**: Multi-strategy portfolio system with Plotly visualization, Flask-Login security, and quarterly financial integration.

---

## Quick Decision Tree

**Adding new feature?** → Update strategy class → Retrain model → Test backtest  
**Modifying filters?** → Edit `filter_candidates()` in strategy file  
**Database changes?** → Add migration script in `tool/` → Update `db_helper.py` functions  
**API integration?** → Use `requests` + `tool.db_helper.get_db_engine()` for storage  
**Testing strategy?** → Create `test/test_<feature>.py` following pytest conventions  
**Frontend changes?** → Edit `templates/*.html` (no build step) → Reload browser  

**Before committing**: Run `pytest -v` + manual backtest to validate changes don't break existing strategies.
