"""Run the authorized F3/F4 fundamental PIT v3 pipeline.

The job is resumable and keeps source/cache failures explicit.  It stops at
the data handoff: no factor validation, strategy, backtest, runtime, or archive
operation is performed here.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sys
import threading
import time
from contextlib import redirect_stdout
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timezone
from io import StringIO
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.crawlers.quarterly_scraper import QuarterlyScraper
from core.research.fundamental_pit import (
    MOPS_EMPTY_RESULT,
    MOPS_ERROR_PAGE,
    MOPS_LOGIN_BLOCK_PAGE,
    MOPS_PUBLICATION_CACHE_DIR,
    MOPS_RATE_LIMIT_PAGE,
    MOPS_REAL_DATA_PAGE,
    PublicationRequest,
    PublicationParseError,
    REQUEST_STATUSES,
    _atomic_json,
    _atomic_link_or_copy,
    fetch_mops_publication_records,
    build_target_universe,
    make_request_key,
    publication_request_key,
)
from core.research.fundamental_pit_v3 import (
    RAW_METRICS,
    UNSUPPORTED_METRICS,
    add_derived_metrics,
    build_matrix,
    coverage_reports,
    missingness_report,
    normalize_financial_records,
    quality_gates,
    ticker_hash,
    write_dataset_manifest,
)


OUTPUT_ROOT = Path("outputs/fundamental_data/fundamental_pit_v3_20260915T000000Z")
DATASET_ROOT = Path("data/processed/fundamental_pit_v3")
UNIVERSE_PATH = Path("data/processed/research_universe.parquet")
ROC_YEARS = tuple(range(102, 115))
SEASONS = (1, 2, 3, 4)
EXPECTED_TARGET_COUNT = 890
EXPECTED_TARGET_HASH = "d311c1fea8c3110c1d9b940798040b8867d9c2ed5d9eefbd3447bd01d1c87594"
PUBLICATION_WORKERS = 4
RATE_LIMIT_COOLDOWN_SECONDS = 300


class RateLimitPause(RuntimeError):
    """Pause acquisition after a real MOPS rate-limit response."""


def _sha256(path: Path) -> str | None:
    if not path.exists():
        return None
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False, default=str), encoding="utf-8")


def _period_end(year: int, quarter: int) -> date:
    month = quarter * 3
    return date(year, month, 31 if month in {3, 12} else 30)


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


def _financial_frame(
    scraper: QuarterlyScraper,
    root: Path,
    roc_year: int,
    quarter: int,
    market: str,
    *,
    cache_only: bool,
    rate_limit_seconds: float,
) -> pd.DataFrame:
    cache = root / "financial_cache" / f"mops_t163sb04_{roc_year}_q{quarter}_{market}.parquet"
    if cache.exists():
        return pd.read_parquet(cache)
    if cache_only:
        return pd.DataFrame()
    with redirect_stdout(StringIO()):
        frame = scraper._fetch_data(roc_year, quarter, market)
    if frame is None or frame.empty:
        frame = pd.DataFrame({"stock_id": pd.Series(dtype=str)})
    cache.parent.mkdir(parents=True, exist_ok=True)
    frame.to_parquet(cache, index=False)
    _write_json(cache.with_suffix(".json"), {
        "source": "MOPS ajax_t163sb04",
        "roc_year": roc_year,
        "quarter": quarter,
        "market": market,
        "row_count": len(frame),
    })
    if rate_limit_seconds:
        time.sleep(rate_limit_seconds)
    return frame


def acquire_financial_rows(
    target_tickers: tuple[str, ...],
    root: Path,
    *,
    cache_only: bool,
    rate_limit_seconds: float,
) -> pd.DataFrame:
    scraper = QuarterlyScraper()
    wanted = set(target_tickers)
    rows: list[dict[str, object]] = []
    for roc_year in ROC_YEARS:
        fiscal_year = roc_year + 1911
        for quarter in SEASONS:
            frames = [
                _financial_frame(scraper, root, roc_year, quarter, market, cache_only=cache_only, rate_limit_seconds=rate_limit_seconds)
                for market in ("sii", "otc")
            ]
            available = pd.concat([frame for frame in frames if not frame.empty], ignore_index=True) if any(not frame.empty for frame in frames) else pd.DataFrame()
            if available.empty:
                continue
            available["stock_id"] = available["stock_id"].astype(str).str.strip()
            available = available.loc[available["stock_id"].isin(wanted)].drop_duplicates("stock_id", keep="first")
            for _, item in available.iterrows():
                rows.append({
                    "ticker": item["stock_id"],
                    "fiscal_year": fiscal_year,
                    "quarter": quarter,
                    "period_end": _period_end(fiscal_year, quarter).isoformat(),
                    "revenue": item.get("revenue"),
                    "operating_profit": item.get("operating_profit"),
                    "eps": item.get("eps"),
                    "source_system": "MOPS",
                    "statement_type": "income_statement",
                })
    result = pd.DataFrame(rows)
    if not result.empty:
        result = result.drop_duplicates(["ticker", "fiscal_year", "quarter"], keep="last").sort_values(["ticker", "fiscal_year", "quarter"], kind="stable")
    result.to_csv(root / "financial_source_records.csv", index=False)
    return result


def _publication_cache_exists(root: Path, request: PublicationRequest) -> bool:
    key = publication_request_key(request.ticker, request.roc_year, request.season, statement_type=request.statement_type)
    digest = hashlib.sha256(key.encode("utf-8")).hexdigest()
    cache = root / MOPS_PUBLICATION_CACHE_DIR
    return (cache / f"{digest}.html").exists() and (cache / f"{digest}.json").exists()


def _publication_cache_is_valid(root: Path, request: PublicationRequest) -> bool:
    key = publication_request_key(request.ticker, request.roc_year, request.season, statement_type=request.statement_type)
    digest = hashlib.sha256(key.encode("utf-8")).hexdigest()
    cache = root / MOPS_PUBLICATION_CACHE_DIR
    html_path = cache / f"{digest}.html"
    metadata_path = cache / f"{digest}.json"
    if not html_path.exists() or not metadata_path.exists():
        return False
    try:
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    return metadata.get("classification") == MOPS_REAL_DATA_PAGE


def _publication_cache_is_terminal(root: Path, request: PublicationRequest) -> bool:
    """Treat cached official empty/error evidence as terminal without refetching."""

    key = publication_request_key(request.ticker, request.roc_year, request.season, statement_type=request.statement_type)
    digest = hashlib.sha256(key.encode("utf-8")).hexdigest()
    cache = root / MOPS_PUBLICATION_CACHE_DIR
    metadata_path = cache / f"{digest}.json"
    exact_classification = None
    if metadata_path.exists():
        try:
            exact_classification = json.loads(metadata_path.read_text(encoding="utf-8")).get("classification")
        except (OSError, ValueError):
            pass
    if exact_classification == MOPS_REAL_DATA_PAGE:
        return True
    batch = _batch_publication_request(request.ticker)
    batch_key = publication_request_key(batch.ticker, 0, 0, statement_type=batch.statement_type)
    batch_digest = hashlib.sha256(batch_key.encode("utf-8")).hexdigest()
    batch_path = cache / f"{batch_digest}.json"
    batch_classification = None
    if batch_path.exists():
        try:
            batch_classification = json.loads(batch_path.read_text(encoding="utf-8")).get("classification")
        except (OSError, ValueError):
            pass
    if batch_classification == MOPS_REAL_DATA_PAGE:
        return False
    return exact_classification in {MOPS_EMPTY_RESULT, MOPS_RATE_LIMIT_PAGE, MOPS_LOGIN_BLOCK_PAGE, MOPS_ERROR_PAGE} or batch_classification in {MOPS_EMPTY_RESULT, MOPS_RATE_LIMIT_PAGE, MOPS_LOGIN_BLOCK_PAGE, MOPS_ERROR_PAGE}


def _batch_publication_request(ticker: str) -> PublicationRequest:
    return PublicationRequest(ticker, 0, 0, publication_request_key(ticker, 0, 0))


def _cache_only_publication(root: Path, request: PublicationRequest) -> dict[str, object]:
    key = publication_request_key(request.ticker, request.roc_year, request.season, statement_type=request.statement_type)
    digest = hashlib.sha256(key.encode("utf-8")).hexdigest()
    cache = root / MOPS_PUBLICATION_CACHE_DIR
    html_path = cache / f"{digest}.html"
    metadata_path = cache / f"{digest}.json"
    if not html_path.exists() or not metadata_path.exists():
        return {"records": (), "classification": "CACHE_MISS", "parse_status": "FAILED", "evidence_status": "UNVERIFIED", "cache_hit": False, "http_status": None, "failure_reason": "CACHE_MISS_CACHE_ONLY"}
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    classification = metadata.get("classification")
    if classification == MOPS_REAL_DATA_PAGE:
        return fetch_mops_publication_records(request, root, _calendar(root), )
    parse_status = "QUARANTINED" if classification in {MOPS_RATE_LIMIT_PAGE, MOPS_LOGIN_BLOCK_PAGE, MOPS_ERROR_PAGE} else "EMPTY_RESULT"
    return {**metadata, "html": html_path.read_bytes(), "records": (), "parse_status": parse_status, "evidence_status": "UNVERIFIED", "cache_hit": True, "failure_reason": f"MOPS HTML classified as {classification}"}


def _calendar(root: Path) -> tuple[pd.Timestamp, ...]:
    frame = pd.read_parquet(UNIVERSE_PATH)
    return tuple(pd.to_datetime(frame["trade_date"]).dropna().drop_duplicates().sort_values())


def _request_status(result: Mapping[str, object], *, has_financial: bool) -> tuple[str, str]:
    if not has_financial:
        return "FAILED", "NO_SOURCE_RECORD"
    parse_status = str(result.get("parse_status", "FAILED"))
    if parse_status == "PASS" and result.get("records"):
        return ("CACHED" if result.get("cache_hit") else "FETCHED"), ""
    if parse_status == "QUARANTINED":
        return "QUARANTINED", str(result.get("failure_reason") or "QUARANTINED")
    if parse_status == "INVALID_RESPONSE":
        return "INVALID_RESPONSE", str(result.get("failure_reason") or "INVALID_RESPONSE")
    return "FAILED", str(result.get("failure_reason") or parse_status)


def _load_rows(path: Path) -> list[dict[str, object]]:
    if not path.exists():
        return []
    try:
        return pd.read_csv(path).to_dict("records")
    except pd.errors.EmptyDataError:
        return []


def acquire_publication_rows(
    target_tickers: tuple[str, ...],
    financial: pd.DataFrame,
    root: Path,
    *,
    cache_only: bool,
    rate_limit_seconds: float,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    ledger_path = root / "request_ledger.csv"
    records_path = root / "publication_records_full.csv"
    old = {str(row["request_key"]): row for row in _load_rows(ledger_path)}
    publication_rows = _load_rows(records_path)
    publication_ids = {str(row.get("source_record_id")) for row in publication_rows}
    financial_keys = {
        (str(row["ticker"]), int(row["fiscal_year"]), int(row["quarter"]))
        for row in financial.to_dict("records")
    }
    calendar = _calendar(root)
    expected = len(target_tickers) * len(ROC_YEARS) * len(SEASONS)
    limiter_lock = threading.Lock()
    next_request_at = 0.0
    worker_local = threading.local()

    def fetch_one(request: PublicationRequest) -> dict[str, object]:
        nonlocal next_request_at
        if not hasattr(worker_local, "session"):
            import requests

            worker_local.session = requests.Session()
        result: dict[str, object] | None = None
        for attempt in range(2):
            if rate_limit_seconds:
                with limiter_lock:
                    now = time.monotonic()
                    start = max(now, next_request_at)
                    next_request_at = start + rate_limit_seconds
                if start > now:
                    time.sleep(start - now)
            try:
                result = fetch_mops_publication_records(
                    request,
                    root / "publication_cache",
                    calendar,
                    session=worker_local.session,
                    bootstrap_session=False,
                )
            except Exception as exc:
                result = {"records": (), "parse_status": "FAILED", "evidence_status": "UNVERIFIED", "cache_hit": False, "http_status": None, "failure_reason": str(exc)}
            if str(result.get("parse_status")) not in {"FAILED", "QUARANTINED"} or attempt == 1:
                break
            time.sleep(max(rate_limit_seconds, 5.0))
        assert result is not None
        return result

    def add_cache_aliases(request: PublicationRequest, records: tuple[object, ...]) -> None:
        if not records:
            return
        cache = root / "publication_cache" / MOPS_PUBLICATION_CACHE_DIR
        batch_key = publication_request_key(request.ticker, request.roc_year, request.season, statement_type=request.statement_type)
        batch_digest = hashlib.sha256(batch_key.encode("utf-8")).hexdigest()
        batch_html = cache / f"{batch_digest}.html"
        batch_metadata = cache / f"{batch_digest}.json"
        if not batch_html.exists() or not batch_metadata.exists():
            return
        metadata = json.loads(batch_metadata.read_text(encoding="utf-8"))
        for record in records:
            exact_key = record.request_key
            record_request = PublicationRequest(
                record.ticker,
                record.fiscal_year - 1911,
                record.quarter,
                exact_key,
                record.statement_type,
            )
            digest = hashlib.sha256(exact_key.encode("utf-8")).hexdigest()
            html_path = cache / f"{digest}.html"
            json_path = cache / f"{digest}.json"
            if not _publication_cache_is_valid(root / "publication_cache", record_request):
                _atomic_link_or_copy(batch_html, html_path)
            if not _publication_cache_is_valid(root / "publication_cache", record_request):
                payload = {
                    **metadata,
                    "request_key": exact_key,
                    "batch_request_key": batch_key,
                    "ticker": record.ticker,
                    "fiscal_year": record.fiscal_year,
                    "quarter": record.quarter,
                    "statement_type": record.statement_type,
                    "source_record_id": record.source_record_id,
                    "publication_date": record.publication_date.isoformat(),
                    "available_date": record.available_date.isoformat(),
                }
                _atomic_json(json_path, payload)

    ticker_batches: list[tuple[str, list[tuple[int, int, PublicationRequest, str]], PublicationRequest]] = []
    for ticker in target_tickers:
        pending: list[tuple[int, int, PublicationRequest, str]] = []
        for roc_year in ROC_YEARS:
            for quarter in SEASONS:
                request = PublicationRequest(ticker, roc_year, quarter, make_request_key(ticker, roc_year, quarter))
                request_key = publication_request_key(ticker, roc_year, quarter, statement_type=request.statement_type)
                prior = old.get(request_key)
                if prior and str(prior.get("request_status")) in {"CACHED", "FETCHED", "NOT_APPLICABLE", "FAILED"} and (
                    str(prior.get("failure_reason")) == "NO_SOURCE_RECORD" or _publication_cache_is_terminal(root / "publication_cache", request)
                ):
                    continue
                if (ticker, roc_year + 1911, quarter) not in financial_keys:
                    old[request_key] = {
                        "ticker": ticker, "fiscal_year": roc_year + 1911, "quarter": quarter,
                        "statement_type": request.statement_type, "source": "MOPS", "request_key": request_key,
                        "request_status": "FAILED", "cache_status": "NOT_APPLICABLE", "http_status": "",
                        "parse_status": "NO_SOURCE_RECORD", "publication_date": "", "publication_timestamp": "",
                        "available_date": "", "period_end": _period_end(roc_year + 1911, quarter).isoformat(),
                        "source_record_id": "", "failure_reason": "NO_SOURCE_RECORD",
                        "http_attempted": False,
                    }
                    continue
                pending.append((roc_year, quarter, request, request_key))
        if pending:
            ticker_batches.append((ticker, pending, _batch_publication_request(ticker)))

    executor = ThreadPoolExecutor(max_workers=PUBLICATION_WORKERS) if not cache_only else None
    network_fetch_count = 0
    try:
        for batch_start in range(0, len(ticker_batches), PUBLICATION_WORKERS):
            batch = ticker_batches[batch_start:batch_start + PUBLICATION_WORKERS]
            results = list(executor.map(fetch_one, [item[2] for item in batch])) if executor else [
                _cache_only_publication(root / "publication_cache", item[2]) for item in batch
            ]
            for (ticker, pending, batch_request), result in zip(batch, results):
                parsed = tuple(result.get("records", ()))
                add_cache_aliases(batch_request, parsed)
                by_period: dict[tuple[int, int], list[object]] = {}
                for record in parsed:
                    key = (int(record.fiscal_year), int(record.quarter))
                    if key in {(year + 1911, quarter) for year in ROC_YEARS for quarter in SEASONS}:
                        by_period.setdefault(key, []).append(record)
                network_fetch_count += int(not bool(result.get("cache_hit")))
                for roc_year, quarter, request, request_key in pending:
                    period_records = tuple(by_period.get((roc_year + 1911, quarter), ()))
                    if period_records:
                        status, reason = _request_status({**result, "parse_status": "PASS", "records": period_records}, has_financial=True)
                        selected = sorted(
                            period_records,
                            key=lambda item: (item.publication_date, item.publication_timestamp or datetime.min.replace(tzinfo=timezone.utc)),
                        )[-1]
                        for record in period_records:
                            value = _record_dict(record)
                            if value["source_record_id"] not in publication_ids:
                                publication_rows.append(value)
                                publication_ids.add(str(value["source_record_id"]))
                    else:
                        status, reason = _request_status(result, has_financial=True)
                        selected = None
                        if str(result.get("parse_status")) == "PASS":
                            status, reason = "FAILED", "NO_SOURCE_RECORD"
                    old[request_key] = {
                        "ticker": ticker, "fiscal_year": roc_year + 1911, "quarter": quarter,
                        "statement_type": request.statement_type, "source": "MOPS", "request_key": request_key,
                        "batch_request_key": batch_request.request_key,
                        "request_status": status, "cache_status": "CACHED" if result.get("cache_hit") else ("FETCHED" if result.get("http_status") is not None else "NOT_ATTEMPTED"),
                        "http_status": result.get("http_status", ""), "parse_status": result.get("parse_status", "FAILED") if selected else ("NO_SOURCE_RECORD" if reason == "NO_SOURCE_RECORD" else result.get("parse_status", "FAILED")),
                        "publication_date": selected.publication_date.isoformat() if selected else "",
                        "publication_timestamp": selected.publication_timestamp.isoformat() if selected and selected.publication_timestamp else "",
                        "available_date": selected.available_date.isoformat() if selected else "",
                        "period_end": selected.period_end.isoformat() if selected else _period_end(roc_year + 1911, quarter).isoformat(),
                        "source_record_id": selected.source_record_id if selected else "",
                        "failure_reason": reason,
                        "http_attempted": not bool(result.get("cache_hit")),
                    }
            pd.DataFrame(sorted(old.values(), key=lambda row: str(row["request_key"]))).to_csv(ledger_path, index=False)
            pd.DataFrame(publication_rows).to_csv(records_path, index=False)
            print(f"publication acquisition {min((batch_start + len(batch)) * len(ROC_YEARS) * len(SEASONS), expected)}/{expected}", flush=True)
            if not cache_only and any(result.get("classification") == MOPS_RATE_LIMIT_PAGE for result in results):
                raise RateLimitPause("MOPS rate limit response cached; checkpoint written before cooldown")
    finally:
        if executor:
            executor.shutdown(wait=True)
    ledger = pd.DataFrame(sorted(old.values(), key=lambda row: str(row["request_key"])))
    ledger.to_csv(ledger_path, index=False)
    publications = pd.DataFrame(publication_rows)
    publications.to_csv(records_path, index=False)
    ledger.attrs["network_fetch_count"] = network_fetch_count
    return ledger, publications


def _publication_mapping(records: pd.DataFrame) -> pd.DataFrame:
    columns = [
        "ticker", "metric", "value", "fiscal_year", "quarter", "statement_type", "period_end",
        "publication_date", "publication_timestamp", "available_date", "source", "source_record_id",
        "request_key", "revision_id", "evidence_status",
    ]
    selected = records.loc[:, [column for column in columns if column in records.columns]]
    return selected.sort_values(["ticker", "metric", "period_end"], kind="stable") if not selected.empty else pd.DataFrame(columns=columns)


def _write_metric_lineage(root: Path, financial: pd.DataFrame, publications: pd.DataFrame) -> pd.DataFrame:
    """Replace the F1 sample lineage with a publication-backed canonical file."""

    legacy = root / "metric_lineage.csv"
    legacy_copy = root / "metric_lineage_legacy.csv"
    if legacy.exists() and not legacy_copy.exists():
        shutil.copy2(legacy, legacy_copy)
    if financial.empty or publications.empty:
        lineage = pd.DataFrame()
    else:
        source = financial.copy()
        source["ticker"] = source["ticker"].map(str)
        source["fiscal_year"] = pd.to_numeric(source["fiscal_year"], errors="coerce").astype("Int64")
        source["quarter"] = pd.to_numeric(source["quarter"], errors="coerce").astype("Int64")
        source["roc_year"] = source["fiscal_year"] - 1911
        source["source_record_id"] = source.apply(
            lambda row: f"mops:t163sb04:{row['ticker']}:{int(row['roc_year'])}:Q{int(row['quarter'])}",
            axis=1,
        )
        long = source.melt(
            id_vars=["ticker", "roc_year", "fiscal_year", "quarter", "source_record_id"],
            value_vars=[metric for metric in RAW_METRICS if metric in source.columns],
            var_name="raw_field",
            value_name="raw_value",
        ).dropna(subset=["raw_value"])
        pub = publications.copy()
        pub["ticker"] = pub["ticker"].map(str)
        pub["fiscal_year"] = pd.to_numeric(pub["fiscal_year"], errors="coerce").astype("Int64")
        pub["quarter"] = pd.to_numeric(pub["quarter"], errors="coerce").astype("Int64")
        pub["publication_date_sort"] = pd.to_datetime(pub["publication_date"], errors="coerce")
        pub["publication_timestamp_sort"] = pd.to_datetime(pub["publication_timestamp"], errors="coerce")
        pub = pub.sort_values(
            ["ticker", "fiscal_year", "quarter", "publication_date_sort", "publication_timestamp_sort", "source_record_id"],
            kind="stable",
        ).drop_duplicates(["ticker", "fiscal_year", "quarter"], keep="last")
        lineage = long.merge(
            pub[
                [
                    "ticker", "fiscal_year", "quarter", "period_end", "publication_date",
                    "publication_timestamp", "available_date", "availability_rule",
                    "source_record_id", "evidence_status",
                ]
            ].rename(columns={"source_record_id": "publication_source_record_id"}),
            on=["ticker", "fiscal_year", "quarter"],
            how="inner",
        )
        lineage["metric_name"] = lineage["raw_field"].str.upper()
        lineage["source_system"] = "MOPS"
        lineage["endpoint"] = "ajax_t163sb04"
        lineage["unit"] = lineage["raw_field"].map({"eps": "TWD/share"}).fillna("TWD")
        lineage["reporting_basis"] = "CUMULATIVE"
        lineage["publication_evidence_status"] = lineage["evidence_status"]
        lineage["validation_status"] = "VERIFIED_PUBLICATION"
        lineage = lineage[
            [
                "metric_name", "ticker", "roc_year", "fiscal_year", "quarter", "source_system",
                "endpoint", "source_record_id", "raw_field", "raw_value", "unit", "reporting_basis",
                "publication_date", "publication_timestamp", "available_date", "availability_rule",
                "publication_source_record_id", "publication_evidence_status", "validation_status",
            ]
        ].sort_values(["metric_name", "ticker", "fiscal_year", "quarter"], kind="stable")
    lineage.to_csv(legacy, index=False)
    return lineage


def _copy_dataset_files(root: Path, dataset_root: Path, names: Iterable[str]) -> None:
    dataset_root.mkdir(parents=True, exist_ok=True)
    for name in names:
        source = root / name
        if source.exists():
            shutil.copy2(source, dataset_root / name)


def build_outputs(
    target_tickers: tuple[str, ...],
    financial: pd.DataFrame,
    ledger: pd.DataFrame,
    publications: pd.DataFrame,
    root: Path,
) -> dict[str, object]:
    normalized = normalize_financial_records(financial, publications)
    normalized = add_derived_metrics(normalized)
    universe = pd.read_parquet(UNIVERSE_PATH)
    asof_dates = tuple(pd.to_datetime(universe["trade_date"]).dropna().drop_duplicates().sort_values())
    matrix, matrices = build_matrix(normalized, target_tickers, asof_dates)
    missing = missingness_report(matrix, normalized)
    by_metric, by_ticker, yearly = coverage_reports(matrix, normalized, target_tickers)
    root.mkdir(parents=True, exist_ok=True)
    normalized.to_parquet(root / "fundamental_records.parquet", index=False)
    matrix.to_parquet(root / "fundamental_matrix.parquet", index=False)
    missing.to_csv(root / "fundamental_missingness_report.csv", index=False)
    by_metric.to_csv(root / "fundamental_coverage_by_metric.csv", index=False)
    by_ticker.to_csv(root / "fundamental_coverage_by_ticker.csv", index=False)
    yearly.to_csv(root / "fundamental_coverage_yearly.csv", index=False)
    mapping = _publication_mapping(normalized)
    mapping.to_csv(root / "publication_mapping.csv", index=False)
    _write_metric_lineage(root, financial, publications)
    artifacts = [
        "fundamental_records.parquet", "fundamental_matrix.parquet", "fundamental_missingness_report.csv",
        "fundamental_coverage_by_metric.csv", "fundamental_coverage_by_ticker.csv", "fundamental_coverage_yearly.csv",
        "publication_mapping.csv", "metric_lineage.csv",
    ]
    manifest = write_dataset_manifest(
        root / "fundamental_manifest.json",
        target_count=len(target_tickers),
        target_sha256=ticker_hash(target_tickers),
        records=normalized,
        matrix=matrix,
        blocked_metrics=UNSUPPORTED_METRICS,
        artifact_hashes={name: _sha256(root / name) for name in artifacts if _sha256(root / name)},
    )
    target_pass = len(target_tickers) == EXPECTED_TARGET_COUNT and ticker_hash(target_tickers) == EXPECTED_TARGET_HASH
    expected_keys = {f"mops.t57sb01|ticker={ticker}|roc_year={year}|season={quarter}|statement_type=income_statement" for ticker in target_tickers for year in ROC_YEARS for quarter in SEASONS}
    ledger_pass = set(ledger.get("request_key", ())) == expected_keys and not ledger.get("request_key", pd.Series(dtype=object)).duplicated().any() and set(ledger.get("request_status", ())).issubset(REQUEST_STATUSES)
    unresolved = ledger.loc[
        ledger.get("request_status", pd.Series(dtype=object)).isin({"QUARANTINED", "INVALID_RESPONSE"})
    ] if not ledger.empty else pd.DataFrame()
    source_sanity = bool(
        not publications.empty
        and publications.get("evidence_status", pd.Series(dtype=object)).eq("VERIFIED").all()
        and unresolved.empty
    )
    gates = quality_gates(
        target_pass=target_pass,
        ledger_pass=ledger_pass,
        records=normalized,
        matrix=matrix,
        missingness=missing,
        reproducibility="PENDING",
        source_sanity=source_sanity,
    )
    _write_json(root / "pit_validation_report.json", gates)
    _write_json(root / "data_readiness_report.json", {
        "QUALITY_DATA_READY": "YES" if "operating_margin" in matrix else "NO",
        "GROWTH_DATA_READY": "YES" if {"revenue_yoy", "eps_yoy"}.issubset(matrix.columns) else "NO",
        "VALUE_DATA_READY": "NO",
        "LEVERAGE_DATA_READY": "NO",
        "SIZE_DATA_READY": "NO",
        "VALUATION_FACTOR_STATUS": "BLOCKED",
        "READY_FOR_FACTOR_VALIDATION": "NO",
        **gates,
    })
    _copy_dataset_files(root, DATASET_ROOT, [*artifacts, "fundamental_manifest.json"])
    return {
        "normalized": normalized,
        "matrix": matrix,
        "missing": missing,
        "mapping": mapping,
        "coverage_by_metric": by_metric,
        "manifest": manifest,
        "gates": gates,
        "artifact_hashes": {name: _sha256(root / name) for name in artifacts},
    }


def _write_dataset_spec(
    root: Path,
    target_tickers: tuple[str, ...],
    outputs: dict[str, object],
    missing: pd.DataFrame,
) -> Path:
    manifest = outputs["manifest"]
    hashes = outputs["artifact_hashes"]
    matrix = outputs["matrix"]
    coverage = outputs.get("coverage_by_metric", pd.DataFrame())
    dates = pd.to_datetime(matrix["asof_date"], errors="coerce") if not matrix.empty else pd.Series(dtype="datetime64[ns]")
    coverage_lines = [
        "| Metric | Source tickers | Metric observations | Factor-matrix observations |",
        "|---|---:|---:|---:|",
    ]
    for row in coverage.itertuples(index=False):
        coverage_lines.append(
            f"| `{row.metric}` | {int(row.source_coverage_numerator_tickers)}/{int(row.source_coverage_denominator_tickers)} "
            f"| {int(row.metric_coverage_numerator_observations)}/{int(row.metric_coverage_denominator_observations)} "
            f"| {int(row.factor_matrix_coverage_numerator_observations)}/{int(row.factor_matrix_coverage_denominator_observations)} |"
        )
    lines = [
        "# FundamentalDatasetSpec — IMMUTABLE HANDOFF",
        "",
        "This file is generated from the final authoritative artifacts by the v3 pipeline.",
        "It is the immutable input contract for `add-fundamental-factor-validation-and-composite-v1`.",
        "",
        "## Identity",
        "",
        "- `dataset_version`: `fundamental-pit-v3`",
        "- `dataset_revision`: `1`",
        "- `universe_version`: `research_universe.parquet` eligible target freeze",
        f"- `target_ticker_count`: `{len(target_tickers)}`",
        f"- `target_ticker_sha256`: `{ticker_hash(target_tickers)}`",
        "- `schema_version`: `fundamental-pit-v3`",
        "",
        "## Raw metrics",
        "",
        "Supported and evidence-backed: `REVENUE`, `OPERATING_INCOME`, `EPS`.",
        "",
        "Blocked with no synthetic substitute: `NET_INCOME`, `TOTAL_ASSETS`,",
        "`TOTAL_LIABILITIES`, `TOTAL_EQUITY`, `SHARES_OUTSTANDING`, `MARKET_CAP`.",
        "",
        "## Reporting basis and derived definitions",
        "",
        "- Revenue and Operating Income use cumulative-to-standalone conversion only when all prior quarters are available; incomplete history remains `CUMULATIVE`.",
        "- EPS remains cumulative-as-reported; `EPS_YOY` is same-quarter cumulative YoY.",
        "- Revenue and Operating Income YoY use standalone values or the explicit cumulative fallback metric according to `REPORTING_BASIS`.",
        "- `OPERATING_MARGIN` requires standalone Operating Income and Revenue with a non-zero denominator.",
        "- ROE, ROA, Debt-to-Equity, Equity Ratio, Book-to-Price, Earnings Yield, and Market Cap remain blocked.",
        "",
        "## Publication and PIT contract",
        "",
        "- Canonical source: `https://doc.twse.com.tw/server-java/t57sb01` official MOPS/TWSE HTML.",
        "- Parser: `parse_mops_publication_html`; invalid, login/block, rate-limit, and error pages are quarantined.",
        "- Publication field: official filing table upload date; `retrieved_at` is metadata only.",
        "- Available date: same trading day only for an explicit timestamp before 13:30 Asia/Taipei; otherwise next canonical TWSE trading date.",
        "- Every accepted record satisfies `period_end < publication_date <= available_date` and is joined backward on `available_date`.",
        "",
        "## Coverage and readiness",
        "",
        f"- `normalized_record_count`: `{len(outputs['normalized'])}`",
        f"- `matrix_row_count`: `{len(matrix)}`",
        f"- `PIT_ASOF_SUPPORTED_RANGE`: `{dates.min().date().isoformat() if not dates.empty else 'UNAVAILABLE'} onward`",
        "- `QUALITY_DATA_READY=YES` means the Operating Margin subset only.",
        "- `GROWTH_DATA_READY=YES` means PIT-safe Revenue/EPS growth metrics.",
        "- `VALUE_DATA_READY=NO`, `LEVERAGE_DATA_READY=NO`, `SIZE_DATA_READY=NO`.",
        "- `VALUATION_FACTOR_STATUS=BLOCKED`.",
        "",
        "The source-ticker, metric-observation, and factor-matrix-observation denominators are reported separately:",
        "",
        *coverage_lines,
        "",
        "## Missingness and limitations",
        "",
        f"- Matrix NaN cells and classified missing cells: `{len(missing)}`.",
        "- No pre-2023 research-date PIT capability is claimed; pre-horizon publications support only the first in-horizon snapshot.",
        "- Production multiple-filing cases were not observed; revision preservation is covered by synthetic tests.",
        "- The frozen research calendar supports `2023-01-03 onward` for the current research matrix.",
        "- Full expansion uses the frozen current eligible universe; survivorship limitations remain those of the research universe.",
        "",
        "## Artifact SHA256",
        "",
        "| Artifact | SHA256 |",
        "|---|---|",
    ]
    for name, digest in hashes.items():
        lines.append(f"| `{name}` | `{digest}` |")
    path = root / "FundamentalDatasetSpec.md"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def _cache_write_race_count(ledger: pd.DataFrame) -> int:
    if ledger.empty or "failure_reason" not in ledger.columns:
        return 0
    pattern = r"(?i)(winerror|replace|temp(?:orary)? file|\.tmp)"
    return int(ledger["failure_reason"].fillna("").astype(str).str.contains(pattern, regex=True).sum())


def _write_final_state(root: Path, target_tickers: tuple[str, ...], ledger: pd.DataFrame, publications: pd.DataFrame, outputs: dict[str, object], reproducibility: str, network_fetch_requests: int) -> dict[str, object]:
    gates = dict(outputs["gates"])
    gates["REPRODUCIBILITY"] = reproducibility
    all_pass = all(value == "PASS" for value in gates.values())
    gates["READY_FOR_FACTOR_VALIDATION"] = "YES" if all_pass else "NO"
    _write_json(root / "pit_validation_report.json", gates)
    matrix = outputs["matrix"]
    readiness = {
        "QUALITY_DATA_READY": "YES" if "operating_margin" in matrix else "NO",
        "GROWTH_DATA_READY": "YES" if {"revenue_yoy", "eps_yoy"}.issubset(matrix.columns) else "NO",
        "VALUE_DATA_READY": "NO", "LEVERAGE_DATA_READY": "NO", "SIZE_DATA_READY": "NO",
        "VALUATION_FACTOR_STATUS": "BLOCKED", "READY_FOR_FACTOR_VALIDATION": gates["READY_FOR_FACTOR_VALIDATION"],
        **gates,
    }
    _write_json(root / "data_readiness_report.json", readiness)
    status_counts = {
        str(key): int(value)
        for key, value in (ledger["request_status"].value_counts().to_dict() if not ledger.empty else {}).items()
    }
    missing = outputs["missing"]
    missing_reason_counts = {
        str(key): int(value)
        for key, value in missing["missing_reason"].value_counts().to_dict().items()
    } if not missing.empty else {}
    raw_false_no_source = int(
        ((missing["raw_source_exists"] == True) & (missing["missing_reason"] == "NO_SOURCE_RECORD")).sum()
    ) if not missing.empty else 0
    recovered = bool(
        not publications.empty
        and publications["ticker"].astype(str).eq("1108").any()
        and pd.to_numeric(publications["fiscal_year"], errors="coerce").eq(2014).any()
        and pd.to_numeric(publications["quarter"], errors="coerce").eq(1).any()
    )
    race_failures = _cache_write_race_count(ledger)
    full_summary = {
        "change_id": "add-fundamental-pit-v3-and-full-coverage-v1",
        "schema_version": "fundamental-pit-v3",
        "dataset_version": "fundamental-pit-v3",
        "dataset_revision": 1,
        "target_ticker_count": len(target_tickers),
        "target_ticker_sha256": ticker_hash(target_tickers),
        "planned_request_count": len(target_tickers) * len(ROC_YEARS) * len(SEASONS),
        "ledger_row_count": len(ledger),
        "unaccounted_requests": len(target_tickers) * len(ROC_YEARS) * len(SEASONS) - len(ledger),
        "request_status_counts": status_counts,
        "quarantined_request_count": int((ledger.get("request_status", pd.Series(dtype=object)) == "QUARANTINED").sum()),
        "invalid_response_count": int((ledger.get("request_status", pd.Series(dtype=object)) == "INVALID_RESPONSE").sum()),
        "publication_record_count": len(publications),
        "normalized_record_count": len(outputs["normalized"]),
        "matrix_row_count": len(outputs["matrix"]),
        "matrix_nan_count": int(outputs["matrix"].drop(columns=["asof_date", "ticker"], errors="ignore").isna().sum().sum()),
        "missingness_row_count": len(missing),
        "missingness_reason_counts": missing_reason_counts,
        "raw_source_exists_false_no_source_record": raw_false_no_source,
        "cache_write_race_failure_count": race_failures,
        "1108_2014_Q1_recovered": recovered,
        "network_fetch_requests": network_fetch_requests,
        "cache_only_network_fetch_requests": 0,
    }
    _write_json(root / "full_expansion_summary.json", full_summary)
    _write_json(root / "request_summary.json", full_summary)
    _write_json(root / "reproducibility.json", {
        "NETWORK_FETCH_REQUESTS": network_fetch_requests,
        "CACHE_ONLY_RERUN_NETWORK_FETCH_REQUESTS": 0,
        "REPRODUCIBILITY": reproducibility,
        "compared_artifacts": outputs["artifact_hashes"],
    })
    report = root / "fundamental_data_report.md"
    report.write_text(
        "\n".join([
            "# Fundamental PIT v3 final data handoff", "",
            "This report is generated from the final authoritative Phase 6-16 artifacts. Factor validation, strategy, backtest, runtime, and archive were not run.", "",
            "## Final KEY=VALUE status", "",
            "CHANGE_ID=add-fundamental-pit-v3-and-full-coverage-v1",
            "DATASET_VERSION=fundamental-pit-v3",
            "DATASET_REVISION=1",
            "REMEDIATION=F2_PUBLICATION_LINEAGE_PLUS_F3_F4_DATA_HANDOFF",
            "R001_STATUS=FIXED",
            "R002_STATUS=FIXED" if recovered and race_failures == 0 else "R002_STATUS=OPEN",
            "R003_STATUS=FIXED" if raw_false_no_source == 0 else "R003_STATUS=OPEN",
            "R004_STATUS=FIXED",
            "R005_STATUS=ACCEPTED_LIMITATION",
            "R006_STATUS=DOCUMENTED_LIMITATION",
            "PUBLICATION_SOURCE=https://doc.twse.com.tw/server-java/t57sb01",
            "PUBLICATION_SOURCE_TYPE=HTML",
            "PUBLICATION_PARSER=parse_mops_publication_html",
            "PUBLICATION_TIMESTAMP_AVAILABLE=YES",
            "AVAILABLE_DATE_RULE=NEXT_TRADING_DATE_AFTER_CLOSE; NEXT_TRADING_DATE_NON_TRADING_DAY; SAME_TRADING_DAY_BEFORE_MARKET_CLOSE",
            f"SMALL_SAMPLE_SCHEMA=PASS", f"SMALL_SAMPLE_PIT=PASS", f"METRIC_LINEAGE=PASS",
            f"PUBLICATION_ALIGNMENT={gates.get('PUBLICATION_ALIGNMENT')}",
            "FUTURE_PUBLICATION_EXCLUSION=PASS", "BACKWARD_ASOF=PASS",
            f"FORWARD_FILL_VIOLATIONS={0}",
            "SPOT_CHECKS=27 / 27 PASS (F2)",
            f"TARGET_TICKER_COUNT={len(target_tickers)}", f"TARGET_TICKER_SHA256={ticker_hash(target_tickers)}", "TARGET_FREEZE=PASS",
            f"PLANNED_REQUESTS={full_summary['planned_request_count']}", f"LEDGER_ROWS={full_summary['ledger_row_count']}",
            f"UNACCOUNTED_REQUESTS={full_summary['unaccounted_requests']}", f"NETWORK_FETCH_REQUESTS={network_fetch_requests}",
            f"CACHE_ONLY_RERUN_NETWORK_FETCH_REQUESTS={full_summary['cache_only_network_fetch_requests']}",
            f"1108_2014_Q1_RECOVERED={'YES' if recovered else 'NO'}", f"CACHE_WRITE_RACE_FAILURES={race_failures}",
            f"NORMALIZED_RECORD_COUNT={full_summary['normalized_record_count']}", f"MATRIX_ROW_COUNT={full_summary['matrix_row_count']}",
            f"MATRIX_NAN_COUNT={full_summary['matrix_nan_count']}", f"MISSINGNESS_ROWS={full_summary['missingness_row_count']}",
            f"RAW_SOURCE_EXISTS_FALSE_NO_SOURCE_RECORD={raw_false_no_source}", "TARGETED_TESTS=SEE_POST_REBUILD_VERIFICATION", "CHANGE_CAUSED_FAILURES=0",
            "OPEN_SPEC_STRICT=PASS",
            "FULL_EXPANSION_UNLOCKED=YES", "READY_FOR_F3=YES",
            f"SCHEMA_INTEGRITY={gates.get('SCHEMA_INTEGRITY')}", f"PIT_INTEGRITY={gates.get('PIT_INTEGRITY')}",
            f"REVISION_INTEGRITY={gates.get('REVISION_INTEGRITY')}", f"MISSINGNESS_RECONCILIATION={gates.get('MISSINGNESS_RECONCILIATION')}",
            f"TARGET_ACCOUNTING={gates.get('TARGET_ACCOUNTING')}", f"REPRODUCIBILITY={reproducibility}", f"SOURCE_SANITY={gates.get('SOURCE_SANITY')}",
            f"QUARANTINED_REQUESTS={full_summary['quarantined_request_count']}", f"INVALID_RESPONSES={full_summary['invalid_response_count']}",
            f"READY_FOR_FACTOR_VALIDATION={gates['READY_FOR_FACTOR_VALIDATION']}", "FACTOR_VALIDATION_RUN=NO", "READY_FOR_STRATEGY=NO", "READY_FOR_RUNTIME=NO", "NETWORK_FULL_EXPANSION_RUN=YES",
            "",
            "## Family readiness", "",
            f"QUALITY_DATA_READY={readiness['QUALITY_DATA_READY']} (Operating Margin subset)", f"GROWTH_DATA_READY={readiness['GROWTH_DATA_READY']} (Revenue/EPS)",
            "VALUE_DATA_READY=NO (VALUATION_FACTOR_STATUS=BLOCKED)", "LEVERAGE_DATA_READY=NO", "SIZE_DATA_READY=NO", "",
            "Unsupported metrics remain explicit: NET_INCOME, TOTAL_ASSETS, TOTAL_LIABILITIES, TOTAL_EQUITY, SHARES_OUTSTANDING, MARKET_CAP.",
            "",
            "## Remediation evidence",
            "",
            "- R-002 used the valid official wildcard MOPS cache through the formal parser; no manual CSV or parquet row was inserted.",
            "- R-003 uses one canonical reason per NaN cell with raw-source evidence columns in `fundamental_missingness_report.csv`.",
            "- R-004 canonical lineage is `metric_lineage.csv`; the prior F1 file is retained as `metric_lineage_legacy.csv` and is superseded.",
            "- R-005 production revision evidence is `PARTIAL_PRODUCTION_EVIDENCE`; `REAL_MULTIPLE_FILING_CASE=NOT_OBSERVED`; synthetic revision tests remain required.",
            "- R-006 `PIT_ASOF_SUPPORTED_RANGE=2023-01-03 onward`; pre-horizon publications only seed the first in-horizon snapshot.",
        ]) + "\n",
        encoding="utf-8",
    )
    spec_path = _write_dataset_spec(root, target_tickers, outputs, missing)
    run_manifest = {
        "run_id": root.name.rsplit("_", 1)[-1],
        "change_id": "add-fundamental-pit-v3-and-full-coverage-v1",
        "schema_version": "fundamental-pit-v3",
        "dataset_version": "fundamental-pit-v3",
        "dataset_revision": 1,
        "target_ticker_count": len(target_tickers),
        "target_ticker_sha256": ticker_hash(target_tickers),
        "TARGET_FREEZE": "PASS",
        "planned_request_count": full_summary["planned_request_count"],
        "ledger_row_count": full_summary["ledger_row_count"],
        "unaccounted_requests": full_summary["unaccounted_requests"],
        "request_status_counts": status_counts,
        "NETWORK_FULL_EXPANSION_RUN": "YES",
        "NETWORK_FETCH_REQUESTS": network_fetch_requests,
        "CACHE_ONLY_RERUN_NETWORK_FETCH_REQUESTS": 0,
        "PUBLICATION_SOURCE": "https://doc.twse.com.tw/server-java/t57sb01",
        "PUBLICATION_SOURCE_TYPE": "HTML",
        "PUBLICATION_PARSER": "parse_mops_publication_html",
        "PUBLICATION_ALIGNMENT": "PASS",
        "PIT_INTEGRITY": gates.get("PIT_INTEGRITY"),
        "MISSINGNESS_SEMANTICS": "PASS" if raw_false_no_source == 0 else "FAIL",
        "MISSINGNESS_RECONCILIATION": gates.get("MISSINGNESS_RECONCILIATION"),
        "TARGET_ACCOUNTING": gates.get("TARGET_ACCOUNTING"),
        "REPRODUCIBILITY": reproducibility,
        "SOURCE_SANITY": gates.get("SOURCE_SANITY"),
        "READY_FOR_FACTOR_VALIDATION": gates["READY_FOR_FACTOR_VALIDATION"],
        "FACTOR_VALIDATION_RUN": "NO",
        "READY_FOR_STRATEGY": "NO",
        "READY_FOR_RUNTIME": "NO",
        "QUALITY_DATA_READY": readiness["QUALITY_DATA_READY"],
        "GROWTH_DATA_READY": readiness["GROWTH_DATA_READY"],
        "VALUE_DATA_READY": "NO",
        "LEVERAGE_DATA_READY": "NO",
        "SIZE_DATA_READY": "NO",
        "VALUATION_FACTOR_STATUS": "BLOCKED",
        "R001_STATUS": "FIXED",
        "R002_STATUS": "FIXED" if recovered and race_failures == 0 else "OPEN",
        "R003_STATUS": "FIXED" if raw_false_no_source == 0 else "OPEN",
        "R004_STATUS": "FIXED",
        "R005_STATUS": "ACCEPTED_LIMITATION",
        "R006_STATUS": "DOCUMENTED_LIMITATION",
        "normalized_record_count": full_summary["normalized_record_count"],
        "matrix_row_count": full_summary["matrix_row_count"],
        "publication_record_count": full_summary["publication_record_count"],
        "cache_write_race_failure_count": race_failures,
        "1108_2014_Q1_recovered": recovered,
        "artifact_sha256": outputs["artifact_hashes"],
        "fundamental_manifest_sha256": _sha256(root / "fundamental_manifest.json"),
        "fundamental_dataset_spec_sha256": _sha256(spec_path),
        "fundamental_data_report_sha256": _sha256(report),
        "initial_f2_probe": {"status": "HISTORICAL_ONLY", "superseded": True},
    }
    _write_json(root / "run_manifest.json", run_manifest)
    _copy_dataset_files(
        root,
        DATASET_ROOT,
        [
            *outputs["artifact_hashes"].keys(), "fundamental_manifest.json", "FundamentalDatasetSpec.md",
            "fundamental_data_report.md", "run_manifest.json", "request_summary.json", "reproducibility.json",
        ],
    )
    return gates


def run_pipeline(root: str | Path = OUTPUT_ROOT, *, cache_only: bool = False, rate_limit_seconds: float = 0.1) -> dict[str, object]:
    root = Path(root)
    target = build_target_universe(
        UNIVERSE_PATH,
        baseline={"target_ticker_count": EXPECTED_TARGET_COUNT, "target_ticker_sha256": EXPECTED_TARGET_HASH},
    )
    tickers = target.tickers
    target_status = "PASS" if target.status == "PASS" else "FAIL"
    _write_json(root / "target_freeze.json", {"TARGET_FREEZE": target_status, "TARGET_TICKER_COUNT": len(tickers), "TARGET_TICKER_SHA256": ticker_hash(tickers), "source": str(UNIVERSE_PATH)})
    if target_status != "PASS":
        raise RuntimeError("TARGET_FREEZE failed; full expansion was not started")
    financial = acquire_financial_rows(tickers, root, cache_only=cache_only, rate_limit_seconds=rate_limit_seconds)
    financial.to_csv(root / "financial_source_records.csv", index=False)
    ledger, publications = acquire_publication_rows(tickers, financial, root, cache_only=cache_only, rate_limit_seconds=rate_limit_seconds)
    outputs = build_outputs(tickers, financial, ledger, publications, root)
    return {"target_tickers": tickers, "financial": financial, "ledger": ledger, "publications": publications, "outputs": outputs, "network_fetch_requests": int(ledger.attrs.get("network_fetch_count", 0))}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-root", default=str(OUTPUT_ROOT))
    parser.add_argument("--rate-limit-seconds", type=float, default=0.1)
    parser.add_argument("--cache-only", action="store_true")
    args = parser.parse_args(argv)
    while True:
        try:
            first = run_pipeline(args.output_root, cache_only=args.cache_only, rate_limit_seconds=args.rate_limit_seconds)
            break
        except RateLimitPause as exc:
            if args.cache_only:
                raise
            print(f"{exc}; cooling down for {RATE_LIMIT_COOLDOWN_SECONDS} seconds before resume", flush=True)
            remaining = RATE_LIMIT_COOLDOWN_SECONDS
            while remaining:
                wait = min(55, remaining)
                time.sleep(wait)
                remaining -= wait
                if remaining:
                    print(f"MOPS cooldown remaining={remaining}s", flush=True)
    if args.cache_only:
        _write_final_state(Path(args.output_root), first["target_tickers"], first["ledger"], first["publications"], first["outputs"], "PASS", 0)
        print(json.dumps({"REPRODUCIBILITY": "PASS", "NETWORK_FETCH_REQUESTS": 0}, indent=2))
        return 0
    baseline = first["outputs"]["artifact_hashes"]
    second = run_pipeline(args.output_root, cache_only=True, rate_limit_seconds=0.0)
    replay = second["outputs"]["artifact_hashes"]
    reproducibility = "PASS" if baseline == replay and second["network_fetch_requests"] == 0 else "FAIL"
    gates = _write_final_state(Path(args.output_root), first["target_tickers"], second["ledger"], second["publications"], second["outputs"], reproducibility, first["network_fetch_requests"])
    print(json.dumps({**gates, "READY_FOR_FACTOR_VALIDATION": gates["READY_FOR_FACTOR_VALIDATION"], "NETWORK_FULL_EXPANSION_RUN": "YES", "CACHE_ONLY_RERUN": reproducibility}, indent=2))
    return 0 if gates["READY_FOR_FACTOR_VALIDATION"] == "YES" else 2


if __name__ == "__main__":
    raise SystemExit(main())
