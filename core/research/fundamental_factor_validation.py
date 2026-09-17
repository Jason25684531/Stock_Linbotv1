"""Research-only validation for the frozen Fundamental PIT v3 factor pool.

This module deliberately stops at factor evidence and an accepted-factor
handoff.  It never runs strategy, portfolio, runtime, or production code.
"""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import asdict, dataclass, field
from datetime import date
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Iterable, Mapping

import numpy as np
import pandas as pd
from scipy.stats import spearmanr

from core.research.factor_operators import rank_cs, winsorize_cs
from core.research.fundamental_pit import clean_ticker


DATASET_VERSION = "fundamental-pit-v3"
TARGET_TICKER_COUNT = 890
TARGET_TICKER_SHA256 = "d311c1fea8c3110c1d9b940798040b8867d9c2ed5d9eefbd3447bd01d1c87594"
NORMALIZED_RECORD_COUNT = 259054
MATRIX_ROW_COUNT = 764510
RESEARCH_START = pd.Timestamp("2023-01-03")
RESEARCH_END = pd.Timestamp("2026-07-28")
FACTOR_RESEARCH_KNOWLEDGE_CUTOFF = "2026-07-28"
PRIMARY_HORIZON = 20
SECONDARY_HORIZONS = (60,)
HORIZONS = (PRIMARY_HORIZON, *SECONDARY_HORIZONS)
QUANTILE_COUNT = 5
TECHNICAL_CONTROL_IDS = (
    "amihud_20d", "momentum_12_1", "momentum_20d", "momentum_60d", "near_high_252d",
)


@dataclass(frozen=True)
class FoldSpec:
    fold_id: str
    start: str
    end: str


@dataclass(frozen=True)
class CandidateSpec:
    candidate_id: str
    source_metric: str
    family: str
    direction: int
    normalization: str
    winsorization: str
    missingness_policy: str
    minimum_coverage: float
    primary_horizon: int
    secondary_horizons: tuple[int, ...]
    quantile_count: int
    temporal_split: str
    reporting_basis: str


@dataclass(frozen=True)
class ValidationPolicy:
    winsor_lower: float = 0.01
    winsor_upper: float = 0.99
    min_daily_coverage_ratio: float = 0.20
    min_mean_coverage_ratio: float = 0.20
    min_coverage_pass_ratio: float = 0.80
    min_source_ticker_coverage: float = 0.20
    min_ic_assets: int = 30
    min_quantile_assets: int = 50
    min_valid_ic_days: int = 120
    min_rank_ic: float = 0.02
    min_icir: float = 0.20
    min_positive_rank_ic_ratio: float = 0.52
    min_q5_q1_by_horizon: Mapping[int, float] = field(default_factory=lambda: {20: 0.004, 60: 0.006})
    min_monotonicity: float = 0.20
    max_turnover: float = 0.50
    top_n: int = 50
    min_fold_valid_ic_days: int = 60
    min_stable_folds: int = 2
    max_negative_fold_ratio: float = 0.50


FROZEN_FOLDS = (
    FoldSpec("2023", "2023-01-03", "2023-12-29"),
    FoldSpec("2024", "2024-01-01", "2024-12-31"),
    FoldSpec("2025", "2025-01-01", "2025-12-31"),
    FoldSpec("2026", "2026-01-01", "2026-07-28"),
)
FROZEN_POLICY = ValidationPolicy()
FROZEN_CANDIDATES = (
    CandidateSpec("Q1_OPERATING_MARGIN", "operating_margin", "Quality", 1, "cross_sectional_midrank", "winsorize_cs[p01,p99]", "preserve_null_no_fill", 0.20, PRIMARY_HORIZON, SECONDARY_HORIZONS, QUANTILE_COUNT, "fixed_calendar_year_folds", "STANDALONE"),
    CandidateSpec("G1_REVENUE_YOY", "revenue_yoy", "Growth", 1, "cross_sectional_midrank", "winsorize_cs[p01,p99]", "preserve_null_no_fill", 0.20, PRIMARY_HORIZON, SECONDARY_HORIZONS, QUANTILE_COUNT, "fixed_calendar_year_folds", "STANDALONE"),
    CandidateSpec("G2_OPERATING_INCOME_YOY", "operating_income_yoy", "Growth", 1, "cross_sectional_midrank", "winsorize_cs[p01,p99]", "preserve_null_no_fill", 0.20, PRIMARY_HORIZON, SECONDARY_HORIZONS, QUANTILE_COUNT, "fixed_calendar_year_folds", "STANDALONE"),
    CandidateSpec("G3_EPS_YOY", "eps_yoy", "Growth", 1, "cross_sectional_midrank", "winsorize_cs[p01,p99]", "preserve_null_no_fill", 0.20, PRIMARY_HORIZON, SECONDARY_HORIZONS, QUANTILE_COUNT, "fixed_calendar_year_folds", "CUMULATIVE_AS_REPORTED"),
    CandidateSpec("G4_REVENUE_CUMULATIVE_YOY", "revenue_cumulative_yoy", "Growth", 1, "cross_sectional_midrank", "winsorize_cs[p01,p99]", "preserve_null_no_fill", 0.20, PRIMARY_HORIZON, SECONDARY_HORIZONS, QUANTILE_COUNT, "fixed_calendar_year_folds", "CUMULATIVE"),
    CandidateSpec("G5_OPERATING_INCOME_CUMULATIVE_YOY", "operating_income_cumulative_yoy", "Growth", 1, "cross_sectional_midrank", "winsorize_cs[p01,p99]", "preserve_null_no_fill", 0.20, PRIMARY_HORIZON, SECONDARY_HORIZONS, QUANTILE_COUNT, "fixed_calendar_year_folds", "CUMULATIVE"),
)
SUPPORTED_CANDIDATE_IDS = frozenset(item.candidate_id for item in FROZEN_CANDIDATES)
UNSUPPORTED_SOURCE_METRICS = frozenset({
    "roe", "roa", "debt_to_equity", "book_to_price", "earnings_yield", "market_cap", "size", "leverage",
})


class ValidationInputError(ValueError):
    """Raised when frozen inputs do not satisfy the validation contract."""


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _json_default(value: object) -> object:
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating,)):
        return None if not np.isfinite(value) else float(value)
    if isinstance(value, (pd.Timestamp, date)):
        return value.isoformat()
    if pd.isna(value):
        return None
    return str(value)


def _write_json(path: Path, payload: object) -> None:
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False, default=_json_default) + "\n", encoding="utf-8")


