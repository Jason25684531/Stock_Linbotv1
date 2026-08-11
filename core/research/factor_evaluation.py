"""Public D4 factor-evaluation statistics over immutable D3 datasets."""

import json
from dataclasses import asdict, dataclass, field
from math import sqrt
from pathlib import Path
from typing import Iterable

import numpy as np
import pandas as pd
from scipy.stats import spearmanr

from core.research.factors import CANONICAL_FACTOR_IDS


@dataclass(frozen=True)
class EvaluationPolicy:
    """Research-only thresholds for the D4 MVP."""

    min_ic_assets: int = 30
    min_quantile_assets: int = 50
    min_effective_days: int = 120
    min_average_coverage: float = 0.50
    min_abs_mean_ic: float = 0.02
    min_positive_ic_ratio: float = 0.52
    min_abs_q5_q1_by_horizon: dict[int, float] = field(default_factory=lambda: {1: 0.001, 5: 0.002, 10: 0.003, 20: 0.004, 60: 0.006})
    min_monotonicity: float = 0.60
    max_turnover: float = 0.50
    quantile_count: int = 5
    top_n: int = 50
    rolling_ic_window: int = 60
    min_years_for_stability: int = 2
    max_negative_year_ratio: float = 0.50
    min_annual_valid_ic_days: int = 60


def _spearman(left: pd.Series, right: pd.Series) -> float:
    """Return NaN for absent or constant samples instead of a fabricated score."""

    if len(left) < 2 or left.nunique(dropna=True) < 2 or right.nunique(dropna=True) < 2:
        return float("nan")
    value = spearmanr(left, right).statistic
    return float(value) if np.isfinite(value) else float("nan")


def compute_daily_rank_ic(dataset: pd.DataFrame, *, horizons: Iterable[int], policy: EvaluationPolicy) -> pd.DataFrame:
    """Compute public D4 daily raw/aligned Rank IC rows without mutating D3 data."""

    required = {"factor_id", "asof_date", "member", "rank_value", "direction", "direction_adjusted_rank"}
    missing = required - set(dataset)
    if missing:
        raise ValueError(f"D3 dataset missing columns: {sorted(missing)}")
    rows: list[dict[str, object]] = []
    source = dataset.copy()
    source["asof_date"] = pd.to_datetime(source["asof_date"])
    for horizon in horizons:
        label = f"forward_return_{horizon}d"
        if label not in source:
            raise ValueError(f"D3 dataset missing column: {label}")
        for (factor_id, asof_date), group in source.groupby(["factor_id", "asof_date"], sort=True):
            eligible = group.loc[group["member"].astype(bool) & group["rank_value"].notna() & group[label].notna()]
            count = len(eligible)
            raw = float("nan")
            aligned = float("nan")
            if count >= policy.min_ic_assets:
                raw = _spearman(eligible["rank_value"], eligible[label])
                if int(eligible["direction"].iloc[0]) != 0:
                    aligned = _spearman(eligible["direction_adjusted_rank"], eligible[label])
            rows.append(
                {
                    "factor_id": factor_id,
                    "asof_date": asof_date,
                    "horizon": int(horizon),
                    "direction": int(eligible["direction"].iloc[0]) if count else int(group["direction"].iloc[0]),
                    "effective_asset_count": count,
                    "raw_ic": raw,
                    "aligned_ic": aligned,
                }
            )
    return pd.DataFrame(rows)


