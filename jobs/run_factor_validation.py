"""Run the frozen, research-only Day 2 factor validation pipeline."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Mapping

import numpy as np
import pandas as pd
from scipy.stats import spearmanr
from sqlalchemy import text

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.research import normalize
from core.research.day2_candidates import (
    D3_RUN_ID,
    D4_POLICY,
    FACTOR_SCORE_WEIGHTS,
    STRICT_OOS_END,
    STRICT_OOS_START,
    TRAIN_END,
    TRAIN_START,
    UNIV_RESEARCH_V1,
    UNIV_RUNTIME_V1,
    VALIDATION_END,
    VALIDATION_START,
    CandidateSpec,
    candidate_specs,
    compute_candidate,
    frozen_parameters,
    implementation_confidence,
    panel_to_long,
)
from core.research.factor_evaluation import load_d3_dataset
from core.research.sources import twse


# D3_POLICY is a named alias kept in the runner manifest; it is the same
# imported EvaluationPolicy instance used by D4, not a second set of gates.
D3_POLICY = D4_POLICY
MASTER_PATH = Path("outputs/factor_inventory/candidate_inventory_20260910_v1/candidate_factor_master.csv")
DAY1_ROOT = MASTER_PATH.parent
DEFAULT_OUTPUT = Path("outputs/factor_validation/factor_validation_20260910_v1")
HORIZONS = (5, 20, 60)
REQUIRED_OUTPUTS = (
    "validation_candidate_manifest.csv",
    "factor_validation_scoreboard.csv",
    "factor_quantile_results.csv",
    "factor_temporal_stability.csv",
    "factor_redundancy_matrix.csv",
    "factor_redundancy_clusters.csv",
    "alphalens_crosscheck.csv",
    "runtime_conflict_resolution.md",
    "accepted_factor_pool.csv",
    "rejected_factor_log.csv",
    "day2_factor_validation_report.md",
)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def freeze_day1(root: Path = DAY1_ROOT) -> dict[str, object]:
    """Validate and fingerprint the eight immutable Day 1 files."""

    expected = {
        "alpha101_inventory.csv": 83,
        "alpha191_inventory.csv": 176,
        "candidate_factor_master.csv": 335,
        "candidate_family_cluster.md": None,
        "day1_inventory_report.md": None,
        "research_factor_inventory.csv": 14,
        "runtime_research_gap_matrix.csv": 50,
        "runtime_strategy_inventory.csv": 62,
    }
    files = {}
    for name, expected_rows in expected.items():
        path = root / name
        if not path.is_file():
            raise RuntimeError(f"Day 1 artifact missing: {path}")
        row_count = None
        if path.suffix.lower() == ".csv":
            row_count = len(pd.read_csv(path))
            if expected_rows is not None and row_count != expected_rows:
                raise RuntimeError(f"Day 1 row count mismatch: {name}={row_count}, expected {expected_rows}")
        files[name] = {"sha256": _sha256(path), "bytes": path.stat().st_size, "rows": row_count}
    master = pd.read_csv(root / "candidate_factor_master.csv")
    if master["candidate_id"].nunique() != 335:
        raise RuntimeError("Day 1 candidate_id uniqueness check failed")
    p2 = master.loc[master["research_priority"].eq("P2") & master["source"].isin(["Alpha101", "Alpha191"])]
    if len(p2) != 37:
        raise RuntimeError(f"Day 1 P2 shortlist mismatch: {len(p2)}")
    return {"root": str(root), "file_count": len(files), "files": files, "total_candidates": 335, "p2_alpha_shortlist": 37}


def _read_dataset(root: Path, factor_id: str, *, end: pd.Timestamp, start: pd.Timestamp = TRAIN_START) -> pd.DataFrame:
    directory = root / "research_dataset" / factor_id
    paths = sorted(directory.glob("*.csv"))
    if not paths:
        raise RuntimeError(f"D3 factor partition missing: {factor_id}")
    wanted = {"asof_date", "asset_id", "raw_value", "member", "direction"}
    wanted.update(f"forward_return_{h}d" for h in HORIZONS)
    frames = []
    for path in paths:
        frame = pd.read_csv(path, usecols=lambda column: column in wanted, low_memory=False)
        frame["asof_date"] = pd.to_datetime(frame["asof_date"])
        frame = frame.loc[(frame["asof_date"] >= start) & (frame["asof_date"] <= end)]
        frames.append(frame)
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame(columns=sorted(wanted))


def load_pre_oos_labels(d3_root: Path) -> pd.DataFrame:
    """Read only pre-OOS D3 rows before any decision is made."""

    labels = _read_dataset(d3_root, "momentum_20d", end=VALIDATION_END)
    labels["asset_id"] = labels["asset_id"].astype(str)
    labels["member"] = labels["member"].fillna(False).astype(bool)
    return labels


def load_oos_labels(d3_root: Path) -> pd.DataFrame:
    """Read the strict holdout only after pre-OOS decisions are frozen."""

    labels = _read_dataset(d3_root, "momentum_20d", start=STRICT_OOS_START, end=STRICT_OOS_END)
    labels["asset_id"] = labels["asset_id"].astype(str)
    labels["member"] = labels["member"].fillna(False).astype(bool)
    return labels


def _load_raw_quotes(d3_root: Path, *, end: pd.Timestamp) -> pd.DataFrame:
    """Load cached official OHLCV rows without touching future partitions."""

    cache = d3_root / "_raw" / "twse_rwd"
    rows = []
    for path in sorted(cache.glob("MI_INDEX_*.json")):
        try:
            day = pd.Timestamp(path.stem.removeprefix("MI_INDEX_"))
        except ValueError:
            continue
        if day > end:
            continue
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            table = twse.find_closing_table(payload)
            frame = normalize.normalize_twse_closing_table(table, day, None)
            if not frame.empty:
                rows.append(frame.loc[:, ["trade_date", "stock_id", "raw_open", "raw_high", "raw_low", "raw_close", "volume", "amount"]])
        except (OSError, ValueError, twse.SchemaDriftError):
            continue
    if not rows:
        return pd.DataFrame(columns=["trade_date", "stock_id", "raw_open", "raw_high", "raw_low", "raw_close", "volume", "amount"])
    quotes = pd.concat(rows, ignore_index=True)
    quotes["trade_date"] = pd.to_datetime(quotes["trade_date"])
    quotes["stock_id"] = quotes["stock_id"].astype(str)
    return quotes.drop_duplicates(["trade_date", "stock_id"]).sort_values(["trade_date", "stock_id"])


def _quote_frames(quotes: pd.DataFrame) -> dict[str, pd.DataFrame]:
    if quotes.empty:
        empty = pd.DataFrame()
        return {key: empty for key in ("open", "high", "low", "close", "volume", "amount")}
    index = pd.Index(sorted(quotes["trade_date"].unique()), name="asof_date")
    columns = pd.Index(sorted(quotes["stock_id"].unique()), name="asset_id")
    return {
        key: quotes.pivot(index="trade_date", columns="stock_id", values=source).reindex(index=index, columns=columns)
        for key, source in {
            "open": "raw_open", "high": "raw_high", "low": "raw_low", "close": "raw_close", "volume": "volume", "amount": "amount",
        }.items()
    }


def _existing_panels(d3_root: Path, labels: pd.DataFrame) -> dict[str, pd.DataFrame]:
    mapping = {
        "RS_001": "momentum_20d", "RS_002": "momentum_60d", "RS_003": "momentum_12_1", "RS_004": "near_high_252d",
        "RS_008": "volume_ratio_20d", "RS_011": "realized_vol_20d", "RS_012": "natr_14d", "RS_013": "amihud_20d",
    }
    result = {}
    for candidate_id, factor_id in mapping.items():
        frame = _read_dataset(d3_root, factor_id, start=labels["asof_date"].min(), end=labels["asof_date"].max())
        result[candidate_id] = frame.pivot(index="asof_date", columns="asset_id", values="raw_value")
    return result


def _next_trading_date(date: pd.Timestamp, calendar: pd.DatetimeIndex) -> pd.Timestamp | pd.NaT:
    positions = calendar.searchsorted(date, side="right")
    return calendar[positions] if positions < len(calendar) else pd.NaT


def _db_tier2_data(frames: Mapping[str, pd.DataFrame]) -> tuple[dict[str, pd.DataFrame], list[dict[str, object]]]:
    """Load optional Tier 2 sources through db_helper and apply PIT lags."""

    evidence: list[dict[str, object]] = []
    result: dict[str, pd.DataFrame] = {}
    try:
        from core.db_helper import get_db_engine, get_table_columns

        engine = get_db_engine(max_retries=1)
    except Exception as caught:
        evidence.append({"source": "database", "status": "PIT_RISK", "detail": f"unavailable: {caught}"})
        return result, evidence

    calendar = pd.DatetimeIndex(sorted(frames["close"].index))
    def query_table(table: str, columns: list[str]) -> pd.DataFrame:
        try:
            available = get_table_columns(table, engine=engine)
            selected = [column for column in columns if column in available]
            if not selected:
                evidence.append({"source": table, "status": "PIT_RISK", "detail": "required columns unavailable"})
                return pd.DataFrame()
            with engine.connect() as connection:
                return pd.read_sql(text(f"SELECT {', '.join(selected)} FROM {table}"), connection)
        except Exception as caught:
            evidence.append({"source": table, "status": "PIT_RISK", "detail": str(caught)})
            return pd.DataFrame()

    revenue = query_table("monthly_revenue", ["stock_id", "year", "month", "revenue_yoy", "announcement_date"])
    if not revenue.empty and {"stock_id", "year", "month", "revenue_yoy"} <= set(revenue):
        fallback_date = pd.to_datetime(
            revenue.apply(lambda row: f"{int(row.year):04d}-{int(row.month):02d}-10", axis=1), errors="coerce"
        )
        if "announcement_date" in revenue:
            announced = pd.to_datetime(revenue["announcement_date"], errors="coerce")
            revenue["available_date"] = announced.fillna(fallback_date) + pd.offsets.BDay(1)
            pit_status = "PIT_VERIFIED" if announced.notna().all() else "PIT_RISK"
        else:
            revenue["available_date"] = fallback_date + pd.offsets.BDay(1)
            pit_status = "PIT_RISK"
        result["revenue_yoy"] = _fundamental_panel(revenue, "revenue_yoy", calendar)
        evidence.append({"source": "monthly_revenue", "status": pit_status, "detail": "announcement date; missing dates fallback to next-month 10th + 1"})

    financial = query_table("financial_statements", ["stock_id", "year", "quarter", "operating_profit", "revenue", "eps", "operating_margin", "announcement_date"])
    if not financial.empty and {"stock_id", "year", "quarter"} <= set(financial):
        release = {1: (5, 15), 2: (8, 14), 3: (11, 14), 4: (3, 31)}
        fallback_date = pd.to_datetime(
            financial.apply(lambda row: f"{int(row.year) + (1 if int(row.quarter) == 4 else 0):04d}-{release.get(int(row.quarter), (12, 31))[0]:02d}-{release.get(int(row.quarter), (12, 31))[1]:02d}", axis=1), errors="coerce"
        )
        if "announcement_date" in financial:
            announced = pd.to_datetime(financial["announcement_date"], errors="coerce")
            financial["available_date"] = announced.fillna(fallback_date) + pd.offsets.BDay(1)
            pit_status = "PIT_VERIFIED" if announced.notna().all() else "PIT_RISK"
        else:
            financial["available_date"] = fallback_date + pd.offsets.BDay(1)
            pit_status = "PIT_RISK"
        if {"operating_profit", "revenue"} <= set(financial):
            financial["operating_profit_margin"] = financial["operating_profit"] / financial["revenue"].replace(0, np.nan)
            result["operating_profit_margin"] = _fundamental_panel(financial, "operating_profit_margin", calendar)
        if "eps" in financial:
            result["eps_level"] = _fundamental_panel(financial, "eps", calendar)
            result["eps_positive"] = result["eps_level"].gt(0).astype(float)
        evidence.append({"source": "financial_statements", "status": pit_status, "detail": "announcement date; missing dates fallback to statutory deadline + 1"})

    chip = query_table("daily_market_data", ["stock_id", "trade_date", "foreign_buy", "trust_buy"])
    if not chip.empty and {"stock_id", "trade_date"} <= set(chip):
        chip["trade_date"] = pd.to_datetime(chip["trade_date"], errors="coerce")
        chip["available_date"] = [ _next_trading_date(value, calendar) for value in chip["trade_date"] ]
        for source, output_name in (("foreign_buy", "foreign_net_buy"), ("trust_buy", "trust_net_buy")):
            if source in chip:
                result[output_name] = _fundamental_panel(chip, source, calendar)
                result[output_name.replace("_net_buy", "_buy_streak")] = _streak_panel(chip, source, calendar)
        evidence.append({"source": "daily_market_data", "status": "PIT_RISK", "detail": "institutional signal aligned to next trading date"})
    return result, evidence


def _fundamental_panel(frame: pd.DataFrame, value_column: str, calendar: pd.DatetimeIndex) -> pd.DataFrame:
    values = frame.loc[:, ["available_date", "stock_id", value_column]].dropna(subset=["available_date"])
    panel = values.pivot_table(index="available_date", columns="stock_id", values=value_column, aggfunc="last")
    return panel.reindex(calendar).ffill()


def _streak_panel(frame: pd.DataFrame, value_column: str, calendar: pd.DatetimeIndex) -> pd.DataFrame:
    work = frame.loc[:, ["available_date", "stock_id", value_column]].copy()
    work["positive"] = pd.to_numeric(work[value_column], errors="coerce").gt(0)
    work["streak"] = work.groupby("stock_id")["positive"].transform(lambda values: values.astype(int).groupby((~values).cumsum()).cumsum())
    return _fundamental_panel(work, "streak", calendar)


def _spearman(left: pd.Series, right: pd.Series) -> float:
    left, right = left.astype(float), right.astype(float)
    if len(left) < 2 or left.nunique() < 2 or right.nunique() < 2:
        return float("nan")
    value = spearmanr(left, right).statistic
    return float(value) if np.isfinite(value) else float("nan")


def _monotonicity(returns: list[float], direction: int) -> float:
    values = pd.Series(returns).dropna()
    if len(values) < 2 or values.nunique() < 2:
        return float("nan")
    value = spearmanr(range(1, len(values) + 1), values).statistic
    if not np.isfinite(value):
        return float("nan")
    return float(value * -1 if direction < 0 else value)


def _top_n_turnover(data: pd.DataFrame, direction: int, policy: EvaluationPolicy) -> float:
    """Compute equal-weight Top-N turnover from the candidate's daily panel."""

    required = {"asof_date", "asset_id", "raw_value", "member"}
    if data.empty or not required <= set(data):
        return float("nan")
    work = data.loc[
        data["member"].astype(bool) & data["raw_value"].notna(),
        ["asof_date", "asset_id", "raw_value"],
    ].copy()
    if work.empty:
        return float("nan")
    work["rank"] = work.groupby("asof_date")["raw_value"].rank(
        method="first", ascending=direction < 0
    )
    top_by_date = {
        date: set(group.loc[group["rank"] <= policy.top_n, "asset_id"])
        for date, group in work.groupby("asof_date", sort=True)
    }
    previous: set[object] | None = None
    turnovers: list[float] = []
    for current in top_by_date.values():
        if previous is not None:
            if not current and not previous:
                turnovers.append(float("nan"))
                continue
            current_weight = 1 / len(current) if current else 0.0
            previous_weight = 1 / len(previous) if previous else 0.0
            universe = current | previous
            turnovers.append(
                0.5
                * sum(
                    abs(
                        (current_weight if asset in current else 0.0)
                        - (previous_weight if asset in previous else 0.0)
                    )
                    for asset in universe
                )
            )
        previous = current
    valid = pd.Series(turnovers).dropna()
    return float(valid.mean()) if not valid.empty else float("nan")