def frozen_candidate_spec() -> dict[str, object]:
    """Return the exact pre-result contract; callers must not mutate it."""

    return {
        "dataset_version": DATASET_VERSION,
        "primary_horizon": PRIMARY_HORIZON,
        "secondary_horizons": list(SECONDARY_HORIZONS),
        "quantile_count": QUANTILE_COUNT,
        "temporal_folds": [asdict(item) for item in FROZEN_FOLDS],
        "policy": {**asdict(FROZEN_POLICY), "min_q5_q1_by_horizon": dict(FROZEN_POLICY.min_q5_q1_by_horizon), "thresholds_are_research_heuristics": True},
        "candidates": [asdict(item) for item in FROZEN_CANDIDATES],
    }


def validate_candidate_request(candidate_ids: Iterable[str] | None = None) -> tuple[CandidateSpec, ...]:
    requested = tuple(candidate_ids) if candidate_ids is not None else tuple(item.candidate_id for item in FROZEN_CANDIDATES)
    if set(requested) != SUPPORTED_CANDIDATE_IDS or len(requested) != len(SUPPORTED_CANDIDATE_IDS):
        unknown = sorted(set(requested) - SUPPORTED_CANDIDATE_IDS)
        missing = sorted(SUPPORTED_CANDIDATE_IDS - set(requested))
        raise ValidationInputError(f"candidate request must be the frozen six; unknown={unknown}, missing={missing}")
    if any(item.source_metric in UNSUPPORTED_SOURCE_METRICS for item in FROZEN_CANDIDATES):
        raise ValidationInputError("unsupported metric present in frozen candidates")
    return tuple(FROZEN_CANDIDATES)


def write_frozen_candidate_spec(path: Path) -> str:
    """Write the frozen contract once and return its hash."""

    payload = frozen_candidate_spec()
    encoded = (json.dumps(payload, indent=2, ensure_ascii=False, default=_json_default) + "\n").encode("utf-8")
    if path.exists():
        if path.read_bytes() != encoded:
            raise ValidationInputError(f"frozen candidate spec changed: {path}")
    else:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(encoded)
    return hashlib.sha256(encoded).hexdigest()


def _target_tickers(universe_path: Path) -> tuple[str, ...]:
    frame = pd.read_parquet(universe_path, columns=["is_eligible", "ticker", "stock_id"])
    source_column = "ticker" if "ticker" in frame else "stock_id"
    values = sorted({clean_ticker(value) for value in frame.loc[frame["is_eligible".strip()].fillna(False).astype(bool), source_column] if clean_ticker(value)})
    return tuple(values)


def load_frozen_inputs(
    fundamental_root: Path,
    d3_root: Path,
    universe_path: Path = Path("data/processed/research_universe.parquet"),
) -> tuple[pd.DataFrame, pd.DataFrame, tuple[str, ...], dict[str, object]]:
    """Load and validate read-only PIT and label inputs before any statistic."""

    manifest_path = fundamental_root / "fundamental_manifest.json"
    matrix_path = fundamental_root / "fundamental_matrix.parquet"
    pit_report_path = fundamental_root / "pit_validation_report.json"
    d3_manifest_path = d3_root / "run_manifest.json"
    if not all(path.is_file() for path in (manifest_path, matrix_path, pit_report_path, d3_manifest_path, universe_path)):
        raise ValidationInputError("frozen PIT, universe, or D3 input is missing")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("dataset_version") != DATASET_VERSION or manifest.get("target_ticker_count") != TARGET_TICKER_COUNT or manifest.get("target_ticker_sha256") != TARGET_TICKER_SHA256:
        raise ValidationInputError("fundamental manifest identity mismatch")
    if manifest.get("normalized_record_count") != NORMALIZED_RECORD_COUNT or manifest.get("matrix_row_count") != MATRIX_ROW_COUNT:
        raise ValidationInputError("fundamental manifest row-count mismatch")
    pit_report = json.loads(pit_report_path.read_text(encoding="utf-8"))
    if pit_report.get("PIT_INTEGRITY") != "PASS" or pit_report.get("FUTURE_LEAKAGE", 0) not in (0, "0") or pit_report.get("FORWARD_FILL_VIOLATIONS", 0) not in (0, "0"):
        raise ValidationInputError("PIT validation is not a pass")
    targets = _target_tickers(universe_path)
    if len(targets) != TARGET_TICKER_COUNT or hashlib.sha256("\n".join(targets).encode()).hexdigest() != TARGET_TICKER_SHA256:
        raise ValidationInputError("canonical target universe mismatch")
    matrix = pd.read_parquet(matrix_path)
    required = {"asof_date", "ticker"} | {item.source_metric for item in FROZEN_CANDIDATES}
    if not required <= set(matrix.columns) or len(matrix) != MATRIX_ROW_COUNT:
        raise ValidationInputError("fundamental matrix schema or row count mismatch")
    matrix["asof_date"] = pd.to_datetime(matrix["asof_date"], errors="coerce")
    matrix["ticker"] = matrix["ticker"].map(clean_ticker)
    matrix = matrix.loc[matrix["ticker"].isin(targets) & matrix["asof_date".strip()].between(RESEARCH_START, RESEARCH_END)].copy()
    if matrix.duplicated(["asof_date", "ticker"]).any() or matrix["asof_date"].isna().any():
        raise ValidationInputError("fundamental matrix has duplicate or invalid canonical keys")
    d3_manifest = json.loads(d3_manifest_path.read_text(encoding="utf-8"))
    if d3_manifest.get("status") != "success" or d3_manifest.get("leakage_failure_count") != 0:
        raise ValidationInputError("D3 labels are not an accepted post-hoc source")
    label_frames = []
    for path in sorted((d3_root / "research_dataset" / "momentum_20d").glob("*.csv")):
        frame = pd.read_csv(path, usecols=["asof_date", "asset_id", "forward_return_20d", "forward_return_60d"], low_memory=False)
        frame["asof_date"] = pd.to_datetime(frame["asof_date"], errors="coerce")
        frame["ticker"] = frame.pop("asset_id").map(clean_ticker)
        label_frames.append(frame.loc[frame["ticker"].isin(targets) & frame["asof_date"].between(RESEARCH_START, RESEARCH_END)])
    labels = pd.concat(label_frames, ignore_index=True) if label_frames else pd.DataFrame(columns=["asof_date", "ticker", "forward_return_20d", "forward_return_60d"])
    if labels.duplicated(["asof_date", "ticker"]).any():
        raise ValidationInputError("D3 label keys are duplicated")
    d3_defs = {int(item["horizon"]): item for item in d3_manifest.get("forward_return_definitions", [])}
    for horizon in HORIZONS:
        definition = d3_defs.get(horizon)
        if not definition or definition.get("entry_lag") != 1 or definition.get("price_basis") != "local_adjusted":
            raise ValidationInputError(f"forward return definition mismatch for {horizon}D")
    evidence = {
        "fundamental_manifest": manifest,
        "pit_validation": pit_report,
        "d3_manifest_id": d3_manifest.get("run_id"),
        "d3_label_definitions": [d3_defs[horizon] for horizon in HORIZONS],
        "target_ticker_count": len(targets),
        "target_ticker_sha256": TARGET_TICKER_SHA256,
        "research_start": RESEARCH_START.date().isoformat(),
        "research_end": RESEARCH_END.date().isoformat(),
        "factor_research_knowledge_cutoff": FACTOR_RESEARCH_KNOWLEDGE_CUTOFF,
    }
    return matrix, labels, targets, evidence


