"""Day 3 canonical portfolio-validation CLI.

Consumes the frozen Day 2 D5 shortlist, replays it through the existing
Custom Engine components, compares it against VectorBT, cross-checks metrics
with Empyrical, attributes alpha/beta with Statsmodels (HAC), runs temporal
stability and cost-stress analysis, and freezes a Day 3 run manifest.

This is validation and diagnostics only: it must not rerun the D5 48-config
search, change the frozen shortlist, or reopen D4's factor evaluation.
"""
import argparse
import hashlib
import json
import platform
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import empyrical
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pandas as pd
import pyfolio as pf

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from config.settings import Config
from core.backtest.costs import CostModel
from core.backtest.research_adapter import replay_config
from core.research.attribution import BenchmarkUnavailableError, load_benchmark_returns, run_attribution
from core.research.portfolio_validation import (
    COST_SCENARIOS,
    assess_strict_oos_feasibility,
    build_validation_scoreboard,
    check_structural_parity,
    classify_validation_status,
    compare_engines,
    empyrical_crosscheck,
    load_price_matrix,
    overall_engine_status,
    run_cost_sensitivity,
    sha256_of,
    temporal_stability,
    tree_sha256,
    verify_day2_run,
)
from core.research.vectorbt_adapter import run_vectorbt

DAY2_ROOT = Path("outputs/portfolio_research")
HANDOFF_ROOT = Path("artifacts/d5_handoff")
D3_ACCEPTED_RUN = Path("outputs/factor_research/d4_acceptance_20260811_v8")


def _version(name: str) -> str | None:
    try:
        from importlib.metadata import version
        return version(name)
    except Exception:
        return None


def _git_commit() -> str:
    return subprocess.run(["git", "rev-parse", "HEAD"], text=True, capture_output=True, check=False).stdout.strip()


def _metrics_to_frame(performance_metrics) -> pd.DataFrame:
    return pd.DataFrame([{"metric": name, "value": metric.value, "reason": metric.reason} for name, metric in performance_metrics.values.items()])


def _custom_row(performance_metrics, transactions: pd.DataFrame, portfolio_value: pd.Series) -> dict:
    row = {name: performance_metrics.get(name).value for name in ("total_return", "annualized_return", "annualized_volatility", "sharpe", "sortino", "max_drawdown", "calmar", "turnover", "trade_count")}
    row["estimated_cost"] = float(transactions["fee"].sum()) if not transactions.empty else 0.0
    row["ending_value"] = float(portfolio_value.iloc[-1]) if len(portfolio_value) else float("nan")
    return {key: (value if value is not None else float("nan")) for key, value in row.items()}


def _write_pyfolio_diagnostics(config_id: str, returns: pd.Series, benchmark_returns, output_dir: Path) -> list[str]:
    config_dir = output_dir / config_id
    config_dir.mkdir(parents=True, exist_ok=True)
    skipped = []
    try:
        pf.timeseries.perf_stats(returns).rename("value").rename_axis("metric").to_csv(config_dir / "perf_stats.csv", header=True)
    except Exception as caught:
        skipped.append(f"perf_stats: {caught}")
    try:
        pf.timeseries.gen_drawdown_table(returns, top=5).to_csv(config_dir / "drawdown_periods.csv")
    except Exception as caught:
        skipped.append(f"drawdown_periods: {caught}")
    try:
        pf.timeseries.aggregate_returns(returns, "monthly").rename("monthly_return").to_csv(config_dir / "monthly_returns.csv", header=True)
    except Exception as caught:
        skipped.append(f"monthly_returns: {caught}")
    try:
        empyrical.roll_sharpe_ratio(returns, window=63).rename("rolling_sharpe").to_csv(config_dir / "rolling_sharpe.csv", header=True)
    except Exception as caught:
        skipped.append(f"rolling_sharpe: {caught}")
    try:
        (returns.rolling(63).std() * (252 ** 0.5)).rename("rolling_volatility").to_csv(config_dir / "rolling_volatility.csv", header=True)
    except Exception as caught:
        skipped.append(f"rolling_volatility: {caught}")
    if benchmark_returns is not None:
        try:
            aligned = pd.concat([returns.rename("strategy"), benchmark_returns.rename("benchmark")], axis=1).dropna()
            pd.DataFrame({
                "strategy_cumulative": (1 + aligned["strategy"]).cumprod() - 1,
                "benchmark_cumulative": (1 + aligned["benchmark"]).cumprod() - 1,
            }).to_csv(config_dir / "benchmark_comparison.csv")
        except Exception as caught:
            skipped.append(f"benchmark_comparison: {caught}")
    else:
        skipped.append("benchmark_comparison: BENCHMARK_UNAVAILABLE")
    try:
        cumulative = (1 + returns).cumprod()
        drawdown = cumulative / cumulative.cummax() - 1
        figure, axes = plt.subplots(2, 1, figsize=(10, 6))
        axes[0].plot(cumulative.index, cumulative.values - 1)
        axes[0].set_title(f"{config_id} cumulative return")
        axes[1].fill_between(drawdown.index, drawdown.values, 0)
        axes[1].set_title(f"{config_id} underwater")
        figure.tight_layout()
        figure.savefig(config_dir / "cumulative_and_drawdown.png", dpi=120)
        plt.close(figure)
    except Exception as caught:
        skipped.append(f"cumulative_and_drawdown.png: {caught}")
    if skipped:
        (config_dir / "SKIPPED.txt").write_text("\n".join(skipped), encoding="utf-8")
    return skipped


