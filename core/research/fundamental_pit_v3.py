"""Small, deterministic v3 fundamental dataset transforms and quality gates."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Iterable, Mapping

import pandas as pd

from core.research.fundamental_pit import clean_ticker, validate_pit_order


RAW_METRICS = ("revenue", "operating_profit", "eps")
UNSUPPORTED_METRICS = (
    "net_income", "total_assets", "total_liabilities", "total_equity",
    "shares_outstanding", "market_cap",
)
MISSINGNESS_CATEGORIES = frozenset({
    "NO_SOURCE_RECORD", "NOT_YET_PUBLISHED", "METRIC_NOT_REPORTED",
    "PRIOR_PERIOD_UNAVAILABLE", "DERIVED_METRIC_NOT_APPLICABLE",
    "REPORTING_BASIS_NOT_APPLICABLE",
    "INVALID_RESPONSE", "PARSER_FAILURE", "MAPPING_FAILURE",
    "UNSUPPORTED_IDENTIFIER", "MISSING_MARKET_CAP", "DIVISION_INVALID", "OTHER",
})
DERIVED_BASE_METRICS = {
    "revenue_yoy": "revenue",
    "revenue_cumulative_yoy": "revenue",
    "operating_income_yoy": "operating_profit",
    "operating_income_cumulative_yoy": "operating_profit",
    "eps_yoy": "eps",
}


def ticker_hash(tickers: Iterable[object]) -> str:
    values = tuple(sorted({clean_ticker(value) for value in tickers if clean_ticker(value)}))
    return hashlib.sha256("\n".join(values).encode("utf-8")).hexdigest()


def standalone_values(raw: pd.DataFrame) -> pd.DataFrame:
    """Convert reliable cumulative income statement values to standalone values."""

    work = raw.copy()
    work["ticker"] = work["ticker"].map(clean_ticker)
    for column in RAW_METRICS:
        if column in work:
            work[column] = pd.to_numeric(work[column], errors="coerce")
    result: list[pd.DataFrame] = []
    for (ticker, year, metric), group in work.groupby(["ticker", "fiscal_year", "metric"], sort=False):
        group = group.sort_values("quarter", kind="stable").copy()
        quarters = [int(value) for value in group["quarter"]]
        reliable = metric == "eps" or all(q - 1 in quarters for q in quarters if q > 1)
        if metric == "eps" or not reliable:
            group["value"] = group["source_value"]
            group["REPORTING_BASIS"] = "CUMULATIVE"
        else:
            unique = group.sort_values("quarter").drop_duplicates("quarter", keep="last").set_index("quarter")
            previous = unique["source_value"].shift(1)
            group["value"] = group["source_value"] - group["quarter"].map(previous)
            group.loc[group["quarter"].eq(1), "value"] = group.loc[group["quarter"].eq(1), "source_value"]
            group["REPORTING_BASIS"] = "STANDALONE"
        result.append(group)
    return pd.concat(result, ignore_index=True) if result else work.iloc[0:0].copy()


def _period_start(year: int, quarter: int) -> str:
    return f"{year:04d}-{(quarter - 1) * 3 + 1:02d}-01"


def _revision_id(source_record_id: object, publication_timestamp: object) -> str:
    value = f"{source_record_id}|{publication_timestamp}"
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:16]


def normalize_financial_records(financial: pd.DataFrame, publications: pd.DataFrame) -> pd.DataFrame:
    """Join source values to publication evidence and emit revision-preserving long form."""

    if financial.empty or publications.empty:
        return pd.DataFrame()
    source = financial.copy()
    source["ticker"] = source["ticker"].map(clean_ticker)
    source["fiscal_year"] = pd.to_numeric(source["fiscal_year"], errors="coerce").astype("Int64")
    source["quarter"] = pd.to_numeric(source["quarter"], errors="coerce").astype("Int64")
    pub = publications.copy()
    pub["ticker"] = pub["ticker"].map(clean_ticker)
    pub["fiscal_year"] = pd.to_numeric(pub["fiscal_year"], errors="coerce").astype("Int64")
    pub["quarter"] = pd.to_numeric(pub["quarter"], errors="coerce").astype("Int64")
    joined = pub.merge(source, on=["ticker", "fiscal_year", "quarter"], how="inner", suffixes=("", "_source"))
    rows: list[dict[str, object]] = []
    for _, item in joined.iterrows():
        for metric in RAW_METRICS:
            if metric not in item or pd.isna(item[metric]):
                continue
            rows.append({
                "ticker": item["ticker"],
                "metric": metric,
                "value": float(item[metric]),
                "source_value": float(item[metric]),
                "period_start": _period_start(int(item["fiscal_year"]), int(item["quarter"])),
                "period_end": item["period_end"],
                "fiscal_year": int(item["fiscal_year"]),
                "quarter": int(item["quarter"]),
                "publication_date": item["publication_date"],
                "publication_timestamp": item.get("publication_timestamp", ""),
                "available_date": item["available_date"],
                "source": item.get("source_system", "MOPS"),
                "source_record_id": item["source_record_id"],
                "revision_id": _revision_id(item["source_record_id"], item.get("publication_timestamp", "")),
                "retrieved_at": item.get("retrieved_at", ""),
                "data_version": "fundamental-pit-v3",
                "REPORTING_BASIS": "CUMULATIVE" if metric == "eps" else "STANDALONE",
            })
    result = pd.DataFrame(rows)
    if result.empty:
        return result
    result = standalone_values(result)
    ok, diagnostics = validate_pit_order(result)
    if not ok:
        raise ValueError(f"PIT_INTEGRITY failed: {diagnostics.to_dict('records')}")
    return result.sort_values(["ticker", "metric", "period_end", "publication_date"], kind="stable").reset_index(drop=True)


def add_derived_metrics(records: pd.DataFrame) -> pd.DataFrame:
    """Add only metrics whose required raw values are evidence-backed."""

    if records.empty:
        return records.copy()
    work = records.copy()
    latest = (
        work.sort_values(["ticker", "fiscal_year", "quarter", "metric", "publication_date", "revision_id"], kind="stable")
        .drop_duplicates(["ticker", "fiscal_year", "quarter", "metric"], keep="last")
    )
    latest_lookup = {
        (str(row.ticker), int(row.fiscal_year), int(row.quarter), str(row.metric)): row
        for row in latest.itertuples(index=False)
    }
    additions: list[dict[str, object]] = []
    for (ticker, year, quarter), group in work.groupby(["ticker", "fiscal_year", "quarter"], sort=False):
        values = group.sort_values(["publication_date", "revision_id"], kind="stable").drop_duplicates("metric", keep="last").set_index("metric")
        for metric, name in (("revenue", "revenue_yoy"), ("operating_profit", "operating_income_yoy"), ("eps", "eps_yoy")):
            if metric not in values.index:
                continue
            current = values.loc[metric]
            previous = latest_lookup.get((str(ticker), int(year) - 1, int(quarter), metric))
            if previous is None or pd.isna(previous.value) or float(previous.value) == 0:
                continue
            if metric != "eps" and current["REPORTING_BASIS"] == "CUMULATIVE":
                name = name.replace("_yoy", "_cumulative_yoy")
            item = current.to_dict()
            item.update({
                "metric": name,
                "value": float(current["value"]) / float(previous.value) - 1,
                "source_value": float(current["value"]) / float(previous.value) - 1,
                "data_version": "fundamental-pit-v3-derived",
            })
            additions.append(item)
        if {"revenue", "operating_profit"}.issubset(values.index):
            revenue = values.loc["revenue"]
            operating = values.loc["operating_profit"]
            if revenue["REPORTING_BASIS"] == operating["REPORTING_BASIS"] == "STANDALONE" and float(revenue["value"]) != 0:
                item = operating.to_dict()
                item.update({
                    "metric": "operating_margin",
                    "value": float(operating["value"]) / float(revenue["value"]),
                    "source_value": float(operating["value"]) / float(revenue["value"]),
                    "data_version": "fundamental-pit-v3-derived",
                })
                additions.append(item)
    if not additions:
        return work
    return pd.concat([work, pd.DataFrame(additions)], ignore_index=True)


def build_matrix(records: pd.DataFrame, target_tickers: Iterable[object], asof_dates: Iterable[object]) -> tuple[pd.DataFrame, dict[str, pd.DataFrame]]:
    """Backward as-of matrix keyed by availability, with no synthetic columns."""

    target = tuple(sorted({clean_ticker(value) for value in target_tickers if clean_ticker(value)}))
    dates = pd.DatetimeIndex(sorted(pd.to_datetime(list(asof_dates))))
    metrics = tuple(metric for metric in records["metric"].drop_duplicates() if records.loc[records["metric"].eq(metric), "value"].notna().any()) if not records.empty else ()
    matrices: dict[str, pd.DataFrame] = {}
    for metric in metrics:
        matrix = pd.DataFrame(index=dates, columns=target, dtype=float)
        for ticker in target:
            right = records.loc[(records["ticker"].eq(ticker)) & (records["metric"].eq(metric)) & records["value"].notna()].copy()
            if right.empty:
                continue
            right["available_date"] = pd.to_datetime(right["available_date"])
            right = right.sort_values(["available_date", "publication_date", "revision_id"], kind="stable")
            joined = pd.merge_asof(
                pd.DataFrame({"asof_date": dates}),
                right,
                left_on="asof_date",
                right_on="available_date",
                direction="backward",
                allow_exact_matches=True,
            )
            valid = (
                joined["value"].notna()
                & (pd.to_datetime(joined["period_end"], errors="coerce") < pd.to_datetime(joined["publication_date"], errors="coerce"))
            )
            if valid.any():
                matrix.loc[joined.loc[valid, "asof_date"], ticker] = joined.loc[valid, "value"].astype(float).to_numpy()
        if matrix.notna().any().any():
            matrices[metric] = matrix
    if not matrices:
        return pd.DataFrame(index=pd.MultiIndex.from_product([dates, target], names=["asof_date", "ticker"])), matrices
    index = pd.MultiIndex.from_product([dates, target], names=["asof_date", "ticker"])
    flattened = pd.DataFrame(index=index).reset_index()
    for metric, matrix in matrices.items():
        values = matrix.rename_axis("asof_date").rename_axis("ticker", axis=1).stack(dropna=False).rename(metric).reset_index()
        flattened = flattened.merge(values, on=["asof_date", "ticker"], how="left")
    return flattened, matrices


def _sorted_evidence(frame: pd.DataFrame) -> pd.DataFrame:
    if frame.empty:
        return frame
    return frame.assign(
        _available=pd.to_datetime(frame["available_date"], errors="coerce"),
        _publication=pd.to_datetime(frame["publication_date"], errors="coerce"),
    ).sort_values(["_available", "_publication", "source_record_id"], kind="stable")


def _missingness_metadata(records: pd.DataFrame, metrics: list[str]) -> dict[tuple[str, str], tuple[object, ...]]:
    """Precompute source evidence once; the matrix can contain millions of NaNs."""

    empty = records.iloc[0:0]
    by_ticker = {ticker: group for ticker, group in records.groupby("ticker", sort=False)} if not records.empty else {}
    by_metric = {
        (str(ticker), str(metric)): _sorted_evidence(group)
        for (ticker, metric), group in records.groupby(["ticker", "metric"], sort=False)
    } if not records.empty else {}
    metadata: dict[tuple[str, str], tuple[object, ...]] = {}

    def pack(reason: str, evidence: pd.DataFrame, first_available: object = None) -> tuple[object, ...]:
        evidence = _sorted_evidence(evidence)
        first = first_available
        if first is None and not evidence.empty:
            values = evidence["_available"].dropna()
            first = values.min() if not values.empty else None
        source_id = str(evidence["source_record_id"].iloc[0]) if not evidence.empty else ""
        first_date = first.date().isoformat() if first is not None and pd.notna(first) else ""
        return reason, first, bool(not evidence.empty), int(len(evidence)), first_date, source_id

    for ticker, ticker_records in by_ticker.items():
        for metric in metrics:
            direct = by_metric.get((str(ticker), metric), empty)
            if not direct.empty:
                metadata[(str(ticker), metric)] = pack("OTHER", direct)
                continue
            if metric in RAW_METRICS:
                metadata[(str(ticker), metric)] = pack("METRIC_NOT_REPORTED", ticker_records)
                continue
            if metric == "operating_margin":
                revenue = ticker_records.loc[ticker_records["metric"].eq("revenue")]
                operating = ticker_records.loc[ticker_records["metric"].eq("operating_profit")]
                if revenue.empty or operating.empty:
                    metadata[(str(ticker), metric)] = pack("METRIC_NOT_REPORTED", ticker_records)
                    continue
                pairs = revenue.merge(
                    operating,
                    on=["ticker", "fiscal_year", "quarter"],
                    suffixes=("_revenue", "_operating"),
                )
                standalone = pairs.loc[
                    pairs["REPORTING_BASIS_revenue"].eq("STANDALONE")
                    & pairs["REPORTING_BASIS_operating"].eq("STANDALONE")
                ]
                if standalone.empty:
                    reason = "REPORTING_BASIS_NOT_APPLICABLE"
                elif pd.to_numeric(standalone["value_revenue"], errors="coerce").eq(0).any():
                    reason = "DIVISION_INVALID"
                else:
                    reason = "DERIVED_METRIC_NOT_APPLICABLE"
                metadata[(str(ticker), metric)] = pack(reason, ticker_records)
                continue
            base_metric = DERIVED_BASE_METRICS.get(metric)
            base = by_metric.get((str(ticker), base_metric), empty) if base_metric else ticker_records
            if base.empty:
                metadata[(str(ticker), metric)] = pack("METRIC_NOT_REPORTED", ticker_records)
            elif metric.endswith("_cumulative_yoy") and not base["REPORTING_BASIS"].eq("CUMULATIVE").any():
                metadata[(str(ticker), metric)] = pack("DERIVED_METRIC_NOT_APPLICABLE", base)
            else:
                metadata[(str(ticker), metric)] = pack("PRIOR_PERIOD_UNAVAILABLE", base)
    return metadata


def missingness_report(matrix: pd.DataFrame, records: pd.DataFrame) -> pd.DataFrame:
    columns = [
        "asof_date", "ticker", "metric", "missing_reason", "raw_source_exists",
        "source_record_count", "first_available_date", "source_record_id",
    ]
    if matrix.empty:
        return pd.DataFrame(columns=columns)
    metrics = [column for column in matrix.columns if column not in {"asof_date", "ticker"}]
    work = records.copy()
    if not work.empty:
        work["ticker"] = work["ticker"].map(clean_ticker)
    metadata = _missingness_metadata(work, metrics)
    rows: list[pd.DataFrame] = []
    for metric in metrics:
        missing = matrix.loc[matrix[metric].isna(), ["asof_date", "ticker"]]
        if missing.empty:
            continue
        missing = missing.copy()
        missing["ticker"] = missing["ticker"].map(clean_ticker)
        missing["metric"] = metric
        detail_rows = []
        for ticker in missing["ticker"].drop_duplicates():
            detail = metadata.get((ticker, metric), ("NO_SOURCE_RECORD", None, False, 0, "", ""))
            detail_rows.append({
                "ticker": ticker,
                "_static_reason": detail[0],
                "_first_available": detail[1],
                "raw_source_exists": detail[2],
                "source_record_count": detail[3],
                "first_available_date": detail[4],
                "source_record_id": detail[5],
            })
        detail_frame = pd.DataFrame(detail_rows)
        missing = missing.merge(detail_frame, on="ticker", how="left", sort=False)
        missing["missing_reason"] = missing["_static_reason"]
        asof = pd.to_datetime(missing["asof_date"], errors="coerce")
        first_available = pd.to_datetime(missing["_first_available"], errors="coerce")
        missing.loc[first_available.notna() & first_available.gt(asof), "missing_reason"] = "NOT_YET_PUBLISHED"
        missing = missing.drop(columns=["_static_reason", "_first_available"])
        rows.append(missing[columns])
    return pd.concat(rows, ignore_index=True) if rows else pd.DataFrame(columns=columns)


def coverage_reports(matrix: pd.DataFrame, records: pd.DataFrame, target_tickers: Iterable[object]) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    target = tuple(sorted({clean_ticker(value) for value in target_tickers if clean_ticker(value)}))
    metrics = [column for column in matrix.columns if column not in {"asof_date", "ticker"}]
    dates = pd.to_datetime(matrix["asof_date"]).dt.date.unique() if not matrix.empty else []
    expected = len(target) * len(dates)
    by_metric: list[dict[str, object]] = []
    for metric in metrics:
        observed = int(matrix[metric].notna().sum())
        source_tickers = int(records.loc[records["metric"].eq(metric), "ticker"].nunique())
        complete = int(matrix[metrics].notna().all(axis=1).sum()) if metrics else 0
        by_metric.append({
            "metric": metric,
            "source_coverage_numerator_tickers": source_tickers,
            "source_coverage_denominator_tickers": len(target),
            "metric_coverage_numerator_observations": observed,
            "metric_coverage_denominator_observations": expected,
            "factor_matrix_coverage_numerator_observations": complete,
            "factor_matrix_coverage_denominator_observations": expected,
            "source_coverage": source_tickers / len(target) if target else None,
            "metric_coverage": observed / expected if expected else None,
            "factor_matrix_coverage": complete / expected if expected else None,
        })
    by_ticker: list[dict[str, object]] = []
    for ticker in target:
        item = matrix.loc[matrix["ticker"].eq(ticker)]
        for metric in metrics:
            observed = int(item[metric].notna().sum())
            by_ticker.append({"ticker": ticker, "metric": metric, "numerator_observations": observed, "denominator_observations": len(dates), "coverage": observed / len(dates) if len(dates) else None})
    yearly: list[dict[str, object]] = []
    if not matrix.empty:
        work = matrix.assign(calendar_year=pd.to_datetime(matrix["asof_date"]).dt.year)
        for year, group in work.groupby("calendar_year", sort=True):
            denominator = len(target) * group["asof_date"].nunique()
            for metric in metrics:
                observed = int(group[metric].notna().sum())
                yearly.append({"calendar_year": int(year), "metric": metric, "numerator_observations": observed, "denominator_observations": denominator, "coverage": observed / denominator if denominator else None})
    return pd.DataFrame(by_metric), pd.DataFrame(by_ticker), pd.DataFrame(yearly)


def write_dataset_manifest(path: str | Path, *, target_count: int, target_sha256: str, records: pd.DataFrame, matrix: pd.DataFrame, blocked_metrics: Iterable[str], artifact_hashes: Mapping[str, str]) -> dict[str, object]:
    payload = {
        "schema_version": "fundamental-pit-v3",
        "dataset_version": "fundamental-pit-v3",
        "dataset_revision": 1,
        "remediation_revision": "fundamental-pit-v3-r1",
        "target_ticker_count": target_count,
        "target_ticker_sha256": target_sha256,
        "supported_metrics": sorted(set(matrix.columns) - {"asof_date", "ticker"}),
        "blocked_metrics": list(blocked_metrics),
        "normalized_record_count": int(len(records)),
        "matrix_row_count": int(len(matrix)),
        "artifact_sha256": dict(artifact_hashes),
    }
    Path(path).write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    return payload


def quality_gates(*, target_pass: bool, ledger_pass: bool, records: pd.DataFrame, matrix: pd.DataFrame, missingness: pd.DataFrame, reproducibility: str, source_sanity: bool) -> dict[str, str]:
    pit_pass, _ = validate_pit_order(records) if not records.empty else (False, pd.DataFrame())
    expected_nan = int(matrix.drop(columns=["asof_date", "ticker"], errors="ignore").isna().sum().sum())
    gates = {
        "SCHEMA_INTEGRITY": "PASS" if set(matrix.columns) >= {"asof_date", "ticker"} and len(set(matrix.columns) - {"asof_date", "ticker"}) > 0 else "FAIL",
        "PIT_INTEGRITY": "PASS" if pit_pass else "FAIL",
        "PUBLICATION_ALIGNMENT": "PASS" if pit_pass else "FAIL",
        "REVISION_INTEGRITY": "PASS" if not records.empty and records["revision_id"].notna().all() else "FAIL",
        "MISSINGNESS_RECONCILIATION": "PASS" if expected_nan == len(missingness) else "FAIL",
        "TARGET_ACCOUNTING": "PASS" if target_pass else "FAIL",
        "REPRODUCIBILITY": reproducibility,
        "SOURCE_SANITY": "PASS" if ledger_pass and source_sanity else "FAIL",
    }
    return gates


__all__ = [
    "MISSINGNESS_CATEGORIES", "RAW_METRICS", "UNSUPPORTED_METRICS", "add_derived_metrics",
    "build_matrix", "coverage_reports", "missingness_report", "normalize_financial_records",
    "quality_gates", "standalone_values", "ticker_hash", "write_dataset_manifest",
]