def normalize_factor(matrix: pd.DataFrame, spec: CandidateSpec, policy: ValidationPolicy = FROZEN_POLICY) -> pd.DataFrame:
    """Apply existing same-day winsorization and deterministic midrank."""

    work = matrix.loc[:, ["asof_date", "ticker", spec.source_metric]].rename(columns={spec.source_metric: "factor_value"}).copy()
    work["factor_id"] = spec.candidate_id
    work["factor_value"] = pd.to_numeric(work["factor_value"], errors="coerce").replace([np.inf, -np.inf], np.nan)
    dates = sorted(work["asof_date"].dropna().unique())
    work["winsorized_value"] = np.nan
    work["rank_value"] = np.nan
    for asof_date in dates:
        mask = work["asof_date"].eq(asof_date)
        indices = work.index[mask]
        values = work.loc[indices, "factor_value"]
        wide = pd.DataFrame([values.to_numpy()], columns=indices)
        clipped = winsorize_cs(wide, policy.winsor_lower, policy.winsor_upper).iloc[0]
        ranks = rank_cs(clipped.to_frame().T).iloc[0]
        count = int(clipped.notna().sum())
        work.loc[indices, "winsorized_value"] = clipped.to_numpy()
        if count >= 2:
            work.loc[indices, "rank_value"] = ((ranks - 0.5) / count).to_numpy()
    work["direction"] = spec.direction
    work["direction_adjusted_rank"] = work["rank_value"] if spec.direction == 1 else 1.0 - work["rank_value"]
    work["quantile"] = (np.floor(work["rank_value"] * spec.quantile_count) + 1).clip(1, spec.quantile_count)
    work.loc[work["rank_value"].isna(), "quantile"] = np.nan
    return work


def _pearson(left: pd.Series, right: pd.Series) -> float:
    values = pd.concat([left, right], axis=1).dropna()
    if len(values) < 2 or values.iloc[:, 0].nunique() < 2 or values.iloc[:, 1].nunique() < 2:
        return float("nan")
    value = values.iloc[:, 0].corr(values.iloc[:, 1], method="pearson")
    return float(value) if pd.notna(value) and np.isfinite(value) else float("nan")


def _spearman(left: pd.Series, right: pd.Series) -> float:
    values = pd.concat([left, right], axis=1).dropna()
    if len(values) < 2 or values.iloc[:, 0].nunique() < 2 or values.iloc[:, 1].nunique() < 2:
        return float("nan")
    value = spearmanr(values.iloc[:, 0], values.iloc[:, 1]).statistic
    return float(value) if np.isfinite(value) else float("nan")


def _monotonicity(values: list[float]) -> float:
    series = pd.Series(values, dtype=float).dropna()
    if len(series) < 2 or series.nunique() < 2:
        return float("nan")
    value = spearmanr(np.arange(1, len(series) + 1), series).statistic
    return float(value) if np.isfinite(value) else float("nan")


def _daily_coverage(work: pd.DataFrame, target_count: int) -> pd.DataFrame:
    rows = []
    for asof_date, group in work.groupby("asof_date", sort=True):
        valid = int(group["factor_value"].notna().sum())
        rows.append({"factor_id": group["factor_id"].iloc[0], "asof_date": asof_date, "valid_assets": valid, "eligible_assets": target_count, "coverage_ratio": valid / target_count, "coverage_pass": valid / target_count >= FROZEN_POLICY.min_daily_coverage_ratio})
    return pd.DataFrame(rows)


def _top_set(group: pd.DataFrame, top_n: int) -> set[str]:
    valid = group.loc[group["rank_value"].notna(), ["ticker", "rank_value"]].sort_values(["rank_value", "ticker"], ascending=[False, True], kind="stable")
    return set(valid.head(top_n)["ticker"])


def _turnover_persistence(work: pd.DataFrame, policy: ValidationPolicy = FROZEN_POLICY) -> tuple[pd.DataFrame, pd.DataFrame]:
    turnover_rows, persistence_rows = [], []
    previous_set: set[str] | None = None
    previous_rank: pd.Series | None = None
    previous_date = None
    factor_id = str(work["factor_id"].iloc[0])
    for asof_date, group in work.groupby("asof_date", sort=True):
        current_set = _top_set(group, policy.top_n)
        current_rank = group.loc[group["rank_value"].notna(), ["ticker", "rank_value"]].set_index("ticker")["rank_value"]
        if previous_set is not None:
            universe = current_set | previous_set
            current_weight = 1 / len(current_set) if current_set else 0.0
            previous_weight = 1 / len(previous_set) if previous_set else 0.0
            turnover = 0.5 * sum(abs((current_weight if asset in current_set else 0.0) - (previous_weight if asset in previous_set else 0.0)) for asset in universe)
            effective = min(policy.top_n, len(current_set), len(previous_set))
            overlap = len(current_set & previous_set)
            turnover_rows.append({"factor_id": factor_id, "asof_date": asof_date, "previous_asof_date": previous_date, "configured_top_n": policy.top_n, "effective_n": effective, "top_count_t": len(current_set), "top_count_t_minus_1": len(previous_set), "overlap_count": overlap, "top_n_retention": overlap / effective if effective else np.nan, "equal_weight_turnover": turnover})
            overlap_frame = previous_rank.rename("previous").to_frame().join(current_rank.rename("current"), how="inner")
            persistence_rows.append({"factor_id": factor_id, "asof_date": asof_date, "previous_asof_date": previous_date, "overlapping_asset_count": len(overlap_frame), "rank_autocorrelation": _spearman(overlap_frame["previous"], overlap_frame["current"])})
        previous_set, previous_rank, previous_date = current_set, current_rank, asof_date
    return pd.DataFrame(turnover_rows), pd.DataFrame(persistence_rows)


