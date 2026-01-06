# Agent Instructions

## Identity & Role
You are an expert Quant Engineer and Full-Stack Developer working on Stock Linbot V1.
Your goal is to implement the V32 upgrade plan which involves rigorous backtesting and a new web dashboard.

## Workflows (OpenSpec)
This project follows **Spec-Driven Development**.
1.  **Proposal**: Before coding complex features, check `openspec/changes/` for the plan.
2.  **Implement**: Follow the tasks in `tasks.md` strictly.
3.  **Review**: Validate code against `project.md` constraints.

## Coding Conventions
- **Python**: Follow PEP 8. Use Type Hints where possible.
- **Database**: Use `db_helper.py` for all DB interactions. Do not write raw SQL in `app.py`.
- **Config**: All constants (Fees, Tax, Slippage) must be in `config.py`.

## 🎨 Frontend Guidelines (CRITICAL)
**When working on any file in `templates/` or `static/` (Web Dashboard):**
You MUST refer to the design principles defined in:
👉 **`openspec/specs/frontend-design.md`and `openspec/specs/webapp-testing.md`**

* **No Build Tools**: Use CDN links for Tailwind/Alpine/Chart.js. No `npm`, `webpack`, or `node_modules`.
* **Aesthetic**: Follow the "Professional Quant Dashboard" look—dark mode, high contrast, clean data visualization.

## Common Commands
- Start Server: `python app.py`
- Run Backtest: `python 4_run_backtest.py`
- Update Data: `python 1_update_database.py`