"""Matplotlib/Agg chart generation for immutable D4 evaluation tables."""

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


def _save(path: Path, title: str, *, x: object = None, y: object = None, ylabel: str = "value") -> None:
    figure, axis = plt.subplots(figsize=(8, 4.5))
    if x is not None and y is not None and len(x):
        axis.plot(x, y, label="D4 evaluation")
        axis.legend()
    else:
        axis.text(0.5, 0.5, "No eligible observations", ha="center", va="center", transform=axis.transAxes)
    axis.set_title(title)
    axis.set_xlabel("D3 as-of date / factor")
    axis.set_ylabel(ylabel)
    axis.grid(alpha=0.25)
    figure.tight_layout()
    figure.savefig(path, dpi=120)
    plt.close(figure)


def _save_lines(path: Path, title: str, *, x: object, series: dict[str, object], ylabel: str) -> None:
    figure, axis = plt.subplots(figsize=(8, 4.5))
    for label, values in series.items():
        if values is not None and len(values):
            axis.plot(x, values, label=label)
    if axis.lines:
        axis.legend()
    else:
        axis.text(0.5, 0.5, "No eligible observations", ha="center", va="center", transform=axis.transAxes)
    axis.set_title(title)
    axis.set_xlabel("D3 as-of date / factor")
    axis.set_ylabel(ylabel)
    axis.grid(alpha=0.25)
    figure.tight_layout()
    figure.savefig(path, dpi=120)
    plt.close(figure)


def _save_heatmap(path: Path, title: str, correlation: pd.DataFrame) -> None:
    figure, axis = plt.subplots(figsize=(8, 6))
    if not correlation.empty:
        matrix = correlation.pivot(index="factor_id", columns="other_factor_id", values="correlation")
        image = axis.imshow(matrix, vmin=-1, vmax=1, cmap="coolwarm")
        axis.set_xticks(range(len(matrix.columns)), matrix.columns, rotation=90, fontsize=7)
        axis.set_yticks(range(len(matrix.index)), matrix.index, fontsize=7)
        figure.colorbar(image, ax=axis, label="Spearman correlation")
    else:
        axis.text(0.5, 0.5, "No eligible observations", ha="center", va="center", transform=axis.transAxes)
    axis.set_title(title)
    axis.set_xlabel("Factor")
    axis.set_ylabel("Factor")
    figure.tight_layout()
    figure.savefig(path, dpi=120)
    plt.close(figure)