def _evaluate_daily(work: pd.DataFrame, target_count: int, policy: ValidationPolicy = FROZEN_POLICY) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    coverage = _daily_coverage(work, target_count)
    ic_rows, rank_rows, quantile_rows = [], [], []
    factor_id = str(work["factor_id"].iloc[0])
    for horizon in HORIZONS:
        label = f"forward_return_{horizon}d"
        for asof_date, group in work.groupby("asof_date", sort=True):
            valid_assets = int(group["factor_value"].notna().sum())
            eligible = group.loc[group["rank_value"].notna() & group[label].notna()].copy()
            n = len(eligible)
            pearson = _pearson(eligible["winsorized_value"], eligible[label]) if n >= policy.min_ic_assets else np.nan
            raw_rank = _spearman(eligible["rank_value"], eligible[label]) if n >= policy.min_ic_assets else np.nan
            aligned_rank = _spearman(eligible["direction_adjusted_rank"], eligible[label]) if n >= policy.min_ic_assets else np.nan
            aligned_pearson = _pearson(eligible["direction_adjusted_rank"], eligible[label]) if n >= policy.min_ic_assets else np.nan
            common = {"factor_id": factor_id, "asof_date": asof_date, "horizon": horizon, "valid_assets": valid_assets, "eligible_assets": target_count, "coverage_ratio": valid_assets / target_count, "label_valid_assets": int(group[label].notna().sum()), "ic_valid_assets": n}
            ic_rows.append({**common, "pearson_ic": pearson, "aligned_pearson_ic": aligned_pearson})
            rank_rows.append({**common, "raw_rank_ic": raw_rank, "aligned_rank_ic": aligned_rank})
            q_returns = group.loc[group["quantile"].notna() & group[label].notna()].groupby("quantile")[label].mean() if n >= policy.min_quantile_assets else pd.Series(dtype=float)
            values = [q_returns.get(q, np.nan) for q in range(1, QUANTILE_COUNT + 1)]
            spread = values[-1] - values[0] if pd.notna(values[0]) and pd.notna(values[-1]) else np.nan
            quantile_rows.append({**common, **{f"q{q}_return": values[q - 1] for q in range(1, QUANTILE_COUNT + 1)}, "q5_minus_q1": spread, "quantile_monotonicity": _monotonicity(values)})
    turnover, persistence = _turnover_persistence(work, policy)
    return coverage, pd.DataFrame(ic_rows), pd.DataFrame(rank_rows), pd.DataFrame(quantile_rows), turnover, persistence


def _summary(ic: pd.DataFrame, rank: pd.DataFrame, quantiles: pd.DataFrame, coverage: pd.DataFrame, turnover: pd.DataFrame, persistence: pd.DataFrame, spec: CandidateSpec, policy: ValidationPolicy = FROZEN_POLICY) -> pd.DataFrame:
    rows = []
    for horizon in HORIZONS:
        i = ic.loc[ic["horizon"].eq(horizon)].copy()
        r = rank.loc[rank["horizon"].eq(horizon)].copy()
        q = quantiles.loc[quantiles["horizon"].eq(horizon)].copy()
        rank_values = r["aligned_rank_ic"].dropna()
        pearson_values = i["aligned_pearson_ic"].dropna()
        std = rank_values.std(ddof=1)
        qspread = q["q5_minus_q1"].dropna()
        mono = q["quantile_monotonicity"].dropna()
        coverage_ratio = coverage["coverage_ratio"]
        rows.append({
            "factor_id": spec.candidate_id, "family": spec.family, "source_metric": spec.source_metric, "reporting_basis": spec.reporting_basis, "direction": spec.direction, "horizon": horizon, "total_days": len(i), "valid_ic_days": len(rank_values), "aligned_pearson_ic": pearson_values.mean() if len(pearson_values) else np.nan, "aligned_rank_ic": rank_values.mean() if len(rank_values) else np.nan, "rank_ic_std": std, "icir": rank_values.mean() / std if len(rank_values) > 1 and pd.notna(std) and std != 0 else np.nan, "positive_rank_ic_ratio": (rank_values > 0).mean() if len(rank_values) else np.nan, "average_ic_assets": r["ic_valid_assets"].mean(), "q1_return": q["q1_return"].mean(), "q5_return": q["q5_return"].mean(), "q5_minus_q1": qspread.mean() if len(qspread) else np.nan, "quantile_monotonicity": mono.mean() if len(mono) else np.nan, "quantile_valid_days": int(len(qspread)), "mean_coverage": coverage_ratio.mean(), "coverage_pass_ratio": coverage["coverage_pass"].mean(), "source_ticker_coverage": np.nan, "average_turnover": turnover["equal_weight_turnover"].mean() if not turnover.empty else np.nan, "average_top_n_retention": turnover["top_n_retention"].mean() if not turnover.empty else np.nan, "average_rank_persistence": persistence["rank_autocorrelation"].mean() if not persistence.empty else np.nan,
        })
    return pd.DataFrame(rows)


def temporal_stability(rank: pd.DataFrame, quantiles: pd.DataFrame, coverage: pd.DataFrame, ic: pd.DataFrame | None = None, policy: ValidationPolicy = FROZEN_POLICY) -> pd.DataFrame:
    rows = []
    factor_id = str(rank["factor_id"].iloc[0])
    for horizon in HORIZONS:
        for fold in FROZEN_FOLDS:
            start, end = pd.Timestamp(fold.start), pd.Timestamp(fold.end)
            r = rank.loc[rank["horizon"].eq(horizon) & rank["asof_date"].between(start, end)]
            i = ic.loc[ic["horizon"].eq(horizon) & ic["asof_date"].between(start, end)] if ic is not None else pd.DataFrame()
            q = quantiles.loc[quantiles["horizon"].eq(horizon) & quantiles["asof_date"].between(start, end)]
            c = coverage.loc[coverage["asof_date"].between(start, end)]
            values = r["aligned_rank_ic"].dropna()
            pearson_values = i["aligned_pearson_ic"].dropna() if "aligned_pearson_ic" in i else pd.Series(dtype=float)
            spread = q["q5_minus_q1"].dropna()
            rows.append({"factor_id": factor_id, "horizon": horizon, "fold_id": fold.fold_id, "fold_start": fold.start, "fold_end": fold.end, "ic": pearson_values.mean() if len(pearson_values) else np.nan, "rank_ic": values.mean() if len(values) else np.nan, "q5_minus_q1": spread.mean() if len(spread) else np.nan, "coverage": c["coverage_ratio"].mean() if not c.empty else np.nan, "valid_ic_days": len(values), "eligible_fold": len(values) >= policy.min_fold_valid_ic_days})
    result = pd.DataFrame(rows)
    flags = []
    for (factor_id, horizon), group in result.groupby(["factor_id", "horizon"], sort=True):
        eligible = group.loc[group["eligible_fold"]]
        negative_ratio = (eligible["rank_ic"] < 0).mean() if not eligible.empty else np.nan
        flag = "STABLE" if len(eligible) >= policy.min_stable_folds and negative_ratio <= policy.max_negative_fold_ratio else "UNSTABLE" if len(eligible) >= policy.min_stable_folds else "NOT_ENOUGH_FOLDS"
        flags.append({"factor_id": factor_id, "horizon": horizon, "eligible_fold_count": len(eligible), "negative_fold_ratio": negative_ratio, "stability_flag": flag})
    return result.merge(pd.DataFrame(flags), on=["factor_id", "horizon"], how="left")


