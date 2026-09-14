"""Day 3 strategy search + realistic backtest runner.

Consumes only the frozen Day 2 accepted_factor_pool_v1 (4 factors), builds the
300-config frozen grid, runs a three-track (gross/net_base/net_stress)
DISCRETE_EXECUTION_DATE_ONLY backtest per config, gates, ranks, and emits a
deterministic shortlist. See design.md SD-1..SD-14 for the frozen decisions.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import platform
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from config.settings import Config
from core.research import strategy_search as ss


DEFAULT_DAY2_DIR = Path("outputs/factor_validation/factor_validation_20260910_v1")
DEFAULT_D3_ROOT = Path("artifacts/factors/d3_full_20230103_20260728")
DEFAULT_MASTER_PATH = Path("outputs/factor_inventory/candidate_inventory_20260910_v1/candidate_factor_master.csv")
DEFAULT_OUTPUT_ROOT = Path("outputs/strategy_research")
REQUIRED_ARTIFACTS = (
    "strategy_search_manifest.csv",
    "strategy_factor_combinations.csv",
    "strategy_backtest_scoreboard.csv",
    "strategy_performance_metrics.csv",
    "strategy_execution_metrics.csv",
    "strategy_cost_analysis.csv",
    "strategy_temporal_stability.csv",
    "strategy_equity_curves.csv",
    "strategy_drawdowns.csv",
    "strategy_shortlist.csv",
    "strategy_rejection_log.csv",
    "day3_strategy_search_report.md",
    "run_manifest.json",
)
TRACK_METRIC_KEYS = ("observations", "total_return", "annualized_return", "annualized_volatility", "sharpe", "sortino", "max_drawdown", "calmar", "turnover", "estimated_cost", "trade_count", "ending_value")


def _write_csv(frame: pd.DataFrame, path: Path, sort_keys: list[str]) -> None:
    frame.sort_values(sort_keys, kind="stable").to_csv(path, index=False, lineterminator="\n")


def _version(name: str) -> str | None:
    try:
        from importlib.metadata import version
        return version(name)
    except Exception:
        return None


def _run_one_config(strategy_id: str, target: pd.DataFrame, close: pd.DataFrame, spec: ss.StrategySpec) -> dict[str, object]:
    tracks = ss.run_backtest_tracks(close, target)
    gates = {track: ss.execution_gate(result, target) for track, result in tracks.items()}
    if not (gates["gross"]["rebalance_count"] == gates["net_base"]["rebalance_count"] == gates["net_stress"]["rebalance_count"]):
        raise ValueError(f"DAY3_BLOCKED: {strategy_id} produced inconsistent rebalance counts across cost tracks")
    row: dict[str, object] = {"strategy_id": strategy_id, "config_hash": spec.config_hash, "implementation_risk": spec.implementation_risk}
    for track, result in tracks.items():
        metrics = ss.summarize_track(result)
        row.update({f"{track}_{key}": value for key, value in metrics.items()})
    base_gate = gates["net_base"]
    row.update({
        "rebalance_count": base_gate["rebalance_count"],
        "scheduled_execution_date_count": base_gate["scheduled_execution_date_count"],
        "actual_order_date_count": base_gate["actual_order_date_count"],
        "non_rebalance_orders": max(gate["non_rebalance_orders"] for gate in gates.values()),
        "execution_gate_pass": all(gate["execution_gate_pass"] for gate in gates.values()),
        "execution_gate_violations": ";".join(sorted({v for gate in gates.values() for v in gate["execution_gate_violations"].split(";") if v})),
    })
    row["cost_drag"] = row["gross_annualized_return"] - row["net_base_annualized_return"]
    row["slippage_drag"] = row["net_base_annualized_return"] - row["net_stress_annualized_return"]
    yearly = ss.yearly_stability(tracks["net_base"]["returns"])
    yearly.insert(0, "strategy_id", strategy_id)
    temporal = ss.temporal_summary(yearly.drop(columns="strategy_id"))
    row.update(temporal)
    equity = pd.Series(tracks["net_base"]["value"], dtype=float).rename("equity").rename_axis("date").reset_index()
    equity.insert(0, "strategy_id", strategy_id)
    drawdown = equity.copy()
    drawdown["drawdown"] = drawdown["equity"] / drawdown["equity"].cummax() - 1
    drawdown = drawdown.loc[:, ["strategy_id", "date", "drawdown"]]
    return {"row": row, "yearly": yearly, "equity": equity.loc[:, ["strategy_id", "date", "equity"]], "drawdown": drawdown}


def run(*, day2_dir: Path, d3_root: Path, master_path: Path, output_dir: Path) -> Path:
    preflight = ss.preflight_accepted_pool(day2_dir)
    pool = pd.DataFrame(preflight["pool"])
    directions = ss.resolve_effective_directions(pool)

    universe = ss.load_universe(d3_root)
    quote_frames = ss.load_market_frames(d3_root)
    panels = ss.compute_factor_panels(quote_frames, master_path)
    ranks_long = ss.build_ranks_long(panels, directions)

    grid, weight_lookup = ss.search_grid(pool)
    grid_hash = ss.grid_sha256(grid)
    scores = ss.build_all_composite_scores(ranks_long, weight_lookup)
    targets = ss.build_all_target_weights(grid, scores, universe)
    close = ss.build_close_matrix(universe)

    # Remediation audit (section 20): prove every date that actually entered
    # Strategy Search stayed inside the frozen development/validation window.
    max_input_date = max(universe["asof_date"].max(), universe["execution_date"].max(), close.index.max())
    strict_oos_rows_read = int((universe["asof_date"] > ss.BACKTEST_END).sum() + (universe["execution_date"] > ss.BACKTEST_END).sum())
    oos_audit = {
        "max_strategy_input_date": str(max_input_date.date()),
        "strict_oos_rows_read": strict_oos_rows_read,
        "strategy_oos_untouched": "PASS" if max_input_date <= ss.BACKTEST_END and strict_oos_rows_read == 0 else "FAIL",
    }

    output_dir.mkdir(parents=True, exist_ok=False)
    _write_csv(grid, output_dir / "strategy_search_manifest.csv", ["strategy_id"])

    combinations_rows = [
        {"composite_id": composite_id, "factor_id": factor_id, "weight": weight}
        for composite_id, weights in weight_lookup.items() for factor_id, weight in weights.items()
    ]
    _write_csv(pd.DataFrame(combinations_rows), output_dir / "strategy_factor_combinations.csv", ["composite_id", "factor_id"])

    scoreboard_rows, yearly_rows, equity_rows, drawdown_rows, specs = [], [], [], [], {}
    for grid_row in grid.to_dict("records"):
        spec = ss.build_strategy_spec(grid_row, weight_lookup[grid_row["composite_id"]], directions)
        specs[spec.strategy_id] = spec
        outcome = _run_one_config(spec.strategy_id, targets[spec.strategy_id], close, spec)
        scoreboard_rows.append(outcome["row"])
        yearly_rows.append(outcome["yearly"])
        equity_rows.append(outcome["equity"])
        drawdown_rows.append(outcome["drawdown"])

    scoreboard = pd.DataFrame(scoreboard_rows)
    if len(scoreboard) != ss.EXPECTED_GRID_SIZE:
        raise ValueError(f"DAY3_BLOCKED: scoreboard row count={len(scoreboard)}, expected {ss.EXPECTED_GRID_SIZE}")
    grid_columns = ["strategy_id", "composite_id", "factor_set", "factor_weighting", "top_n", "rebalance_days", "portfolio_weighting"]
    scoreboard = grid.loc[:, grid_columns].merge(scoreboard, on="strategy_id", how="inner", validate="one_to_one")
    gated = ss.apply_gates(scoreboard)
    _write_csv(gated, output_dir / "strategy_backtest_scoreboard.csv", ["strategy_id"])

    performance_columns = ["strategy_id", "config_hash"] + [f"{track}_{key}" for track in ss.COST_TRACKS for key in ("total_return", "annualized_return", "annualized_volatility", "sharpe", "sortino", "max_drawdown", "calmar")]
    _write_csv(gated.loc[:, performance_columns], output_dir / "strategy_performance_metrics.csv", ["strategy_id"])

    execution_columns = ["strategy_id", "rebalance_count", "scheduled_execution_date_count", "actual_order_date_count", "non_rebalance_orders", "execution_gate_pass", "execution_gate_violations"] + [f"{track}_{key}" for track in ss.COST_TRACKS for key in ("turnover", "trade_count")]
    _write_csv(gated.loc[:, execution_columns], output_dir / "strategy_execution_metrics.csv", ["strategy_id"])

    cost_columns = ["strategy_id", "gross_annualized_return", "net_base_annualized_return", "net_stress_annualized_return", "cost_drag", "slippage_drag", "gross_estimated_cost", "net_base_estimated_cost", "net_stress_estimated_cost"]
    _write_csv(gated.loc[:, cost_columns], output_dir / "strategy_cost_analysis.csv", ["strategy_id"])

    temporal = pd.concat(yearly_rows, ignore_index=True).merge(gated.loc[:, ["strategy_id", "positive_year_ratio", "excluding_best_year_return", "single_year_dependent"]], on="strategy_id", how="left")
    _write_csv(temporal, output_dir / "strategy_temporal_stability.csv", ["strategy_id", "year"])
    _write_csv(pd.concat(equity_rows, ignore_index=True), output_dir / "strategy_equity_curves.csv", ["strategy_id", "date"])
    _write_csv(pd.concat(drawdown_rows, ignore_index=True), output_dir / "strategy_drawdowns.csv", ["strategy_id", "date"])

    rejected = gated.loc[~gated["strategy_valid"]].copy()
    _write_csv(rejected, output_dir / "strategy_rejection_log.csv", ["strategy_id"])

    valid = gated.loc[gated["strategy_valid"]].copy()
    ranked = ss.rank_strategies(valid)
    shortlist = ss.select_shortlist(ranked)
    shortlist_spec_rows = [
        {**{f"spec_{key}": value for key, value in specs[row.strategy_id].record().items()}, "strategy_id": row.strategy_id}
        for row in shortlist.itertuples()
    ]
    shortlist = shortlist.merge(pd.DataFrame(shortlist_spec_rows), on="strategy_id", how="left")
    _write_csv(shortlist, output_dir / "strategy_shortlist.csv", ["shortlist_rank"])

    best = {
        "best_net_cagr": float(valid["net_base_annualized_return"].max()) if len(valid) else None,
        "best_sharpe": float(valid["net_base_sharpe"].max()) if len(valid) else None,
        "best_max_drawdown": float(valid["net_base_max_drawdown"].max()) if len(valid) else None,
        "lowest_cost_drag": float(valid["cost_drag"].min()) if len(valid) else None,
    }
    report_text = _build_report(preflight=preflight, grid=grid, gated=gated, valid=valid, shortlist=shortlist, best=best)
    (output_dir / "day3_strategy_search_report.md").write_text(report_text, encoding="utf-8")

    code_files = {
        "core/research/strategy_search.py": ss.__file__,
        "jobs/run_strategy_search.py": __file__,
        "test/test_strategy_search.py": str(Path(__file__).resolve().parents[1] / "test" / "test_strategy_search.py"),
        # Remediation MINOR-3: this file supplies run_vectorbt's slippage/fill
        # semantics and directly affects backtest execution, so its fingerprint
        # must be recorded too.
        "core/research/vectorbt_adapter.py": str(Path(__file__).resolve().parents[1] / "core" / "research" / "vectorbt_adapter.py"),
    }
    artifact_hashes = {path.name: ss.sha256_of(path) for path in sorted(output_dir.glob("*")) if path.is_file()}
    manifest = {
        "change_id": "2026-09-14-add-day3-strategy-search-and-realistic-backtest-v1",
        "run_id": output_dir.name,
        "search_spec_version": "v1",
        "execution_run_version": "v2_remediation" if "v2" in output_dir.name else "v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "status": "success",
        "day3_status": "COMPLETE" if len(valid) >= 3 else "PARTIAL",
        "accepted_factor_pool_version": ss.SOURCE_FACTOR_POOL_VERSION,
        "accepted_factor_ids": list(ss.ACCEPTED_FACTOR_IDS),
        "effective_strategy_directions": preflight["effective_directions"],
        "day2_input_hashes": preflight["day2_input_hashes"],
        "strategy_search_grid_hash": grid_hash,
        "universe_id": ss.UNIVERSE_ID,
        "data_version": "d3_full_20230103_20260728",
        "execution_semantics": "DISCRETE_EXECUTION_DATE_ONLY",
        "execution_lag": "T+1",
        "cost_model": {"fee_rate": Config.FEE_RATE, "tax_rate": Config.TAX_RATE, "vectorbt_fee_approximation": "fee + tax / 2"},
        "slippage_base": ss.SLIPPAGE_BASE,
        "slippage_screening_stress": ss.SLIPPAGE_SCREENING_STRESS,
        "strategy_count": len(grid),
        "valid_strategy_count": int(len(valid)),
        "rejected_strategy_count": int(len(rejected)),
        "shortlist_size": int(len(shortlist)),
        "shortlist_strategy_ids": shortlist["strategy_id"].tolist(),
        "ranking_policy": {"weights": ss.RANKING_WEIGHTS, "shortlist_target": ss.SHORTLIST_TARGET},
        "development_window": {"start": str(ss.BACKTEST_START.date()), "end": str(ss.BACKTEST_END.date())},
        "strategy_oos_status": "NOT_ESTABLISHED",
        "strategy_oos_audit": oos_audit,
        "validity_gate_semantic_parity": "PASS",
        "tier2_factor_blocker": "NOT_ESTABLISHED_DB_UNAVAILABLE",
        "best": best,
        "non_rebalance_order_check": "PASS" if bool((gated["non_rebalance_orders"] == 0).all()) else "FAIL",
        "environment": {"python": platform.python_version(), "numpy": _version("numpy"), "pandas": _version("pandas"), "vectorbt": _version("vectorbt")},
        "git_head": subprocess.run(["git", "rev-parse", "HEAD"], text=True, capture_output=True, check=False).stdout.strip(),
        "code_file_sha256": {name: ss.sha256_of(path) for name, path in code_files.items()},
        "artifact_sha256": artifact_hashes,
        "required_artifacts": list(REQUIRED_ARTIFACTS),
        "known_limitations": [
            "slippage screening only uses a single 10bps stress track; full slippage sensitivity is Robustness scope",
            "STRATEGY_STRICT_OOS_STATUS = NOT_ESTABLISHED: 2026-01-01..2026-07-28 was never read",
            "accepted_factor_pool_v1 excludes Growth/Quality/InstitutionalFlow (Tier2 DB unavailable)",
            "A191_132 is IMPLEMENTATION_APPROXIMATE; strategies containing it carry that risk flag",
        ],
    }
    (output_dir / "run_manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")
    return output_dir


def _build_report(*, preflight: dict, grid: pd.DataFrame, gated: pd.DataFrame, valid: pd.DataFrame, shortlist: pd.DataFrame, best: dict) -> str:
    single = gated.loc[gated["factor_set"].isin(ss.ACCEPTED_FACTOR_IDS)]
    multi = gated.loc[~gated["factor_set"].isin(ss.ACCEPTED_FACTOR_IDS)]
    baseline_b = gated.loc[(gated["factor_set"] == "+".join(ss.ACCEPTED_FACTOR_IDS)) & (gated["factor_weighting"] == "equal")]
    rejected = gated.loc[~gated["strategy_valid"]]
    top_combinations = (
        valid.groupby("composite_id")[["net_base_sharpe", "net_base_annualized_return"]].mean().round(4)
        .sort_values("net_base_sharpe", ascending=False).head(3)
    )
    turnover_by_rebalance = gated.groupby("rebalance_days")[["net_base_turnover", "net_base_annualized_return"]].mean().round(4)
    top_after_cost = valid.sort_values("net_base_annualized_return", ascending=False).head(5).loc[:, ["strategy_id", "net_base_annualized_return", "cost_drag"]]
    cost_rejected = rejected.loc[rejected["rejection_reason"].str.contains("COST_GATE_FAILED", na=False)].sort_values("gross_turnover", ascending=False).head(3).loc[:, ["strategy_id", "gross_turnover", "gross_annualized_return", "net_base_annualized_return"]]
    risk_rejected = rejected.loc[rejected["rejection_reason"].str.contains("RETURN_RISK_GATE_FAILED", na=False)].loc[:, ["strategy_id", "net_base_max_drawdown", "net_base_annualized_return"]]
    single_year_dependent = gated.loc[gated["single_year_dependent"]].sort_values("excluding_best_year_return").head(5).loc[:, ["strategy_id", "excluding_best_year_return", "net_base_annualized_return", "strategy_valid"]]
    lines = [
        "# Day 3 Strategy Search Report",
        "",
        f"- FACTOR_POOL_VERSION = {preflight['factor_pool_version']}",
        f"- FACTOR_POOL_SIZE = {preflight['factor_pool_size']}",
        "- FACTOR_POOL_V1_INCOMPLETE_FAMILIES = Growth, Quality, InstitutionalFlow",
        "- TIER2_FACTOR_BLOCKER = NOT_ESTABLISHED_DB_UNAVAILABLE",
        "- Claim scope: best Strategy Candidates under accepted_factor_pool_v1 only, not the full factor universe.",
        "",
        "## 1. Configs generated",
        f"- TOTAL_STRATEGY_CONFIGS = {len(grid)} (25 composite definitions x 3 TopN x 2 rebalance x 2 portfolio weighting)",
        "",
        "## 2. Single-factor baseline vs multi-factor",
        f"- Single-factor configs: {len(single)}, mean net_base CAGR = {single['net_base_annualized_return'].mean():.4f}, mean net_base Sharpe = {single['net_base_sharpe'].mean():.4f}",
        f"- Multi-factor configs: {len(multi)}, mean net_base CAGR = {multi['net_base_annualized_return'].mean():.4f}, mean net_base Sharpe = {multi['net_base_sharpe'].mean():.4f}",
        f"- Baseline B (full 4-factor EQUAL_FACTOR_WEIGHT, {len(baseline_b)} TopN/rebalance/weighting variants): mean net_base CAGR = {baseline_b['net_base_annualized_return'].mean():.4f}",
        "",
        "## 3. Factor weighting comparison (mean net_base Sharpe by method)",
        gated.groupby("factor_weighting")["net_base_sharpe"].mean().round(4).to_string(),
        "",
        "## 4. Top N / rebalance comparison (mean net_base CAGR)",
        gated.groupby(["top_n", "rebalance_days"])["net_base_annualized_return"].mean().round(4).to_string(),
        "",
        "## 5. Cost impact",
        f"- Mean cost_drag across all {len(grid)} configs = {gated['cost_drag'].mean():.4f}",
        f"- Configs failing the cost gate = {int((~gated['gate_cost_pass']).sum())}",
        "",
        "## 6. Gate / rejection summary",
        gated["rejection_reason"].value_counts(dropna=False).to_string(),
        "",
        "## 7. Temporal stability",
        f"- Configs failing the stability gate = {int((~gated['gate_stability_pass']).sum())}",
        f"- Configs flagged single_year_dependent = {int(gated['single_year_dependent'].sum())}",
        "",
        "## 8. Shortlist",
        f"- VALID_STRATEGIES = {len(valid)}, SHORTLIST_SIZE = {len(shortlist)}",
        shortlist.loc[:, ["shortlist_rank", "strategy_id", "net_base_annualized_return", "net_base_sharpe", "net_base_max_drawdown", "cost_drag", "strategy_ranking_score", "implementation_risk"]].to_string(index=False) if len(shortlist) else "(none)",
        "",
        "## 9. Best headline metrics (valid strategies only)",
        f"- BEST_NET_CAGR = {best['best_net_cagr']}",
        f"- BEST_SHARPE = {best['best_sharpe']}",
        f"- BEST_MAX_DRAWDOWN = {best['best_max_drawdown']}",
        f"- LOWEST_COST_DRAG = {best['lowest_cost_drag']}",
        "",
        "## 10. Strategy-level Strict OOS",
        "- STRATEGY_STRICT_OOS_STATUS = NOT_ESTABLISHED. The 2026-01-01..2026-07-28 window was never read by this change; it is reserved untouched for the next Robustness / Strategy-level Strict OOS change.",
        "",
        "## 11. Strategy-level detail (Q4 value, Q7 rebalance/turnover, Q8/Q9 cost impact, Q10 MDD, Q11/Q12 temporal)",
        "- Q4: top 3 composite_id among gate-eligible strategies by mean net_base Sharpe:",
        top_combinations.to_string() if len(top_combinations) else "(none)",
        f"- Q7: mean net_base turnover / CAGR by rebalance_days (all {len(grid)} configs):",
        turnover_by_rebalance.to_string(),
        "- Q8: top 5 gate-eligible strategies by net_base CAGR (still high return after canonical cost):",
        top_after_cost.to_string(index=False) if len(top_after_cost) else "(none)",
        "- Q9: sample of configs rejected by the cost gate (highest gross turnover among cost-gate failures):",
        cost_rejected.to_string(index=False) if len(cost_rejected) else "(none)",
        "- Q10: configs rejected by the return/risk gate (catastrophic drawdown or invalid Sharpe):",
        risk_rejected.to_string(index=False) if len(risk_rejected) else "(none)",
        "- Q11/Q12: most single-year-dependent configs (lowest excluding-best-year return; strategy_valid shows whether it still passed all gates):",
        single_year_dependent.to_string(index=False) if len(single_year_dependent) else "(none)",
    ]
    return "\n".join(str(line) for line in lines) + "\n"


def verify_repro(canonical_dir: Path, repro_dir: Path) -> dict[str, object]:
    canonical = {path.relative_to(canonical_dir).as_posix(): ss.sha256_of(path) for path in canonical_dir.rglob("*.csv")}
    repro = {path.relative_to(repro_dir).as_posix(): ss.sha256_of(path) for path in repro_dir.rglob("*.csv")}
    paths = sorted(set(canonical) | set(repro))
    mismatches = [path for path in paths if canonical.get(path) != repro.get(path)]
    report = {"canonical_run_id": canonical_dir.name, "repro_run_id": repro_dir.name, "artifact_count": len(paths), "exact_match_count": len(paths) - len(mismatches), "mismatch_count": len(mismatches), "mismatching_artifacts": mismatches}
    (repro_dir / "reproducibility.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    if mismatches:
        raise ValueError(f"reproducibility mismatch: {', '.join(mismatches)}")
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--day2-dir", type=Path, default=DEFAULT_DAY2_DIR)
    parser.add_argument("--d3-root", type=Path, default=DEFAULT_D3_ROOT)
    parser.add_argument("--master-path", type=Path, default=DEFAULT_MASTER_PATH)
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    parser.add_argument("--run-id", default="strategy_search_20260914_v1")
    parser.add_argument("--preflight", action="store_true")
    parser.add_argument("--repro-against", type=Path)
    args = parser.parse_args(argv)

    if args.preflight:
        preflight = ss.preflight_accepted_pool(args.day2_dir)
        print(json.dumps({**preflight, "frozen_parameters": ss.frozen_parameters()}, indent=2, default=str))
        return 0

    output_dir = args.output_root / args.run_id
    output_dir = run(day2_dir=args.day2_dir, d3_root=args.d3_root, master_path=args.master_path, output_dir=output_dir)
    if args.repro_against:
        verify_repro(args.repro_against, output_dir)
    print(f"Day 3 strategy search complete: output={output_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
