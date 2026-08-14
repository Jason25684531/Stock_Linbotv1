import json

import pandas as pd

from core.research.factor_charts import CANDIDATE_CHARTS, FACTOR_CHARTS, GLOBAL_CHARTS, comparison_frame, write_charts
from core.research.factor_report import write_evaluation_artifacts


def test_report_writer_creates_manifest_scoreboard_and_relative_factor_report(tmp_path):
    output = tmp_path / "eval"
    scoreboard = pd.DataFrame(
        [{"factor_id": "momentum_20d", "status": "CANDIDATE", "best_horizon": 5, "best_horizon_confidence": "CLEAR", "direction": 1}]
    )
    outputs = write_evaluation_artifacts(
        output,
        manifest={"status": "success", "evaluation_run_id": "eval"},
        tables={"ic_summary": pd.DataFrame([{"factor_id": "momentum_20d", "horizon": 5, "raw_mean_ic": 0.1}])},
        scoreboard=scoreboard,
    )

    assert (output / "run_manifest.json").exists()
    assert json.loads((output / "run_manifest.json").read_text(encoding="utf-8"))["status"] == "success"
    assert (output / "factor_scoreboard.csv").exists()
    candidates = pd.read_csv(output / "application_candidates.csv")
    assert {"mean_ic", "icir", "monotonicity", "coverage", "turnover"}.issubset(candidates.columns)
    report = output / "reports" / "factors" / "momentum_20d.md"
    assert report.exists()
    text = report.read_text(encoding="utf-8")
    assert "CANDIDATE" in text
    assert "## Quantile Returns" in text
    assert "## Signal Persistence" in text
    assert "## Application Recommendation" in text
    assert "## Factor Definition" in text
    assert "## Interpretation" in text
    assert "## Known Research Limitations" in (output / "FACTOR_RESEARCH_REPORT.md").read_text(encoding="utf-8")
    assert outputs["report"] == output / "FACTOR_RESEARCH_REPORT.md"


def test_charts_are_nonempty_and_factor_markdown_paths_resolve(tmp_path):
    output = tmp_path / "eval"
    scoreboard = pd.DataFrame([{"factor_id": "momentum_20d", "raw_mean_ic": 0.01, "aligned_mean_ic": 0.02, "raw_icir": 0.1, "aligned_icir": 0.2, "q5_minus_q1": 0.003, "turnover": 0.1, "average_coverage": 0.8}])
    tables = {
        "ic_timeseries": pd.DataFrame([{"factor_id": "momentum_20d", "asof_date": "2026-01-02", "raw_ic": 0.01, "aligned_ic": 0.02, "raw_rolling_ic": 0.01, "aligned_rolling_ic": 0.02}]),
        "ic_summary": pd.DataFrame([{"factor_id": "momentum_20d", "horizon": 1, "raw_mean_ic": 0.01, "aligned_mean_ic": 0.02}]),
        "quantile_timeseries": pd.DataFrame([{"factor_id": "momentum_20d", "asof_date": "2026-01-02", "raw_q5_minus_q1": 0.01, "aligned_long_short_spread": 0.02}]),
        "annual_results": pd.DataFrame([{"factor_id": "momentum_20d", "year": 2026, "raw_mean_ic": 0.01, "aligned_mean_ic": 0.02}]),
        "coverage": pd.DataFrame([{"factor_id": "momentum_20d", "horizon": 1, "average_coverage": 0.8}]),
        "rank_autocorrelation": pd.DataFrame(),
        "top_n_retention": pd.DataFrame(),
        "factor_correlation": pd.DataFrame(),
    }
    write_charts(output, tables=tables, scoreboard=scoreboard, source_run_id="d3", evaluation_run_id="d4")
    write_evaluation_artifacts(output, manifest={"status": "success", "evaluation_run_id": "d4"}, tables=tables, scoreboard=scoreboard.assign(status="CANDIDATE", best_horizon=1, best_horizon_confidence="CLEAR", direction=1))

    for filename in FACTOR_CHARTS:
        assert (output / "charts" / "factors" / "momentum_20d" / filename).stat().st_size > 0
    for filename in GLOBAL_CHARTS:
        assert (output / "charts" / filename).stat().st_size > 0
    report = output / "reports" / "factors" / "momentum_20d.md"
    for relative in ("../../charts/factors/momentum_20d/ic_timeseries.png", "../../charts/factors/momentum_20d/coverage_timeseries.png"):
        assert (report.parent / relative).resolve().is_file()