def _artifact_hashes(output_dir: Path) -> dict[str, str]:
    """Freeze every deterministic run artifact; PNG bytes are presentation-only."""
    return {
        path.relative_to(output_dir).as_posix(): sha256_of(path)
        for path in sorted(output_dir.rglob("*"))
        if path.is_file() and path.name != "run_manifest.json" and path.suffix.lower() != ".png"
    }


def _write_stopped_run(output_dir: Path, reason: str) -> Path:
    output_dir.mkdir(parents=True, exist_ok=False)
    (output_dir / "FINAL_RESEARCH_REPORT.md").write_text(
        "# Day 3 Final Research Report\n\n"
        "FINAL_STATUS = STOPPED\n"
        "RESEARCH_PIPELINE = NOT_COMPLETE\n"
        "READY_FOR_NEXT_STAGE = NO\n"
        "READY_FOR_LIVE_TRADING = NO\n\n"
        f"BENCHMARK_UNAVAILABLE: {reason}\n",
        encoding="utf-8",
    )
    manifest = {
        "schema_version": 1,
        "run_id": output_dir.name,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "final_status": "STOPPED",
        "stop_reason": "BENCHMARK_UNAVAILABLE",
        "benchmark_provenance": {"status": "BENCHMARK_UNAVAILABLE", "reason": reason},
        "artifact_sha256": _artifact_hashes(output_dir),
    }
    (output_dir / "run_manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return output_dir


def _compare_reproducibility(reference_dir: Path, output_dir: Path) -> dict[str, int | str]:
    reference = json.loads((reference_dir / "run_manifest.json").read_text(encoding="utf-8"))["artifact_sha256"]
    actual = _artifact_hashes(output_dir)
    keys = sorted(set(reference) | set(actual))
    matches = sum(reference.get(key) == actual.get(key) for key in keys)
    return {
        "status": "PASS" if matches == len(keys) else "FAIL",
        "compared_artifact_count": len(keys),
        "exact_match_count": matches,
        "mismatch_count": len(keys) - matches,
    }


def run(*, day2_dir: Path, dataset_path: Path, raw_cache_dir: Path, output_dir: Path, initial_capital: float = 1_000_000.0) -> Path:
    try:
        benchmark_returns, benchmark_provenance = load_benchmark_returns(raw_cache_dir, Config.MARKET_SYMBOL)
    except BenchmarkUnavailableError as caught:
        return _write_stopped_run(output_dir, str(caught))

    day2_manifest = verify_day2_run(day2_dir)
    shortlist_ids = day2_manifest["shortlist_config_ids"]
    candidate_ids = day2_manifest["candidate_ids"]
    handoff_path = HANDOFF_ROOT / day2_manifest["provenance"]["source_handoff_id"]

    handoff_hash_before = tree_sha256(handoff_path)
    dataset_hash_before = tree_sha256(dataset_path)
    if handoff_hash_before != day2_manifest["provenance"]["source_handoff_sha256"]:
        raise ValueError("STOP: frozen D4 handoff hash differs from the Day 2 manifest")
    if dataset_hash_before != day2_manifest["provenance"]["d3_dataset_sha256"]:
        raise ValueError("STOP: D3 accepted dataset hash differs from the Day 2 manifest")
    d3_run_hash_before = tree_sha256(D3_ACCEPTED_RUN) if D3_ACCEPTED_RUN.is_dir() else None

    price_matrix = load_price_matrix(dataset_path, candidate_ids)
    cost_model = CostModel(Config.FEE_RATE, Config.TAX_RATE, 20.0)

    output_dir.mkdir(parents=True, exist_ok=False)
    custom_root, pyfolio_root, statsmodels_root = output_dir / "custom_engine", output_dir / "pyfolio", output_dir / "statsmodels"
    for path in (custom_root, pyfolio_root, statsmodels_root):
        path.mkdir()
    benchmark_root = output_dir / "benchmark"
    benchmark_root.mkdir()
    benchmark_returns.rename_axis("date").to_csv(benchmark_root / "returns.csv", header=["daily_return"])
    (benchmark_root / "provenance.json").write_text(json.dumps(benchmark_provenance, indent=2), encoding="utf-8")

    day2_scoreboard = pd.read_csv(day2_dir / "portfolio_scoreboard.csv").set_index("config_id")
    engine_comparisons, crosschecks, temporal_rows, cost_rows, attribution_rows, parity_rows, scoreboard_rows = [], [], [], [], [], [], []

    strict_oos_feasible, strict_oos_reason = assess_strict_oos_feasibility(handoff_path / "candidate_factors.csv")

    for config_id in shortlist_ids:
        target_path = day2_dir / "target_weights" / f"{config_id}.csv"
        targets = pd.read_csv(target_path, parse_dates=["asof_date", "execution_date"])
        replay = replay_config(targets, price_matrix, cost_model, config_id=config_id, source_run_id=day2_manifest["run_id"], initial_capital=initial_capital, slippage=0.0)
        parity_rows.append(check_structural_parity(replay.consumed_targets, target_path))
        config_dir = custom_root / config_id
        config_dir.mkdir(parents=True)
        replay.daily_returns.rename_axis("date").to_csv(config_dir / "daily_returns.csv", header=["daily_return"])
        replay.portfolio_value.rename_axis("date").to_csv(config_dir / "portfolio_value.csv", header=["portfolio_value"])
        replay.positions.to_csv(config_dir / "positions.csv", index=False)
        replay.transactions.to_csv(config_dir / "transactions.csv", index=False)
        replay.execution_log.to_csv(config_dir / "execution_log.csv", index=False)
        _metrics_to_frame(replay.performance_metrics).to_csv(config_dir / "performance_metrics.csv", index=False)
        (config_dir / "provenance.json").write_text(json.dumps({"config_id": config_id, "source_day2_run_id": day2_manifest["run_id"]}, indent=2), encoding="utf-8")

        # Regenerate this ONE frozen config's VectorBT run (same target weights, same
        # prices) purely to obtain the daily-return series Day 2 did not persist;
        # this reruns nothing about the 48-config search or shortlist selection.
        vbt_result = run_vectorbt(price_matrix, targets, fee_rate=Config.FEE_RATE, tax_rate=Config.TAX_RATE, initial_capital=initial_capital)
        vbt_row, custom_row = day2_scoreboard.loc[config_id].to_dict(), _custom_row(replay.performance_metrics, replay.transactions, replay.portfolio_value)
        comparison = compare_engines(config_id, vbt_row, custom_row, vbt_result["returns"], replay.daily_returns)
        engine_comparisons.append(comparison)
        engine_status = overall_engine_status(comparison)

        crosscheck = empyrical_crosscheck(config_id, replay.daily_returns, replay.performance_metrics)
        crosschecks.append(crosscheck)

        temporal = temporal_stability(config_id, replay.daily_returns)
        temporal_rows.append(temporal)

        cost_sensitivity = run_cost_sensitivity(config_id, targets, price_matrix, cost_model, initial_capital=initial_capital)
        cost_rows.append(cost_sensitivity)

        attribution = run_attribution(config_id, replay.daily_returns, benchmark_returns)
        attribution_rows.append(attribution)
        attribution_dir = statsmodels_root / config_id
        attribution_dir.mkdir(parents=True)
        pd.DataFrame([attribution]).to_csv(attribution_dir / "regression_attribution.csv", index=False)
        (attribution_dir / "regression_summary.txt").write_text(
            f"config_id={config_id}\nalpha={attribution['alpha']:.6f} (HAC p={attribution['alpha_p_hac']:.4f})\n"
            f"beta={attribution['beta']:.4f} (HAC p={attribution['beta_p_hac']:.4f})\n"
            f"r_squared={attribution['r_squared']:.4f} n_obs={attribution['n_obs']} hac_maxlags={attribution['hac_maxlags']}\n"
            "p<0.05 alone is not proof of a production alpha; see FINAL_RESEARCH_REPORT.md for economic-magnitude and multi-config context.\n",
            encoding="utf-8",
        )
        hac_alpha, hac_p_value = attribution["alpha"], attribution["alpha_p_hac"]

        _write_pyfolio_diagnostics(config_id, replay.daily_returns, benchmark_returns, pyfolio_root)

        status = classify_validation_status(
            engine_status=engine_status,
            temporal_segment_sharpes=[value for value in temporal["sharpe"].tolist() if value is not None],
            cost_stress_total_returns=cost_sensitivity["total_return"].tolist(),
            hac_alpha=hac_alpha, hac_p_value=hac_p_value,
        )
        scoreboard_rows.append({
            "config_id": config_id,
            "vectorbt_sharpe": vbt_row["sharpe"], "custom_sharpe": custom_row["sharpe"],
            "engine_comparison_status": engine_status,
            "target_intent_parity_status": parity_rows[-1].iloc[0]["target_intent_parity_status"],
            "unfilled_count": int(replay.execution_log["status"].eq("UNFILLED").sum()) if not replay.execution_log.empty else 0,
            "daily_return_correlation": comparison.loc[comparison["metric"].eq("daily_return_correlation"), "custom_value"].iloc[0],
            "empyrical_status": "PASS" if not crosscheck["status"].eq("FAIL").any() else "FAIL",
            "custom_total_return": custom_row["total_return"],
            "custom_max_drawdown": custom_row["max_drawdown"],
            "temporal_late_sharpe": temporal.loc[temporal["segment"] == "late_20pct", "sharpe"].iloc[0],
            "strict_oos_status": "STRICT_OOS_NOT_ESTABLISHED" if not strict_oos_feasible else "ESTABLISHED",
            "cost_base_total_return": cost_sensitivity.loc[cost_sensitivity["scenario"] == "BASE", "total_return"].iloc[0],
            "cost_stress_total_return": cost_sensitivity.loc[cost_sensitivity["scenario"] == "STRESS", "total_return"].iloc[0],
            "cost_pessimistic_total_return": cost_sensitivity.loc[cost_sensitivity["scenario"] == "PESSIMISTIC", "total_return"].iloc[0],
            "beta": attribution["beta"], "hac_t_stat": attribution["alpha_t_hac"],
            "hac_alpha": hac_alpha, "hac_p_value": hac_p_value,
            "temporal_stability_status": "PASS" if (temporal["sharpe"] >= 0).all() else "WEAK",
            "validation_status": status,
        })

    engine_comparison_frame = pd.concat(engine_comparisons, ignore_index=True)
    crosscheck_frame = pd.concat(crosschecks, ignore_index=True)
    temporal_frame = pd.concat(temporal_rows, ignore_index=True)
    cost_frame = pd.concat(cost_rows, ignore_index=True)
    scoreboard_frame = build_validation_scoreboard(scoreboard_rows, shortlist_ids)

    engine_comparison_frame.to_csv(output_dir / "vectorbt_vs_custom.csv", index=False)
    crosscheck_frame.to_csv(output_dir / "metric_crosscheck.csv", index=False)
    temporal_frame.to_csv(output_dir / "temporal_stability.csv", index=False)
    cost_frame.to_csv(output_dir / "cost_sensitivity.csv", index=False)
    scoreboard_frame.to_csv(output_dir / "validation_scoreboard.csv", index=False)
    pd.concat(parity_rows, ignore_index=True).to_csv(output_dir / "structural_parity.csv", index=False)
    pd.DataFrame([{
        "status": "STRICT_OOS_NOT_ESTABLISHED" if not strict_oos_feasible else "ESTABLISHED",
        "reason": strict_oos_reason,
    }]).to_csv(output_dir / "is_oos_comparison.csv", index=False)
    if attribution_rows:
        pd.DataFrame(attribution_rows).to_csv(output_dir / "statsmodels" / "attribution_summary.csv", index=False)

    _write_final_report(output_dir, day2_manifest, scoreboard_frame, strict_oos_feasible, strict_oos_reason, benchmark_provenance, final_status="COMPLETE")

    manifest = {
        "schema_version": 1, "run_id": output_dir.name, "generated_at": datetime.now(timezone.utc).isoformat(), "final_status": "COMPLETE",
        "provenance": {
            "source_day2_run_id": day2_manifest["run_id"], "source_day2_manifest_sha256": sha256_of(day2_dir / "run_manifest.json"),
            "source_day2_shortlist_sha256": sha256_of(day2_dir / "shortlisted_configs.csv"),
            "source_handoff_id": day2_manifest["provenance"]["source_handoff_id"], "git_commit": _git_commit(),
        },
        "shortlisted_config_ids": shortlist_ids,
        "custom_engine_settings": {"initial_capital": initial_capital, "execution_lag": "T+1", "slippage_base": 0.0, "execution_price": "genuine execution-date price only", "mark_price": "last known price for valuation only"},
        "cost_model_settings": {"fee_rate": Config.FEE_RATE, "tax_rate": Config.TAX_RATE, "minimum_fee": 20.0},
        "benchmark_provenance": benchmark_provenance,
        "environment": {"python": platform.python_version(), "numpy": _version("numpy"), "pandas": _version("pandas"), "vectorbt": _version("vectorbt"), "numba": _version("numba"), "empyrical": _version("empyrical-reloaded") or _version("empyrical"), "pyfolio": _version("pyfolio-reloaded") or _version("pyfolio"), "statsmodels": _version("statsmodels")},
        "policies": {
            "engine_comparison": "structural exact match (Level A) + tolerance-classified metrics (Level B); see design.md decision B",
            "metric_crosscheck": "annualization=252d, risk_free=0.0; see design.md decision C",
            "hac": "Newey-West maxlags = floor(4*(n/100)**(2/9)); see design.md decision E",
            "temporal_validation": "60/20/20 chronological split labeled temporal stability, not OOS; see design.md decision F",
            "cost_stress": "BASE/STRESS/PESSIMISTIC slippage 0/0.001/0.002 on the frozen CostModel; see design.md decision G",
            "sizing": "integer shares reduced until shares * execution_price + CostModel.buy_cost(notional) <= cash",
        },
        "strict_oos_status": "STRICT_OOS_NOT_ESTABLISHED" if not strict_oos_feasible else "ESTABLISHED",
        "strict_oos_reason": strict_oos_reason,
        "upstream_hashes": {"handoff": handoff_hash_before, "d3_dataset": dataset_hash_before, "d4_accepted_run": d3_run_hash_before},
        "unfilled": {"per_config": dict(zip(scoreboard_frame["config_id"], scoreboard_frame["unfilled_count"])), "total": int(scoreboard_frame["unfilled_count"].sum())},
        "artifact_sha256": _artifact_hashes(output_dir),
        "known_limitations": [
            "VectorBT remains a research approximation; Custom Engine replay uses full liquidate-and-rebuild rebalancing (declared model difference)",
            "benchmark is a single-stock proxy (2330), not a market-weighted index",
            "momentum_12_1 redundancy remains UNKNOWN",
            "no final production-readiness claim; READY_FOR_LIVE_TRADING = NO",
        ],
    }
    reference_dir = output_dir.parent / output_dir.name.replace("_repro", "_v1") if output_dir.name.endswith("_repro") else None
    if reference_dir and reference_dir.is_dir():
        manifest["reproducibility"] = _compare_reproducibility(reference_dir, output_dir)
        if manifest["reproducibility"]["status"] == "FAIL":
            manifest["final_status"] = "STOPPED"
    (output_dir / "run_manifest.json").write_text(json.dumps(manifest, indent=2, default=str), encoding="utf-8")

    if manifest.get("reproducibility", {}).get("status") == "FAIL":
        raise ValueError("STOP: reproducibility comparison found numeric artifact mismatches")

    handoff_hash_after, dataset_hash_after = tree_sha256(handoff_path), tree_sha256(dataset_path)
    if handoff_hash_after != handoff_hash_before or dataset_hash_after != dataset_hash_before:
        raise ValueError("STOP: upstream frozen artifacts changed during the Day 3 run")

    return output_dir


def _write_final_report(output_dir, day2_manifest, scoreboard, strict_oos_feasible, strict_oos_reason, benchmark_provenance, *, final_status: str) -> None:
    lines = [
        "# Day 3 Final Research Report",
        "",
        f"Source Day 2 run: `{day2_manifest['run_id']}`",
        f"Frozen shortlist ({len(day2_manifest['shortlist_config_ids'])} configs): {', '.join(day2_manifest['shortlist_config_ids'])}",
        f"FINAL_STATUS = {final_status}",
        "",
        "## Validation scoreboard",
        "",
        "```\n" + scoreboard.to_string(index=False) + "\n```",
        "",
        "## Strict OOS status",
        "",
        f"{'STRICT_OOS_NOT_ESTABLISHED' if not strict_oos_feasible else 'ESTABLISHED'}: {strict_oos_reason}",
        "",
        "## Benchmark provenance",
        "",
        json.dumps(benchmark_provenance, indent=2, default=str),
        "",
        "## Statistical significance vs economic magnitude",
        "",
        "A HAC p-value below 0.05 is evidence of a non-zero alpha under the declared inference "
        "policy; it is not proof of a durable production alpha. All five frozen shortlisted "
        "configs arose from Day 2's prior research selection, so multiple-comparison context "
        "applies. Robustness classification also requires positive temporal-stability Sharpe in "
        "every segment and a positive total return under every cost-stress scenario.",
        "",
        "## Research readiness vs live readiness",
        "",
        "RESEARCH_PIPELINE = COMPLETE; READY_FOR_NEXT_STAGE = YES (paper trading / production "
        "engineering review). READY_FOR_LIVE_TRADING = NO — live readiness requires a separate "
        "production stage (order routing, real-time data, monitoring, risk controls) not built here.",
    ]
    (output_dir / "FINAL_RESEARCH_REPORT.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-id", default=datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ_d6d7"))
    parser.add_argument("--day2-run-id", default="d5_portfolio_research_20260814_v1")
    parser.add_argument("--day2-root", type=Path, default=DAY2_ROOT)
    parser.add_argument("--dataset", type=Path, default=Path("artifacts/factors/d3_full_20230103_20260728/research_dataset"))
    parser.add_argument("--raw-cache", type=Path, default=Path("artifacts/factors/d3_full_20230103_20260728/_raw/twse_rwd"))
    parser.add_argument("--output-root", type=Path, default=Path("outputs/portfolio_validation"))
    parser.add_argument("--initial-capital", type=float, default=1_000_000.0)
    args = parser.parse_args(argv)
    run(
        day2_dir=args.day2_root / args.day2_run_id,
        dataset_path=args.dataset,
        raw_cache_dir=args.raw_cache,
        output_dir=args.output_root / args.run_id,
        initial_capital=args.initial_capital,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
