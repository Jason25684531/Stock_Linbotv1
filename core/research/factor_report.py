"""D4 reproducibility CSV and Markdown report writers."""

import json
from pathlib import Path

import numpy as np
import pandas as pd

from core.research.factor_charts import COMPARISON_HORIZON, comparison_frame


def _safe_csv(frame: pd.DataFrame, path: Path) -> None:
    result = frame.copy()
    numeric = result.select_dtypes(include="number").columns
    result.loc[:, numeric] = result.loc[:, numeric].replace([np.inf, -np.inf], np.nan)
    result.to_csv(path, index=False)


def _table(frame: pd.DataFrame) -> str:
    return "```csv\n" + (frame.to_csv(index=False) if not frame.empty else "No eligible observations\n") + "```"


def _factor_report(row: pd.Series, tables: dict[str, pd.DataFrame], manifest: dict[str, object]) -> str:
    factor_id = row.factor_id
    summary = tables.get("ic_summary", pd.DataFrame())
    summary = summary.loc[summary.get("factor_id", pd.Series(dtype=str)).eq(factor_id)] if not summary.empty else summary
    annual = tables.get("annual_results", pd.DataFrame())
    annual = annual.loc[annual.get("factor_id", pd.Series(dtype=str)).eq(factor_id)] if not annual.empty else annual
    quantiles = tables.get("quantile_timeseries", pd.DataFrame())
    quantiles = quantiles.loc[quantiles.get("factor_id", pd.Series(dtype=str)).eq(factor_id)] if not quantiles.empty else quantiles
    persistence = tables.get("rank_autocorrelation", pd.DataFrame())
    persistence = persistence.loc[persistence.get("factor_id", pd.Series(dtype=str)).eq(factor_id)] if not persistence.empty else persistence
    retention = tables.get("top_n_retention", pd.DataFrame())
    retention = retention.loc[retention.get("factor_id", pd.Series(dtype=str)).eq(factor_id)] if not retention.empty else retention
    coverage = tables.get("coverage", pd.DataFrame())
    coverage = coverage.loc[coverage.get("factor_id", pd.Series(dtype=str)).eq(factor_id)] if not coverage.empty else coverage
    charts = "\n".join(f"![{name}](../../charts/factors/{factor_id}/{name})" for name in (
        "ic_timeseries.png", "rolling_ic.png", "ic_by_horizon.png", "quantile_returns.png", "q5_q1_cumulative.png", "factor_decay.png", "rank_autocorrelation.png", "top_n_retention.png", "annual_ic.png", "coverage_timeseries.png"))
    return f"""# Factor Research — {factor_id}

## Verdict

Status: {row.status}
Best Research Horizon: {row.best_horizon}
Best Horizon Confidence: {row.best_horizon_confidence}
Direction: {row.direction}
Research Direction: {row.get('research_direction', 'NOT_APPLICABLE')}
Average Coverage: {row.get('average_coverage', float('nan'))}
Effective Days: {row.get('raw_valid_ic_days', float('nan'))}

## Factor Definition

Factor ID: {factor_id}
Factor Version: {row.get('factor_version', 'D3 canonical registry')}
Direction Metadata: {row.direction}
D3 Source Run: {manifest.get('d3_source_run_id', 'unavailable')}
Evaluation Run: {manifest.get('evaluation_run_id', 'unavailable')}

## Rank IC Summary

{_table(summary)}

## Annual Results

{_table(annual)}

## Quantile Returns

{_table(quantiles)}

## Factor Decay

See the horizon rows in Rank IC Summary and `factor_decay.png`.

## Signal Persistence

### Rank Autocorrelation

{_table(persistence)}

### Top-N Retention / Turnover

{_table(retention)}

## Coverage

{_table(coverage)}

## Redundancy

Most correlated factor: {row.get('most_correlated_factor', 'UNKNOWN')}

Maximum absolute correlation: {row.get('max_abs_factor_correlation', float('nan'))}

Redundancy flag: {row.get('redundancy_flag', 'UNKNOWN')}

## Interpretation

The evidence describes historical rank/return association only. It does not prove a tradable or production-ready alpha, and any role suggestion is RESEARCH INTERPRETATION ONLY.

## Application Recommendation

Application Role: {row.get('application_role', 'review_required')}

Candidate Horizon: {row.best_horizon}

Next Stage: {row.get('next_stage', 'research_review')}

Risks: {row.get('notes', 'RESEARCH INTERPRETATION ONLY')}

## Charts

{charts}

## Limitations

Factor statistical evidence does not imply tradable or production-ready alpha. No transaction-cost backtest, portfolio construction, market-impact model, capacity, position sizing, out-of-sample production performance, live execution, or production approval. D3 60D labels are incomplete near the requested end because no post-window +61 trading days were loaded.
"""


