"""Matplotlib/Agg presentation charts for immutable D4 evaluation tables."""

from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pandas as pd


FACTOR_CHARTS = (
    "ic_timeseries.png", "rolling_ic.png", "ic_by_horizon.png", "quantile_returns.png", "q5_q1_cumulative.png",
    "factor_decay.png", "rank_autocorrelation.png", "top_n_retention.png", "annual_ic.png", "coverage_timeseries.png",
)
GLOBAL_CHARTS = (
    "factor_ic_comparison.png", "factor_icir_comparison.png", "factor_spread_comparison.png",
    "factor_turnover_comparison.png", "factor_coverage_comparison.png", "factor_correlation_heatmap.png",
)
CANDIDATE_CHARTS = (
    "factor_ic_comparison.png", "factor_icir_comparison.png", "factor_spread_comparison.png",
    "factor_turnover_comparison.png", "factor_correlation_heatmap.png",
)
COMPARISON_HORIZON = 60


def _factor_rows(frame: pd.DataFrame, factor_id: str) -> pd.DataFrame:
    return frame.loc[frame.get("factor_id", pd.Series(dtype=str)).eq(factor_id)] if not frame.empty else frame


def _preferred(frame: pd.DataFrame, aligned: str, raw: str) -> pd.Series:
    return frame[aligned] if aligned in frame and frame[aligned].notna().any() else frame.get(raw, pd.Series(dtype=float))


def _horizon_groups(frame: pd.DataFrame):
    return frame.groupby("horizon") if "horizon" in frame else ((COMPARISON_HORIZON, frame),)


def comparison_frame(tables: dict[str, pd.DataFrame], scoreboard: pd.DataFrame) -> pd.DataFrame:
    """Return presentation-only cross-factor values from their D4 source tables."""

    result = scoreboard.loc[:, [column for column in ("factor_id", "status") if column in scoreboard]].copy()
    if "factor_id" not in result:
        return pd.DataFrame(columns=("factor_id", "status", "mean_rank_ic", "icir", "q5_minus_q1", "turnover", "coverage"))
    summary = tables.get("ic_summary", pd.DataFrame())
    summary = summary.loc[summary.get("horizon", pd.Series(dtype=int)).eq(COMPARISON_HORIZON)]
    if not summary.empty:
        summary = summary.loc[:, ["factor_id"]].assign(
            mean_rank_ic=_preferred(summary, "aligned_mean_ic", "raw_mean_ic").to_numpy(),
            icir=_preferred(summary, "aligned_icir", "raw_icir").to_numpy(),
        )
        result = result.merge(summary, on="factor_id", how="left")
    quantiles = tables.get("quantile_returns", pd.DataFrame())
    quantiles = quantiles.loc[quantiles.get("horizon", pd.Series(dtype=int)).eq(COMPARISON_HORIZON)]
    if not quantiles.empty:
        means = quantiles.groupby(["factor_id", "quantile"])["mean_return"].mean().unstack("quantile")
        spread = (means.get(5, pd.Series(dtype=float)) - means.get(1, pd.Series(dtype=float))).rename("q5_minus_q1")
        result = result.merge(spread, left_on="factor_id", right_index=True, how="left")
    coverage = tables.get("coverage", pd.DataFrame())
    coverage = coverage.loc[coverage.get("horizon", pd.Series(dtype=int)).eq(COMPARISON_HORIZON)]
    if not coverage.empty:
        result = result.merge(coverage.loc[:, ["factor_id", "average_coverage"]].rename(columns={"average_coverage": "coverage"}), on="factor_id", how="left")
    turnover = tables.get("turnover", pd.DataFrame())
    if not turnover.empty:
        values = turnover.groupby("factor_id")["equal_weight_turnover"].mean().rename("turnover")
        result = result.merge(values, left_on="factor_id", right_index=True, how="left")
    return result


def _save_lines(path: Path, title: str, *, series: dict[str, tuple[object, object]], ylabel: str, xlabel: str = "Date") -> None:
    figure, axis = plt.subplots(figsize=(8, 4.5))
    for label, (x, values) in series.items():
        if values is not None and len(values):
            axis.plot(x, values, label=label)
    if axis.lines:
        axis.legend(fontsize=8)
    else:
        axis.text(0.5, 0.5, "No eligible observations", ha="center", va="center", transform=axis.transAxes)
    axis.set(title=title, xlabel=xlabel, ylabel=ylabel)
    axis.tick_params(axis="x", rotation=30)
    axis.grid(alpha=0.25)
    figure.tight_layout()
    figure.savefig(path, dpi=120)
    plt.close(figure)


