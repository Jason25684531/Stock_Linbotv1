"""Day 3 validation helpers: Day 2 verification, engine comparison, Empyrical
cross-check, temporal stability, and cost-stress scenarios.

Policies here (tolerance classes, cost scenarios) are frozen per design.md
decisions B/C/F/G before any real validation run; they must not be tuned
after seeing results.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import empyrical
import numpy as np
import pandas as pd


def verify_day2_run(run_dir: Path) -> dict:
    """Recompute every Day 2 artifact SHA-256 and shortlist identity; raise on mismatch."""
    run_dir = Path(run_dir)
    manifest = json.loads((run_dir / "run_manifest.json").read_text(encoding="utf-8"))
    mismatches = [
        rel_path for rel_path, expected in manifest["artifact_sha256"].items()
        if hashlib.sha256((run_dir / rel_path).read_bytes()).hexdigest() != expected
    ]
    if mismatches:
        raise ValueError(f"Day 2 artifact hash mismatch: {mismatches}")
    shortlist_ids = pd.read_csv(run_dir / "shortlisted_configs.csv")["config_id"].tolist()
    if shortlist_ids != manifest["shortlist_config_ids"]:
        raise ValueError("Day 2 shortlist config IDs differ from the frozen manifest")
    for config_id in shortlist_ids:
        target_path = run_dir / "target_weights" / f"{config_id}.csv"
        if not target_path.is_file():
            raise ValueError(f"missing target-weight file for shortlisted config: {config_id}")
        frame = pd.read_csv(target_path, parse_dates=["asof_date", "execution_date"])
        if not (frame["execution_date"] > frame["asof_date"]).all():
            raise ValueError(f"execution_date not strictly after asof_date for {config_id}")
    return manifest


def sha256_of(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def tree_sha256(path: Path) -> str:
    """Deterministic combined SHA-256 over every file under path (name + content).

    Mirrors jobs/run_portfolio_research.py's `_tree_sha256`, so re-hashing the
    frozen handoff/dataset here reproduces the exact values already recorded in
    the Day 2 manifest's provenance.
    """
    path = Path(path)
    entries = [f"{file.relative_to(path).as_posix()}:{sha256_of(file)}" for file in sorted(path.rglob("*")) if file.is_file()]
    return hashlib.sha256("\n".join(entries).encode()).hexdigest()


def assess_strict_oos_feasibility(handoff_candidates_path: Path) -> tuple[bool, str]:
    """Determine whether composite weights can be re-derived from pre-OOS data only.

    D5's ic/icir combination weights are frozen full-sample scalars from D4 (one
    mean_ic/icir per factor, not a per-window series). Recomputing them from only
    pre-OOS data would require reopening D4's evaluation, which Day 3 must not do.
    """
    candidates = pd.read_csv(handoff_candidates_path)
    if {"mean_ic", "icir"} <= set(candidates.columns) and len(candidates) > 0:
        return False, (
            "D4 handoff exposes a single full-sample mean_ic/icir scalar per candidate factor "
            "(not a per-window series); composite weights cannot be re-derived from pre-OOS data "
            "alone without reopening D4's evaluation, which Day 3 must not do."
        )
    return True, "handoff does not expose full-sample-only composite inputs"


def load_price_matrix(dataset_path: Path, factor_ids: list[str]) -> pd.DataFrame:
    """Rebuild the T+1 entry-price matrix from the same D3 dataset source as Day 2.

    ponytail: mirrors jobs/run_portfolio_research.py's inline construction; small
    duplication instead of importing jobs/ from core/ (wrong dependency direction).
    """
    columns = ["asof_date", "asset_id", "execution_date", "entry_price"]
    frames = [
        pd.read_csv(file, usecols=columns)
        for factor_id in factor_ids
        for file in sorted((Path(dataset_path) / factor_id).glob("*.csv"))
    ]
    data = pd.concat(frames, ignore_index=True)
    universe = data.groupby(["asof_date", "asset_id"], as_index=False).agg(
        execution_date=("execution_date", "first"), entry_price=("entry_price", "first")
    )
    universe["execution_date"] = pd.to_datetime(universe["execution_date"])
    return universe.pivot(index="execution_date", columns="asset_id", values="entry_price").sort_index()


# ---------------------------------------------------------------------------
# Phase 3: two-layer engine comparison (design.md decision B)
# ---------------------------------------------------------------------------

def check_structural_parity(replay_targets: pd.DataFrame, frozen_target_path: Path) -> pd.DataFrame:
    """Level A: compare frozen intent against evidence consumed by the replay."""
    frozen = pd.read_csv(frozen_target_path, parse_dates=["asof_date", "execution_date"])
    if (frozen["execution_date"] <= frozen["asof_date"]).any():
        raise ValueError("structural parity failed: same-close or lookahead execution in frozen artifact")
    frozen_cmp = frozen[["asof_date", "execution_date", "asset_id", "target_weight"]].sort_values(["execution_date", "asset_id"]).reset_index(drop=True)
    candidate_cmp = replay_targets[["asof_date", "execution_date", "asset_id", "target_weight"]].copy()
    candidate_cmp["asof_date"] = pd.to_datetime(candidate_cmp["asof_date"])
    candidate_cmp["execution_date"] = pd.to_datetime(candidate_cmp["execution_date"])
    candidate_cmp = candidate_cmp.sort_values(["execution_date", "asset_id"]).reset_index(drop=True)
    status = "PASS" if candidate_cmp.equals(frozen_cmp) else "FAIL"
    evidence = pd.DataFrame([{
        "config_id": replay_targets["config_id"].iloc[0] if "config_id" in replay_targets and not replay_targets.empty else None,
        "frozen_target_sha256": sha256_of(frozen_target_path),
        "replay_consumed_row_count": len(candidate_cmp),
        "frozen_row_count": len(frozen_cmp),
        "target_intent_parity_status": status,
        "execution_outcome": "SEE_EXECUTION_LOG",
        "difference_reason": None if status == "PASS" else "REPLAY_CONSUMED_TARGETS_DIFFER",
    }])
    if status == "FAIL":
        raise ValueError("structural parity failed: replay target weights differ from the frozen Day 2 artifact")
    return evidence


RETURN_METRICS = ("total_return", "annualized_return", "annualized_volatility", "sharpe", "sortino", "max_drawdown", "calmar")
COST_METRICS = ("turnover", "trade_count", "estimated_cost", "ending_value")
_COST_METRIC_REASON = {
    "turnover": "execution_accounting",
    "trade_count": "execution_accounting",
    "estimated_cost": "asymmetric_tax_and_minimum_commission",
    "ending_value": "cash_handling",
}


def _classify_metric(metric: str, vbt_value: float, custom_value: float) -> tuple[str, str | None]:
    if any(v is None for v in (vbt_value, custom_value)) or pd.isna(vbt_value) or pd.isna(custom_value) or not np.isfinite(vbt_value) or not np.isfinite(custom_value):
        return "UNEXPLAINED_DIFFERENCE", "non_finite_value"
    absolute_difference = abs(custom_value - vbt_value)
    relative_difference = absolute_difference / abs(vbt_value) if vbt_value else float("inf")
    if metric in RETURN_METRICS:
        if relative_difference <= 0.10 or absolute_difference <= 0.05:
            return "MATCH_WITHIN_TOLERANCE", None
        return "UNEXPLAINED_DIFFERENCE", "exceeds_declared_tolerance"
    if metric in COST_METRICS:
        return "EXPECTED_MODEL_DIFFERENCE", _COST_METRIC_REASON[metric]
    raise ValueError(f"metric not covered by the declared comparison policy: {metric}")


def compare_engines(config_id: str, vbt_row: dict, custom_row: dict, vbt_returns: pd.Series, custom_returns: pd.Series) -> pd.DataFrame:
    """Level B: classify VectorBT-vs-Custom differences under the frozen tolerance policy."""
    rows = []
    for metric in (*RETURN_METRICS, *COST_METRICS):
        vbt_value, custom_value = float(vbt_row[metric]), float(custom_row[metric])
        status, reason = _classify_metric(metric, vbt_value, custom_value)
        absolute_difference = abs(custom_value - vbt_value)
        relative_difference = absolute_difference / abs(vbt_value) if vbt_value else float("inf")
        rows.append({
            "config_id": config_id, "metric": metric, "vectorbt_value": vbt_value, "custom_value": custom_value,
            "absolute_difference": absolute_difference, "relative_difference": relative_difference,
            "comparison_status": status, "difference_reason": reason,
        })
    aligned = pd.concat([vbt_returns.rename("vbt"), custom_returns.rename("custom")], axis=1).dropna()
    correlation = aligned["vbt"].corr(aligned["custom"]) if len(aligned) > 1 else float("nan")
    if pd.isna(correlation):
        corr_status, corr_reason = "UNEXPLAINED_DIFFERENCE", "insufficient_overlapping_daily_returns"
    elif correlation >= 0.95:
        corr_status, corr_reason = "MATCH", None
    elif correlation >= 0.80:
        corr_status, corr_reason = "REVIEW", "execution_accounting"
    else:
        corr_status, corr_reason = "UNEXPLAINED_DIFFERENCE", "daily_return_correlation_below_declared_floor"
    rows.append({
        "config_id": config_id, "metric": "daily_return_correlation", "vectorbt_value": float("nan"), "custom_value": correlation,
        "absolute_difference": float("nan"), "relative_difference": float("nan"),
        "comparison_status": corr_status, "difference_reason": corr_reason,
    })
    return pd.DataFrame(rows)


def overall_engine_status(comparison: pd.DataFrame) -> str:
    if (comparison["comparison_status"] == "UNEXPLAINED_DIFFERENCE").any():
        return "FAIL"
    if (comparison["comparison_status"] == "EXPECTED_MODEL_DIFFERENCE").any() or (comparison["comparison_status"] == "REVIEW").any():
        return "PASS_WITH_EXPLAINED_DIFFERENCES"
    return "PASS"


# ---------------------------------------------------------------------------
# Phase 4: Empyrical cross-check (design.md decision C)
# ---------------------------------------------------------------------------

_CROSSCHECK_METRICS = ("annual_return", "annual_volatility", "sharpe_ratio", "sortino_ratio", "max_drawdown", "calmar_ratio")
_CONVENTION_DIFFERENCE_METRICS = {"sharpe_ratio", "sortino_ratio", "annual_volatility"}
_CUSTOM_METRIC_NAME = {
    "annual_return": "cagr", "annual_volatility": "annualized_volatility", "sharpe_ratio": "sharpe",
    "sortino_ratio": "sortino", "max_drawdown": "max_drawdown", "calmar_ratio": "calmar",
}


def _empyrical_value(metric: str, returns: pd.Series) -> float:
    if metric == "annual_return":
        return float(empyrical.annual_return(returns, period="daily"))
    if metric == "annual_volatility":
        return float(empyrical.annual_volatility(returns, period="daily"))
    if metric == "sharpe_ratio":
        return float(empyrical.sharpe_ratio(returns, risk_free=0.0, period="daily"))
    if metric == "sortino_ratio":
        return float(empyrical.sortino_ratio(returns, required_return=0.0, period="daily"))
    if metric == "max_drawdown":
        return float(empyrical.max_drawdown(returns))
    return float(empyrical.calmar_ratio(returns, period="daily"))


def empyrical_crosscheck(config_id: str, custom_returns: pd.Series, custom_metrics) -> pd.DataFrame:
    """Cross-check Custom Engine metrics against Empyrical on the same daily return series."""
    rows = []
    for metric in _CROSSCHECK_METRICS:
        custom_metric = custom_metrics.get(_CUSTOM_METRIC_NAME[metric])
        empyrical_value = _empyrical_value(metric, custom_returns)
        if custom_metric.value is None:
            rows.append({
                "config_id": config_id, "metric": metric, "custom_engine_value": None, "empyrical_value": empyrical_value,
                "absolute_difference": None, "relative_difference": None, "status": "CUSTOM_METRIC_UNAVAILABLE", "notes": custom_metric.reason,
            })
            continue
        custom_value = custom_metric.value
        absolute_difference = abs(empyrical_value - custom_value)
        relative_difference = absolute_difference / abs(custom_value) if custom_value else float("inf")
        if absolute_difference <= 1e-6 or relative_difference <= 1e-4:
            status, notes = "MATCH", ""
        elif metric in _CONVENTION_DIFFERENCE_METRICS and relative_difference <= 0.01:
            status, notes = "CONVENTION_DIFFERENCE", "annualization/formula convention differs (see design.md decision C)"
        else:
            raise ValueError(f"metric_crosscheck STOP: {metric} disagrees beyond declared tolerance for {config_id} (rel_diff={relative_difference:.6f})")
        rows.append({
            "config_id": config_id, "metric": metric, "custom_engine_value": custom_value, "empyrical_value": empyrical_value,
            "absolute_difference": absolute_difference, "relative_difference": relative_difference, "status": status, "notes": notes,
        })
    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# Phase 7: temporal stability (design.md decision F)
# ---------------------------------------------------------------------------

def temporal_stability(config_id: str, daily_returns: pd.Series) -> pd.DataFrame:
    """Split a canonical shortlist config's replay returns into 60/20/20 chronological segments.

    Always labeled temporal stability, never strict OOS: the Day 2 shortlist was
    selected using the full sample, so the tail segment already informed selection.
    """
    returns = daily_returns.sort_index()
    n = len(returns)
    if n < 3:
        raise ValueError("insufficient observations for a temporal stability split")
    first_cut, second_cut = int(n * 0.6), int(n * 0.8)
    segments = {
        "early_60pct": returns.iloc[:first_cut],
        "middle_20pct": returns.iloc[first_cut:second_cut],
        "late_20pct": returns.iloc[second_cut:],
    }
    rows = []
    for segment_name, segment_returns in segments.items():
        std = segment_returns.std(ddof=0)
        rows.append({
            "config_id": config_id, "segment": segment_name, "label": "temporal_stability_not_strict_oos",
            "observations": len(segment_returns),
            "total_return": float((1 + segment_returns).prod() - 1) if len(segment_returns) else float("nan"),
            "annualized_return": float(segment_returns.mean() * 252) if len(segment_returns) else float("nan"),
            "sharpe": float(segment_returns.mean() / std * (252 ** 0.5)) if len(segment_returns) > 1 and std else float("nan"),
        })
    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# Phase 8: cost stress (design.md decision G)
# ---------------------------------------------------------------------------

COST_SCENARIOS = {"BASE": 0.0, "STRESS": 0.001, "PESSIMISTIC": 0.002}


def run_cost_sensitivity(config_id: str, target_weights: pd.DataFrame, price_matrix: pd.DataFrame, cost_model, *, initial_capital: float) -> pd.DataFrame:
    from core.backtest.research_adapter import replay_config

    rows = []
    for scenario, slippage in COST_SCENARIOS.items():
        result = replay_config(target_weights, price_matrix, cost_model, config_id=config_id, source_run_id="", initial_capital=initial_capital, slippage=slippage)
        metrics = result.performance_metrics
        rows.append({
            "config_id": config_id, "scenario": scenario,
            "total_return": metrics.get("total_return").value,
            "annualized_return": metrics.get("annualized_return").value,
            "sharpe": metrics.get("sharpe").value,
            "sortino": metrics.get("sortino").value,
            "max_drawdown": metrics.get("max_drawdown").value,
            "turnover": metrics.get("turnover").value,
            "total_cost": float(result.transactions["fee"].sum()) if not result.transactions.empty else 0.0,
            "ending_value": float(result.portfolio_value.iloc[-1]) if len(result.portfolio_value) else float("nan"),
        })
    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# Phase 9.1: validation scoreboard classification
# ---------------------------------------------------------------------------

def classify_validation_status(*, engine_status: str, temporal_segment_sharpes: list[float], cost_stress_total_returns: list[float], hac_alpha: float | None, hac_p_value: float | None) -> str:
    """Deterministic ROBUST/REVIEW/WEAK/INVALID classification; p<0.05 alone is never sufficient."""
    if engine_status == "FAIL":
        return "INVALID"
    if any((not np.isfinite(sharpe)) or sharpe <= 0 for sharpe in temporal_segment_sharpes):
        return "WEAK"
    if any(total_return <= 0 for total_return in cost_stress_total_returns):
        return "WEAK"
    if hac_alpha is None or hac_p_value is None or hac_p_value >= 0.05 or hac_alpha <= 0:
        return "REVIEW"
    return "ROBUST"


def build_validation_scoreboard(rows: list[dict], expected_config_ids: list[str]) -> pd.DataFrame:
    frame = pd.DataFrame(rows)
    missing = set(expected_config_ids) - set(frame["config_id"]) if not frame.empty else set(expected_config_ids)
    if missing or len(frame) != len(expected_config_ids):
        raise ValueError(f"validation scoreboard is missing frozen shortlist configs: {sorted(missing)}")
    return frame
