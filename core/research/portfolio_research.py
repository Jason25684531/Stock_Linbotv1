"""Parameter grid and deterministic D5 shortlist policy."""

import itertools
import numpy as np
import pandas as pd


def safe_zscore(values: pd.Series) -> pd.Series:
    """Finite population z-score; NaN/Inf and zero variance deliberately score zero."""
    values = pd.to_numeric(values, errors="coerce").replace([np.inf, -np.inf], np.nan)
    finite = values.dropna()
    if len(finite) < 2 or finite.std(ddof=0) == 0:
        return pd.Series(0.0, index=values.index)
    return ((values - finite.mean()) / finite.std(ddof=0)).fillna(0.0)


def parameter_grid() -> pd.DataFrame:
    return pd.DataFrame([{"config_id": f"{m}_top{n}_reb{r}_{w}", "combination_method": m, "top_n": n, "rebalance_days": r, "stock_weighting": w} for m, n, r, w in itertools.product(("equal", "ic", "icir", "redundancy_adjusted"), (20, 30, 50), (20, 60), ("equal", "score_weighted"))])


def shortlist_eligibility(scoreboard: pd.DataFrame) -> pd.DataFrame:
    """Expose shortlist eligibility before scoring; never impute raw non-finite metrics."""
    result = scoreboard.copy()
    metrics = ("sharpe", "sortino", "calmar", "turnover", "sharpe_drop")
    result["shortlist_eligible"] = True
    result["invalid_reason"] = pd.NA
    for metric in metrics:
        invalid = ~np.isfinite(pd.to_numeric(result[metric], errors="coerce"))
        set_reason = invalid & result["shortlist_eligible"]
        result.loc[set_reason, "invalid_reason"] = f"non_finite_{metric}"
        result.loc[invalid, "shortlist_eligible"] = False
    gated = result.total_return.notna() & (result.total_return > 0) & result.max_drawdown.notna() & result.neighbor_sharpe_min.notna() & (result.neighbor_sharpe_min > 0)
    result.loc[~gated & result["shortlist_eligible"], "invalid_reason"] = "hard_gate_failed"
    result.loc[~gated, "shortlist_eligible"] = False
    return result


def select_shortlist(scoreboard: pd.DataFrame, count: int = 3) -> pd.DataFrame:
    eligible = shortlist_eligibility(scoreboard).loc[lambda x: x.shortlist_eligible].copy()
    if len(eligible) < count:
        raise ValueError("shortlist gate produced fewer than requested configurations")
    for column, sign in (("sharpe", 1), ("sortino", 1), ("calmar", 1), ("turnover", -1), ("sharpe_drop", -1)):
        eligible[f"z_{column}"] = safe_zscore(eligible[column]) * sign
    eligible["shortlist_score"] = eligible[[column for column in eligible if column.startswith("z_")]].mean(axis=1)
    return eligible.sort_values(["shortlist_score", "config_id"], ascending=[False, True], kind="stable").head(count).reset_index(drop=True)