def summarize_ic(daily_ic: pd.DataFrame) -> pd.DataFrame:
    """Summarize daily IC series, preserving unavailable metrics as NaN."""

    rows: list[dict[str, object]] = []
    for (factor_id, horizon), group in daily_ic.groupby(["factor_id", "horizon"], sort=True):
        item: dict[str, object] = {"factor_id": factor_id, "horizon": int(horizon), "total_days": len(group)}
        for prefix in ("raw", "aligned"):
            values = group[f"{prefix}_ic"].dropna()
            mean, std = values.mean(), values.std(ddof=1)
            icir = mean / std if len(values) > 1 and pd.notna(std) and std != 0 else float("nan")
            item.update(
                {
                    f"{prefix}_mean_ic": mean,
                    f"{prefix}_ic_std": std,
                    f"{prefix}_icir": icir,
                    f"{prefix}_annualized_icir": icir * sqrt(252) if pd.notna(icir) else float("nan"),
                    f"{prefix}_positive_ic_ratio": (values > 0).mean() if len(values) else float("nan"),
                    f"{prefix}_valid_ic_days": len(values),
                }
            )
        counts = group["effective_asset_count"]
        item.update({"average_effective_assets": counts.mean(), "median_effective_assets": counts.median(), "min_effective_assets": counts.min(), "max_effective_assets": counts.max()})
        rows.append(item)
    return pd.DataFrame(rows)


REQUIRED_D3_COLUMNS = {
    "asof_date", "asset_id", "factor_id", "raw_value", "winsorized_value", "rank_value", "direction",
    "direction_adjusted_rank", "member", "forward_return_1d", "forward_return_5d", "forward_return_10d",
    "forward_return_20d", "forward_return_60d",
}


class InvalidD3Input(ValueError):
    """Raised before any D4 calculation when immutable input violates contract."""


def policy_config(policy: EvaluationPolicy) -> dict[str, object]:
    """Serialize the frozen policy for a reproducibility manifest."""

    return asdict(policy)


def load_d3_dataset(run_dir: Path, *, factors: Iterable[str] | None = None) -> tuple[pd.DataFrame, dict[str, object]]:
    """Load only D3 canonical CSV partitions after validating manifest and schema."""

    manifest_path = run_dir / "run_manifest.json"
    dataset_dir = run_dir / "research_dataset"
    if not manifest_path.exists() or not dataset_dir.is_dir():
        raise InvalidD3Input("D3 manifest or research_dataset is missing")
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as caught:
        raise InvalidD3Input("D3 manifest is malformed") from caught
    if manifest.get("status") != "success" or not manifest.get("d3_enabled") or manifest.get("leakage_failure_count") != 0:
        raise InvalidD3Input("D3 manifest is not an accepted D3 input")
    requested = tuple(factors or sorted(CANONICAL_FACTOR_IDS))
    unknown = set(requested) - CANONICAL_FACTOR_IDS
    if unknown:
        raise InvalidD3Input(f"non-canonical factors requested: {sorted(unknown)}")
    partitions = {factor_id: sorted((dataset_dir / factor_id).glob("*.csv")) for factor_id in requested}
    absent = sorted(factor_id for factor_id, paths in partitions.items() if not paths)
    if absent:
        raise InvalidD3Input(f"D3 dataset missing requested factors: {absent}")
    paths = [path for factor_id in requested for path in partitions[factor_id]]
    if not paths:
        raise InvalidD3Input("no canonical D3 partitions found")
    frames = [pd.read_csv(path, low_memory=False) for path in paths]
    data = pd.concat(frames, ignore_index=True)
    missing = REQUIRED_D3_COLUMNS - set(data)
    if missing:
        raise InvalidD3Input(f"D3 dataset missing columns: {sorted(missing)}")
    keys = ["asof_date", "asset_id", "factor_id"]
    if data.duplicated(keys).any():
        raise InvalidD3Input("D3 dataset has duplicate canonical keys")
    numeric = data.select_dtypes(include="number")
    if np.isinf(numeric.to_numpy()).any():
        raise InvalidD3Input("D3 dataset has infinite numeric values")
    return data, manifest


