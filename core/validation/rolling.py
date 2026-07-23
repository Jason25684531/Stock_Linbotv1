from __future__ import annotations

from core.backtest.metrics import calculate_metrics


def rolling_metrics(equity: list[float], windows=(63, 126, 252)):
    return {window: [calculate_metrics(equity[index - window:index]).values for index in range(window, len(equity) + 1)] for window in windows if window > 1}
