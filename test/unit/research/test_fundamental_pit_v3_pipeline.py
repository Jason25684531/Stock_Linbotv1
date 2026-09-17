from __future__ import annotations

from datetime import date

import pandas as pd

from core.research.fundamental_pit_v3 import (
    add_derived_metrics,
    build_matrix,
    coverage_reports,
    missingness_report,
    normalize_financial_records,
    quality_gates,
    standalone_values,
)


def _source() -> pd.DataFrame:
    rows = []
    for year, multiplier in ((2024, 1), (2025, 2)):
        for quarter in range(1, 5):
            rows.append({
                "ticker": "2330", "fiscal_year": year, "quarter": quarter,
                "period_end": date(year, quarter * 3, 31 if quarter in {1, 4} else 30).isoformat(),
                "revenue": float(multiplier * quarter * 100),
                "operating_profit": float(multiplier * quarter * 10),
                "eps": float(multiplier * quarter),
            })
    return pd.DataFrame(rows)


def _publications() -> pd.DataFrame:
    rows = []
    for _, row in _source().iterrows():
        publication = (pd.Timestamp(row["period_end"]) + pd.Timedelta(days=10)).date()
        rows.append({
            "ticker": "2330", "fiscal_year": row["fiscal_year"], "quarter": row["quarter"],
            "period_end": row["period_end"], "publication_date": publication.isoformat(),
            "publication_timestamp": f"{publication.isoformat()}T19:00:00+08:00",
            "available_date": date(publication.year, publication.month, publication.day + 1).isoformat(),
            "source_system": "MOPS", "source_record_id": f"mops:{row['fiscal_year']}:{row['quarter']}",
            "request_key": f"mops|{row['fiscal_year']}|{row['quarter']}", "retrieved_at": "2026-01-01T00:00:00+00:00",
            "evidence_status": "VERIFIED",
        })
    return pd.DataFrame(rows)


def test_cumulative_income_values_are_split_to_standalone():
    raw = pd.DataFrame([
        {"ticker": "2330", "fiscal_year": 2025, "quarter": 1, "metric": "revenue", "source_value": 100.0},
        {"ticker": "2330", "fiscal_year": 2025, "quarter": 2, "metric": "revenue", "source_value": 250.0},
    ])
    result = standalone_values(raw)
    assert result["value"].tolist() == [100.0, 150.0]
    assert result["REPORTING_BASIS"].tolist() == ["STANDALONE", "STANDALONE"]


def test_incomplete_cumulative_series_keeps_cumulative_basis():
    raw = pd.DataFrame([
        {"ticker": "2330", "fiscal_year": 2025, "quarter": 2, "metric": "revenue", "source_value": 250.0},
    ])
    result = standalone_values(raw)
    assert result.loc[0, "value"] == 250.0
    assert result.loc[0, "REPORTING_BASIS"] == "CUMULATIVE"


def test_cumulative_split_handles_same_period_revisions():
    raw = pd.DataFrame([
        {"ticker": "2330", "fiscal_year": 2025, "quarter": 1, "metric": "revenue", "source_value": 100.0},
        {"ticker": "2330", "fiscal_year": 2025, "quarter": 2, "metric": "revenue", "source_value": 250.0},
        {"ticker": "2330", "fiscal_year": 2025, "quarter": 2, "metric": "revenue", "source_value": 260.0},
    ])
    result = standalone_values(raw)
    assert result["value"].tolist() == [100.0, 150.0, 160.0]


def test_normalization_preserves_all_source_revisions():
    source = _source().iloc[[0]].copy()
    publications = pd.concat([_publications().iloc[[0]], _publications().iloc[[0]].assign(source_record_id="mops:revision")])
    result = normalize_financial_records(source, publications)
    assert len(result) == 6
    assert result["revision_id"].nunique() == 2


def test_derived_yoy_uses_same_quarter_and_standalone_basis():
    result = add_derived_metrics(normalize_financial_records(_source(), _publications()))
    row = result.loc[(result["ticker"] == "2330") & (result["fiscal_year"] == 2025) & (result["quarter"] == 2) & (result["metric"] == "revenue_yoy")].iloc[0]
    assert row["value"] == 1.0


def test_operating_margin_is_only_built_from_available_raw_metrics():
    result = add_derived_metrics(normalize_financial_records(_source(), _publications()))
    assert "operating_margin" in set(result["metric"])
    assert "roe" not in set(result["metric"])


def test_matrix_uses_available_date_backward_asof():
    records = add_derived_metrics(normalize_financial_records(_source(), _publications()))
    matrix, matrices = build_matrix(records, ["2330", "2317"], pd.to_datetime(["2024-04-09", "2024-04-11", "2025-01-11"]))
    assert pd.isna(matrix.loc[(matrix["asof_date"] == "2024-04-09") & (matrix["ticker"] == "2330"), "revenue"].iloc[0])
    assert matrices["revenue"].loc[pd.Timestamp("2024-04-11"), "2330"] == 100.0
    assert set(matrix["ticker"]) == {"2330", "2317"}


def test_matrix_does_not_create_unsupported_columns():
    records = normalize_financial_records(_source(), _publications())
    matrix, _ = build_matrix(records, ["2330"], ["2025-01-11"])
    assert not set(["net_income", "total_assets", "market_cap"]).intersection(matrix.columns)