def compute_rank_autocorrelation(dataset: pd.DataFrame) -> pd.DataFrame:
    """Compute overlap-only consecutive-date rank persistence for each factor."""

    rows: list[dict[str, object]] = []
    data = dataset.copy()
    data["asof_date"] = pd.to_datetime(data["asof_date"])
    for factor_id, factor in data.groupby("factor_id", sort=True):
        direction = int(factor["direction"].iloc[0])
        column = "direction_adjusted_rank" if direction != 0 else "rank_value"
        previous: pd.Series | None = None
        previous_date = None
        for asof_date, group in factor.groupby("asof_date", sort=True):
            current = group.loc[group["member"].astype(bool) & group[column].notna(), ["asset_id", column]].set_index("asset_id")[column]
            if previous is not None:
                overlap = previous.to_frame("previous").join(current.rename("current"), how="inner")
                rows.append({"factor_id": factor_id, "asof_date": asof_date, "previous_asof_date": previous_date, "overlapping_asset_count": len(overlap), "rank_autocorrelation": _spearman(overlap["previous"], overlap["current"])})
            previous, previous_date = current, asof_date
    return pd.DataFrame(rows)


def compute_top_n_retention(dataset: pd.DataFrame, *, policy: EvaluationPolicy) -> pd.DataFrame:
    """Compute true equal-weight Top-N turnover independently of retention."""

    rows: list[dict[str, object]] = []
    data = dataset.copy()
    data["asof_date"] = pd.to_datetime(data["asof_date"])
    for factor_id, factor in data.groupby("factor_id", sort=True):
        direction = int(factor["direction"].iloc[0])
        column = "direction_adjusted_rank" if direction != 0 else "rank_value"
        prior: set[object] | None = None
        prior_date = None
        for asof_date, group in factor.groupby("asof_date", sort=True):
            ranked = group.loc[group["member"].astype(bool) & group[column].notna(), ["asset_id", column]].sort_values(column, ascending=False)
            top = set(ranked.head(policy.top_n)["asset_id"])
            if prior is not None:
                effective = min(policy.top_n, len(prior), len(top))
                overlap = len(prior & top)
                weights = {asset: 1 / len(top) for asset in top} if top else {}
                prior_weights = {asset: 1 / len(prior) for asset in prior} if prior else {}
                turnover = 0.5 * sum(abs(weights.get(asset, 0) - prior_weights.get(asset, 0)) for asset in top | prior)
                rows.append({"factor_id": factor_id, "asof_date": asof_date, "previous_asof_date": prior_date, "configured_top_n": policy.top_n, "effective_n": effective, "top_count_t": len(top), "top_count_t_minus_1": len(prior), "overlap_count": overlap, "top_n_retention": overlap / effective if effective else float("nan"), "equal_weight_turnover": turnover, "rank_basis": "aligned" if direction != 0 else "raw_rank_diagnostic"})
            prior, prior_date = top, asof_date
    return pd.DataFrame(rows)


def compute_annual_results(daily_ic: pd.DataFrame) -> pd.DataFrame:
    """Summarize IC by calendar year while retaining ineligible years as data."""

    data = daily_ic.copy()
    data["year"] = pd.to_datetime(data["asof_date"]).dt.year
    rows: list[dict[str, object]] = []
    for (factor_id, horizon, year), group in data.groupby(["factor_id", "horizon", "year"], sort=True):
        direction = int(group["direction"].iloc[0])
        values = group["aligned_ic" if direction != 0 else "raw_ic"].dropna()
        std = values.std(ddof=1)
        rows.append({"factor_id": factor_id, "horizon": int(horizon), "year": int(year), "direction": direction, "raw_mean_ic": group["raw_ic"].mean(), "aligned_mean_ic": group["aligned_ic"].mean(), "ic_std": std, "icir": values.mean() / std if len(values) > 1 and std not in (0, np.nan) else float("nan"), "positive_ic_ratio": (values > 0).mean() if len(values) else float("nan"), "average_effective_assets": group["effective_asset_count"].mean(), "annual_valid_ic_days": len(values)})
    return pd.DataFrame(rows)