def _save_bars(path: Path, title: str, values: pd.DataFrame, *, value: str, ylabel: str, xlabel: str = "Factor") -> None:
    figure, axis = plt.subplots(figsize=(8, 4.5))
    if not values.empty and value in values:
        numeric = pd.to_numeric(values[value], errors="coerce")
        bars = axis.bar(values["factor_id"].astype(str), numeric.fillna(0.0))
        for bar, missing in zip(bars, numeric.isna()):
            if missing:
                axis.text(bar.get_x() + bar.get_width() / 2, 0, "N/A", ha="center", va="bottom", fontsize=8)
    else:
        axis.text(0.5, 0.5, "No eligible observations", ha="center", va="center", transform=axis.transAxes)
    axis.set(title=title, xlabel=xlabel, ylabel=ylabel)
    axis.tick_params(axis="x", rotation=30)
    axis.grid(axis="y", alpha=0.25)
    figure.tight_layout()
    figure.savefig(path, dpi=120)
    plt.close(figure)


def _save_quantile_bars(path: Path, title: str, returns: pd.DataFrame) -> None:
    figure, axis = plt.subplots(figsize=(8, 4.5))
    means = returns.groupby(["horizon", "quantile"])["mean_return"].mean().unstack("quantile") if not returns.empty else pd.DataFrame()
    if not means.empty:
        quantiles = sorted(means.columns)
        width = 0.8 / len(means)
        for index, (horizon, row) in enumerate(means.iterrows()):
            axis.bar([q + (index - (len(means) - 1) / 2) * width for q in quantiles], row.reindex(quantiles), width=width, label=f"{horizon}D")
        axis.set_xticks(quantiles, [f"Q{quantile}" for quantile in quantiles])
        axis.legend(title="Horizon", fontsize=8)
    else:
        axis.text(0.5, 0.5, "No eligible observations", ha="center", va="center", transform=axis.transAxes)
    axis.set(title=title, xlabel="Quantile", ylabel="Full-period mean return")
    axis.grid(axis="y", alpha=0.25)
    figure.tight_layout()
    figure.savefig(path, dpi=120)
    plt.close(figure)


def _save_heatmap(path: Path, title: str, correlation: pd.DataFrame, factor_ids: list[str]) -> None:
    figure, axis = plt.subplots(figsize=(8, 6))
    matrix = correlation.pivot(index="factor_id", columns="other_factor_id", values="correlation").reindex(index=factor_ids, columns=factor_ids) if not correlation.empty else pd.DataFrame(index=factor_ids, columns=factor_ids)
    if factor_ids:
        image = axis.imshow(matrix.astype(float), vmin=-1, vmax=1, cmap="coolwarm")
        axis.set_xticks(range(len(factor_ids)), factor_ids, rotation=90, fontsize=7)
        axis.set_yticks(range(len(factor_ids)), factor_ids, fontsize=7)
        for row, factor_id in enumerate(factor_ids):
            for column, other_id in enumerate(factor_ids):
                if pd.isna(matrix.loc[factor_id, other_id]):
                    axis.text(column, row, "N/A", ha="center", va="center", fontsize=6)
        figure.colorbar(image, ax=axis, label="Spearman correlation")
    else:
        axis.text(0.5, 0.5, "No eligible observations", ha="center", va="center", transform=axis.transAxes)
    axis.set(title=title, xlabel="Factor", ylabel="Factor")
    figure.tight_layout()
    figure.savefig(path, dpi=120)
    plt.close(figure)


def _write_comparison_charts(root: Path, values: pd.DataFrame, correlation: pd.DataFrame, title_prefix: str, *, include_coverage: bool) -> list[Path]:
    root.mkdir(parents=True, exist_ok=True)
    charts = (("factor_ic_comparison.png", "mean_rank_ic", "Mean Rank IC"), ("factor_icir_comparison.png", "icir", "ICIR"), ("factor_spread_comparison.png", "q5_minus_q1", "Q5-Q1 Spread"), ("factor_turnover_comparison.png", "turnover", "Equal-weight Turnover"))
    if include_coverage:
        charts += (("factor_coverage_comparison.png", "coverage", "Coverage"),)
    written = []
    for filename, column, label in charts:
        path = root / filename
        _save_bars(path, f"{title_prefix} — {label} — 60D" if column not in {"turnover"} else f"{title_prefix} — {label}", values, value=column, ylabel=label)
        written.append(path)
    path = root / "factor_correlation_heatmap.png"
    _save_heatmap(path, f"{title_prefix} — Correlation", correlation, values["factor_id"].astype(str).tolist())
    written.append(path)
    return written