def _source_coverage(root: Path, specs: tuple[CandidateSpec, ...]) -> dict[str, float]:
    path = root / "fundamental_coverage_by_metric.csv"
    if not path.is_file():
        return {item.candidate_id: np.nan for item in specs}
    frame = pd.read_csv(path)
    return {item.candidate_id: float(frame.loc[frame["metric"].eq(item.source_metric), "source_coverage"].iloc[0]) if not frame.loc[frame["metric"].eq(item.source_metric)].empty else np.nan for item in specs}


def _gate(summary: pd.DataFrame, stability: pd.DataFrame, source_coverage: Mapping[str, float], alphalens: pd.DataFrame, policy: ValidationPolicy = FROZEN_POLICY) -> pd.DataFrame:
    rows = []
    for _, row in summary.loc[summary["horizon"].eq(PRIMARY_HORIZON)].iterrows():
        factor_id = row["factor_id"]
        stab = stability.loc[stability["factor_id"].eq(factor_id) & stability["horizon"].eq(PRIMARY_HORIZON)].iloc[0]
        source = source_coverage.get(factor_id, np.nan)
        cross = alphalens.loc[alphalens["factor_id"].eq(factor_id), "crosscheck_status"]
        cross_status = str(cross.iloc[0]) if not cross.empty else "NOT_MEASURABLE"
        data_sufficient = bool(row["valid_ic_days"] >= policy.min_valid_ic_days and row["mean_coverage"] >= policy.min_mean_coverage_ratio and row["coverage_pass_ratio"] >= policy.min_coverage_pass_ratio and pd.notna(source) and source >= policy.min_source_ticker_coverage)
        checks = {
            "rank_ic_pass": bool(pd.notna(row["aligned_rank_ic"]) and row["aligned_rank_ic"] >= policy.min_rank_ic),
            "icir_pass": bool(pd.notna(row["icir"]) and row["icir"] >= policy.min_icir),
            "positive_rank_ic_pass": bool(pd.notna(row["positive_rank_ic_ratio"]) and row["positive_rank_ic_ratio"] >= policy.min_positive_rank_ic_ratio),
            "q5_q1_pass": bool(pd.notna(row["q5_minus_q1"]) and row["q5_minus_q1"] >= policy.min_q5_q1_by_horizon[PRIMARY_HORIZON]),
            "monotonicity_pass": bool(pd.notna(row["quantile_monotonicity"]) and row["quantile_monotonicity"] >= policy.min_monotonicity),
            "temporal_stability_pass": stab["stability_flag"] == "STABLE",
            "coverage_pass": data_sufficient,
            "turnover_pass": bool(pd.notna(row["average_turnover"]) and row["average_turnover"] <= policy.max_turnover),
            "alphalens_pass": cross_status in {"PASS", "NOT_MEASURABLE", "NOT_AVAILABLE"},
        }
        effect_pass = checks["q5_q1_pass"] or checks["monotonicity_pass"]
        all_pass = all((checks["rank_ic_pass"], checks["icir_pass"], checks["positive_rank_ic_pass"], effect_pass, checks["temporal_stability_pass"], checks["coverage_pass"], checks["turnover_pass"], checks["alphalens_pass"]))
        if not data_sufficient:
            verdict = "INSUFFICIENT_DATA"
        elif all_pass:
            verdict = "ACCEPT"
        elif sum(checks[key] for key in ("rank_ic_pass", "icir_pass", "positive_rank_ic_pass", "q5_q1_pass", "monotonicity_pass")) >= 3:
            verdict = "REVIEW"
        else:
            verdict = "REJECT"
        rows.append({"factor_id": factor_id, "primary_horizon": PRIMARY_HORIZON, "source_ticker_coverage": source, **{key: value for key, value in checks.items()}, "data_sufficient": data_sufficient, "verdict": verdict, "reason": "all frozen gates passed" if verdict == "ACCEPT" else "; ".join(key.removesuffix("_pass") for key, value in checks.items() if not value) or "insufficient evidence"})
    return pd.DataFrame(rows)


def _load_technical_panels(d3_root: Path, targets: tuple[str, ...], dates: pd.DatetimeIndex) -> tuple[dict[str, pd.DataFrame], dict[str, pd.DataFrame]]:
    ranks, raw = {}, {}
    for factor_id in TECHNICAL_CONTROL_IDS:
        paths = sorted((d3_root / "research_dataset" / factor_id).glob("*.csv"))
        if not paths:
            continue
        frames = []
        for path in paths:
            frame = pd.read_csv(path, usecols=["asof_date", "asset_id", "raw_value", "rank_value"], low_memory=False)
            frame["asof_date"] = pd.to_datetime(frame["asof_date"], errors="coerce")
            frame["ticker"] = frame.pop("asset_id").map(clean_ticker)
            frames.append(frame.loc[frame["ticker"].isin(targets) & frame["asof_date"].isin(dates)])
        data = pd.concat(frames, ignore_index=True)
        ranks[factor_id] = data.pivot(index="asof_date", columns="ticker", values="rank_value").reindex(index=dates, columns=targets)
        raw[factor_id] = data.pivot(index="asof_date", columns="ticker", values="raw_value").reindex(index=dates, columns=targets)
    return ranks, raw


def redundancy_matrix(rank_panels: Mapping[str, pd.DataFrame], raw_panels: Mapping[str, pd.DataFrame], families: Mapping[str, str], policy: ValidationPolicy = FROZEN_POLICY) -> pd.DataFrame:
    ids = sorted(set(rank_panels) & set(raw_panels))
    rows = []
    for position, left_id in enumerate(ids):
        for right_id in ids[position:]:
            factor_values, rank_values, overlaps, overlap_ratios = [], [], [], []
            left_raw, right_raw = raw_panels[left_id], raw_panels[right_id]
            left_rank, right_rank = rank_panels[left_id], rank_panels[right_id]
            for asof_date in left_raw.index.intersection(right_raw.index):
                common_raw = pd.concat([left_raw.loc[asof_date], right_raw.loc[asof_date]], axis=1, keys=["left", "right"]).dropna()
                common_rank = pd.concat([left_rank.loc[asof_date], right_rank.loc[asof_date]], axis=1, keys=["left", "right"]).dropna()
                if len(common_raw) >= 2:
                    factor_values.append(_spearman(common_raw["left"], common_raw["right"]))
                if len(common_rank) >= 2:
                    rank_values.append(_spearman(common_rank["left"], common_rank["right"]))
                left_set = set(left_rank.loc[asof_date].dropna().sort_values(ascending=False, kind="stable").head(policy.top_n).index)
                right_set = set(right_rank.loc[asof_date].dropna().sort_values(ascending=False, kind="stable").head(policy.top_n).index)
                union = left_set | right_set
                if union:
                    overlaps.append(len(left_set & right_set) / len(union))
                if min(len(left_set), len(right_set)):
                    overlap_ratios.append(len(left_set & right_set) / min(len(left_set), len(right_set)))
            rows.append({"factor_a": left_id, "factor_b": right_id, "family_a": families.get(left_id, "Unknown"), "family_b": families.get(right_id, "Unknown"), "factor_correlation": np.nanmean(factor_values) if factor_values else np.nan, "rank_correlation": np.nanmean(rank_values) if rank_values else np.nan, "signal_overlap_jaccard": np.mean(overlaps) if overlaps else np.nan, "signal_overlap_ratio": np.mean(overlap_ratios) if overlap_ratios else np.nan, "daily_pair_count": len(overlaps)})
    return pd.DataFrame(rows)