def annual_stability(annual: pd.DataFrame, *, policy: EvaluationPolicy) -> pd.DataFrame:
    """Classify annual stability without punishing naturally insufficient history."""

    rows = []
    for (factor_id, horizon), group in annual.groupby(["factor_id", "horizon"], sort=True):
        direction = int(group["direction"].iloc[0])
        eligible = group.loc[group["annual_valid_ic_days"] >= policy.min_annual_valid_ic_days]
        if direction == 0:
            flag, negative_ratio = "NOT_APPLICABLE", float("nan")
        elif len(eligible) < policy.min_years_for_stability:
            flag, negative_ratio = "NOT_ENOUGH_YEARS", float("nan")
        else:
            negative_ratio = (eligible["aligned_mean_ic"] < 0).mean()
            flag = "UNSTABLE" if negative_ratio > policy.max_negative_year_ratio else "STABLE"
        rows.append({"factor_id": factor_id, "horizon": int(horizon), "eligible_year_count": len(eligible), "negative_year_ratio": negative_ratio, "stability_flag": flag})
    return pd.DataFrame(rows)


def compute_factor_correlation(dataset: pd.DataFrame) -> pd.DataFrame:
    """Average same-date raw-rank Spearman correlations; never flatten time."""

    records: list[pd.DataFrame] = []
    data = dataset.loc[dataset["member"].astype(bool) & dataset["rank_value"].notna(), ["asof_date", "asset_id", "factor_id", "rank_value"]]
    for _, group in data.groupby("asof_date", sort=True):
        wide = group.pivot(index="asset_id", columns="factor_id", values="rank_value")
        records.append(wide.corr(method="spearman"))
    if not records:
        return pd.DataFrame(columns=["factor_id", "other_factor_id", "correlation"])
    average = sum(frame.reindex(index=sorted(CANONICAL_FACTOR_IDS), columns=sorted(CANONICAL_FACTOR_IDS)) for frame in records) / len(records)
    return average.rename_axis(index="factor_id", columns="other_factor_id").stack(dropna=False).rename("correlation").reset_index()


def summarize_quantiles(daily: pd.DataFrame) -> pd.DataFrame:
    """Aggregate D4 daily quantile evidence by factor and horizon."""

    if daily.empty:
        return pd.DataFrame()
    aggregations = {column: "mean" for column in daily.columns if column in {"raw_q1_return", "raw_q5_return", "raw_q5_minus_q1", "raw_monotonicity", "aligned_q1_return", "aligned_q5_return", "aligned_long_short_spread", "aligned_monotonicity"}}
    result = daily.groupby(["factor_id", "horizon", "direction"], as_index=False).agg(aggregations)
    result["quantile_valid_days"] = daily.groupby(["factor_id", "horizon", "direction"]).size().to_numpy()
    return result