def test_cross_factor_reporting_uses_artifact_60d_values_and_status_candidates(tmp_path):
    output = tmp_path / "eval"
    scoreboard = pd.DataFrame([
        {"factor_id": "candidate_a", "status": "CANDIDATE", "aligned_mean_ic": 999.0, "best_horizon": 5, "best_horizon_confidence": "CLEAR", "direction": 1},
        {"factor_id": "candidate_b", "status": "CANDIDATE", "aligned_mean_ic": 999.0, "best_horizon": 20, "best_horizon_confidence": "CLEAR", "direction": 1},
        {"factor_id": "weak", "status": "WEAK", "aligned_mean_ic": 999.0, "best_horizon": 60, "best_horizon_confidence": "CLEAR", "direction": 1},
    ])
    tables = {
        "ic_summary": pd.DataFrame([
            {"factor_id": factor_id, "horizon": horizon, "raw_mean_ic": 0.01, "aligned_mean_ic": value, "raw_icir": 0.1, "aligned_icir": value * 10}
            for factor_id, horizon, value in (("candidate_a", 60, 0.02), ("candidate_a", 5, 0.80), ("candidate_b", 60, 0.03), ("weak", 60, 0.04))
        ]),
        "quantile_returns": pd.DataFrame([
            {"factor_id": factor_id, "horizon": 60, "quantile": quantile, "mean_return": value}
            for factor_id, value in (("candidate_a", 0.01), ("candidate_b", 0.02), ("weak", 0.03)) for quantile, value in ((1, value), (5, value + 0.05))
        ]),
        "coverage": pd.DataFrame([
            {"factor_id": factor_id, "horizon": 60, "average_coverage": value}
            for factor_id, value in (("candidate_a", 0.7), ("candidate_b", 0.8), ("weak", 0.9))
        ]),
        "turnover": pd.DataFrame([
            {"factor_id": factor_id, "equal_weight_turnover": value}
            for factor_id, value in (("candidate_a", 0.1), ("candidate_b", 0.2), ("weak", 0.3))
        ]),
        "ic_timeseries": pd.DataFrame([
            {"factor_id": factor_id, "horizon": 60, "asof_date": "2026-01-02", "raw_ic": 0.01, "aligned_ic": 0.02, "raw_rolling_ic": 0.01, "aligned_rolling_ic": 0.02}
            for factor_id in ("candidate_a", "candidate_b", "weak")
        ]),
        "quantile_timeseries": pd.DataFrame([
            {"factor_id": factor_id, "horizon": 60, "asof_date": "2026-01-02", "raw_q5_minus_q1": 0.01, "aligned_long_short_spread": 0.02}
            for factor_id in ("candidate_a", "candidate_b", "weak")
        ]),
        "annual_results": pd.DataFrame([
            {"factor_id": factor_id, "year": 2026, "raw_mean_ic": 0.01, "aligned_mean_ic": 0.02}
            for factor_id in ("candidate_a", "candidate_b", "weak")
        ]),
        "rank_autocorrelation": pd.DataFrame([
            {"factor_id": factor_id, "asof_date": "2026-01-02", "rank_autocorrelation": 0.5}
            for factor_id in ("candidate_a", "candidate_b", "weak")
        ]),
        "top_n_retention": pd.DataFrame([
            {"factor_id": factor_id, "asof_date": "2026-01-02", "top_n_retention": 0.5}
            for factor_id in ("candidate_a", "candidate_b", "weak")
        ]),
        "factor_correlation": pd.DataFrame([
            {"factor_id": "candidate_a", "other_factor_id": "candidate_b", "correlation": 0.2},
            {"factor_id": "candidate_b", "other_factor_id": "candidate_a", "correlation": 0.2},
        ]),
    }

    comparison = comparison_frame(tables, scoreboard).set_index("factor_id")
    assert comparison.loc["candidate_a", ["mean_rank_ic", "icir", "q5_minus_q1", "turnover", "coverage"]].tolist() == [0.02, 0.2, 0.05, 0.1, 0.7]
    assert comparison.loc["candidate_b", "mean_rank_ic"] == 0.03

    write_charts(output, tables=tables, scoreboard=scoreboard, source_run_id="d3", evaluation_run_id="d4")
    for filename in CANDIDATE_CHARTS:
        assert (output / "charts" / "candidates" / filename).is_file()
    write_evaluation_artifacts(output, manifest={"status": "success", "evaluation_run_id": "d4"}, tables=tables, scoreboard=scoreboard)
    report = (output / "FACTOR_RESEARCH_REPORT.md").read_text(encoding="utf-8")
    assert "## All Factors — 60D" in report
    assert "## D4 Candidates — 60D" in report
    assert "## Individual Factor Tear Sheets" in report
    assert "candidate_a" in report and "candidate_b" in report