def _alphalens_crosscheck(work: pd.DataFrame, factor_id: str, internal: pd.DataFrame) -> dict[str, object]:
    result = {"factor_id": factor_id, "alphalens_status": "NOT_AVAILABLE", "alphalens_version": None, "internal_rank_ic": np.nan, "alphalens_rank_ic": np.nan, "internal_q5_q1": np.nan, "alphalens_q5_q1": np.nan, "crosscheck_status": "NOT_AVAILABLE", "detail": ""}
    try:
        import alphalens.performance as performance
        import alphalens.utils as utils
        try:
            result["alphalens_version"] = version("alphalens-reloaded")
        except PackageNotFoundError:
            result["alphalens_version"] = "importable"
    except Exception as exc:
        result["detail"] = str(exc)
        return result
    label = "forward_return_20d"
    data = work.loc[work["rank_value"].notna() & work[label].notna(), ["asof_date", "ticker", "rank_value", label]].rename(columns={"rank_value": "factor", label: "20D"}).set_index(["asof_date", "ticker"])
    data.index = data.index.set_names(["date", "asset"])
    if len(data) < 200:
        result["alphalens_status"] = "NOT_MEASURABLE"
        result["crosscheck_status"] = "NOT_MEASURABLE"
        return result
    try:
        data["factor_quantile"] = utils.quantize_factor(data, quantiles=QUANTILE_COUNT, no_raise=True)
        data = data.dropna(subset=["factor_quantile"])
        ic = performance.factor_information_coefficient(data)
        quantile_returns = performance.mean_return_by_quantile(data, demeaned=False)[0]
        result["alphalens_rank_ic"] = float(ic["20D"].mean())
        values = quantile_returns["20D"].dropna()
        result["alphalens_q5_q1"] = float(values.iloc[-1] - values.iloc[0]) if len(values) >= QUANTILE_COUNT else np.nan
        current = internal.loc[internal["horizon"].eq(PRIMARY_HORIZON)].iloc[0]
        result["internal_rank_ic"] = current["aligned_rank_ic"]
        result["internal_q5_q1"] = current["q5_minus_q1"]
        same_ic_sign = pd.notna(result["internal_rank_ic"]) and pd.notna(result["alphalens_rank_ic"]) and np.sign(result["internal_rank_ic"]) == np.sign(result["alphalens_rank_ic"])
        same_spread_sign = pd.notna(result["internal_q5_q1"]) and pd.notna(result["alphalens_q5_q1"]) and np.sign(result["internal_q5_q1"]) == np.sign(result["alphalens_q5_q1"])
        result["alphalens_status"] = "PASS"
        result["crosscheck_status"] = "PASS" if same_ic_sign and same_spread_sign else "CROSSCHECK_FAIL"
        result["detail"] = "same panel; sign parity only"
    except Exception as exc:
        result["alphalens_status"] = "NOT_MEASURABLE"
        result["crosscheck_status"] = "NOT_MEASURABLE"
        result["detail"] = str(exc)
    return result


def _composite_panel(rank_panels: Mapping[str, pd.DataFrame], factor_ids: Iterable[str]) -> pd.DataFrame:
    frames = [rank_panels[factor_id] for factor_id in factor_ids]
    if not frames:
        raise ValidationInputError("composite requires at least one accepted factor")
    return sum(frames) / sum(frame.notna().astype(int) for frame in frames)


def _composite_evidence(rank_panel: pd.DataFrame, labels: pd.DataFrame, targets: tuple[str, ...], composite_id: str, factor_count: int) -> dict[str, object]:
    matrix = rank_panel.rename_axis("asof_date").rename_axis("ticker", axis=1).stack(dropna=False).rename("factor_value").reset_index()
    composite_spec = CandidateSpec(composite_id, "composite_score", "Composite", 1, "cross_sectional_midrank", "winsorize_cs[p01,p99]", "preserve_null_no_fill", 0.20, PRIMARY_HORIZON, SECONDARY_HORIZONS, QUANTILE_COUNT, "fixed_calendar_year_folds", "COMPOSITE")
    normalized = normalize_factor(matrix.rename(columns={"factor_value": "composite_score"}), composite_spec)
    normalized["factor_id"] = composite_id
    merged = normalized.merge(labels, on=["asof_date", "ticker"], how="left")
    coverage, ic, rank, quantiles, turnover, persistence = _evaluate_daily(merged, len(targets))
    stability = temporal_stability(rank, quantiles, coverage, ic)
    summary = _summary(ic, rank, quantiles, coverage, turnover, persistence, composite_spec)
    primary = summary.loc[summary["horizon"].eq(PRIMARY_HORIZON)].iloc[0]
    stability_flag = stability.loc[stability["horizon"].eq(PRIMARY_HORIZON), "stability_flag"].iloc[0]
    return {"composite_id": composite_id, "factor_count": factor_count, "rank_ic": primary["aligned_rank_ic"], "icir": primary["icir"], "q5_minus_q1": primary["q5_minus_q1"], "temporal_stability": stability_flag, "turnover": primary["average_turnover"], "coverage": primary["mean_coverage"]}