def test_missingness_reconciles_each_nan_cell():
    records = normalize_financial_records(_source(), _publications())
    matrix, _ = build_matrix(records, ["2330", "2317"], ["2025-01-11", "2025-04-11"])
    missing = missingness_report(matrix, records)
    assert len(missing) == int(matrix.drop(columns=["asof_date", "ticker"]).isna().sum().sum())
    assert set(missing["missing_reason"]).issubset({
        "NO_SOURCE_RECORD", "NOT_YET_PUBLISHED", "METRIC_NOT_REPORTED",
        "PRIOR_PERIOD_UNAVAILABLE", "DERIVED_METRIC_NOT_APPLICABLE",
        "REPORTING_BASIS_NOT_APPLICABLE", "DIVISION_INVALID", "OTHER",
    })
    assert ((missing["raw_source_exists"] == True) & (missing["missing_reason"] == "NO_SOURCE_RECORD")).sum() == 0


def test_missingness_distinguishes_source_and_derived_reasons():
    records = pd.DataFrame([
        {"ticker": "2330", "metric": "revenue", "value": 0.0, "fiscal_year": 2025, "quarter": 1,
         "period_end": "2025-03-31", "publication_date": "2025-04-01", "available_date": "2025-04-02",
         "REPORTING_BASIS": "STANDALONE", "source_record_id": "rev-2025"},
        {"ticker": "2330", "metric": "operating_profit", "value": 1.0, "fiscal_year": 2025, "quarter": 1,
         "period_end": "2025-03-31", "publication_date": "2025-04-01", "available_date": "2025-04-02",
         "REPORTING_BASIS": "STANDALONE", "source_record_id": "op-2025"},
        {"ticker": "2330", "metric": "eps", "value": 1.0, "fiscal_year": 2025, "quarter": 1,
         "period_end": "2025-03-31", "publication_date": "2025-04-01", "available_date": "2025-04-02",
         "REPORTING_BASIS": "CUMULATIVE", "source_record_id": "eps-2025"},
    ])
    matrix = pd.DataFrame([
        {"asof_date": "2025-04-03", "ticker": "2330", "revenue": 0.0,
         "operating_profit": 1.0, "revenue_yoy": float("nan"),
         "revenue_cumulative_yoy": float("nan"), "operating_margin": float("nan")},
        {"asof_date": "2025-04-03", "ticker": "9999", "revenue": float("nan"),
         "operating_profit": float("nan"), "revenue_yoy": float("nan"),
         "revenue_cumulative_yoy": float("nan"), "operating_margin": float("nan")},
    ])
    missing = missingness_report(matrix, records)
    reasons = missing.set_index(["ticker", "metric"])["missing_reason"]
    assert reasons["2330", "revenue_cumulative_yoy"] == "DERIVED_METRIC_NOT_APPLICABLE"
    assert reasons["2330", "operating_margin"] == "DIVISION_INVALID"
    assert reasons["2330", "revenue_yoy"] == "PRIOR_PERIOD_UNAVAILABLE"
    assert reasons["9999", "revenue"] == "NO_SOURCE_RECORD"


def test_missingness_marks_not_yet_published_with_evidence():
    records = pd.DataFrame([{
        "ticker": "2330", "metric": "revenue", "value": 1.0, "fiscal_year": 2025, "quarter": 1,
        "period_end": "2025-03-31", "publication_date": "2025-05-01", "available_date": "2025-05-02",
        "REPORTING_BASIS": "STANDALONE", "source_record_id": "rev-future",
    }])
    matrix = pd.DataFrame([{"asof_date": "2025-04-30", "ticker": "2330", "revenue": float("nan")}])
    missing = missingness_report(matrix, records)
    row = missing.iloc[0]
    assert row["missing_reason"] == "NOT_YET_PUBLISHED"
    assert row["raw_source_exists"] == True
    assert row["source_record_id"] == "rev-future"


def test_three_coverage_denominators_are_distinct():
    records = normalize_financial_records(_source(), _publications())
    matrix, _ = build_matrix(records, ["2330", "2317"], ["2025-01-11", "2025-04-11"])
    by_metric, by_ticker, yearly = coverage_reports(matrix, records, ["2330", "2317"])
    assert {"source_coverage_denominator_tickers", "metric_coverage_denominator_observations", "factor_matrix_coverage_denominator_observations"}.issubset(by_metric.columns)
    assert {"numerator_observations", "denominator_observations"}.issubset(by_ticker.columns)
    assert {"calendar_year", "numerator_observations", "denominator_observations"}.issubset(yearly.columns)


def test_yearly_coverage_denominator_is_target_by_date():
    records = normalize_financial_records(_source(), _publications())
    matrix, _ = build_matrix(records, ["2330", "2317"], ["2024-01-11", "2024-04-11", "2025-01-11"])
    _, _, yearly = coverage_reports(matrix, records, ["2330", "2317"])
    assert set(yearly.loc[yearly["calendar_year"] == 2024, "denominator_observations"]) == {4}
    assert set(yearly.loc[yearly["calendar_year"] == 2025, "denominator_observations"]) == {2}


def test_quality_gates_report_independently():
    records = normalize_financial_records(_source(), _publications())
    matrix, _ = build_matrix(records, ["2330"], ["2025-01-11"])
    missing = missingness_report(matrix, records)
    gates = quality_gates(target_pass=True, ledger_pass=True, records=records, matrix=matrix, missingness=missing, reproducibility="PASS", source_sanity=True)
    assert all(value == "PASS" for value in gates.values())
