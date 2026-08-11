"""CLI for reproducible D4 factor-quality evaluation."""

import argparse
import hashlib
import json
import sys
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.research.factor_charts import write_charts
from core.research.factor_evaluation import (
    EvaluationPolicy, annual_stability, build_scoreboard, compute_annual_results, compute_daily_rank_ic,
    compute_factor_correlation, compute_rank_autocorrelation, compute_top_n_retention, evaluate_horizons,
    factor_status, load_d3_dataset, policy_config, select_best_horizons, summarize_ic, summarize_quantiles,
)
from core.research.factor_report import write_evaluation_artifacts
from core.research.quantile_analysis import compute_quantile_returns


def _csv_values(value: str | None, cast=str) -> tuple:
    return tuple(cast(item) for item in value.split(",") if item) if value else ()


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--eval-run-id")
    parser.add_argument("--output-root", default="outputs/factor_research")
    parser.add_argument("--factors")
    parser.add_argument("--horizons")
    parser.add_argument("--quantiles", type=int)
    parser.add_argument("--top-n", type=int)
    parser.add_argument("--rolling-ic-window", type=int)
    parser.add_argument("--min-ic-assets", type=int)
    parser.add_argument("--min-quantile-assets", type=int)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    evaluation_run_id = args.eval_run_id or f"eval_{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}_{args.run_id}"
    output_dir = Path(args.output_root) / evaluation_run_id
    if output_dir.exists():
        print(f"evaluation output already exists: {output_dir}", file=sys.stderr)
        return 1
    policy = EvaluationPolicy()
    overrides = {"quantile_count": args.quantiles, "top_n": args.top_n, "rolling_ic_window": args.rolling_ic_window, "min_ic_assets": args.min_ic_assets, "min_quantile_assets": args.min_quantile_assets}
    policy = replace(policy, **{key: value for key, value in overrides.items() if value is not None})
    factors = _csv_values(args.factors)
    horizons = _csv_values(args.horizons, int) or (1, 5, 10, 20, 60)
    try:
        dataset, source_manifest = load_d3_dataset(Path("artifacts") / "factors" / args.run_id, factors=factors or None)
    except ValueError as caught:
        print(str(caught), file=sys.stderr)
        return 1
    daily_ic = compute_daily_rank_ic(dataset, horizons=horizons, policy=policy)
    daily_ic = daily_ic.sort_values(["factor_id", "horizon", "asof_date"])
    daily_ic["raw_rolling_ic"] = daily_ic.groupby(["factor_id", "horizon"])["raw_ic"].transform(lambda values: values.rolling(policy.rolling_ic_window, min_periods=policy.rolling_ic_window).mean())
    daily_ic["aligned_rolling_ic"] = daily_ic.groupby(["factor_id", "horizon"])["aligned_ic"].transform(lambda values: values.rolling(policy.rolling_ic_window, min_periods=policy.rolling_ic_window).mean())
    ic_summary = summarize_ic(daily_ic)
    quantiles = compute_quantile_returns(dataset, horizons=horizons, policy=policy)
    quantile_summary = summarize_quantiles(quantiles["summary"])
    annual = compute_annual_results(daily_ic)
    if not quantiles["summary"].empty:
        annual_quantiles = quantiles["summary"].copy()
        annual_quantiles["year"] = pd.to_datetime(annual_quantiles["asof_date"]).dt.year
        annual_quantiles = annual_quantiles.groupby(["factor_id", "horizon", "year"], as_index=False).agg(
            q1_return=("raw_q1_return", "mean"), q5_return=("raw_q5_return", "mean"), q5_minus_q1=("raw_q5_minus_q1", "mean"),
            aligned_q1_return=("aligned_q1_return", "mean"), aligned_q5_return=("aligned_q5_return", "mean"), aligned_long_short_spread=("aligned_long_short_spread", "mean"),
        )
        annual = annual.merge(annual_quantiles, on=["factor_id", "horizon", "year"], how="left")
    stability = annual_stability(annual, policy=policy)
    persistence = compute_rank_autocorrelation(dataset)
    retention = compute_top_n_retention(dataset, policy=policy)
    correlation = compute_factor_correlation(dataset)
    horizon_results = evaluate_horizons(ic_summary, quantile_summary, stability, retention, policy=policy)
    best = select_best_horizons(horizon_results)
    statuses = factor_status(horizon_results, best, policy=policy)
    scoreboard = build_scoreboard(statuses, horizon_results, correlation)
    if not persistence.empty:
        aggregate = persistence.groupby("factor_id")["rank_autocorrelation"].mean()
        scoreboard["rank_autocorrelation"] = scoreboard["factor_id"].map(aggregate)
    if not retention.empty:
        aggregate = retention.groupby("factor_id").agg(top_n_retention=("top_n_retention", "mean"), turnover=("equal_weight_turnover", "mean"))
        scoreboard = scoreboard.drop(columns=[column for column in ("top_n_retention", "turnover") if column in scoreboard]).merge(aggregate, on="factor_id", how="left")
    coverage = daily_ic.groupby(["factor_id", "horizon"], as_index=False).agg(valid_ic_days=("raw_ic", lambda values: int(values.notna().sum())), total_days=("raw_ic", "size"), average_effective_assets=("effective_asset_count", "mean"))
    coverage["average_coverage"] = coverage["valid_ic_days"] / coverage["total_days"]
    tables = {
        "ic_timeseries": daily_ic, "ic_summary": ic_summary, "quantile_returns": quantiles["returns"],
        "quantile_timeseries": quantiles["summary"], "factor_decay": ic_summary, "rank_autocorrelation": persistence,
        "top_n_retention": retention, "turnover": retention, "factor_correlation": correlation,
        "annual_results": annual, "coverage": coverage,
    }
    config = policy_config(policy)
    manifest = {
        "evaluation_run_id": evaluation_run_id, "d3_source_run_id": args.run_id, "evaluation_policy_name": "d4_mvp_v1",
        "policy_is_research_heuristic": True, "evaluation_config": config,
        "evaluation_config_hash": hashlib.sha256(json.dumps(config, sort_keys=True).encode()).hexdigest(),
        "source_dataset_identity": {"run_id": args.run_id, "manifest_generated_at": source_manifest.get("generated_at")},
        "source_dataset_hash": hashlib.sha256(json.dumps(source_manifest, sort_keys=True, default=str).encode()).hexdigest(),
        "requested_factors": list(factors or sorted(dataset.factor_id.unique())), "evaluated_factors": sorted(dataset.factor_id.unique()),
        "horizons": list(horizons), "quantile_count": policy.quantile_count, "top_n": policy.top_n,
        "rolling_ic_window": policy.rolling_ic_window, "start_date": str(dataset.asof_date.min()), "end_date": str(dataset.asof_date.max()),
        "generated_at_utc": datetime.now(timezone.utc).isoformat(), "row_counts": {name: len(frame) for name, frame in tables.items()},
        "valid_ic_days": coverage.to_dict(orient="records"), "warnings": ["KNOWN_NON_BLOCKING_D3_GAP: 60D tail labels are incomplete near requested_end."],
        "errors": [], "known_d3_gaps": ["forward-return tail extension", "factor_history_sufficient technical debt"], "status": "success",
    }
    write_evaluation_artifacts(output_dir, manifest=manifest, tables=tables, scoreboard=scoreboard)
    write_charts(output_dir, tables=tables, scoreboard=scoreboard, source_run_id=args.run_id, evaluation_run_id=evaluation_run_id)
    counts = scoreboard["status"].value_counts().to_dict()
    print(
        f"Evaluation run: {evaluation_run_id}\nD3 source: {args.run_id}\nFactors: {len(scoreboard)}\n"
        f"Output: {output_dir}\nReport: {output_dir / 'FACTOR_RESEARCH_REPORT.md'}\n"
        f"Candidate: {counts.get('CANDIDATE', 0)}\nReview: {counts.get('REVIEW', 0)}\n"
        f"Weak: {counts.get('WEAK', 0)}\nInvalid: {counts.get('INVALID', 0)}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
