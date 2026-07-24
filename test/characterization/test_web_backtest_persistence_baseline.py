"""驗證 Web 回測（_run_portfolio_backtest）預設不寫入 DB，且可明確 opt-in。

2026-07-24: 一次互動式驗證意外觸發真實 PortfolioBacktestEngine 執行並整表覆寫
backtest_trades/backtest_equity_curve，因為 Web 唯一呼叫點從未顯式聲明持久化意圖。
"""


class _StubPortfolioBacktestEngine:
    """捕捉建構參數，不觸及真實回測邏輯或資料庫。"""

    last_kwargs = None

    def __init__(self, **kwargs):
        type(self).last_kwargs = kwargs

    def run_portfolio_backtest(self):
        return {'metrics': {}, 'strategy_performance': {}}


def test_run_portfolio_backtest_defaults_to_no_db_persist(monkeypatch):
    import app as app_module

    monkeypatch.setattr('core.backtest.runner.PortfolioBacktestEngine', _StubPortfolioBacktestEngine)

    app_module._run_portfolio_backtest(
        ['hybrid_trend_rank'], start_date='2026-01-01', end_date='2026-01-02'
    )

    assert _StubPortfolioBacktestEngine.last_kwargs['persist_to_db'] is False
    # persist_results 未被顯式傳遞，維持類別預設 True，CSV 輸出/`/backtest` 圖表行為不受影響。
    assert 'persist_results' not in _StubPortfolioBacktestEngine.last_kwargs


def test_run_portfolio_backtest_can_opt_in_to_db_persist(monkeypatch):
    import app as app_module

    monkeypatch.setattr('core.backtest.runner.PortfolioBacktestEngine', _StubPortfolioBacktestEngine)

    app_module._run_portfolio_backtest(
        ['hybrid_trend_rank'],
        start_date='2026-01-01',
        end_date='2026-01-02',
        persist_to_db=True,
    )

    assert _StubPortfolioBacktestEngine.last_kwargs['persist_to_db'] is True