def write_charts(output_dir: Path, *, tables: dict[str, pd.DataFrame], scoreboard: pd.DataFrame, source_run_id: str, evaluation_run_id: str) -> list[Path]:
    """Write all required non-interactive charts, with meaningful data where present."""

    root = output_dir / "charts"
    root.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    daily = tables.get("ic_timeseries", pd.DataFrame())
    summary = tables.get("ic_summary", pd.DataFrame())
    quantiles = tables.get("quantile_timeseries", pd.DataFrame())
    annual = tables.get("annual_results", pd.DataFrame())
    coverage = tables.get("coverage", pd.DataFrame())
    persistence = tables.get("rank_autocorrelation", pd.DataFrame())
    retention = tables.get("top_n_retention", pd.DataFrame())
    for factor_id in scoreboard.get("factor_id", pd.Series(dtype=str)):
        directory = root / "factors" / str(factor_id)
        directory.mkdir(parents=True, exist_ok=True)
        factor_daily = daily.loc[daily.get("factor_id", pd.Series(dtype=str)).eq(factor_id)] if not daily.empty else daily
        factor_persistence = persistence.loc[persistence.get("factor_id", pd.Series(dtype=str)).eq(factor_id)] if not persistence.empty else persistence
        factor_retention = retention.loc[retention.get("factor_id", pd.Series(dtype=str)).eq(factor_id)] if not retention.empty else retention
        factor_summary = summary.loc[summary.get("factor_id", pd.Series(dtype=str)).eq(factor_id)] if not summary.empty else summary
        factor_quantiles = quantiles.loc[quantiles.get("factor_id", pd.Series(dtype=str)).eq(factor_id)] if not quantiles.empty else quantiles
        factor_annual = annual.loc[annual.get("factor_id", pd.Series(dtype=str)).eq(factor_id)] if not annual.empty else annual
        factor_coverage = coverage.loc[coverage.get("factor_id", pd.Series(dtype=str)).eq(factor_id)] if not coverage.empty else coverage
        for filename in FACTOR_CHARTS:
            if "retention" in filename and not factor_retention.empty:
                x, y, ylabel = factor_retention["asof_date"], factor_retention["top_n_retention"], "retention"
            elif "autocorrelation" in filename and not factor_persistence.empty:
                x, y, ylabel = factor_persistence["asof_date"], factor_persistence["rank_autocorrelation"], "Spearman correlation"
            elif filename == "rolling_ic.png" and not factor_daily.empty:
                path = directory / filename
                _save_lines(path, f"{factor_id} ??rolling ic\nD3 {source_run_id} | D4 {evaluation_run_id}", x=factor_daily["asof_date"], series={"raw rolling IC": factor_daily["raw_rolling_ic"], "aligned rolling IC": factor_daily["aligned_rolling_ic"]}, ylabel="rolling rank IC")
                written.append(path)
                continue
            elif filename in {"ic_by_horizon.png", "factor_decay.png"} and not factor_summary.empty:
                value = "aligned_mean_ic" if factor_summary["aligned_mean_ic"].notna().any() else "raw_mean_ic"
                x, y, ylabel = factor_summary["horizon"], factor_summary[value], value
            elif filename in {"quantile_returns.png", "q5_q1_cumulative.png"} and not factor_quantiles.empty:
                value = "aligned_long_short_spread" if factor_quantiles["aligned_long_short_spread"].notna().any() else "raw_q5_minus_q1"
                x, y, ylabel = factor_quantiles["asof_date"], factor_quantiles[value].cumsum() if filename == "q5_q1_cumulative.png" else factor_quantiles[value], value
            elif filename == "annual_ic.png" and not factor_annual.empty:
                value = "aligned_mean_ic" if factor_annual["aligned_mean_ic"].notna().any() else "raw_mean_ic"
                x, y, ylabel = factor_annual["year"], factor_annual[value], value
            elif filename == "coverage_timeseries.png" and not factor_coverage.empty:
                x, y, ylabel = factor_coverage["horizon"], factor_coverage["average_coverage"], "coverage"
            elif not factor_daily.empty:
                value = "aligned_ic" if factor_daily["aligned_ic"].notna().any() else "raw_ic"
                x, y, ylabel = factor_daily["asof_date"], factor_daily[value], value
            else:
                x = y = None
                ylabel = "value"
            path = directory / filename
            _save(path, f"{factor_id} — {filename[:-4]}\nD3 {source_run_id} | D4 {evaluation_run_id}", x=x, y=y, ylabel=ylabel)
            written.append(path)
    global_metrics = {
        "factor_ic_comparison.png": ("aligned_mean_ic", "raw_mean_ic", "mean rank IC"),
        "factor_icir_comparison.png": ("aligned_icir", "raw_icir", "ICIR"),
        "factor_spread_comparison.png": ("q5_minus_q1", None, "Q5-Q1 spread"),
        "factor_turnover_comparison.png": ("turnover", None, "equal-weight turnover"),
        "factor_coverage_comparison.png": ("average_coverage", None, "coverage"),
    }
    for filename, (preferred, fallback, ylabel) in global_metrics.items():
        value = preferred if preferred in scoreboard and scoreboard[preferred].notna().any() else fallback
        path = root / filename
        _save(path, f"{filename[:-4]}\nD3 {source_run_id} | D4 {evaluation_run_id}", x=scoreboard.get("factor_id"), y=scoreboard.get(value) if value else None, ylabel=ylabel)
        written.append(path)
    path = root / "factor_correlation_heatmap.png"
    _save_heatmap(path, f"factor correlation heatmap\nD3 {source_run_id} | D4 {evaluation_run_id}", tables.get("factor_correlation", pd.DataFrame()))
    written.append(path)
    return written