def write_evaluation_artifacts(output_dir: Path, *, manifest: dict[str, object], tables: dict[str, pd.DataFrame], scoreboard: pd.DataFrame) -> dict[str, Path]:
    """Write reproducible D4 artifacts. Existing output directories are never overwritten."""

    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "run_manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    for name, frame in tables.items():
        _safe_csv(frame, output_dir / f"{name}.csv")
    _safe_csv(scoreboard, output_dir / "factor_scoreboard.csv")
    candidates = scoreboard.copy()
    index = candidates.index
    raw_mean = candidates.get("raw_mean_ic", pd.Series(np.nan, index=index))
    aligned_mean = candidates.get("aligned_mean_ic", pd.Series(np.nan, index=index))
    raw_icir = candidates.get("raw_icir", pd.Series(np.nan, index=index))
    aligned_icir = candidates.get("aligned_icir", pd.Series(np.nan, index=index))
    direction = candidates.get("direction", pd.Series(0, index=index))
    candidates["mean_ic"] = aligned_mean.where(direction.ne(0), raw_mean)
    candidates["icir"] = aligned_icir.where(direction.ne(0), raw_icir)
    candidates["monotonicity"] = candidates.get("quantile_monotonicity", pd.Series(np.nan, index=index))
    candidates["coverage"] = candidates.get("average_coverage", pd.Series(np.nan, index=index))
    candidate_columns = (
        "factor_id", "status", "best_horizon", "best_horizon_confidence", "direction", "research_direction",
        "mean_ic", "icir", "q5_minus_q1", "monotonicity", "coverage", "turnover", "stability_flag",
        "redundancy_flag", "application_role", "next_stage", "notes",
    )
    for column in candidate_columns:
        if column not in candidates:
            candidates[column] = pd.NA
    _safe_csv(candidates.loc[:, candidate_columns], output_dir / "application_candidates.csv")
    reports = output_dir / "reports" / "factors"
    reports.mkdir(parents=True, exist_ok=True)
    for _, row in scoreboard.iterrows():
        (reports / f"{row.factor_id}.md").write_text(_factor_report(row, tables, manifest), encoding="utf-8")
    counts = scoreboard["status"].value_counts().to_dict() if not scoreboard.empty else {}
    comparison = comparison_frame(tables, scoreboard)
    candidates = comparison.loc[comparison["status"].eq("CANDIDATE")] if "status" in comparison else comparison.iloc[0:0]
    factor_links = "\n".join(f"- [{factor_id}](reports/factors/{factor_id}.md)" for factor_id in scoreboard.get("factor_id", pd.Series(dtype=str)))
    global_report = output_dir / "FACTOR_RESEARCH_REPORT.md"
    global_report.write_text(
        "# Factor Research Report\n\n"
        f"D3 Source Run: {manifest.get('d3_source_run_id')}\n\nEvaluation Run: {manifest.get('evaluation_run_id')}\n\n"
        f"## Executive Summary\n\n{json.dumps(counts, ensure_ascii=False)}\n\n"
        f"## All Factors — {COMPARISON_HORIZON}D\n\n"
        "Cross-factor IC, ICIR, Q5-Q1 spread, and coverage use the common 60D horizon. Turnover uses the D4 turnover artifact.\n\n"
        + _table(comparison) + "\n\n"
        "### Correlation\n\n" + _table(tables.get("factor_correlation", pd.DataFrame())) + "\n\n"
        f"## D4 Candidates — {COMPARISON_HORIZON}D\n\n"
        "Candidates are derived only from `status == CANDIDATE`; this is not a Top-N or production selection.\n\n"
        + _table(candidates) + "\n\n"
        "## Individual Factor Tear Sheets\n\n"
        "Individual tear sheets retain all research horizons for diagnosis; cross-factor charts do not mix horizons.\n\n"
        + factor_links + "\n\n"
        "## Factor Scoreboard\n\n" + _table(scoreboard) + "\n\n"
        "## Known Research Limitations\n\nD3 forward-return tail coverage is incomplete near requested_end because no +61 trading-day post-window was loaded. D4 does not imply portfolio, cost, OOS, or production readiness.\n",
        encoding="utf-8",
    )
    return {"manifest": output_dir / "run_manifest.json", "scoreboard": output_dir / "factor_scoreboard.csv", "report": global_report}
