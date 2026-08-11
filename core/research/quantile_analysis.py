"""Deterministic D4 quantile analysis over D3-provided ranks and labels."""

from typing import Iterable

import numpy as np
import pandas as pd
from scipy.stats import spearmanr

from core.research.factor_evaluation import EvaluationPolicy


def assign_quantiles(data: pd.DataFrame, *, rank_column: str, policy: EvaluationPolicy) -> pd.DataFrame:
    """Assign floor(rank * N) + 1 only to member rows with D3-valid ranks."""

    result = data.copy()
    eligible = result["member"].astype(bool) & result[rank_column].notna()
    values = result.loc[eligible, rank_column]
    result["quantile"] = pd.Series(pd.NA, index=result.index, dtype="Int64")
    result.loc[eligible, "quantile"] = (np.floor(values * policy.quantile_count).astype(int) + 1).clip(1, policy.quantile_count)
    return result


def _monotonicity(values: pd.Series) -> float:
    if len(values) < 2 or values.isna().any() or values.nunique() < 2:
        return float("nan")
    value = spearmanr(range(1, len(values) + 1), values).statistic
    return float(value) if np.isfinite(value) else float("nan")


def _one_side(data: pd.DataFrame, *, rank_column: str, label: str, policy: EvaluationPolicy) -> tuple[pd.DataFrame, pd.DataFrame]:
    assigned = assign_quantiles(data, rank_column=rank_column, policy=policy)
    rows, summaries = [], []
    for (factor_id, asof_date), group in assigned.groupby(["factor_id", "asof_date"], sort=True):
        eligible = group.loc[group["quantile"].notna() & group[label].notna()]
        if len(eligible) < policy.min_quantile_assets:
            continue
        means = eligible.groupby("quantile", observed=True)[label].mean().reindex(range(1, policy.quantile_count + 1))
        for quantile, value in means.items():
            rows.append({"factor_id": factor_id, "asof_date": asof_date, "quantile": int(quantile), "mean_return": value, "effective_asset_count": len(eligible)})
        summaries.append(
            {
                "factor_id": factor_id,
                "asof_date": asof_date,
                "q1_return": means.iloc[0],
                "q5_return": means.iloc[-1],
                "q5_minus_q1": means.iloc[-1] - means.iloc[0] if means.notna().all() else float("nan"),
                "monotonicity": _monotonicity(means),
                "effective_asset_count": len(eligible),
            }
        )
    return pd.DataFrame(rows), pd.DataFrame(summaries)


def compute_quantile_returns(dataset: pd.DataFrame, *, horizons: Iterable[int], policy: EvaluationPolicy) -> dict[str, pd.DataFrame]:
    """Return raw/aligned daily bucket means and signed factor-horizon summaries."""

    all_returns, all_summaries = [], []
    for horizon in horizons:
        label = f"forward_return_{horizon}d"
        raw_returns, raw_summary = _one_side(dataset, rank_column="rank_value", label=label, policy=policy)
        if not raw_returns.empty:
            raw_returns = raw_returns.assign(horizon=int(horizon), rank_basis="raw")
            all_returns.append(raw_returns)
        if raw_summary.empty:
            continue
        raw_summary = raw_summary.rename(columns={column: f"raw_{column}" for column in ("q1_return", "q5_return", "q5_minus_q1", "monotonicity", "effective_asset_count")})
        raw_summary["horizon"] = int(horizon)
        directions = dataset.groupby("factor_id")["direction"].first()
        raw_summary["direction"] = raw_summary["factor_id"].map(directions).astype(int)
        aligned = raw_summary.loc[raw_summary["direction"].ne(0)].copy()
        if not aligned.empty:
            factor_data = dataset.loc[dataset["factor_id"].isin(aligned["factor_id"])].copy()
            aligned_returns, aligned_summary = _one_side(factor_data, rank_column="direction_adjusted_rank", label=label, policy=policy)
            if not aligned_returns.empty:
                all_returns.append(aligned_returns.assign(horizon=int(horizon), rank_basis="aligned"))
            if not aligned_summary.empty:
                aligned_summary = aligned_summary.rename(columns={"q1_return": "aligned_q1_return", "q5_return": "aligned_q5_return", "q5_minus_q1": "aligned_long_short_spread", "monotonicity": "aligned_monotonicity", "effective_asset_count": "aligned_effective_asset_count"})
                aligned_summary["horizon"] = int(horizon)
                raw_summary = raw_summary.merge(aligned_summary, on=["factor_id", "asof_date", "horizon"], how="left")
        all_summaries.append(raw_summary)
    return {
        "returns": pd.concat(all_returns, ignore_index=True) if all_returns else pd.DataFrame(),
        "summary": pd.concat(all_summaries, ignore_index=True) if all_summaries else pd.DataFrame(),
    }