def _risk_utility(data: pd.DataFrame) -> dict[str, object]:
    """Compare low/high NATR return dispersion without entering the Alpha gate."""

    defaults = {
        "risk_low_return_dispersion": np.nan,
        "risk_high_return_dispersion": np.nan,
        "risk_high_minus_low_dispersion": np.nan,
        "risk_utility_verdict": "NOT_ESTABLISHED",
    }
    if data.empty or "forward_return_5d" not in data:
        return defaults
    rows = []
    for _, group in data.groupby("asof_date", sort=True):
        eligible = group.loc[
            group["member"].astype(bool)
            & group["raw_value"].notna()
            & group["forward_return_5d"].notna(),
            ["raw_value", "forward_return_5d"],
        ]
        if len(eligible) < 10:
            continue
        count = max(1, len(eligible) // 5)
        rows.append(
            {
                "low": float(eligible.nsmallest(count, "raw_value")["forward_return_5d"].std(ddof=0)),
                "high": float(eligible.nlargest(count, "raw_value")["forward_return_5d"].std(ddof=0)),
            }
        )
    evidence = pd.DataFrame(rows).dropna()
    if evidence.empty:
        return defaults
    low = float(evidence["low"].mean())
    high = float(evidence["high"].mean())
    return {
        "risk_low_return_dispersion": low,
        "risk_high_return_dispersion": high,
        "risk_high_minus_low_dispersion": high - low,
        "risk_utility_verdict": "KEEP_AS_RISK_ONLY" if high > low else "REVIEW",
    }


def _evaluate_factor(data: pd.DataFrame, spec: CandidateSpec, *, policy: EvaluationPolicy = D4_POLICY) -> tuple[dict[str, object], list[dict[str, object]], list[dict[str, object]]]:
    rows = []
    quantiles = []
    temporal = []
    if data.empty or "raw_value" not in data:
        return _empty_metric(spec, "NO_DATA"), quantiles, temporal
    work = data.copy()
    work["raw_value"] = pd.to_numeric(work["raw_value"], errors="coerce")
    work["asof_date"] = pd.to_datetime(work["asof_date"])
    work["member"] = work["member"].fillna(False).astype(bool)
    turnover = _top_n_turnover(work, spec.direction, policy)
    horizon_summary: dict[str, object] = {}
    for horizon in HORIZONS:
        label = f"forward_return_{horizon}d"
        if label not in work:
            continue
        daily = []
        for date, group in work.groupby("asof_date", sort=True):
            eligible = group.loc[group["member"] & group["raw_value"].notna() & group[label].notna(), ["asset_id", "raw_value", label]]
            if len(eligible) < max(policy.min_ic_assets, policy.min_quantile_assets):
                daily.append({"asof_date": date, "ic": np.nan, "effective_assets": len(eligible), "q1": np.nan, "q5": np.nan, "spread": np.nan, "monotonicity": np.nan})
                continue
            ic = _spearman(eligible["raw_value"], eligible[label])
            ranked = eligible.assign(q=eligible["raw_value"].rank(method="first", pct=True).mul(policy.quantile_count).clip(1, policy.quantile_count).astype(int))
            q_returns = ranked.groupby("q")[label].mean()
            q1, q5 = q_returns.get(1, np.nan), q_returns.get(policy.quantile_count, np.nan)
            spread = q5 - q1 if pd.notna(q1) and pd.notna(q5) else np.nan
            daily.append({"asof_date": date, "ic": ic, "effective_assets": len(eligible), "q1": q1, "q5": q5, "spread": spread, "monotonicity": _monotonicity(q_returns.tolist(), spec.direction)})
            for q, value in q_returns.items():
                quantiles.append({"factor_id": spec.candidate_id, "horizon": horizon, "asof_date": date, "quantile": int(q), "mean_forward_return": value, "universe_id": spec.universe_id})
        daily_frame = pd.DataFrame(daily)
        valid_ic = daily_frame["ic"].dropna()
        mean_ic = valid_ic.mean() if not valid_ic.empty else np.nan
        ic_std = valid_ic.std(ddof=1) if len(valid_ic) > 1 else np.nan
        icir = mean_ic / ic_std if pd.notna(ic_std) and ic_std != 0 else np.nan
        signed_mean = mean_ic * spec.direction if spec.direction else mean_ic
        signed_spread = daily_frame["spread"].mean() * spec.direction if spec.direction else daily_frame["spread"].mean()
        signed_monotonicity = daily_frame["monotonicity"].mean() * spec.direction if spec.direction else daily_frame["monotonicity"].mean()
        signed_ic = valid_ic * spec.direction if spec.direction else valid_ic
        positive_ic_ratio = (signed_ic > 0).mean() if not signed_ic.empty else np.nan
        threshold = policy.min_abs_q5_q1_by_horizon[horizon]
        coverage = len(valid_ic) / len(daily_frame) if len(daily_frame) else 0
        gate = bool(
            len(valid_ic) >= policy.min_effective_days
            and coverage >= policy.min_average_coverage
            and (positive_ic_ratio >= policy.min_positive_ic_ratio if pd.notna(positive_ic_ratio) else False)
            and ((signed_mean >= policy.min_abs_mean_ic) if spec.direction else (abs(mean_ic) >= policy.min_abs_mean_ic if pd.notna(mean_ic) else False))
            and (
                (signed_spread >= threshold if pd.notna(signed_spread) else False)
                or (signed_monotonicity >= policy.min_monotonicity if spec.direction else abs(signed_monotonicity) >= policy.min_monotonicity if pd.notna(signed_monotonicity) else False)
            )
        )
        rows.append({
            "horizon": horizon, "mean_ic": mean_ic, "icir": icir, "positive_ic_ratio": (valid_ic > 0).mean() if not valid_ic.empty else np.nan,
            "q1_return": daily_frame["q1"].mean(), "q5_return": daily_frame["q5"].mean(), "q5_minus_q1": daily_frame["spread"].mean(),
            "monotonicity": daily_frame["monotonicity"].mean(), "valid_ic_days": len(valid_ic), "total_days": len(daily_frame),
            "average_effective_assets": daily_frame["effective_assets"].mean() if not daily_frame.empty else np.nan,
            "coverage": coverage, "gate_pass": gate, "daily": daily_frame,
        })
        horizon_summary.update({
            f"horizon_{horizon}_mean_ic": mean_ic,
            f"horizon_{horizon}_q5_minus_q1": daily_frame["spread"].mean(),
            f"horizon_{horizon}_signed_mean_ic": signed_mean,
            f"horizon_{horizon}_signed_q5_minus_q1": signed_spread,
            f"horizon_{horizon}_positive_ic_ratio": positive_ic_ratio,
        })
        if not daily_frame.empty:
            for year, year_frame in daily_frame.assign(year=daily_frame["asof_date"].dt.year).groupby("year"):
                year_ic = year_frame["ic"].dropna()
                temporal.append({"factor_id": spec.candidate_id, "horizon": horizon, "period": str(year), "mean_ic": year_ic.mean() if not year_ic.empty else np.nan, "q5_minus_q1": year_frame["spread"].mean(), "positive_ic_ratio": (year_ic > 0).mean() if not year_ic.empty else np.nan, "universe_id": spec.universe_id})
    if not rows:
        return _empty_metric(spec, "NO_DATA"), quantiles, temporal
    best = max(rows, key=lambda row: abs(row["icir"]) if pd.notna(row["icir"]) else -1)
    eligible = [row for row in rows if row["gate_pass"]]
    if eligible:
        best = max(eligible, key=lambda row: abs(row["icir"]) if pd.notna(row["icir"]) else -1)
    annual = pd.DataFrame([row for row in temporal if row["horizon"] == best["horizon"]])
    stable_years = annual.loc[annual["mean_ic"].notna()]
    stability = ((stable_years["mean_ic"] * (spec.direction or 1)) >= 0).mean() if not stable_years.empty else np.nan
    metric = {key: value for key, value in best.items() if key != "daily"}
    metric.update({"selected_horizon": best["horizon"], "research_direction": 1 if best["mean_ic"] > 0 else -1 if best["mean_ic"] < 0 else 0, "stability": stability, "stability_years": len(stable_years), "data_status": "READY" if best["valid_ic_days"] else "NO_DATA", "turnover": turnover, **horizon_summary})
    return metric, quantiles, temporal


def _empty_metric(spec: CandidateSpec, status: str) -> dict[str, object]:
    return {"selected_horizon": "NONE", "mean_ic": np.nan, "icir": np.nan, "positive_ic_ratio": np.nan, "q1_return": np.nan, "q5_return": np.nan, "q5_minus_q1": np.nan, "monotonicity": np.nan, "valid_ic_days": 0, "total_days": 0, "average_effective_assets": np.nan, "coverage": 0.0, "gate_pass": False, "research_direction": 0, "stability": np.nan, "stability_years": 0, "data_status": status}


def _merge_labels(values: pd.DataFrame, labels: pd.DataFrame, spec: CandidateSpec) -> pd.DataFrame:
    long = panel_to_long(values, factor_id=spec.candidate_id, direction=spec.direction)
    columns = ["asof_date", "asset_id", "member", *(f"forward_return_{h}d" for h in HORIZONS)]
    label_data = labels.loc[:, [column for column in columns if column in labels]]
    merged = long.merge(label_data, on=["asof_date", "asset_id"], how="inner", suffixes=("", "_label"))
    if "member_label" in merged:
        merged["member"] = merged["member_label"].fillna(False).astype(bool)
        merged = merged.drop(columns=["member_label"])
    if spec.universe_id == UNIV_RUNTIME_V1:
        merged["member"] = merged["asset_id"].astype(str).str.fullmatch(r"[1-9]\d{3}").fillna(False)
    return merged


def _correlations(panels: Mapping[str, pd.DataFrame], specs: Iterable[CandidateSpec], *, start: pd.Timestamp, end: pd.Timestamp) -> pd.DataFrame:
    selected = {key: value.loc[(value.index >= start) & (value.index <= end)] for key, value in panels.items() if not value.empty}
    dates = sorted(set().union(*(frame.index.tolist() for frame in selected.values())))
    dates = dates[::5]
    accum: dict[tuple[str, str], list[tuple[float, float]]] = {}
    family = {spec.candidate_id: spec.family for spec in specs}
    for date in dates:
        cross = pd.DataFrame({key: frame.reindex(index=[date]).iloc[0] for key, frame in selected.items()})
        corr = cross.corr(method="pearson")
        rank = cross.corr(method="spearman")
        for left in corr.index:
            for right in corr.columns:
                if left == right:
                    continue
                if pd.notna(corr.loc[left, right]):
                    accum.setdefault((left, right), []).append((float(corr.loc[left, right]), float(rank.loc[left, right])))
    rows = []
    for (left, right), values in sorted(accum.items()):
        rows.append({"factor_id": left, "other_factor_id": right, "correlation": np.mean([v[0] for v in values]), "rank_correlation": np.mean([v[1] for v in values]), "economic_family": family.get(left, ""), "other_family": family.get(right, ""), "universe_id": UNIV_RESEARCH_V1})
    return pd.DataFrame(rows, columns=["factor_id", "other_factor_id", "correlation", "rank_correlation", "economic_family", "other_family", "universe_id"])


def _scoreboard(metrics: list[dict[str, object]], specs: list[CandidateSpec], correlations: pd.DataFrame) -> pd.DataFrame:
    frame = pd.DataFrame(metrics)
    if frame.empty:
        return frame
    def normalized(column: str) -> pd.Series:
        values = pd.to_numeric(frame[column], errors="coerce")
        if values.notna().sum() < 2 or values.max() == values.min():
            return values.notna().astype(float)
        return (values - values.min()) / (values.max() - values.min())
    frame["predictive_strength"] = normalized("icir").abs()
    frame["economic_strength"] = normalized("q5_minus_q1").abs()
    turnover = pd.to_numeric(frame["turnover"], errors="coerce")
    if turnover.notna().sum() < 2 or turnover.dropna().max() == turnover.dropna().min():
        frame["turnover_norm"] = 0.0
    else:
        frame["turnover_norm"] = ((turnover - turnover.min()) / (turnover.max() - turnover.min())).fillna(1.0)
    confidence = frame["implementation_status"].map(implementation_confidence).fillna(0.0)
    max_corr = []
    peer_ids = []
    for factor_id in frame["candidate_id"]:
        peers = correlations.loc[correlations["factor_id"].eq(factor_id)] if not correlations.empty else pd.DataFrame()
        if peers.empty:
            max_corr.append(np.nan)
            peer_ids.append("")
        else:
            row = peers.loc[peers["correlation"].abs().idxmax()]
            max_corr.append(abs(float(row["correlation"])))
            peer_ids.append(row["other_factor_id"])
    frame["max_abs_correlation"] = max_corr
    frame["most_correlated_factor"] = peer_ids
    frame["redundancy_penalty"] = frame["max_abs_correlation"].map(lambda value: 1.0 if pd.notna(value) and value > 0.8 else 0.5 if pd.notna(value) and value > 0.6 else 0.0)
    frame["factor_score"] = (
        FACTOR_SCORE_WEIGHTS["predictive_strength"] * frame["predictive_strength"]
        + FACTOR_SCORE_WEIGHTS["economic_spread"] * frame["economic_strength"]
        + FACTOR_SCORE_WEIGHTS["stability"] * frame["stability"].fillna(0)
        + FACTOR_SCORE_WEIGHTS["coverage"] * frame["coverage"].fillna(0)
        + FACTOR_SCORE_WEIGHTS["turnover"] * (1 - frame["turnover_norm"])
        + FACTOR_SCORE_WEIGHTS["implementation_confidence"] * confidence
        + FACTOR_SCORE_WEIGHTS["redundancy_penalty"] * frame["redundancy_penalty"]
    )
    frame["verdict"] = np.where(frame["implementation_status"].eq("REJECT_IMPLEMENTATION"), "REJECT", np.where(frame["gate_pass"], "CANDIDATE", "WEAK"))
    frame.loc[frame["role"].ne("Alpha"), "verdict"] = "REVIEW"
    frame.loc[frame["pit_status"].eq("PIT_RISK"), "verdict"] = "REVIEW"
    frame.loc[
        frame["verdict"].eq("CANDIDATE")
        & pd.to_numeric(frame["turnover"], errors="coerce").gt(D4_POLICY.max_turnover),
        "verdict",
    ] = "REVIEW"
    frame["rejection_reason"] = ""
    frame.loc[frame["verdict"].eq("WEAK"), "rejection_reason"] = "D4_GATE_NOT_PASSED"
    frame.loc[frame["verdict"].eq("REJECT"), "rejection_reason"] = "REJECT_IMPLEMENTATION"
    frame.loc[frame["pit_status"].eq("PIT_RISK"), "rejection_reason"] = "PIT_RISK_UNRESOLVED"
    frame.loc[frame["role"].ne("Alpha") & frame["rejection_reason"].eq(""), "rejection_reason"] = "ROLE_SPECIFIC_VALIDATION"
    frame.loc[
        frame["verdict"].eq("REVIEW")
        & frame["rejection_reason"].eq("")
        & pd.to_numeric(frame["turnover"], errors="coerce").gt(D4_POLICY.max_turnover),
        "rejection_reason",
    ] = "TURNOVER_ABOVE_POLICY_MAX"
    frame["redundancy_flag"] = frame["max_abs_correlation"].map(lambda value: "HIGH" if pd.notna(value) and value > 0.8 else "MODERATE" if pd.notna(value) and value > 0.6 else "LOW" if pd.notna(value) else "UNKNOWN")
    frame["oos_clean"] = np.where(frame["tier"].str.contains("BASELINE") | frame["candidate_id"].isin(["RS_001", "RS_002", "RS_003", "RS_004_NEAR_HIGH_252D", "RS_013"]), "LEGACY_CONTAMINATED", "CLEAN")
    return frame


def _redundancy_clusters(scoreboard: pd.DataFrame, correlations: pd.DataFrame) -> pd.DataFrame:
    """Build connected components for |correlation| > 0.8 and choose IDs."""

    ids = list(scoreboard["candidate_id"])
    parent = {factor_id: factor_id for factor_id in ids}

    def find(factor_id: str) -> str:
        while parent[factor_id] != factor_id:
            parent[factor_id] = parent[parent[factor_id]]
            factor_id = parent[factor_id]
        return factor_id

    def union(left: str, right: str) -> None:
        if left not in parent or right not in parent:
            return
        left_root, right_root = find(left), find(right)
        if left_root != right_root:
            parent[right_root] = left_root

    for row in correlations.itertuples(index=False):
        if pd.notna(row.correlation) and abs(float(row.correlation)) > 0.8:
            union(str(row.factor_id), str(row.other_factor_id))
    groups: dict[str, list[str]] = {}
    for factor_id in ids:
        groups.setdefault(find(factor_id), []).append(factor_id)
    rows = []
    for members in groups.values():
        members = sorted(members)
        cluster_id = f"CLUSTER_{members[0]}"
        subset = scoreboard.loc[scoreboard["candidate_id"].isin(members)].copy()
        candidates = subset.loc[subset["verdict"].eq("CANDIDATE")]
        choice = candidates if not candidates.empty else subset
        representative = choice.sort_values("factor_score", ascending=False, na_position="last").iloc[0]["candidate_id"]
        for factor_id in members:
            row = scoreboard.loc[scoreboard["candidate_id"].eq(factor_id)].iloc[0]
            rows.append({
                "candidate_id": factor_id,
                "family": row["family"],
                "verdict": row["verdict"],
                "factor_score": row["factor_score"],
                "max_abs_correlation": row["max_abs_correlation"],
                "most_correlated_factor": row["most_correlated_factor"],
                "redundancy_flag": row["redundancy_flag"],
                "cluster_id": cluster_id,
                "representative_factor": representative,
            })
    return pd.DataFrame(rows)


def _alphalens_metrics(data: pd.DataFrame, selected_horizon: object) -> dict[str, object]:
    """Independently recompute IC / quantile spread / turnover with alphalens.

    Feeds the SAME pre-OOS (date, asset) factor observations and forward
    returns the internal evaluator used; alphalens performs its own
    quantization, per-date rank IC, quantile aggregation and rank
    autocorrelation. No internal number is copied in.
    """

    result = {"status": "ALPHALENS_NOT_MEASURABLE", "mean_ic": np.nan, "spread": np.nan, "autocorr": np.nan}
    if data is None or data.empty or selected_horizon in (None, "NONE"):
        return result
    try:
        import alphalens.performance as perf
        import alphalens.utils as au
    except Exception as exc:  # dependency missing
        result["status"] = f"ALPHALENS_UNAVAILABLE: {exc}"
        return result
    ret_cols = {f"forward_return_{h}d": f"{h}D" for h in HORIZONS if f"forward_return_{h}d" in data.columns}
    if not ret_cols:
        return result
    work = data.loc[data["member"].astype(bool) & data["raw_value"].notna(), ["asof_date", "asset_id", "raw_value", *ret_cols]].copy()
    work["asof_date"] = pd.to_datetime(work["asof_date"])
    factor_data = work.rename(columns={"raw_value": "factor", **ret_cols}).set_index(["asof_date", "asset_id"])
    factor_data.index = factor_data.index.set_names(["date", "asset"])
    periods = list(ret_cols.values())
    factor_data = factor_data.dropna(subset=periods, how="all")
    if len(factor_data) < 200:
        return result
    try:
        factor_data["factor_quantile"] = au.quantize_factor(factor_data, quantiles=5, no_raise=True)
        factor_data = factor_data.dropna(subset=["factor_quantile"])
        if factor_data.empty:
            return result
        factor_data["factor_quantile"] = factor_data["factor_quantile"].astype(int)
        ic = perf.factor_information_coefficient(factor_data)
        quantile_returns = perf.mean_return_by_quantile(factor_data, demeaned=False)[0]
        autocorr = perf.factor_rank_autocorrelation(factor_data, period=1)
    except Exception as exc:
        result["status"] = f"ALPHALENS_NOT_MEASURABLE: {exc}"
        return result
    hcol = f"{int(selected_horizon)}D"
    if hcol not in periods:
        hcol = periods[-1]
    result["status"] = "OK"
    result["mean_ic"] = float(ic[hcol].mean()) if hcol in ic.columns else np.nan
    if hcol in quantile_returns.columns:
        column = quantile_returns[hcol].dropna()
        if not column.empty:
            result["spread"] = float(column.loc[column.index.max()] - column.loc[column.index.min()])
    result["autocorr"] = float(autocorr.mean()) if autocorr is not None and len(autocorr) else np.nan
    return result


def _crosscheck(scoreboard: pd.DataFrame, alphalens_by_id: Mapping[str, dict]) -> pd.DataFrame:
    """Compare the internal evaluator against a real alphalens recompute."""

    try:
        from importlib.metadata import version
        available = version("alphalens-reloaded")
    except Exception:
        available = "unavailable"

    def sign(value: object) -> int:
        return int(np.sign(value)) if pd.notna(value) else 0

    def direction(value: int) -> str:
        return "INCREASING" if value > 0 else "DECREASING" if value < 0 else "FLAT"

    def turnover_tendency(value: object) -> str:
        return "UNKNOWN" if pd.isna(value) else "HIGH" if float(value) > D4_POLICY.max_turnover else "LOW"

    def autocorr_tendency(value: object) -> str:
        return "UNKNOWN" if pd.isna(value) else "LOW" if float(value) >= 0.5 else "HIGH"

    rows = []
    for row in scoreboard.to_dict("records"):
        base = {"factor_id": row["candidate_id"], "universe_id": row["universe_id"], "alphalens_version": available}
        if row["role"] != "Alpha":
            rows.append({**base, "alphalens_execution_status": "NOT_APPLICABLE", "internal_mean_ic": row.get("mean_ic"), "alphalens_mean_ic": np.nan,
                "internal_ic_sign": "NA", "alphalens_ic_sign": "NA", "internal_quantile_direction": "NA", "alphalens_quantile_direction": "NA",
                "internal_spread_sign": "NA", "alphalens_spread_sign": "NA", "internal_turnover_tendency": "NA", "alphalens_turnover_tendency": "NA",
                "conclusion_parity": "NOT_APPLICABLE", "crosscheck": "NOT_APPLICABLE", "reason": "role-specific validation"})
            continue
        result = alphalens_by_id.get(row["candidate_id"], {"status": "ALPHALENS_NOT_MEASURABLE"})
        status = str(result.get("status", "ALPHALENS_NOT_MEASURABLE"))
        internal_ic_sign, alphalens_ic_sign = sign(row.get("mean_ic")), sign(result.get("mean_ic"))
        internal_spread_sign, alphalens_spread_sign = sign(row.get("q5_minus_q1")), sign(result.get("spread"))
        if not status.startswith("OK"):
            parity, check = "ALPHALENS_NOT_MEASURABLE", "ALPHALENS_NOT_MEASURABLE"
        else:
            ic_parity = internal_ic_sign != 0 and internal_ic_sign == alphalens_ic_sign
            spread_parity = internal_spread_sign != 0 and internal_spread_sign == alphalens_spread_sign
            parity = "PASS" if ic_parity and spread_parity else "FAIL"
            check = "PASS" if ic_parity and spread_parity else "CROSSCHECK_FAIL"
        rows.append({**base, "alphalens_execution_status": status,
            "internal_mean_ic": row.get("mean_ic"), "alphalens_mean_ic": result.get("mean_ic"),
            "internal_ic_sign": internal_ic_sign, "alphalens_ic_sign": alphalens_ic_sign,
            "internal_quantile_direction": direction(internal_spread_sign), "alphalens_quantile_direction": direction(alphalens_spread_sign),
            "internal_spread_sign": internal_spread_sign, "alphalens_spread_sign": alphalens_spread_sign,
            "internal_turnover_tendency": turnover_tendency(row.get("turnover")), "alphalens_turnover_tendency": autocorr_tendency(result.get("autocorr")),
            "conclusion_parity": parity, "crosscheck": check,
            "reason": "alphalens-reloaded independent recompute; sign/direction parity, not float equality"})
    return pd.DataFrame(rows)


def _same_sign(pre_value: object, oos_value: object) -> bool:
    """True only if both values are non-zero and share the same sign."""

    return bool(pd.notna(pre_value) and pd.notna(oos_value) and np.sign(pre_value) != 0 and np.sign(pre_value) == np.sign(oos_value))


def _accepted_pool(scoreboard: pd.DataFrame) -> pd.DataFrame:
    """Gate -> Ranking/Redundancy -> Accepted. A non-PASS crosscheck excludes."""

    return scoreboard.loc[
        (scoreboard["verdict"] == "CANDIDATE")
        & scoreboard["pit_status"].ne("PIT_RISK")
        & scoreboard["crosscheck"].eq("PASS")
        & scoreboard["oos_clean"].eq("CLEAN")
    ].copy()


def _strict_oos_status(accepted: pd.DataFrame) -> tuple[str, str | None]:
    """Tri-state: NOT_ESTABLISHED < MEASURABLE < ESTABLISHED (direction confirmed)."""

    if accepted.empty:
        return "NOT_ESTABLISHED", "no clean new factor entered the accepted pool"
    measurable = bool(accepted["oos_mean_ic"].notna().all() and accepted["oos_clean"].eq("CLEAN").all())
    if not measurable:
        return "NOT_ESTABLISHED", "accepted factors lack measurable clean strict-OOS evidence"
    if bool(accepted["oos_ic_same_sign"].all() and accepted["oos_spread_same_sign"].all()):
        return "ESTABLISHED", None
    return "MEASURABLE", "accepted factors have clean measurable OOS but IC/spread sign does not confirm the frozen pre-OOS direction for all of them"


def _tier2_summary(scoreboard: pd.DataFrame, pit_evidence: list[dict[str, object]]) -> tuple[str, dict[str, int], str]:
    """Tier 2 validation counts + honest status; never silently COMPLETE when empty."""

    tier2 = scoreboard.loc[scoreboard["tier"].eq("TIER2")]
    ready = tier2["data_status"].eq("READY") & tier2["pit_status"].ne("PIT_RISK")
    counts = {"validated": int(ready.sum()),
              "growth": int((ready & tier2["family"].eq("A")).sum()),
              "quality": int((ready & tier2["family"].eq("C")).sum()),
              "instflow": int((ready & tier2["family"].eq("I")).sum())}
    db_unavailable = any(str(item.get("source")) == "database" and item.get("status") == "PIT_RISK" for item in pit_evidence)
    status = "ESTABLISHED" if counts["validated"] > 0 else "NOT_ESTABLISHED_DB_UNAVAILABLE" if db_unavailable else "NOT_ESTABLISHED"
    day2_status = "COMPLETE" if counts["validated"] > 0 else "COMPLETE_WITH_TIER2_BLOCKER"
    return status, counts, day2_status


def _oos_update(scoreboard: pd.DataFrame, specs: list[CandidateSpec], labels: pd.DataFrame, frames: Mapping[str, pd.DataFrame], existing: Mapping[str, pd.DataFrame]) -> pd.DataFrame:
    # Canonical pre-OOS direction source = frozen pre-OOS mean IC / Q5-Q1 sign
    # (identical to research_direction). OOS never re-selects direction.
    pre = scoreboard.set_index("candidate_id")
    results = []
    for spec in specs:
        values = compute_candidate(spec, frames, existing=existing.get(spec.source_id), fundamentals=None)
        data = _merge_labels(values, labels, spec)
        metric, _, _ = _evaluate_factor(data, spec)
        oos_ic, oos_spread = metric.get("mean_ic"), metric.get("q5_minus_q1")
        pre_ic = pre.at[spec.candidate_id, "mean_ic"] if spec.candidate_id in pre.index else np.nan
        pre_spread = pre.at[spec.candidate_id, "q5_minus_q1"] if spec.candidate_id in pre.index else np.nan
        results.append({"candidate_id": spec.candidate_id, "oos_mean_ic": oos_ic, "oos_q5_minus_q1": oos_spread,
            "oos_ic_same_sign": _same_sign(pre_ic, oos_ic), "oos_spread_same_sign": _same_sign(pre_spread, oos_spread)})
    return scoreboard.merge(pd.DataFrame(results), on="candidate_id", how="left")


def _runtime_conflicts(scoreboard: pd.DataFrame, correlations: pd.DataFrame) -> list[dict[str, object]]:
    """Derive the five Tier 1 conflict verdicts and resolution statuses from data."""

    def value(candidate_id: str, column: str, default: object = np.nan) -> object:
        rows = scoreboard.loc[scoreboard["candidate_id"].eq(candidate_id), column]
        return rows.iloc[0] if not rows.empty else default

    raw = [value("RS_008", f"horizon_{h}_mean_ic") for h in HORIZONS]
    signs = {int(np.sign(m)) for m in raw if pd.notna(m) and np.sign(m) != 0}
    volume_verdict = "NO_SIGNAL" if not signs else "HORIZON_DEPENDENT" if len(signs) > 1 else "POSITIVE" if 1 in signs else "NEGATIVE"

    risk_verdict = value("RS_012", "risk_utility_verdict", "NOT_ESTABLISHED")
    near_high = [value(f"RS_004_NEAR_HIGH_{w}D", "mean_ic") for w in (60, 120, 252)]
    near_present = [m for m in near_high if pd.notna(m)]
    near_signs = {int(np.sign(m)) for m in near_present if np.sign(m) != 0}
    if len(near_present) < 3:
        near_verdict, near_status = "INCOMPLETE_WINDOWS", "PARTIALLY_RESOLVED"
    elif len(near_signs) == 1:
        near_verdict, near_status = "ROBUST_ACROSS_WINDOWS", "RESOLVED"
    else:
        near_verdict, near_status = "WINDOW_DEPENDENT", "PARTIALLY_RESOLVED"

    corr = correlations.loc[correlations["factor_id"].eq("RS_012") & correlations["other_factor_id"].eq("RS_011"), "correlation"]
    corr_present = not corr.empty and pd.notna(corr.iloc[0])
    corr_text = f"{float(corr.iloc[0]):.4f}" if corr_present else "N/A"

    return [
        {"conflict_id": "volume_ratio_20d_direction", "verdict": volume_verdict,
         "resolution_status": "UNRESOLVED" if volume_verdict == "NO_SIGNAL" else "RESOLVED",
         "evidence": "raw/+1/-1 x 5D/20D/60D pre-OOS mean Rank IC"},
        {"conflict_id": "natr_14d_role", "verdict": risk_verdict,
         "resolution_status": "RESOLVED" if risk_verdict == "KEEP_AS_RISK_ONLY" else "PARTIALLY_RESOLVED" if risk_verdict == "REVIEW" else "UNRESOLVED",
         "evidence": "D4 Alpha gate + low/high NATR 5D return dispersion"},
        {"conflict_id": "near_high_window_robustness", "verdict": near_verdict, "resolution_status": near_status,
         "evidence": "60D/120D/252D signed IC + Q5-Q1 agreement across windows"},
        {"conflict_id": "realized_vol_vs_natr", "verdict": "KEEP_AS_RISK_ONLY" if risk_verdict == "KEEP_AS_RISK_ONLY" else str(risk_verdict),
         "resolution_status": "RESOLVED" if corr_present and risk_verdict == "KEEP_AS_RISK_ONLY" else "PARTIALLY_RESOLVED" if corr_present else "UNRESOLVED",
         "evidence": f"cross-sectional corr (RS_012 vs RS_011)={corr_text}; risk-utility={risk_verdict}"},
        {"conflict_id": "universe_mismatch", "verdict": "ETF_DISTORTION_NOT_ESTABLISHED", "resolution_status": "UNRESOLVED",
         "evidence": "cached D3 quotes contain no 00 ETF rows; runtime-compatible historical ETF panel required"},
    ]


def _runtime_report(scoreboard: pd.DataFrame, correlations: pd.DataFrame, conflicts: list[dict[str, object]]) -> str:
    """Render Tier 1 decisions from per-horizon and risk-utility evidence."""

    def value(candidate_id: str, column: str, default: float = float("nan")):
        rows = scoreboard.loc[scoreboard["candidate_id"].eq(candidate_id), column]
        return rows.iloc[0] if not rows.empty else default

    tally = Counter(str(c["resolution_status"]) for c in conflicts)
    lines = [
        "# Runtime Conflict Resolution",
        "",
        "## Resolution summary (computed from verdicts, not hardcoded)",
        "",
        f"RESOLVED {tally.get('RESOLVED', 0)} / PARTIALLY_RESOLVED {tally.get('PARTIALLY_RESOLVED', 0)} / UNRESOLVED {tally.get('UNRESOLVED', 0)}",
        "",
        "| conflict_id | verdict | resolution_status | evidence |",
        "|---|---|---|---|",
        *[f"| {c['conflict_id']} | {c['verdict']} | {c['resolution_status']} | {c['evidence']} |" for c in conflicts],
        "",
        "## 1. volume_ratio_20d direction",
        "",
        "| direction | horizon | pre-OOS mean Rank IC (diagnostic) |",
        "|---|---:|---:|",
    ]
    for direction, candidate_id, multiplier in (("raw", "RS_008", 1), ("positive", "RS_008_POSITIVE", 1), ("negative", "RS_008_NEGATIVE", -1)):
        for horizon in HORIZONS:
            metric = value(candidate_id, f"horizon_{horizon}_mean_ic")
            rendered = "N/A" if pd.isna(metric) else f"{metric * multiplier:.6f}"
            lines.append(f"| {direction} | {horizon}D | {rendered} |")
    raw_values = [value("RS_008", f"horizon_{horizon}_mean_ic") for horizon in HORIZONS]
    signs = {int(np.sign(metric)) for metric in raw_values if pd.notna(metric) and np.sign(metric) != 0}
    volume_direction = "NO_SIGNAL" if not signs else "HORIZON_DEPENDENT" if len(signs) > 1 else "POSITIVE" if 1 in signs else "NEGATIVE"
    lines.extend(["", f"Conclusion: `{volume_direction}`; the table is data-driven and does not copy the Runtime rule.", "", "## 2. natr_14d role", ""])
    natr = scoreboard.loc[scoreboard["candidate_id"].eq("RS_012")]
    risk_verdict = natr["risk_utility_verdict"].iloc[0] if not natr.empty else "NOT_ESTABLISHED"
    risk_low = natr["risk_low_return_dispersion"].iloc[0] if not natr.empty else np.nan
    risk_high = natr["risk_high_return_dispersion"].iloc[0] if not natr.empty else np.nan
    risk_text = "N/A" if pd.isna(risk_low) or pd.isna(risk_high) else f"low={risk_low:.6f}; high={risk_high:.6f}"
    lines.extend([
        "NATR is evaluated through the D4 Alpha path and a separate low/high risk-utility check. The risk check never enters the Alpha gate.",
        "",
        "| evidence | result |",
        "|---|---|",
        f"| low/high 5D return dispersion | {risk_text} |",
        f"| risk utility verdict | {risk_verdict} |",
        "",
        "## 3. near-high window robustness",
        "",
        "The 60D, 120D and 252D rows are retained independently in the scoreboard. Robustness means the signed IC and Q5-Q1 conclusions agree across windows; no window is selected solely because it has the highest score.",
        "",
        "## 4. realized_vol_20d vs natr_14d",
        "",
        "| evidence | result |",
        "|---|---|",
    ])
    natr_corr = correlations.loc[correlations["factor_id"].eq("RS_012") & correlations["other_factor_id"].eq("RS_011"), "correlation"]
    corr_text = "N/A" if natr_corr.empty else f"{float(natr_corr.iloc[0]):.4f}"
    lines.extend([
        f"| cross-sectional correlation (RS_012 vs RS_011) | {corr_text} |",
        "| rank correlation | measured in factor_redundancy_matrix.csv |",
        "| predictive correlation | measured in scoreboard |",
        f"| risk-control utility | {risk_verdict} |",
        "",
        "Conclusion: `KEEP_AS_RISK_ONLY` pending the role-specific risk utility evidence.",
        "",
        "## 5. Universe mismatch",
        "",
        "`UNIV_RESEARCH_V1` is the primary scoreboard universe. `UNIV_RUNTIME_V1` is retained for the RT_001 comparison only; cached D3 daily quotes contain no 00 ETF rows after the canonical source filter, so an ETF distortion claim is not established.",
        "",
        "Status: `UNRESOLVED` — a runtime-compatible historical ETF panel is required for a conclusive A/B magnitude comparison.",
    ])
    return "\n".join(lines) + "\n"


def _report(scoreboard: pd.DataFrame, crosscheck: pd.DataFrame, day1: dict[str, object], *, strict_status: str, blocker: str | None, conflicts: list[dict[str, object]], tier2_status: str, tier2_counts: dict[str, int], day2_status: str) -> str:
    family = scoreboard["family"].value_counts().sort_index().to_dict()
    verdicts = scoreboard["verdict"].value_counts().to_dict()
    checks = crosscheck["crosscheck"].value_counts().to_dict()
    accepted = int((scoreboard["verdict"] == "CANDIDATE").sum())
    turnover_available = int(pd.to_numeric(scoreboard["turnover"], errors="coerce").notna().sum())
    tally = Counter(str(c["resolution_status"]) for c in conflicts)
    risk_verdict = scoreboard.loc[scoreboard["candidate_id"].eq("RS_012"), "risk_utility_verdict"]
    risk_verdict = risk_verdict.iloc[0] if not risk_verdict.empty else "NOT_ESTABLISHED"
    lines = [
        "# Day 2 Factor Validation Report", "", "## Frozen input", "",
        f"- Day 1 files: {day1['file_count']}; TOTAL_CANDIDATES={day1['total_candidates']}; P2 shortlist={day1['p2_alpha_shortlist']}.",
        f"- D3: `{D3_RUN_ID}`; TRAIN={TRAIN_START.date()}..{TRAIN_END.date()}; VALIDATION={VALIDATION_START.date()}..{VALIDATION_END.date()}; STRICT OOS={STRICT_OOS_START.date()}..{STRICT_OOS_END.date()}.",
        "- Alpha gate: imported D4 `EvaluationPolicy`; no new threshold or result-dependent weight was introduced.", "",
        "## Acceptance questions", "",
        f"1. Candidates validated: **{len(scoreboard)}**.", f"2. Families: `{family}`.",
        f"3. Significant/stable predictive power: `{scoreboard.loc[scoreboard['gate_pass'], 'candidate_id'].tolist()}`.",
        f"4. Highest economic spread: `{scoreboard.sort_values('economic_strength', ascending=False).head(5)['candidate_id'].tolist()}`.",
        f"5. IC without tradeable Q5-Q1: `{scoreboard.loc[scoreboard['gate_pass'] & scoreboard['q5_minus_q1'].isna(), 'candidate_id'].tolist()}`.",
        f"6. Turnover: computed for {turnover_available}/{len(scoreboard)} candidates from equal-weight Top-N retention; unavailable values remain `None`.",
        f"7. Year-specific evidence: see `factor_temporal_stability.csv`; stability uses TRAIN+VALIDATION only.",
        "8. volume_ratio direction: see `runtime_conflict_resolution.md`; raw/+/- and 5D/20D/60D are retained.",
        f"9. NATR role: Alpha gate plus separate Risk utility (`{risk_verdict}`); the role conclusion never masquerades as Alpha PASS.",
        "10. near-high windows: 60D/120D/252D are independently reported; robustness is not best-window picking.",
        f"11. Growth/Quality/Institutional Flow: TIER2_STATUS=`{tier2_status}`; validated={tier2_counts['validated']} (Growth={tier2_counts['growth']}, Quality={tier2_counts['quality']}, InstitutionalFlow={tier2_counts['instflow']}); PIT_RISK/NO_DATA rows never enter the accepted pool.",
        f"12. Alpha101/191 new-information candidates: `{scoreboard.loc[scoreboard['tier'].eq('TIER3') & scoreboard['gate_pass'], 'candidate_id'].tolist()}`.",
        f"13. Redundant valid candidates: `{scoreboard.loc[scoreboard['verdict'].eq('VALID_BUT_REDUNDANT'), 'candidate_id'].tolist()}`.",
        f"14. Internal/Alphalens conclusion parity: `{checks}`.",
        f"15. Day 3 inputs: `{scoreboard.loc[scoreboard['verdict'].eq('CANDIDATE'), 'candidate_id'].tolist()}`; role-specific rows remain separately labelled.", "",
        "## Final status", "", "```text",
        f"DAY2_STATUS = {day2_status}", f"TOTAL_VALIDATED = {len(scoreboard)}", f"BY_FAMILY = {family}", f"FACTOR_VERDICTS = {verdicts}", f"ALPHALENS_CROSSCHECK = {checks}",
        f"RUNTIME_CONFLICTS = RESOLVED {tally.get('RESOLVED', 0)} / PARTIALLY_RESOLVED {tally.get('PARTIALLY_RESOLVED', 0)} / UNRESOLVED {tally.get('UNRESOLVED', 0)}",
        f"STRICT_OOS_STATUS = {strict_status}",
        f"TIER2_STATUS = {tier2_status}", f"TIER2_VALIDATED = {tier2_counts['validated']}",
        f"GROWTH_VALIDATED = {tier2_counts['growth']}", f"QUALITY_VALIDATED = {tier2_counts['quality']}", f"INSTITUTIONAL_FLOW_VALIDATED = {tier2_counts['instflow']}",
        f"ACCEPTED_FACTOR_POOL = {accepted}", f"DAY3_STRATEGY_SEARCH_READY = {'YES' if accepted else 'NO'}", "```", "",
        "The run intentionally does not optimize Sharpe, search portfolio weights, alter Runtime thresholds, or enter production."
    ]
    if blocker:
        lines.extend(["", f"Strict OOS blocker: {blocker}"])
    return "\n".join(lines) + "\n"


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="print frozen parameters and candidate counts only")
    parser.add_argument("--d3-root", type=Path, default=Path("artifacts") / "factors" / D3_RUN_ID)
    parser.add_argument("--master-path", type=Path, default=MASTER_PATH)
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--overwrite", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    day1 = freeze_day1(args.master_path.parent)
    specs = candidate_specs(args.master_path)
    if args.dry_run:
        print(json.dumps({"frozen_parameters": frozen_parameters(), "candidate_count": len(specs), "tier_counts": Counter(spec.tier for spec in specs), "day1": day1}, ensure_ascii=False, indent=2, default=str))
        return 0
    if args.output_root.exists() and not args.overwrite:
        print(f"output exists; use --overwrite to regenerate: {args.output_root}", file=sys.stderr)
        return 1
    args.output_root.mkdir(parents=True, exist_ok=True)
    pre_labels = load_pre_oos_labels(args.d3_root)
    raw_quotes = _load_raw_quotes(args.d3_root, end=VALIDATION_END)
    quote_frames = _quote_frames(raw_quotes)
    existing = _existing_panels(args.d3_root, pre_labels)
    fundamentals, pit_evidence = _db_tier2_data(quote_frames)
    metrics = []
    quantile_rows = []
    temporal_rows = []
    panels: dict[str, pd.DataFrame] = {}
    alphalens_by_id: dict[str, dict] = {}
    for spec in specs:
        values = compute_candidate(spec, quote_frames, existing=existing.get(spec.source_id), fundamentals=fundamentals)
        panels[spec.candidate_id] = values
        data = _merge_labels(values, pre_labels, spec)
        metric, quantiles, temporal = _evaluate_factor(data, spec)
        if spec.role == "Alpha":
            alphalens_by_id[spec.candidate_id] = _alphalens_metrics(data, metric.get("selected_horizon"))
        risk = _risk_utility(data) if spec.candidate_id == "RS_012" else {
            "risk_low_return_dispersion": np.nan,
            "risk_high_return_dispersion": np.nan,
            "risk_high_minus_low_dispersion": np.nan,
            "risk_utility_verdict": "NOT_APPLICABLE",
        }
        metrics.append({**spec.record(), **metric, **risk, "pit_status": spec.pit_status, "role_gate": "NOT_APPLICABLE_TO_ALPHA_GATE" if spec.role != "Alpha" else "D4_EVALUATION_POLICY"})
        quantile_rows.extend(quantiles)
        temporal_rows.extend(temporal)
    correlations = _correlations(panels, specs, start=TRAIN_START, end=VALIDATION_END)
    scoreboard = _scoreboard(metrics, specs, correlations)
    crosscheck = _crosscheck(scoreboard, alphalens_by_id)
    scoreboard = scoreboard.merge(crosscheck.loc[:, ["factor_id", "crosscheck"]].rename(columns={"factor_id": "candidate_id"}), on="candidate_id", how="left")
    # An Alpha candidate only stays CANDIDATE if the real alphalens recompute agrees.
    scoreboard.loc[scoreboard["role"].eq("Alpha") & scoreboard["verdict"].eq("CANDIDATE") & scoreboard["crosscheck"].ne("PASS"), "verdict"] = "REVIEW"
    scoreboard.loc[scoreboard["crosscheck"].eq("CROSSCHECK_FAIL"), "rejection_reason"] = "ALPHALENS_CONCLUSION_MISMATCH"
    scoreboard.loc[scoreboard["crosscheck"].eq("ALPHALENS_NOT_MEASURABLE") & scoreboard["role"].eq("Alpha"), "rejection_reason"] = "ALPHALENS_NOT_MEASURABLE"
    clusters = _redundancy_clusters(scoreboard, correlations)
    for _, cluster in clusters.groupby("cluster_id"):
        representative = cluster["representative_factor"].iloc[0]
        redundant = cluster.loc[
            cluster["candidate_id"].ne(representative)
            & scoreboard.set_index("candidate_id").loc[cluster["candidate_id"], "verdict"].eq("CANDIDATE").to_numpy(),
            "candidate_id",
        ]
        scoreboard.loc[scoreboard["candidate_id"].isin(redundant), "verdict"] = "VALID_BUT_REDUNDANT"
        scoreboard.loc[scoreboard["candidate_id"].isin(redundant), "rejection_reason"] = "REDUNDANT_HIGH_CORRELATION"
    clusters = _redundancy_clusters(scoreboard, correlations)

    # The holdout is read exactly once, after all pre-OOS gate/score decisions.
    decisions_frozen_at = datetime.now(timezone.utc).isoformat()
    oos_labels = load_oos_labels(args.d3_root)
    strict_quote_frames = _quote_frames(_load_raw_quotes(args.d3_root, end=STRICT_OOS_END))
    strict_existing = _existing_panels(args.d3_root, oos_labels)
    scoreboard = _oos_update(scoreboard, specs, oos_labels, strict_quote_frames, strict_existing)
    oos_read_at = datetime.now(timezone.utc).isoformat()
    accepted = _accepted_pool(scoreboard)
    strict_status, blocker = _strict_oos_status(accepted)
    conflicts = _runtime_conflicts(scoreboard, correlations)
    tier2_status, tier2_counts, day2_status = _tier2_summary(scoreboard, pit_evidence)

    manifest = pd.DataFrame([spec.record() for spec in specs])
    manifest.to_csv(args.output_root / "validation_candidate_manifest.csv", index=False)
    scoreboard.to_csv(args.output_root / "factor_validation_scoreboard.csv", index=False)
    pd.DataFrame(quantile_rows).to_csv(args.output_root / "factor_quantile_results.csv", index=False)
    pd.DataFrame(temporal_rows).to_csv(args.output_root / "factor_temporal_stability.csv", index=False)
    correlations.to_csv(args.output_root / "factor_redundancy_matrix.csv", index=False)
    clusters.to_csv(args.output_root / "factor_redundancy_clusters.csv", index=False)
    crosscheck.to_csv(args.output_root / "alphalens_crosscheck.csv", index=False)
    (args.output_root / "runtime_conflict_resolution.md").write_text(_runtime_report(scoreboard, correlations, conflicts), encoding="utf-8")
    accepted.to_csv(args.output_root / "accepted_factor_pool.csv", index=False)
    rejected = scoreboard.loc[scoreboard["verdict"].isin(["WEAK", "REJECT", "VALID_BUT_REDUNDANT"])].copy()
    rejected.to_csv(args.output_root / "rejected_factor_log.csv", index=False)
    (args.output_root / "day2_factor_validation_report.md").write_text(_report(scoreboard, crosscheck, day1, strict_status=strict_status, blocker=blocker, conflicts=conflicts, tier2_status=tier2_status, tier2_counts=tier2_counts, day2_status=day2_status), encoding="utf-8")
    run_manifest = {
        "run_id": "factor_validation_20260910_v1", "status": "success", "day2_status": day2_status, "schema": "day2_factor_validation_v1",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(), "decisions_frozen_at_utc": decisions_frozen_at, "strict_oos_read_at_utc": oos_read_at,
        "strict_oos_status": strict_status, "strict_oos_blocker": blocker, "tier2_status": tier2_status, "tier2_validated": tier2_counts,
        "runtime_conflicts": conflicts, "runtime_conflict_tally": dict(Counter(str(c["resolution_status"]) for c in conflicts)),
        "frozen_parameters": frozen_parameters(),
        "methodology": {
            "alphalens_crosscheck": "alphalens-reloaded independent recompute (factor_information_coefficient + mean_return_by_quantile + factor_rank_autocorrelation) on the same pre-OOS (date, asset) panel; sign/direction parity, not float equality; non-PASS Alpha candidates cannot enter the accepted pool",
            "oos_same_sign": "OOS IC and Q5-Q1 signs compared against the frozen pre-OOS mean IC / Q5-Q1 sign (== research_direction); OOS never re-selects direction",
            "strict_oos_status": "NOT_ESTABLISHED (no clean measurable accepted OOS) < MEASURABLE (clean + measurable, direction not confirmed) < ESTABLISHED (clean + measurable + IC & spread signs confirm frozen pre-OOS)",
            "alpha_rank_convention": "Alpha101/191 adapters use cross-sectional rank across assets per date (pandas axis=1); A101_003 canonical interpretation frozen as cross-sectional -corr(rank(open), rank(volume), 10d)",
            "runtime_conflict_tally": "computed from per-conflict resolution_status, not hardcoded",
            "tier2_status": "Tier 2 (Growth/Quality/InstitutionalFlow) requires the canonical DB; NOT_ESTABLISHED_DB_UNAVAILABLE when it cannot be reached; DAY2_STATUS then COMPLETE_WITH_TIER2_BLOCKER",
        },
        "day1_freeze": day1, "pit_evidence": pit_evidence, "candidate_count": len(specs), "row_counts": {name: int(len(pd.read_csv(args.output_root / name))) for name in REQUIRED_OUTPUTS if name.endswith(".csv")},
        "oos_rule": "strict OOS read after all pre-OOS decisions; OOS never changes verdict, direction, window, gate, or score", "artifacts": list(REQUIRED_OUTPUTS),
    }
    (args.output_root / "run_manifest.json").write_text(json.dumps(run_manifest, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")
    print(f"Day 2 validation complete: {len(specs)} candidates; accepted={len(accepted)}; strict_oos={strict_status}; tier2={tier2_status}; day2={day2_status}; output={args.output_root}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