def write_charts(output_dir: Path, *, tables: dict[str, pd.DataFrame], scoreboard: pd.DataFrame, source_run_id: str, evaluation_run_id: str) -> list[Path]:
    """Write presentation-only charts from existing D4 artifact tables."""

    root = output_dir / "charts"
    root.mkdir(parents=True, exist_ok=True)
    written = []
    daily = tables.get("ic_timeseries", pd.DataFrame())
    summary = tables.get("ic_summary", pd.DataFrame())
    quantile_returns = tables.get("quantile_returns", pd.DataFrame())
    quantiles = tables.get("quantile_timeseries", pd.DataFrame())
    annual = tables.get("annual_results", pd.DataFrame())
    coverage = tables.get("coverage", pd.DataFrame())
    persistence = tables.get("rank_autocorrelation", pd.DataFrame())
    retention = tables.get("top_n_retention", pd.DataFrame())
    for factor_id in scoreboard.get("factor_id", pd.Series(dtype=str)):
        directory = root / "factors" / str(factor_id)
        directory.mkdir(parents=True, exist_ok=True)
        factor_daily = _factor_rows(daily, factor_id)
        factor_summary = _factor_rows(summary, factor_id)
        factor_returns = _factor_rows(quantile_returns, factor_id)
        factor_quantiles = _factor_rows(quantiles, factor_id)
        factor_annual = _factor_rows(annual, factor_id)
        factor_coverage = _factor_rows(coverage, factor_id)
        factor_persistence = _factor_rows(persistence, factor_id)
        factor_retention = _factor_rows(retention, factor_id)
        value = "aligned_ic" if "aligned_ic" in factor_daily and factor_daily["aligned_ic"].notna().any() else "raw_ic"
        lines = {f"{horizon}D": (group["asof_date"], group[value]) for horizon, group in _horizon_groups(factor_daily)}
        _save_lines(directory / "ic_timeseries.png", f"{factor_id} — Rank IC\nD3 {source_run_id} | D4 {evaluation_run_id}", series=lines, ylabel="rank IC")
        rolling = "aligned_rolling_ic" if "aligned_rolling_ic" in factor_daily and factor_daily["aligned_rolling_ic"].notna().any() else "raw_rolling_ic"
        lines = {f"{horizon}D": (group["asof_date"], group[rolling]) for horizon, group in _horizon_groups(factor_daily)}
        _save_lines(directory / "rolling_ic.png", f"{factor_id} — Rolling IC\nD3 {source_run_id} | D4 {evaluation_run_id}", series=lines, ylabel="rolling rank IC")
        summary_value = "aligned_mean_ic" if "aligned_mean_ic" in factor_summary and factor_summary["aligned_mean_ic"].notna().any() else "raw_mean_ic"
        _save_bars(directory / "ic_by_horizon.png", f"{factor_id} — Mean Rank IC by Horizon", factor_summary.loc[:, ["horizon", summary_value]].rename(columns={"horizon": "factor_id", summary_value: "value"}), value="value", ylabel="mean rank IC", xlabel="Horizon")
        _save_quantile_bars(directory / "quantile_returns.png", f"{factor_id} — Quantile Returns", factor_returns)
        spread = "aligned_long_short_spread" if "aligned_long_short_spread" in factor_quantiles and factor_quantiles["aligned_long_short_spread"].notna().any() else "raw_q5_minus_q1"
        lines = {f"{horizon}D": (group["asof_date"], group[spread].cumsum()) for horizon, group in _horizon_groups(factor_quantiles)}
        _save_lines(directory / "q5_q1_cumulative.png", f"{factor_id} — Cumulative Q5-Q1 Spread", series=lines, ylabel="cumulative spread")
        _save_bars(directory / "factor_decay.png", f"{factor_id} — Factor Decay", factor_summary.loc[:, ["horizon", summary_value]].rename(columns={"horizon": "factor_id", summary_value: "value"}), value="value", ylabel="mean rank IC", xlabel="Horizon")
        _save_lines(directory / "rank_autocorrelation.png", f"{factor_id} — Rank Autocorrelation", series={"Spearman": (factor_persistence.get("asof_date", []), factor_persistence.get("rank_autocorrelation", []))}, ylabel="Spearman correlation")
        _save_lines(directory / "top_n_retention.png", f"{factor_id} — Top-N Retention", series={"Top-N retention": (factor_retention.get("asof_date", []), factor_retention.get("top_n_retention", []))}, ylabel="retention")
        annual_value = "aligned_mean_ic" if "aligned_mean_ic" in factor_annual and factor_annual["aligned_mean_ic"].notna().any() else "raw_mean_ic"
        _save_bars(directory / "annual_ic.png", f"{factor_id} — Annual Rank IC", factor_annual.loc[:, ["year", annual_value]].rename(columns={"year": "factor_id", annual_value: "value"}), value="value", ylabel="mean rank IC", xlabel="Year")
        _save_bars(directory / "coverage_timeseries.png", f"{factor_id} — Coverage by Horizon", factor_coverage.loc[:, ["horizon", "average_coverage"]].rename(columns={"horizon": "factor_id", "average_coverage": "value"}), value="value", ylabel="coverage", xlabel="Horizon")
        written.extend(directory / filename for filename in FACTOR_CHARTS)
    values = comparison_frame(tables, scoreboard)
    correlation = tables.get("factor_correlation", pd.DataFrame())
    written.extend(_write_comparison_charts(root, values, correlation, "All Factors", include_coverage=True))
    candidates = values.loc[values["status"].eq("CANDIDATE")] if "status" in values else values.iloc[0:0]
    candidate_ids = candidates["factor_id"].astype(str)
    candidate_correlation = correlation.loc[correlation.get("factor_id", pd.Series(dtype=str)).isin(candidate_ids) & correlation.get("other_factor_id", pd.Series(dtype=str)).isin(candidate_ids)] if not correlation.empty else correlation
    written.extend(_write_comparison_charts(root / "candidates", candidates, candidate_correlation, "D4 Candidates", include_coverage=False))
    return written