def evaluate_horizons(ic_summary: pd.DataFrame, quantile_summary: pd.DataFrame, stability: pd.DataFrame, turnover: pd.DataFrame, *, policy: EvaluationPolicy) -> pd.DataFrame:
    """Apply the signed D4 horizon gates without changing source directions."""

    result = ic_summary.merge(quantile_summary, on=["factor_id", "horizon"], how="left", suffixes=("", "_quantile"))
    if not stability.empty:
        result = result.merge(stability, on=["factor_id", "horizon"], how="left")
    if not turnover.empty:
        average = turnover.groupby("factor_id", as_index=False)["equal_weight_turnover"].mean().rename(columns={"equal_weight_turnover": "average_equal_weight_turnover"})
        result = result.merge(average, on="factor_id", how="left")
    result["average_coverage"] = result["raw_valid_ic_days"] / result["total_days"]
    rows: list[dict[str, object]] = []
    for _, row in result.iterrows():
        direction = int(row.get("direction", row.get("direction_quantile", 0)))
        threshold = policy.min_abs_q5_q1_by_horizon[int(row["horizon"])]
        if direction == 0:
            ic_pass = abs(row["raw_mean_ic"]) >= policy.min_abs_mean_ic if pd.notna(row["raw_mean_ic"]) else False
            spread, mono = row.get("raw_q5_minus_q1"), row.get("raw_monotonicity")
            quantile_pass = (abs(spread) >= threshold if pd.notna(spread) else False) or (abs(mono) >= policy.min_monotonicity if pd.notna(mono) else False)
        else:
            ic_pass = row["aligned_mean_ic"] >= policy.min_abs_mean_ic if pd.notna(row["aligned_mean_ic"]) else False
            spread, mono = row.get("aligned_long_short_spread"), row.get("aligned_monotonicity")
            quantile_pass = (spread >= threshold if pd.notna(spread) else False) or (mono >= policy.min_monotonicity if pd.notna(mono) else False)
        evidence_days = int(row["raw_valid_ic_days"])
        eligible = evidence_days >= policy.min_effective_days and row["average_coverage"] >= policy.min_average_coverage and ic_pass and quantile_pass
        rows.append({**row.to_dict(), "direction": direction, "ic_pass": ic_pass, "quantile_pass": quantile_pass, "core_pass": bool(ic_pass and quantile_pass), "horizon_eligible": bool(eligible)})
    return pd.DataFrame(rows)


def select_best_horizons(horizons: pd.DataFrame) -> pd.DataFrame:
    """Choose a signed winner; exploratory direction=0 may use raw absolute evidence."""

    rows = []
    for factor_id, group in horizons.groupby("factor_id", sort=True):
        direction = int(group["direction"].iloc[0])
        eligible = group.loc[group["horizon_eligible"]].copy()
        if eligible.empty:
            rows.append({"factor_id": factor_id, "best_horizon": "NONE", "best_horizon_confidence": "NONE"})
            continue
        icir_column = "aligned_icir" if direction != 0 else "raw_icir"
        mean_column = "aligned_mean_ic" if direction != 0 else "raw_mean_ic"
        if direction == 0:
            icir_winner = eligible.loc[eligible[icir_column].abs().idxmax()]
            mean_winner = eligible.loc[eligible[mean_column].abs().idxmax()]
        else:
            icir_winner = eligible.loc[eligible[icir_column].idxmax()]
            mean_winner = eligible.loc[eligible[mean_column].idxmax()]
        if int(icir_winner.horizon) == int(mean_winner.horizon):
            confidence = "CLEAR"
        elif direction != 0 and (pd.isna(icir_winner[icir_column]) or icir_winner[icir_column] <= 0):
            confidence = "CONFLICT"
        else:
            competing = mean_winner[icir_column]
            gap = (icir_winner[icir_column] - competing) / icir_winner[icir_column] if icir_winner[icir_column] else float("nan")
            confidence = "CLEAR" if pd.notna(gap) and gap >= 0.20 else "CONFLICT"
        rows.append({"factor_id": factor_id, "best_horizon": int(icir_winner.horizon), "best_horizon_confidence": confidence})
    return pd.DataFrame(rows)


def factor_status(horizons: pd.DataFrame, best: pd.DataFrame, *, policy: EvaluationPolicy) -> pd.DataFrame:
    """Return the D4 public factor status ladder, capped for direction=0."""

    rows = []
    for factor_id, group in horizons.groupby("factor_id", sort=True):
        direction = int(group["direction"].iloc[0])
        choice = best.loc[best["factor_id"].eq(factor_id)].iloc[0]
        selected = group.loc[group["horizon"].eq(choice.best_horizon)] if choice.best_horizon != "NONE" else pd.DataFrame()
        if group["raw_valid_ic_days"].max() < policy.min_effective_days:
            status = "UNTESTED"
        elif selected.empty or not bool(selected.iloc[0]["core_pass"]):
            status = "WEAK"
        elif direction == 0 or choice.best_horizon_confidence == "CONFLICT" or selected.iloc[0].get("stability_flag") == "UNSTABLE" or selected.iloc[0].get("average_equal_weight_turnover", 0) > policy.max_turnover:
            status = "REVIEW"
        else:
            status = "CANDIDATE"
        rows.append({"factor_id": factor_id, "direction": direction, "status": status, "best_horizon": choice.best_horizon, "best_horizon_confidence": choice.best_horizon_confidence})
    return pd.DataFrame(rows)


