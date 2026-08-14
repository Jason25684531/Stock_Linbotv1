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


def build_parameter_robustness(scoreboard: pd.DataFrame) -> pd.DataFrame:
    """Summarize each configuration against neighbors differing by one knob."""
    rows = []
    for row in scoreboard.itertuples(index=False):
        neighbors = scoreboard.loc[
            (scoreboard.combination_method == row.combination_method)
            & (
                ((scoreboard.top_n - row.top_n).abs().isin((10, 20)))
                & scoreboard.rebalance_days.eq(row.rebalance_days)
                & scoreboard.stock_weighting.eq(row.stock_weighting)
                | (scoreboard.top_n.eq(row.top_n)
                   & (scoreboard.rebalance_days - row.rebalance_days).abs().eq(40)
                   & scoreboard.stock_weighting.eq(row.stock_weighting))
                | (scoreboard.top_n.eq(row.top_n)
                   & scoreboard.rebalance_days.eq(row.rebalance_days)
                   & scoreboard.stock_weighting.ne(row.stock_weighting))
            ),
            "sharpe",
        ]
        mean = neighbors.mean()
        rows.append({"config_id": row.config_id, "neighbor_count": len(neighbors), "neighbor_sharpe_mean": mean, "neighbor_sharpe_min": neighbors.min(), "neighbor_return_mean": scoreboard.loc[neighbors.index, "total_return"].mean(), "neighbor_drawdown_mean": scoreboard.loc[neighbors.index, "max_drawdown"].mean(), "neighbor_turnover_mean": scoreboard.loc[neighbors.index, "turnover"].mean(), "sharpe_drop": row.sharpe - mean})
    return pd.DataFrame(rows)


def summarize_portfolio(config: dict[str, object], result: dict[str, object]) -> dict[str, object]:
    """Return the finite, portable metrics required by the D5 scoreboard."""
    returns = pd.Series(result["returns"], dtype=float).replace([np.inf, -np.inf], np.nan).dropna()
    value = pd.Series(result["value"], dtype=float).replace([np.inf, -np.inf], np.nan).dropna()
    volatility = returns.std(ddof=0) * np.sqrt(252) if len(returns) else np.nan
    annualized = (1 + returns).prod() ** (252 / len(returns)) - 1 if len(returns) else np.nan
    total = value.iloc[-1] / value.iloc[0] - 1 if len(value) > 1 and value.iloc[0] else np.nan
    drawdown = (value / value.cummax() - 1).min() if len(value) else np.nan
    downside = returns.loc[returns < 0].std(ddof=0) * np.sqrt(252)
    orders = result["orders"]
    initial = value.iloc[0] if len(value) else np.nan
    turnover = (orders["Size"].abs() * orders["Price"]).sum() / initial if initial else np.nan
    return {**config, "start_date": value.index.min() if len(value) else pd.NaT, "end_date": value.index.max() if len(value) else pd.NaT, "total_return": total, "annualized_return": annualized, "annualized_volatility": volatility, "sharpe": annualized / volatility if volatility else np.nan, "sortino": annualized / downside if downside else np.nan, "max_drawdown": drawdown, "calmar": annualized / abs(drawdown) if drawdown else np.nan, "turnover": turnover, "estimated_cost": orders["Fees"].sum(), "trade_count": len(orders), "ending_value": value.iloc[-1] if len(value) else np.nan}


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


def select_shortlist(scoreboard: pd.DataFrame, count: int = 5) -> pd.DataFrame:
    eligible = shortlist_eligibility(scoreboard).loc[lambda x: x.shortlist_eligible].copy()
    if len(eligible) < 3:
        raise ValueError("shortlist gate produced fewer than three configurations")
    for column, sign in (("sharpe", 1), ("sortino", 1), ("calmar", 1), ("turnover", -1), ("sharpe_drop", -1)):
        eligible[f"z_{column}"] = safe_zscore(eligible[column]) * sign
    eligible["shortlist_score"] = eligible[[column for column in eligible if column.startswith("z_")]].mean(axis=1)
    result = eligible.sort_values(["shortlist_score", "config_id"], ascending=[False, True], kind="stable").head(min(count, len(eligible))).reset_index(drop=True)
    result.insert(0, "shortlist_rank", result.index + 1)
    return result
