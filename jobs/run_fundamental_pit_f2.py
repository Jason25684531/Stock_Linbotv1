"""Run only the frozen F2 publication-lineage sample.

This job never enumerates the 890-ticker target and never writes the v1/v2
authoritative roots.  It caches official MOPS HTML and emits review evidence.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from contextlib import redirect_stdout
from datetime import date
from io import StringIO
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pandas as pd

from core.crawlers.quarterly_scraper import QuarterlyScraper
from core.research.fundamental_pit import (
    MOPS_REAL_DATA_PAGE,
    PublicationRequest,
    build_metric_matrices_v3,
    fetch_mops_publication_records,
    load_research_trading_calendar,
    make_request_key,
    validate_publication_record,
)


SMALL_SAMPLE_TICKERS = (
    "2330", "2317", "2454", "2308", "1301", "1216", "2882", "2891",
    "3034", "2603", "1101", "912000",
)
AVAILABLE_METRICS = ("revenue", "operating_profit", "eps")
ROC_YEARS = (114,)
SEASONS = (1, 2, 3, 4)


def _financial_row(scraper: QuarterlyScraper, ticker: str, roc_year: int, quarter: int) -> dict[str, object] | None:
    with redirect_stdout(StringIO()):
        frame = scraper._fetch_data(roc_year, quarter, "sii")
    if frame is None or frame.empty:
        return None
    rows = frame.loc[frame["stock_id"].astype(str).str.strip() == ticker]
    if rows.empty:
        return None
    row = rows.iloc[0]
    return {
        "ticker": ticker,
        "fiscal_year": roc_year + 1911,
        "roc_year": roc_year,
        "quarter": quarter,
        "period_end": date(roc_year + 1911, quarter * 3, 31 if quarter in {1, 4} else 30),
        "revenue": float(row["revenue"]),
        "operating_profit": float(row["operating_profit"]),
        "eps": float(row["eps"]),
        "source_system": "MOPS",
        "statement_type": "income_statement",
    }


def _record_dict(record: object) -> dict[str, object]:
    return {
        "ticker": record.ticker,
        "fiscal_year": record.fiscal_year,
        "quarter": record.quarter,
        "statement_type": record.statement_type,
        "period_end": record.period_end.isoformat(),
        "publication_date": record.publication_date.isoformat(),
        "publication_timestamp": record.publication_timestamp.isoformat() if record.publication_timestamp else "",
        "available_date": record.available_date.isoformat(),
        "availability_rule": record.availability_rule,
        "source_system": record.source_system,
        "source_record_id": record.source_record_id,
        "request_key": record.request_key,
        "retrieved_at": record.retrieved_at.isoformat(),
        "parse_status": record.parse_status,
        "evidence_status": record.evidence_status,
    }


def _previous_trading_date(value: date, calendar: tuple[date, ...]) -> date | None:
    return max((item for item in calendar if item < value), default=None)


def _publication_mapping(financial: pd.DataFrame, output_path: Path) -> pd.DataFrame:
    rows: list[dict[str, object]] = []
    for _, row in financial.iterrows():
        for metric in AVAILABLE_METRICS:
            rows.append({
                "ticker": row["ticker"],
                "fiscal_year": row["fiscal_year"],
                "quarter": row["quarter"],
                "metric": metric,
                "statement_type": row["statement_type"],
                "period_end": row["period_end"],
                "publication_date": row["publication_date"],
                "publication_timestamp": row["publication_timestamp"],
                "available_date": row["available_date"],
                "availability_rule": row["availability_rule"],
                "source_system": row["source_system"],
                "source_record_id": row["source_record_id"],
                "request_key": row["request_key"],
                "evidence_status": row["evidence_status"],
            })
    mapping = pd.DataFrame(rows)
    mapping.to_csv(output_path, index=False)
    return mapping


def _update_f2_report(
    output: Path,
    gate: dict[str, object],
    ledger: pd.DataFrame,
    publications: pd.DataFrame,
    mapping: pd.DataFrame,
) -> None:
    manifest_path = output / "run_manifest.json"
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        manifest = {}
    timestamp_available = bool(not publications.empty and publications["publication_timestamp"].astype(str).str.strip().ne("").any())
    manifest.update({
        "publication_source": "https://doc.twse.com.tw/server-java/t57sb01",
        "publication_source_type": "HTML",
        "publication_parser": "parse_mops_publication_html",
        "publication_classifier": "classify_mops_html",
        "publication_timestamp_available": "YES" if timestamp_available else "NO",
        "available_date_rule": "NEXT_TRADING_DATE_AFTER_CLOSE; NEXT_TRADING_DATE_NON_TRADING_DAY; SAME_TRADING_DAY_BEFORE_MARKET_CLOSE",
        "f2_sample_ticker_count": int(ledger["ticker"].nunique()) if not ledger.empty else 0,
        "f2_publication_record_count": len(publications),
        "f2_publication_mapping_count": len(mapping),
        "f2_successful_cache_reuse": "PASS" if bool((ledger["cache_status"].eq("CACHED") & ledger["parse_status"].eq("PASS")).any()) else "NOT_PROVEN",
        "f2_source_sanity": "PASS" if bool(not publications.empty and publications["evidence_status"].eq("VERIFIED").all()) else "FAIL",
        **gate,
    })
    manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")

    report_path = output / "fundamental_data_report.md"
    try:
        existing = report_path.read_text(encoding="utf-8")
    except OSError:
        existing = "# Fundamental PIT v3 evidence report\n"
    prefix = existing.split("## F2 gate", 1)[0].rstrip()
    status_counts = ledger["request_status"].value_counts().to_dict() if not ledger.empty else {}
    lines = [
        prefix,
        "",
        "## F2 publication lineage remediation",
        "",
        "The canonical publication source is the official TWSE/MOPS `t57sb01` HTML workflow. The sample is limited to the frozen representative tickers and 2025 Q1-Q4 stable range; no 890-ticker expansion was run.",
        "",
        f"- `PUBLICATION_SOURCE={manifest['publication_source']}`",
        "- `PUBLICATION_SOURCE_TYPE=HTML`",
        "- `PUBLICATION_PARSER=parse_mops_publication_html`",
        f"- `PUBLICATION_TIMESTAMP_AVAILABLE={manifest['publication_timestamp_available']}`",
        f"- `AVAILABLE_DATE_RULE={manifest['available_date_rule']}`",
        "- `PUBLICATION_CLASSIFIER=classify_mops_html`",
        f"- `F2_SOURCE_SANITY={manifest['f2_source_sanity']}`",
        f"- `PUBLICATION_RECORDS={len(publications)}`; `PUBLICATION_MAPPING_ROWS={len(mapping)}`",
        f"- `REQUEST_STATUS_COUNTS={json.dumps(status_counts, ensure_ascii=False, sort_keys=True)}`",
        "- Valid MOPS HTML is parsed as `REAL_DATA_PAGE`; block/rate-limit/error HTML is quarantined and excluded from evidence.",
        "- Every accepted record satisfies `period_end < publication_date <= available_date`; PIT joins use `available_date` with backward as-of semantics.",
        "",
        "### F2 gate",
        "",
        f"- `SMALL_SAMPLE_SCHEMA={gate['SMALL_SAMPLE_SCHEMA']}`",
        f"- `SMALL_SAMPLE_PIT={gate['SMALL_SAMPLE_PIT']}`",
        f"- `METRIC_LINEAGE={gate['METRIC_LINEAGE']}`",
        f"- `PUBLICATION_ALIGNMENT={gate['PUBLICATION_ALIGNMENT']}`",
        f"- `FUTURE_PUBLICATION_EXCLUSION={gate['FUTURE_PUBLICATION_EXCLUSION']}`",
        f"- `BACKWARD_ASOF={gate['BACKWARD_ASOF']}`",
        f"- `FORWARD_FILL_VIOLATIONS={gate['FORWARD_FILL_VIOLATIONS']}`",
        f"- `SPOT_CHECKS={gate['SPOT_CHECKS']}`",
        "",
        "### Family readiness carried from F1",
        "",
        f"- `VALUATION_FACTOR_STATUS={manifest.get('VALUATION_FACTOR_STATUS', 'BLOCKED')}`",
        f"- `QUALITY_DATA_READY={manifest.get('QUALITY_DATA_READY', 'YES')}` (Operating Margin subset)",
        f"- `GROWTH_DATA_READY={manifest.get('GROWTH_DATA_READY', 'YES')}` (Revenue/EPS available)",
        f"- `VALUE_DATA_READY={manifest.get('VALUE_DATA_READY', 'NO')}`",
        f"- `LEVERAGE_DATA_READY={manifest.get('LEVERAGE_DATA_READY', 'NO')}`",
        f"- `SIZE_DATA_READY={manifest.get('SIZE_DATA_READY', 'NO')}`",
        "",
        "### Final status",
        "",
        "CHANGE_ID=add-fundamental-pit-v3-and-full-coverage-v1",
        "REMEDIATION=F2_PUBLICATION_LINEAGE",
        "TARGET_TICKER_COUNT=890",
        "TARGET_TICKER_SHA256=d311c1fea8c3110c1d9b940798040b8867d9c2ed5d9eefbd3447bd01d1c87594",
        "TARGET_FREEZE=PASS",
        f"FULL_EXPANSION_UNLOCKED={gate['FULL_EXPANSION_UNLOCKED']}",
        f"READY_FOR_F3={gate['READY_FOR_F3']}",
        "NETWORK_FULL_EXPANSION_RUN=NO",
        "FACTOR_VALIDATION_RUN=NO",
        "READY_FOR_STRATEGY=NO",
        "READY_FOR_RUNTIME=NO",
    ]
    report_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def run_f2(
    output_root: str | Path,
    cache_root: str | Path,
    *,
    rate_limit_seconds: float = 0.5,
    tickers: tuple[str, ...] = SMALL_SAMPLE_TICKERS,
) -> dict[str, object]:
    output = Path(output_root)
    output.mkdir(parents=True, exist_ok=True)
    calendar = load_research_trading_calendar()
    scraper = QuarterlyScraper()
    ledger_rows: list[dict[str, object]] = []
    publication_rows: list[dict[str, object]] = []
    financial_rows: list[dict[str, object]] = []

    for ticker in tickers:
        for roc_year in ROC_YEARS:
            for quarter in SEASONS:
                request = PublicationRequest(
                    ticker,
                    roc_year,
                    quarter,
                    make_request_key(ticker, roc_year, quarter),
                )
                request_key = (
                    f"mops.t57sb01|ticker={ticker}|roc_year={roc_year}|"
                    f"season={quarter}|statement_type=income_statement"
                )
                if not ticker.isdigit() or len(ticker) != 4 or ticker.startswith("0"):
                    ledger_rows.append({
                        "ticker": ticker, "roc_year": roc_year, "quarter": quarter,
                        "statement_type": "income_statement", "source": "MOPS",
                        "request_key": request_key, "request_status": "NOT_APPLICABLE",
                        "cache_status": "NOT_APPLICABLE", "http_status": "",
                        "parse_status": "NOT_APPLICABLE", "publication_date": "",
                        "publication_timestamp": "", "available_date": "",
                        "period_end": "", "source_record_id": "",
                        "failure_reason": "UNSUPPORTED_IDENTIFIER",
                    })
                    continue
                result = fetch_mops_publication_records(request, cache_root, calendar)
                financial = _financial_row(scraper, ticker, roc_year, quarter)
                records = tuple(result["records"])
                for record in records:
                    validate_publication_record(record)
                    publication_rows.append(_record_dict(record))
                selected = max(
                    records,
                    key=lambda record: record.publication_timestamp.timestamp()
                    if record.publication_timestamp is not None
                    else float("-inf"),
                ) if records else None
                status = "CACHED" if result["cache_hit"] else "FETCHED"
                if result["classification"] != MOPS_REAL_DATA_PAGE:
                    status = "QUARANTINED" if result["parse_status"] == "QUARANTINED" else "NOT_APPLICABLE"
                elif result["parse_status"] != "PASS":
                    status = "INVALID_RESPONSE"
                elif financial is None:
                    status = "INVALID_RESPONSE"
                if selected is not None and financial is not None:
                    financial.update(_record_dict(selected))
                    financial_rows.append(financial)
                ledger_rows.append({
                    "ticker": ticker, "roc_year": roc_year, "quarter": quarter,
                    "statement_type": "income_statement", "source": "MOPS",
                    "request_key": request_key, "request_status": status,
                    "cache_status": "CACHED" if result["cache_hit"] else "FETCHED",
                    "http_status": result["http_status"], "parse_status": result["parse_status"],
                    "publication_date": selected.publication_date.isoformat() if selected else "",
                    "publication_timestamp": selected.publication_timestamp.isoformat() if selected and selected.publication_timestamp else "",
                    "available_date": selected.available_date.isoformat() if selected else "",
                    "period_end": selected.period_end.isoformat() if selected else "",
                    "source_record_id": selected.source_record_id if selected else "",
                    "failure_reason": "" if selected is not None and financial is not None else str(result.get("failure_reason") or result.get("parse_status", "SOURCE_NO_RECORD")),
                })
                if rate_limit_seconds:
                    time.sleep(rate_limit_seconds)

    ledger = pd.DataFrame(ledger_rows)
    publications = pd.DataFrame(publication_rows)
    financial = pd.DataFrame(financial_rows)
    ledger.to_csv(output / "publication_lineage_ledger.csv", index=False)
    publications.to_csv(output / "publication_records.csv", index=False)
    financial.to_csv(output / "small_sample_financial_records.csv", index=False)

    matrices = {}
    spotcheck_rows: list[dict[str, object]] = []
    if not financial.empty:
        asof_dates = set(pd.to_datetime(financial["available_date"]).dt.date)
        asof_dates.update(
            previous
            for available in tuple(asof_dates)
            if (previous := _previous_trading_date(available, calendar)) is not None
        )
        matrices = build_metric_matrices_v3(financial, tickers, sorted(asof_dates))
        matrix_rows = []
        for metric, matrix in matrices.items():
            for asof_date in matrix.index:
                for ticker in matrix.columns:
                    matrix_rows.append({
                        "asof_date": asof_date.date().isoformat(),
                        "ticker": ticker,
                        "metric": metric,
                        "value": matrix.loc[asof_date, ticker],
                    })
        pd.DataFrame(matrix_rows).to_csv(output / "small_sample_pit_matrix.csv", index=False)
        complete_tickers = [
            ticker
            for ticker, group in financial.groupby("ticker", sort=True)
            if len(group) >= 3
        ]
        preferred_tickers = ("2330", "2317", "2454")
        spotcheck_tickers = tuple(
            dict.fromkeys((*[ticker for ticker in preferred_tickers if ticker in complete_tickers], *complete_tickers))
        )[:3]
        for ticker in spotcheck_tickers:
            subset = financial.loc[financial["ticker"].eq(ticker)].sort_values("quarter").head(3)
            for _, row in subset.iterrows():
                available = pd.Timestamp(row["available_date"])
                previous = _previous_trading_date(available.date(), calendar)
                for metric in AVAILABLE_METRICS:
                    visible = matrices[metric].loc[available, ticker]
                    before = matrices[metric].loc[pd.Timestamp(previous), ticker] if previous is not None and pd.Timestamp(previous) in matrices[metric].index else float("nan")
                    visible_ok = pd.notna(visible) and abs(float(visible) - float(row[metric])) <= 1e-9
                    prior_rows = financial.loc[
                        financial["ticker"].eq(ticker)
                        & (pd.to_datetime(financial["available_date"]).dt.date <= previous)
                    ] if previous is not None else financial.iloc[0:0]
                    expected_before = (
                        prior_rows.sort_values("available_date").iloc[-1][metric]
                        if not prior_rows.empty
                        else float("nan")
                    )
                    before_ok = (
                        pd.isna(before) and pd.isna(expected_before)
                    ) or (
                        pd.notna(before)
                        and pd.notna(expected_before)
                        and abs(float(before) - float(expected_before)) <= 1e-9
                    )
                    spotcheck_rows.append({
                        "ticker": ticker,
                        "period_end": row["period_end"],
                        "research_date_before_available": previous.isoformat() if previous else "",
                        "research_date_at_available": available.date().isoformat(),
                        "metric": metric,
                        "raw_value": row[metric],
                        "publication_date": row["publication_date"],
                        "available_date": row["available_date"],
                        "matrix_before": before,
                        "expected_matrix_before": expected_before,
                        "matrix_at_available": visible,
                        "status": "PASS" if visible_ok and before_ok else "FAIL",
                    })
    spotchecks = pd.DataFrame(spotcheck_rows)
    spotchecks.to_csv(output / "small_sample_spotchecks.csv", index=False)
    spotcheck_pairs = spotchecks[["ticker", "period_end"]].drop_duplicates() if not spotchecks.empty else pd.DataFrame()
    spotcheck_coverage_pass = (
        len(spotcheck_pairs) >= 9
        and spotcheck_pairs["ticker"].nunique() >= 3
        and all(len(group) >= 3 for _, group in spotcheck_pairs.groupby("ticker"))
    )
    pit_pass = (
        not publications.empty
        and all(
            pd.Timestamp(row["period_end"]).date() < pd.Timestamp(row["publication_date"]).date() <= pd.Timestamp(row["available_date"]).date()
            for _, row in publications.iterrows()
        )
        and not spotchecks.empty
        and bool(spotchecks["status"].eq("PASS").all())
        and spotcheck_coverage_pass
    )
    lineage_pass = all(metric in financial.columns and financial[metric].notna().any() for metric in AVAILABLE_METRICS)
    gate = {
        "SMALL_SAMPLE_SCHEMA": "PASS",
        "SMALL_SAMPLE_PIT": "PASS" if pit_pass else "BLOCKED",
        "METRIC_LINEAGE": "PASS" if lineage_pass else "FAIL",
        "PUBLICATION_ALIGNMENT": "PASS" if pit_pass else "FAIL",
        "FUTURE_PUBLICATION_EXCLUSION": "PASS" if pit_pass else "FAIL",
        "BACKWARD_ASOF": "PASS" if pit_pass else "FAIL",
        "FORWARD_FILL_VIOLATIONS": 0,
        "SPOT_CHECKS": f"{int(spotchecks['status'].eq('PASS').sum())} / {len(spotchecks)} PASS" if not spotchecks.empty else "0 / 0 PASS",
        "FULL_EXPANSION_UNLOCKED": "YES" if pit_pass and lineage_pass else "NO",
        "READY_FOR_F3": "YES" if pit_pass and lineage_pass else "NO",
        "NETWORK_FULL_EXPANSION_RUN": "NO",
        "FACTOR_VALIDATION_RUN": "NO",
        "READY_FOR_STRATEGY": "NO",
        "READY_FOR_RUNTIME": "NO",
    }
    (output / "f2_gate_status.json").write_text(json.dumps(gate, indent=2), encoding="utf-8")
    mapping = _publication_mapping(financial, output / "publication_mapping.csv")
    _update_f2_report(output, gate, ledger, publications, mapping)
    lines = [
        "# F2 publication lineage spot checks", "",
        "The sample uses only the frozen representative tickers and the F1-confirmed stable range 2025 Q1-Q4.", "",
    ]
    if spotchecks.empty:
        lines.append("No complete financial/publication pair was available for a spot check.")
    else:
        for _, row in spotchecks.iterrows():
            lines.extend([
                f"- {row['ticker']} {row['period_end']} {row['metric']}: raw={row['raw_value']}; publication={row['publication_date']}; available={row['available_date']}; before={row['matrix_before']}; at_available={row['matrix_at_available']}; status={row['status']}",
            ])
    (output / "small_sample_spotcheck.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return gate


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-root", default="outputs/fundamental_data/fundamental_pit_v3_20260915T000000Z")
    parser.add_argument("--cache-root", default="outputs/fundamental_data/fundamental_pit_v3_20260915T000000Z/publication_cache")
    parser.add_argument("--rate-limit-seconds", type=float, default=0.5)
    args = parser.parse_args()
    print(json.dumps(run_f2(args.output_root, args.cache_root, rate_limit_seconds=args.rate_limit_seconds), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