def build_scoreboard(status: pd.DataFrame, horizons: pd.DataFrame, correlation: pd.DataFrame) -> pd.DataFrame:
    """Assemble one consumer-facing row per factor without using redundancy as a status gate."""

    rows = []
    for _, item in status.iterrows():
        factor_id = item.factor_id
        selected = horizons.loc[(horizons["factor_id"] == factor_id) & (horizons["horizon"].astype(str) == str(item.best_horizon))]
        metrics = selected.iloc[0].to_dict() if not selected.empty else {}
        peers = correlation.loc[(correlation["factor_id"] == factor_id) & (correlation["other_factor_id"] != factor_id)].copy() if not correlation.empty else pd.DataFrame()
        if peers.empty or peers["correlation"].dropna().empty:
            peer, maximum = pd.NA, float("nan")
        else:
            peers["absolute"] = peers["correlation"].abs()
            top = peers.loc[peers["absolute"].idxmax()]
            peer, maximum = top.other_factor_id, top.absolute
        if pd.isna(maximum):
            redundancy = "UNKNOWN"
        elif maximum < 0.50:
            redundancy = "LOW"
        elif maximum < 0.80:
            redundancy = "MODERATE"
        else:
            redundancy = "HIGH"
        direction = int(item.direction)
        raw_mean = metrics.get("raw_mean_ic", float("nan"))
        research_direction = "NOT_APPLICABLE" if direction != 0 else ("positive" if raw_mean > 0 else "negative" if raw_mean < 0 else "undetermined")
        rows.append({
            **item.to_dict(), **{key: value for key, value in metrics.items() if key not in {"factor_id", "direction"}},
            "research_direction": research_direction,
            "raw_mean_ic": raw_mean,
            "aligned_mean_ic": metrics.get("aligned_mean_ic", float("nan")),
            "raw_icir": metrics.get("raw_icir", float("nan")),
            "aligned_icir": metrics.get("aligned_icir", float("nan")),
            "positive_ic_ratio": metrics.get("aligned_positive_ic_ratio" if direction != 0 else "raw_positive_ic_ratio", float("nan")),
            "q1_return": metrics.get("aligned_q1_return" if direction != 0 else "raw_q1_return", float("nan")),
            "q5_return": metrics.get("aligned_q5_return" if direction != 0 else "raw_q5_return", float("nan")),
            "q5_minus_q1": metrics.get("aligned_long_short_spread" if direction != 0 else "raw_q5_minus_q1", float("nan")),
            "quantile_monotonicity": metrics.get("aligned_monotonicity" if direction != 0 else "raw_monotonicity", float("nan")),
            "rank_autocorrelation": float("nan"),
            "top_n_retention": float("nan"),
            "turnover": metrics.get("average_equal_weight_turnover", float("nan")),
            "average_coverage": metrics.get("average_coverage", float("nan")),
            "average_effective_assets": metrics.get("average_effective_assets", float("nan")),
            "stability_flag": metrics.get("stability_flag", "NOT_ENOUGH_YEARS"),
            "max_abs_factor_correlation": maximum,
            "most_correlated_factor": peer,
            "redundancy_flag": redundancy,
            "redundancy_notes": "Research diversification diagnostic only; it does not change D4 status.",
            "application_role": "review_required",
            "next_stage": "portfolio_backtest" if item.status == "CANDIDATE" else "research_review",
            "notes": "RESEARCH INTERPRETATION ONLY; no production approval.",
        })
    return pd.DataFrame(rows)