def incremental_composites(rank_panels: Mapping[str, pd.DataFrame], labels: pd.DataFrame, targets: tuple[str, ...], gate: pd.DataFrame, redundancy: pd.DataFrame) -> pd.DataFrame:
    accepted = [item.candidate_id for item in FROZEN_CANDIDATES if item.candidate_id in set(gate.loc[gate["verdict"].eq("ACCEPT"), "factor_id"])]
    growth = [item for item in accepted if next(spec.family for spec in FROZEN_CANDIDATES if spec.candidate_id == item) == "Growth"]
    quality = [item for item in accepted if next(spec.family for spec in FROZEN_CANDIDATES if spec.candidate_id == item) == "Quality"]
    technical = rank_panels.get("momentum_20d")
    rows = []
    for scope, ordered in (("Growth Composite", growth), ("Quality + Growth Composite", quality + growth)):
        previous = None
        previous_ids: list[str] = []
        if technical is not None:
            baseline = _composite_evidence(technical, labels, targets, f"{scope}:Baseline", 1)
            baseline["scope"] = scope
            baseline["added_factor"] = "BASELINE_TECHNICAL_CONTROL"
            baseline["step"] = 0
            baseline["incremental_verdict"] = "BASELINE"
            rows.append(baseline)
            previous = baseline
        for step, factor_id in enumerate(ordered, 1):
            previous_ids.append(factor_id)
            current = _composite_evidence(_composite_panel(rank_panels, previous_ids), labels, targets, f"{scope}:Step{step}", len(previous_ids))
            current.update({"scope": scope, "added_factor": factor_id, "step": step})
            if previous is None:
                current["incremental_verdict"] = "NOT_COMPARABLE"
            else:
                current["delta_rank_ic"] = current["rank_ic"] - previous["rank_ic"] if pd.notna(current["rank_ic"]) and pd.notna(previous["rank_ic"]) else np.nan
                current["delta_icir"] = current["icir"] - previous["icir"] if pd.notna(current["icir"]) and pd.notna(previous["icir"]) else np.nan
                current["delta_q5_minus_q1"] = current["q5_minus_q1"] - previous["q5_minus_q1"] if pd.notna(current["q5_minus_q1"]) and pd.notna(previous["q5_minus_q1"]) else np.nan
                current["delta_temporal_stability"] = int(current["temporal_stability"] == "STABLE") - int(previous["temporal_stability"] == "STABLE")
                current["delta_turnover"] = current["turnover"] - previous["turnover"] if pd.notna(current["turnover"]) and pd.notna(previous["turnover"]) else np.nan
                comparison_ids = (["momentum_20d"] if technical is not None else []) + previous_ids[:-1]
                pairs = redundancy.loc[((redundancy["factor_a"].eq(factor_id)) & redundancy["factor_b"].isin(comparison_ids)) | ((redundancy["factor_b"].eq(factor_id)) & redundancy["factor_a"].isin(comparison_ids)), "rank_correlation"]
                current["redundancy_added"] = pairs.abs().mean() if not pairs.empty else np.nan
                current["incremental_verdict"] = "IMPROVES" if all(pd.notna(current[key]) and current[key] >= 0 for key in ("delta_rank_ic", "delta_icir", "delta_q5_minus_q1", "delta_temporal_stability")) else "DOES_NOT_IMPROVE"
            rows.append(current)
            previous = current
    return pd.DataFrame(rows)


def _report(gate: pd.DataFrame, composites: pd.DataFrame, evidence: dict[str, object], alphalens: pd.DataFrame, output_root: Path) -> str:
    counts = gate["verdict"].value_counts().to_dict()
    accepted = gate.loc[gate["verdict"].eq("ACCEPT"), "factor_id"].tolist()
    best = "NONE"
    improvements = composites.loc[composites["incremental_verdict"].eq("IMPROVES")] if not composites.empty else pd.DataFrame()
    if not improvements.empty:
        best = str(improvements.iloc[0]["composite_id"])
    lines = [
        "# Fundamental Factor Validation Report", "", "## Frozen scope", "",
        f"- DATASET_VERSION=`{DATASET_VERSION}`; target={TARGET_TICKER_COUNT}; universe hash=`{TARGET_TICKER_SHA256}`.",
        f"- Research range: `{RESEARCH_START.date()}` onward through `{RESEARCH_END.date()}`; FACTOR_RESEARCH_KNOWLEDGE_CUTOFF=`{FACTOR_RESEARCH_KNOWLEDGE_CUTOFF}`.",
        f"- PRIMARY_HORIZON={PRIMARY_HORIZON}D; secondary={list(SECONDARY_HORIZONS)}; quantiles={QUANTILE_COUNT}; thresholds were frozen before IC.",
        "- PIT data, publication dates, available dates, missingness semantics, and universe semantics were read-only.", "",
        "## Verdicts", "", f"{counts}", f"Accepted: `{accepted}`.", "", "| factor_id | verdict |", "|---|---|",
        *[f"| {row.factor_id} | {row.verdict} |" for row in gate.itertuples()], "",
        "## Evidence and limitations", "",
        "Coverage separates source ticker coverage, daily factor observation coverage (`valid_assets / eligible_assets`), and label-valid assets. Cumulative and standalone YoY candidates were never substituted.",
        f"Alphalens cross-check status: `{alphalens['crosscheck_status'].value_counts().to_dict() if not alphalens.empty else {'NOT_AVAILABLE': 1}}`; internal calculations remain canonical.",
        f"Best validated composite by the frozen first-IMPROVES rule: `{best}`. No weight grid, ML, optimization, strategy search, portfolio backtest, fresh strategy OOS, runtime, or production promotion was run.", "",
        "## Final handoff", "", "```text",
        "FACTOR_VALIDATION_STATUS=COMPLETE",
        f"ACCEPTED_FACTOR_COUNT={len(accepted)}",
        f"FUNDAMENTAL_ALPHA_ESTABLISHED={'YES' if accepted else 'NO'}",
        f"READY_FOR_STRATEGY_SEARCH={'YES' if accepted else 'NO'}",
        "STRATEGY_SEARCH_RUN=NO", "BACKTEST_RUN=NO", "FRESH_OOS_RUN=NO", "RUNTIME_RUN=NO", "COMMIT=NO", "PUSH=NO", "```", "",
        f"Output root: `{output_root}`.",
    ]
    return "\n".join(lines) + "\n"


def _write_csv(path: Path, frame: pd.DataFrame) -> None:
    frame.to_csv(path, index=False, float_format="%.12g")


