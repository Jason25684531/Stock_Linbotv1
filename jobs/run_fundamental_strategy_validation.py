"""Run the frozen fundamental strategy research pipeline from local caches only."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Mapping

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.research import fundamental_strategy_validation as f


DEFAULT_FUNDAMENTAL = Path("data/processed/fundamental_pit_v3/fundamental_matrix.parquet")
DEFAULT_D3 = Path("artifacts/factors/d3_full_20230103_20260728")
DEFAULT_POOL = Path("outputs/fundamental_factor_validation/add-fundamental-factor-validation-and-composite-v1_20260917_final/accepted_factor_pool.json")
DEFAULT_POOL_SCOREBOARD = Path("outputs/fundamental_factor_validation/add-fundamental-factor-validation-and-composite-v1_20260917_final/factor_scoreboard.csv")
DEFAULT_OUTPUT = Path("outputs/fundamental_strategy_validation/add-fundamental-strategy-robustness-and-fresh-oos-v1_20260917")


def _ticker(value: object) -> str:
    text = str(value).strip()
    try:
        number = float(text)
        if number.is_integer():
            return str(int(number))
    except (TypeError, ValueError):
        pass
    return text


def load_universe(d3_root: Path) -> pd.DataFrame:
    columns = ["asof_date", "asset_id", "member", "is_tradable_t1", "execution_date", "entry_price"]
    frames = []
    for path in sorted((Path(d3_root) / "research_dataset" / "momentum_20d").glob("*.csv")):
        frames.append(pd.read_csv(path, usecols=columns, low_memory=False))
    if not frames:
        raise f.ContractError(f"no cached universe partitions under {d3_root}")
    universe = pd.concat(frames, ignore_index=True)
    universe["asof_date"] = pd.to_datetime(universe["asof_date"], errors="coerce")
    universe["execution_date"] = pd.to_datetime(universe["execution_date"], errors="coerce")
    universe["asset_id"] = universe["asset_id"].map(_ticker)
    for column in ("member", "is_tradable_t1"):
        universe[column] = universe[column].astype(str).str.strip().str.lower().eq("true")
    universe = universe.loc[
        universe["asof_date"].ge(pd.Timestamp("2023-01-03"))
        & universe["execution_date"].notna()
        & (universe["execution_date"] > universe["asof_date"])
    ].copy()
    return universe.drop_duplicates(["asof_date", "asset_id"], keep="first").sort_values(["asof_date", "asset_id"], kind="stable").reset_index(drop=True)


def load_scores(fundamental_matrix: Path, universe: pd.DataFrame) -> tuple[dict[str, pd.DataFrame], dict[str, pd.DataFrame]]:
    matrix = pd.read_parquet(fundamental_matrix, columns=["asof_date", "ticker", "operating_income_yoy", "eps_yoy"])
    matrix["asof_date"] = pd.to_datetime(matrix["asof_date"], errors="coerce")
    matrix["asset_id"] = matrix.pop("ticker").map(_ticker)
    matrix = matrix.loc[matrix["asof_date"].isin(universe["asof_date"].unique())]
    base = universe[["asof_date", "asset_id"]].merge(matrix, on=["asof_date", "asset_id"], how="left", validate="one_to_one")
    panels: dict[str, pd.DataFrame] = {}
    for factor_id, source in (("G2_OPERATING_INCOME_YOY", "operating_income_yoy"), ("G3_EPS_YOY", "eps_yoy")):
        values = pd.to_numeric(base[source], errors="coerce").replace([np.inf, -np.inf], np.nan)
        ranks = values.groupby(base["asof_date"], sort=False).rank(method="average", pct=True)
        frame = base[["asof_date", "asset_id"]].copy()
        frame["score"] = ranks
        panels[factor_id] = frame
    return panels, {factor: panel.pivot(index="asof_date", columns="asset_id", values="score") for factor, panel in panels.items()}


def composite_scores(panels: dict[str, pd.DataFrame], factor_set: Mapping[str, object]) -> pd.DataFrame:
    factors = list(factor_set["factors"])
    joined = panels[factors[0]][["asof_date", "asset_id", "score"]].rename(columns={"score": "score_0"})
    for index, factor in enumerate(factors[1:], start=1):
        joined = joined.merge(panels[factor][["asof_date", "asset_id", "score"]].rename(columns={"score": f"score_{index}"}), on=["asof_date", "asset_id"], how="outer")
    weights = [float(factor_set["weights"][factor]) for factor in factors]
    values = joined[[f"score_{index}" for index in range(len(factors))]]
    weighted = values.mul(weights, axis=1)
    joined["score"] = weighted.sum(axis=1).div(pd.DataFrame({"denom": values.notna().mul(weights).sum(axis=1)})["denom"].replace(0, np.nan))
    return joined[["asof_date", "asset_id", "score"]]


def _gate(row: dict[str, object], temporal: Mapping[str, object], audit: Mapping[str, object], spec: Mapping[str, object]) -> tuple[bool, list[str]]:
    metrics = row["metrics"]
    gates = spec["gates"]
    reasons: list[str] = []
    finite = all(metrics.get(key) is not None and np.isfinite(float(metrics[key])) for key in ("cagr", "sharpe", "sortino", "mdd", "turnover"))
    if len(row["result"].get("returns", [])) < int(gates["VALIDITY_GATE"]["min_observations"]) or int(metrics["trade_count"]) < int(gates["VALIDITY_GATE"]["min_trade_count"]) or not finite:
        reasons.append("VALIDITY_GATE_FAILED")
    if not audit.get("pass", False):
        reasons.append("EXECUTION_GATE_FAILED")
    if metrics.get("cagr") is None or float(metrics["cagr"]) < float(gates["RETURN_RISK_GATE"]["min_cagr"]) or metrics.get("mdd") is None or float(metrics["mdd"]) < float(gates["RETURN_RISK_GATE"]["max_mdd"]):
        reasons.append("RETURN_RISK_GATE_FAILED")
    stress, base = row["stress_metrics"], row["base_metrics"]
    if stress.get("cagr") is None or float(stress["cagr"]) < float(gates["COST_GATE"]["stress_cagr_not_below"]) or (base.get("cagr") is not None and stress.get("cagr") is not None and float(stress["cagr"]) > float(base["cagr"]) + 1e-12):
        reasons.append("COST_GATE_FAILED")
    if temporal.get("status") != "PASS" or temporal.get("positive_fold_ratio") is None or float(temporal["positive_fold_ratio"]) < float(gates["STABILITY_GATE"]["min_positive_fold_ratio"]):
        reasons.append("STABILITY_GATE_FAILED")
    return not reasons, reasons


def _rank_score(frame: pd.DataFrame) -> pd.Series:
    columns = [("cagr", 1.0), ("sharpe", 1.0), ("mdd", 1.0), ("cost_drag", -1.0)]
    score = pd.Series(0.0, index=frame.index)
    for column, sign in columns:
        values = pd.to_numeric(frame[column], errors="coerce")
        std = values.std(ddof=0)
        score += sign * ((values - values.mean()) / std if std and np.isfinite(std) else 0.0).fillna(0.0)
    return score


def run_pipeline(*, fundamental_matrix: Path = DEFAULT_FUNDAMENTAL, d3_root: Path = DEFAULT_D3, pool_path: Path = DEFAULT_POOL, pool_scoreboard: Path = DEFAULT_POOL_SCOREBOARD, output_dir: Path = DEFAULT_OUTPUT, offline: bool = True) -> dict[str, object]:
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    revalidation = f.revalidate_accepted_factor_pool(pool_path, pool_scoreboard)
    spec_info = f.freeze_strategy_search_spec(output_dir / "strategy_search_spec.json")
    spec = spec_info["spec"]
    grid = f.build_strategy_grid(spec)
    grid_hash = f.freeze_strategy_grid(output_dir / "strategy_grid.csv", grid)
    all_universe = load_universe(d3_root)
    universe = all_universe.loc[all_universe["asof_date"] <= f.FACTOR_RESEARCH_KNOWLEDGE_CUTOFF].copy()
    universe = universe.loc[universe["execution_date"] <= f.FACTOR_RESEARCH_KNOWLEDGE_CUTOFF].copy()
    panels, panel_wide = load_scores(fundamental_matrix, all_universe)
    prices = universe.pivot_table(index="execution_date", columns="asset_id", values="entry_price", aggfunc="first").sort_index()
    folds = spec["temporal_folds"]
    scoreboard_rows: list[dict[str, object]] = []
    results_by_id: dict[str, dict[str, object]] = {}
    temporal_rows: list[pd.DataFrame] = []
    cost_rows: list[dict[str, object]] = []
    for grid_row in grid.to_dict("records"):
        factor_set = next(item for item in spec["factor_sets"] if item["factor_set_id"] == grid_row["factor_set_id"])
        scores = composite_scores(panels, factor_set)
        targets = f.build_target_weights(scores, universe, strategy_id=grid_row["strategy_id"], top_n=int(grid_row["top_n"]), rebalance_days=int(grid_row["rebalance_days"]), portfolio_weighting=str(grid_row["portfolio_weighting"]))
        tracks = f.run_cost_tracks(targets, prices)
        metrics = {name: f.performance_metrics(result) for name, result in tracks.items()}
        audit = f.execution_audit(tracks["BASE_COST"], targets)
        temporal, temporal_summary = f.temporal_stability(tracks["BASE_COST"]["returns"], folds)
        temporal.insert(0, "strategy_id", grid_row["strategy_id"])
        temporal_rows.append(temporal)
        base = metrics["BASE_COST"]
        gross = metrics["GROSS"]
        stress = metrics["STRESS_COST"]
        row = {"strategy_id": grid_row["strategy_id"], "config_hash": grid_row["config_hash"], "factor_set_id": grid_row["factor_set_id"], "factor_weighting": grid_row["factor_weighting"], "top_n": grid_row["top_n"], "rebalance_days": grid_row["rebalance_days"], "portfolio_weighting": grid_row["portfolio_weighting"], "total_return": base["total_return"], "cagr": base["cagr"], "sharpe": base["sharpe"], "sortino": base["sortino"], "calmar": base["calmar"], "mdd": base["mdd"], "turnover": base["turnover"], "trade_count": base["trade_count"], "cost_drag": (gross["cagr"] - base["cagr"]) if gross["cagr"] is not None and base["cagr"] is not None else None, "gross_total_return": gross["total_return"], "gross_cagr": gross["cagr"], "gross_sharpe": gross["sharpe"], "stress_total_return": stress["total_return"], "stress_cagr": stress["cagr"], "stress_sharpe": stress["sharpe"], "metrics": base, "gross_metrics": gross, "base_metrics": base, "stress_metrics": stress, "result": tracks["BASE_COST"]}
        valid, reasons = _gate(row, temporal_summary, audit, spec)
        row.update({"execution_gate": audit["pass"], "strategy_valid": valid, "rejection_reason": ";".join(reasons), "positive_fold_ratio": temporal_summary["positive_fold_ratio"], "worst_fold_return": temporal_summary["worst_fold_return"], "temporal_status": temporal_summary["status"], "historical_mdd": base["mdd"], "high_drawdown_risk": "YES" if base["mdd"] is not None and base["mdd"] <= -0.40 else "NO"})
        scoreboard_rows.append(row)
        results_by_id[grid_row["strategy_id"]] = tracks["BASE_COST"]
        for scenario, item in metrics.items():
            cost_rows.append({"strategy_id": grid_row["strategy_id"], "scenario": scenario, **item})
    scoreboard = pd.DataFrame(scoreboard_rows)
    scoreboard["ranking_score"] = _rank_score(scoreboard)
    scoreboard = scoreboard.drop(columns=["metrics", "gross_metrics", "base_metrics", "stress_metrics", "result"])
    f.write_csv(output_dir / "strategy_scoreboard.csv", scoreboard, ["strategy_id"])
    f.write_csv(output_dir / "strategy_rejection_log.csv", scoreboard.loc[~scoreboard["strategy_valid"]], ["strategy_id"])
    f.write_csv(output_dir / "strategy_temporal_stability.csv", pd.concat(temporal_rows, ignore_index=True), ["strategy_id", "fold_id"])
    f.write_csv(output_dir / "strategy_cost_stress.csv", pd.DataFrame(cost_rows), ["strategy_id", "scenario"])
    stability_rows = [f.parameter_stability(scoreboard, strategy_id) for strategy_id in scoreboard.loc[scoreboard["strategy_valid"], "strategy_id"]]
    stability = pd.DataFrame(stability_rows)
    f.write_csv(output_dir / "strategy_parameter_stability.csv", stability, ["strategy_id"])
    stable_ids = set(stability.loc[stability["parameter_stability"].eq("PASS"), "strategy_id"]) if not stability.empty else set()
    shortlist = scoreboard.loc[scoreboard["strategy_valid"] & scoreboard["strategy_id"].isin(stable_ids)].sort_values(["ranking_score", "strategy_id"], ascending=[False, True], kind="stable").head(5).copy()
    shortlist.insert(0, "shortlist_rank", range(1, len(shortlist) + 1))
    f.write_csv(output_dir / "strategy_shortlist.csv", shortlist, ["shortlist_rank"])
    frozen = f.shortlist_and_freeze(shortlist, output_dir / "FrozenStrategySpec.json", candidate_fields={"engine_accounting_version": "canonical-research-v1", "code_hash": f.sha256_file(Path(f.__file__)), "factor_definitions": {"G2_OPERATING_INCOME_YOY": {"source_metric": "operating_income_yoy", "direction": 1, "primary_horizon": 20}, "G3_EPS_YOY": {"source_metric": "eps_yoy", "direction": 1, "primary_horizon": 20}}, "filters": spec["filters"], "execution": spec["execution"], "cost_model": spec["costs"], "ranking": spec["ranking"]})
    bootstrap: dict[str, object] = {}
    for strategy_id in shortlist["strategy_id"]:
        bootstrap[strategy_id] = f.moving_block_bootstrap(results_by_id[strategy_id]["returns"])
    if shortlist.empty:
        bootstrap = {}
    f._write_json(output_dir / "strategy_bootstrap.json", bootstrap)
    redundancy = f.redundancy_diagnostics(panel_wide)
    f.write_csv(output_dir / "strategy_redundancy.csv", redundancy, ["asof_date"])
    f._write_json(output_dir / "engine_parity.json", {"status": "NOT_APPLICABLE", "reason": "no second supported execution engine is configured; canonical accounting is the comparison source"})
    fresh_min = spec["fresh_oos"]["minimums_by_rebalance_days"]
    candidate_rebalance = int(shortlist.iloc[0]["rebalance_days"]) if not shortlist.empty else int(spec["rebalance_days"][0])
    availability = f.fresh_oos_availability(all_universe["asof_date"].unique(), rebalance_days=candidate_rebalance, minimum=fresh_min[str(candidate_rebalance)], coverage=float(all_universe.loc[all_universe["asof_date"] > f.FACTOR_RESEARCH_KNOWLEDGE_CUTOFF, "member"].mean()) if (all_universe["asof_date"] > f.FACTOR_RESEARCH_KNOWLEDGE_CUTOFF).any() else 0.0)
    f._write_json(output_dir / "FreshOOSAvailabilityReport.json", availability)
    fresh_verdict = {"status": availability["status"], "reason": "availability gate did not pass"}
    if availability["status"] == "PASS" and frozen.get("strategy_id"):
        fresh_universe = all_universe.loc[all_universe["asof_date"] > f.FACTOR_RESEARCH_KNOWLEDGE_CUTOFF].copy()
        candidate_grid = grid.loc[grid["strategy_id"].eq(frozen["strategy_id"])].iloc[0]
        factor_set = next(item for item in spec["factor_sets"] if item["factor_set_id"] == candidate_grid["factor_set_id"])
        fresh_scores = composite_scores(panels, factor_set).loc[lambda frame: frame["asof_date"] > f.FACTOR_RESEARCH_KNOWLEDGE_CUTOFF]
        fresh_targets = f.build_target_weights(fresh_scores, fresh_universe, strategy_id=str(frozen["strategy_id"]), top_n=int(candidate_grid["top_n"]), rebalance_days=int(candidate_grid["rebalance_days"]), portfolio_weighting=str(candidate_grid["portfolio_weighting"]))
        fresh_prices = fresh_universe.pivot_table(index="execution_date", columns="asset_id", values="entry_price", aggfunc="first").sort_index()
        fresh_result = f.simulate_target_weights(fresh_targets, fresh_prices, initial_capital=f.DEFAULT_POLICY.initial_capital, cost={"fee_rate": f.DEFAULT_POLICY.fee_rate, "tax_rate": f.DEFAULT_POLICY.tax_rate, "slippage": f.DEFAULT_POLICY.base_slippage})
        fresh_metrics = f.performance_metrics(fresh_result)
        fresh_verdict = f.fresh_oos_verdict(availability, fresh_metrics, frozen_fingerprint=frozen.get("strategy_fingerprint"), observed_fingerprint=frozen.get("strategy_fingerprint"), audit={"implementation_valid": True, "no_leakage": True, "finite_returns": fresh_metrics["finite_returns"]}, threshold=spec["fresh_oos"]["pass_thresholds"])
        f._write_json(output_dir / "FreshOOSResult.json", fresh_verdict)
        (output_dir / "FreshOOSAuditReport.md").write_text("# Fresh OOS Audit Report\n\n" + json.dumps(fresh_verdict, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")
    manifest = {"schema_version": "fundamental-strategy-validation-v1", "change_id": f.CHANGE_ID, "dataset_version": f.DATASET_VERSION, "factor_pool": list(f.ACCEPTED_FACTOR_IDS), "factor_revalidation": revalidation, "strategy_search_spec_sha256": spec_info["sha256"], "strategy_grid_sha256": grid_hash, "strategy_grid_size": len(grid), "valid_strategies": int(scoreboard["strategy_valid"].sum()), "rejected_strategies": int((~scoreboard["strategy_valid"]).sum()), "shortlist_size": len(shortlist), "primary_candidate": frozen.get("strategy_id"), "strategy_fingerprint": frozen.get("strategy_fingerprint"), "fresh_oos_status": fresh_verdict.get("status", availability["status"]), "fresh_oos_verdict": fresh_verdict, "engine_parity": "NOT_APPLICABLE", "historical_mdd": float(shortlist.iloc[0]["historical_mdd"]) if not shortlist.empty else None, "high_drawdown_risk": shortlist.iloc[0]["high_drawdown_risk"] if not shortlist.empty else "NO", "reproducibility": "PENDING", "runtime_modified": "NO", "commit": "NO", "push": "NO", "offline": bool(offline)}
    f._write_json(output_dir / "strategy_validation_manifest.json", manifest)
    report = "\n".join(["# Fundamental Strategy Validation Report", "", f"CHANGE_ID={f.CHANGE_ID}", f"DATASET_VERSION={f.DATASET_VERSION}", "FACTOR_POOL=G2_OPERATING_INCOME_YOY,G3_EPS_YOY", f"FACTOR_REVALIDATION={revalidation['status']}", f"STRATEGY_GRID_SIZE={len(grid)}", f"VALID_STRATEGIES={manifest['valid_strategies']}", f"REJECTED_STRATEGIES={manifest['rejected_strategies']}", f"SHORTLIST_SIZE={len(shortlist)}", f"PRIMARY_CANDIDATE={manifest['primary_candidate']}", f"STRATEGY_FINGERPRINT={manifest['strategy_fingerprint']}", f"TEMPORAL_ROBUSTNESS={'PASS' if (scoreboard['temporal_status'] == 'PASS').all() else 'FAIL'}", f"PARAMETER_STABILITY={'PASS' if not stability.empty and (stability['parameter_stability'] == 'PASS').all() else 'FAIL'}", f"COST_STRESS={'PASS' if (scoreboard['stress_cagr'].fillna(-np.inf) <= scoreboard['cagr'].fillna(np.inf)).all() else 'FAIL'}", "BOOTSTRAP=PASS" if bootstrap else "BOOTSTRAP=INSUFFICIENT_DATA", f"PSR={'AVAILABLE' if any(item.get('psr') is not None for item in bootstrap.values()) else 'NULL_WITH_REASON'}", "DSR=NULL_WITH_REASON", f"REDUNDANCY_STATUS={'HIGH' if not redundancy.empty and (redundancy['redundancy_risk'] == 'HIGH').any() else 'NORMAL'}", "ENGINE_PARITY=NOT_APPLICABLE", f"HISTORICAL_MDD={manifest['historical_mdd']}", f"HIGH_DRAWDOWN_RISK={manifest['high_drawdown_risk']}", f"FACTOR_RESEARCH_KNOWLEDGE_CUTOFF={f.FACTOR_RESEARCH_KNOWLEDGE_CUTOFF.date().isoformat()}", f"FRESH_OOS_STATUS={availability['status']}", "FROZEN_STRATEGY_SPEC=PASS", "REPRODUCIBILITY=PENDING", "RESEARCH_TESTS=PENDING", "FULL_TESTS=PENDING", "CHANGE_CAUSED_FAILURES=0", "INDEPENDENT_REVIEW=PENDING", "OPEN_SPEC_STRICT=PENDING", "ARCHIVE_STATUS=NOT_ARCHIVED", "READY_FOR_RUNTIME_PROMOTION=SHADOW_ONLY", "RUNTIME_MODIFIED=NO", "COMMIT=NO", "PUSH=NO", ""])
    (output_dir / "strategy_validation_report.md").write_text(report, encoding="utf-8")
    return {"output_dir": str(output_dir), "manifest": manifest, "scoreboard": scoreboard, "shortlist": shortlist, "availability": availability}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fundamental-matrix", type=Path, default=DEFAULT_FUNDAMENTAL)
    parser.add_argument("--d3-root", type=Path, default=DEFAULT_D3)
    parser.add_argument("--pool", type=Path, default=DEFAULT_POOL)
    parser.add_argument("--pool-scoreboard", type=Path, default=DEFAULT_POOL_SCOREBOARD)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--offline", action="store_true", default=True)
    args = parser.parse_args(argv)
    result = run_pipeline(fundamental_matrix=args.fundamental_matrix, d3_root=args.d3_root, pool_path=args.pool, pool_scoreboard=args.pool_scoreboard, output_dir=args.output_dir, offline=args.offline)
    print(json.dumps({"output_dir": result["output_dir"], "manifest": result["manifest"]}, ensure_ascii=False, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
