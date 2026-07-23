from __future__ import annotations

import pandas as pd


def correlation_matrix(returns: pd.DataFrame) -> pd.DataFrame:
    return returns.corr()


def correlation_analysis(daily_returns: pd.DataFrame, *, holdings=None, exposures=None, drawdowns=None):
    """Return the complete, already-computed strategy correlation evidence."""
    result = {"daily_return_correlation": correlation_matrix(daily_returns)}
    if holdings is not None:
        names = list(holdings)
        result["holding_overlap"] = pd.DataFrame(
            [[len(set(holdings[left]) & set(holdings[right])) / max(1, len(set(holdings[left]) | set(holdings[right])))
              for right in names] for left in names], index=names, columns=names)
    if exposures is not None:
        result["exposure_correlation"] = pd.DataFrame(exposures).T.corr()
    if drawdowns is not None:
        result["drawdown_correlation"] = correlation_matrix(drawdowns)
    return result