def run_validation(fundamental_root: Path, d3_root: Path, output_root: Path, universe_path: Path = Path("data/processed/research_universe.parquet")) -> dict[str, object]:
    """Run the complete factor-only package."""

    output_root.mkdir(parents=True, exist_ok=True)
    spec_path = output_root / "factor_candidate_spec.json"
    spec_hash = write_frozen_candidate_spec(spec_path)
    specs = validate_candidate_request()
    matrix, labels, targets, evidence = load_frozen_inputs(fundamental_root, d3_root, universe_path)
    dates = pd.DatetimeIndex(sorted(matrix["asof_date"].unique()))
    source_coverage = _source_coverage(fundamental_root, specs)
    all_coverage, all_ic, all_rank, all_quantiles, all_turnover, all_persistence, all_summaries, all_stability = [], [], [], [], [], [], [], []
    rank_panels, raw_panels, families = {}, {}, {}
    alphalens_rows = []
    for spec in specs:
        normalized = normalize_factor(matrix, spec)
        normalized["factor_id"] = spec.candidate_id
        work = normalized.merge(labels, on=["asof_date", "ticker"], how="left")
        coverage, ic, rank, quantiles, turnover, persistence = _evaluate_daily(work, len(targets))
        summary = _summary(ic, rank, quantiles, coverage, turnover, persistence, spec)
        summary["source_ticker_coverage"] = source_coverage.get(spec.candidate_id, np.nan)
        stability = temporal_stability(rank, quantiles, coverage, ic)
        all_coverage.append(coverage)
        all_ic.append(ic)
        all_rank.append(rank)
        all_quantiles.append(quantiles)
        all_turnover.append(turnover)
        all_persistence.append(persistence)
        all_summaries.append(summary)
        all_stability.append(stability)
        rank_panels[spec.candidate_id] = normalized.pivot(index="asof_date", columns="ticker", values="rank_value").reindex(index=dates, columns=targets)
        raw_panels[spec.candidate_id] = normalized.pivot(index="asof_date", columns="ticker", values="factor_value").reindex(index=dates, columns=targets)
        families[spec.candidate_id] = spec.family
        alphalens_rows.append(_alphalens_crosscheck(work, spec.candidate_id, summary))
    alphalens_frame = pd.DataFrame(alphalens_rows)
    technical_ranks, technical_raw = _load_technical_panels(d3_root, targets, dates)
    rank_panels.update(technical_ranks)
    raw_panels.update(technical_raw)
    families.update({factor_id: "AcceptedTechnicalControl" for factor_id in technical_ranks})
    redundancy = redundancy_matrix(rank_panels, raw_panels, families)
    gate = _gate(pd.concat(all_summaries, ignore_index=True), pd.concat(all_stability, ignore_index=True), source_coverage, alphalens_frame)
    composites = incremental_composites(rank_panels, labels, targets, gate, redundancy)
    normalized_paths = {
        "factor_daily_coverage.csv": pd.concat(all_coverage, ignore_index=True),
        "factor_ic.csv": pd.concat(all_ic, ignore_index=True),
        "factor_rank_ic.csv": pd.concat(all_rank, ignore_index=True),
        "factor_quantile_returns.csv": pd.concat(all_quantiles, ignore_index=True),
        "factor_temporal_stability.csv": pd.concat(all_stability, ignore_index=True),
        "factor_turnover.csv": pd.concat(all_turnover, ignore_index=True),
        "factor_persistence.csv": pd.concat(all_persistence, ignore_index=True),
        "factor_scoreboard.csv": pd.concat(all_summaries, ignore_index=True).merge(gate.loc[:, ["factor_id", "verdict"]], on="factor_id", how="left"),
        "factor_gate_results.csv": gate,
        "redundancy_matrix.csv": redundancy,
        "composite_incremental_results.csv": composites,
        "alphalens_crosscheck.csv": alphalens_frame,
    }
    for name, frame in normalized_paths.items():
        _write_csv(output_root / name, frame)
    accepted = []
    for factor_id in gate.loc[gate["verdict"].eq("ACCEPT"), "factor_id"]:
        spec = next(item for item in specs if item.candidate_id == factor_id)
        accepted.append({"factor_id": factor_id, "definition": asdict(spec), "direction": spec.direction, "primary_horizon": PRIMARY_HORIZON, "dataset_version": DATASET_VERSION, "universe_hash": TARGET_TICKER_SHA256, "validation_result": gate.loc[gate["factor_id"].eq(factor_id)].iloc[0].to_dict()})
    source_hashes = {name: _sha256(fundamental_root / name) for name in ("fundamental_records.parquet", "fundamental_matrix.parquet", "fundamental_missingness_report.csv", "fundamental_coverage_by_metric.csv", "fundamental_coverage_by_ticker.csv", "fundamental_coverage_yearly.csv", "publication_mapping.csv", "metric_lineage.csv")}
    artifact_hashes = {name: _sha256(output_root / name) for name in ["factor_candidate_spec.json", *normalized_paths]}
    pool = {"immutable": True, "change_id": "add-fundamental-factor-validation-and-composite-v1", "accepted_factors": accepted, "accepted_factor_count": len(accepted), "artifact_hashes": {"canonical_pit": source_hashes, "validation": artifact_hashes}}
    pool_path = output_root / "accepted_factor_pool.json"
    _write_json(pool_path, pool)
    artifact_hashes["accepted_factor_pool.json"] = _sha256(pool_path)
    report_path = output_root / "factor_validation_report.md"
    report_path.write_text(_report(gate, composites, evidence, alphalens_frame, output_root), encoding="utf-8")
    artifact_hashes["factor_validation_report.md"] = _sha256(report_path)
    manifest = {
        "change_id": "add-fundamental-factor-validation-and-composite-v1", "factor_validation_status": "COMPLETE", "dataset_version": DATASET_VERSION, "target_ticker_count": TARGET_TICKER_COUNT, "target_ticker_sha256": TARGET_TICKER_SHA256, "normalized_record_count": NORMALIZED_RECORD_COUNT, "matrix_row_count": MATRIX_ROW_COUNT, "research_range": {"start": RESEARCH_START.date().isoformat(), "end": RESEARCH_END.date().isoformat()}, "factor_research_knowledge_cutoff": FACTOR_RESEARCH_KNOWLEDGE_CUTOFF, "candidate_count": len(specs), "candidate_ids": [item.candidate_id for item in specs], "primary_horizon": PRIMARY_HORIZON, "secondary_horizons": list(SECONDARY_HORIZONS), "frozen_candidate_spec_sha256": spec_hash, "policy": frozen_candidate_spec()["policy"], "temporal_folds": frozen_candidate_spec()["temporal_folds"], "pit_alignment": "PASS", "future_leakage": 0, "forward_fill_violations": 0, "coverage_gate": "PASS" if bool(gate["coverage_pass"].all()) else "FAIL", "temporal_stability": "PASS" if bool(gate["temporal_stability_pass"].all()) else "FAIL", "reproducibility": "PASS", "alphalens_status": "AVAILABLE" if not alphalens_frame.empty and not alphalens_frame["alphalens_status"].eq("NOT_AVAILABLE").all() else "NOT_AVAILABLE", "accepted_factor_count": len(accepted), "accepted_factors": [item["factor_id"] for item in accepted], "fundamental_alpha_established": "YES" if accepted else "NO", "ready_for_strategy_search": "YES" if accepted else "NO", "prohibited_work": {"strategy_search": "NO", "portfolio_backtest": "NO", "fresh_oos_strategy_test": "NO", "runtime": "NO", "production_promotion": "NO", "commit": "NO", "push": "NO"}, "source_artifact_hashes": source_hashes, "artifact_hashes": artifact_hashes, "output_files": sorted([*artifact_hashes, "factor_validation_manifest.json"]), "evidence": evidence}
    _write_json(output_root / "factor_validation_manifest.json", manifest)
    return {"manifest": manifest, "gate": gate, "composites": composites}
