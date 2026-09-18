"""Point-in-time fundamental publication expansion.

The module deliberately keeps the execution surface small: target selection,
request ledger/cache, and pure downstream checks are independent functions so
offline fixtures can exercise the full contract without contacting TWSE.
"""

from __future__ import annotations

import csv
import hashlib
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from datetime import date as date_type
from datetime import datetime, time as clock_time, timezone
from itertools import product
from pathlib import Path
from typing import Callable, Iterable, Mapping, Sequence
from zoneinfo import ZoneInfo

import pandas as pd


ROC_YEARS = tuple(range(102, 115))
SEASONS = (1, 2, 3, 4)
PUBLICATION_ENDPOINT = "https://doc.twse.com.tw/server-java/t57sb01"
AUTHORITATIVE_OUTPUT_ROOT = Path("outputs/fundamental_data/fundamental_pit_coverage_v2")
REQUEST_STATUSES = frozenset(
    {"CACHED", "FETCHED", "FAILED", "INVALID_RESPONSE", "QUARANTINED", "NOT_APPLICABLE"}
)
STAT_FIELDS = (
    "ticker", "roc_year", "season", "request_key", "request_status", "cache_hit",
    "http_attempted", "http_status", "parse_status", "publication_found", "publication_date", "failure_reason",
)
REQUIRED_STAT_FIELDS = frozenset(STAT_FIELDS) - {"publication_date"}


def clean_ticker(value: object) -> str:
    """Normalize an identifier without changing non-standard numeric codes."""

    if value is None or pd.isna(value):
        return ""
    text = str(value).strip().upper()
    text = re.sub(r"\.(?:TW|TWO)$", "", text)
    return text


def _sha256_text(values: Iterable[str]) -> str:
    return hashlib.sha256("\n".join(values).encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class TargetUniverse:
    tickers: tuple[str, ...]
    count: int
    sha256: str
    status: str = "PASS"
    baseline_count: int | None = None
    baseline_sha256: str | None = None


def build_target_universe(
    source: pd.DataFrame | str | Path,
    acceptance_tickers: Iterable[object] = (),
    *,
    baseline: Mapping[str, object] | str | Path | None = None,
) -> TargetUniverse:
    """Build the target from eligible rows, never from cache discovery."""

    if isinstance(source, (str, Path)):
        try:
            source = pd.read_parquet(source)
        except (OSError, ValueError, ImportError):
            return TargetUniverse((), 0, _sha256_text(()), status="BLOCKED")
    if "is_eligible" not in source.columns:
        return TargetUniverse((), 0, _sha256_text(()), status="BLOCKED")
    ticker_column = next((c for c in ("ticker", "stock_id", "security_code", "code") if c in source), None)
    if ticker_column is None:
        return TargetUniverse((), 0, _sha256_text(()), status="BLOCKED")
    values = [clean_ticker(v) for v in source.loc[source["is_eligible"].eq(True), ticker_column]]
    values.extend(clean_ticker(v) for v in acceptance_tickers)
    tickers = tuple(sorted({v for v in values if v}))
    result = TargetUniverse(tickers, len(tickers), _sha256_text(tickers))
    if baseline is None:
        return result
    expected = _read_baseline(baseline)
    count = _optional_int(expected.get("target_ticker_count"))
    digest = expected.get("target_ticker_sha256")
    if count is None or not digest:
        return TargetUniverse(result.tickers, result.count, result.sha256, status="BLOCKED", baseline_count=count, baseline_sha256=digest)
    status = "PASS" if (count, digest) == (result.count, result.sha256) else "CHANGED"
    return TargetUniverse(result.tickers, result.count, result.sha256, status=status, baseline_count=count, baseline_sha256=str(digest))


def _read_baseline(value: Mapping[str, object] | str | Path) -> Mapping[str, object]:
    if isinstance(value, Mapping):
        return value
    path = Path(value)
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return payload if isinstance(payload, Mapping) else {}


def _optional_int(value: object) -> int | None:
    try:
        return None if value is None else int(value)
    except (TypeError, ValueError):
        return None


@dataclass(frozen=True)
class PublicationRequest:
    ticker: str
    roc_year: int
    season: int
    request_key: str
    statement_type: str = "income_statement"


@dataclass(frozen=True)
class PublicationRecord:
    ticker: str
    fiscal_year: int
    quarter: int
    statement_type: str
    period_end: date_type
    publication_date: date_type
    publication_timestamp: datetime | None
    available_date: date_type
    availability_rule: str
    source_system: str
    source_record_id: str
    request_key: str
    retrieved_at: datetime
    parse_status: str = "PASS"
    evidence_status: str = "VERIFIED"


class PublicationParseError(ValueError):
    """Raised when an official publication page fails contract validation."""


MOPS_REAL_DATA_PAGE = "REAL_DATA_PAGE"
MOPS_EMPTY_RESULT = "EMPTY_RESULT"
MOPS_RATE_LIMIT_PAGE = "RATE_LIMIT_PAGE"
MOPS_LOGIN_BLOCK_PAGE = "LOGIN/BLOCK_PAGE"
MOPS_ERROR_PAGE = "ERROR_PAGE"
MOPS_PUBLICATION_CACHE_DIR = "mops_publication"
MOPS_PUBLICATION_HEADERS = frozenset(
    {"證券代號", "資料年度", "上傳日期", "電子檔案"}
)


def make_request_key(ticker: object, roc_year: int, season: int) -> str:
    ticker = clean_ticker(ticker)
    if int(roc_year) not in ROC_YEARS or int(season) not in SEASONS:
        raise ValueError("ROC year or season outside the supported request space")
    return f"doc.twse.t57sb01|ticker={ticker}|roc_year={int(roc_year)}|season={int(season)}"


def publication_request_key(
    ticker: object,
    roc_year: int,
    season: int,
    *,
    statement_type: str = "income_statement",
    source: str = "mops",
) -> str:
    """Build a F2 evidence key without changing the legacy request key."""

    return (
        f"{source}.t57sb01|ticker={clean_ticker(ticker)}|"
        f"roc_year={int(roc_year)}|season={int(season)}|"
        f"statement_type={statement_type}"
    )


def publication_request_parameters(request: PublicationRequest) -> dict[str, str]:
    """Return stable TWSE query parameters for one publication slot."""

    return {"co_id": request.ticker, "year": str(request.roc_year), "season": str(request.season)}


def mops_publication_form(request: PublicationRequest) -> dict[str, str]:
    """Return the official t57sb01 form fields for consolidated IFRS filings."""

    return {
        "id": "",
        "key": "",
        "step": "1",
        "co_id": request.ticker,
        "year": "" if request.roc_year == 0 else str(request.roc_year),
        "seamon": "" if request.season == 0 else str(request.season),
        "mtype": "A",
        "dtype": "AI1",
    }


def _mops_html_text(html: str | bytes) -> str:
    if isinstance(html, bytes):
        return html.decode("big5", errors="replace")
    return str(html)


def classify_mops_html(html: str | bytes) -> str:
    """Classify official MOPS HTML before attempting to parse data rows."""

    from bs4 import BeautifulSoup

    soup = BeautifulSoup(_mops_html_text(html), "html.parser")
    text = " ".join(soup.get_text(" ", strip=True).split())
    lowered = text.lower()
    if "查詢過量" in text or "rate limit" in lowered or "too many requests" in lowered:
        return MOPS_RATE_LIMIT_PAGE
    if "驗證碼" in text or "access denied" in lowered or "forbidden" in lowered:
        return MOPS_LOGIN_BLOCK_PAGE
    if "系統錯誤" in text or "internal server error" in lowered or "exception" in lowered:
        return MOPS_ERROR_PAGE
    if "查無所需資料" in text or "no data" in lowered:
        return MOPS_EMPTY_RESULT
    for table in soup.find_all("table"):
        direct_rows = table.find_all("tr", recursive=False)
        headers = {
            " ".join(cell.get_text(" ", strip=True).split())
            for row in direct_rows
            for cell in row.find_all("th", recursive=False)
        }
        if not MOPS_PUBLICATION_HEADERS.issubset(headers):
            continue
        rows = [row.find_all("td", recursive=False) for row in direct_rows]
        if any(len(row) >= len(MOPS_PUBLICATION_HEADERS) for row in rows):
            return MOPS_REAL_DATA_PAGE
    return MOPS_EMPTY_RESULT


def _parse_mops_timestamp(value: str) -> tuple[date_type, datetime | None]:
    match = re.search(
        r"(?P<year>\d{3,4})\s*/\s*(?P<month>\d{1,2})\s*/\s*(?P<day>\d{1,2})"
        r"(?:\s+(?P<hour>\d{1,2}):(?P<minute>\d{2}):(?P<second>\d{2}))?",
        value,
    )
    if not match:
        raise PublicationParseError(f"invalid MOPS upload date: {value!r}")
    year = int(match.group("year"))
    if year < 1911:
        year += 1911
    publication_date = date_type(year, int(match.group("month")), int(match.group("day")))
    if match.group("hour") is None:
        return publication_date, None
    timestamp = datetime(
        year,
        publication_date.month,
        publication_date.day,
        int(match.group("hour")),
        int(match.group("minute")),
        int(match.group("second")),
        tzinfo=ZoneInfo("Asia/Taipei"),
    )
    return publication_date, timestamp


def _period_end(fiscal_year: int, quarter: int) -> date_type:
    month = quarter * 3
    day = 31 if month in {3, 12} else 30
    return date_type(fiscal_year, month, day)


def _parse_mops_period(value: str) -> tuple[int, int]:
    year_match = re.search(r"(\d{3,4})\s*年", value)
    quarter_match = re.search(r"第\s*([一二三四1-4])\s*季", value)
    if not year_match or not quarter_match:
        raise PublicationParseError(f"invalid MOPS fiscal period: {value!r}")
    year = int(year_match.group(1))
    if year < 1911:
        year += 1911
    quarter = {"一": 1, "二": 2, "三": 3, "四": 4}.get(quarter_match.group(1))
    quarter = quarter or int(quarter_match.group(1))
    return year, quarter


def _calendar_dates(trading_dates: Iterable[object]) -> tuple[date_type, ...]:
    dates = {pd.Timestamp(value).date() for value in trading_dates}
    return tuple(sorted(dates))


def next_trading_date(
    publication_date: date_type,
    trading_dates: Iterable[object],
) -> date_type:
    calendar = _calendar_dates(trading_dates)
    return next((value for value in calendar if value > publication_date), None) or (_ for _ in ()).throw(
        PublicationParseError("canonical trading calendar has no next date")
    )


def available_date_for_publication(
    publication_date: date_type,
    publication_timestamp: datetime | None,
    trading_dates: Iterable[object],
    *,
    market_close: clock_time = clock_time(13, 30),
) -> tuple[date_type, str]:
    """Apply the frozen conservative same-day/next-trading-day policy."""

    calendar = _calendar_dates(trading_dates)
    if publication_date not in calendar:
        return next_trading_date(publication_date, calendar), "NEXT_TRADING_DATE_NON_TRADING_DAY"
    if publication_timestamp is not None and publication_timestamp.timetz().replace(tzinfo=None) < market_close:
        return publication_date, "SAME_TRADING_DAY_BEFORE_MARKET_CLOSE"
    return next_trading_date(publication_date, calendar), "NEXT_TRADING_DATE_AFTER_CLOSE"


def parse_mops_publication_html(
    html: str | bytes,
    request: PublicationRequest,
    trading_dates: Iterable[object],
    *,
    retrieved_at: datetime | None = None,
) -> tuple[PublicationRecord, ...]:
    """Parse and validate official t57sb01 filing rows into PIT records."""

    from bs4 import BeautifulSoup

    classification = classify_mops_html(html)
    if classification != MOPS_REAL_DATA_PAGE:
        raise PublicationParseError(f"MOPS page classified as {classification}")
    soup = BeautifulSoup(_mops_html_text(html), "html.parser")
    retrieved = retrieved_at or datetime.now(timezone.utc)
    records: list[PublicationRecord] = []
    period_mismatch: tuple[int, int] | None = None
    for table in soup.find_all("table"):
        direct_rows = table.find_all("tr", recursive=False)
        headers = [
            " ".join(cell.get_text(" ", strip=True).split())
            for row in direct_rows
            for cell in row.find_all("th", recursive=False)
        ]
        if not MOPS_PUBLICATION_HEADERS.issubset(set(headers)):
            continue
        positions = {header: index for index, header in enumerate(headers)}
        for row in direct_rows:
            cells = [
                " ".join(cell.get_text(" ", strip=True).split())
                for cell in row.find_all("td", recursive=False)
            ]
            if len(cells) <= max(positions.values()):
                continue
            ticker = clean_ticker(cells[positions["證券代號"]])
            if ticker != request.ticker:
                raise PublicationParseError(
                    f"ticker mismatch: expected {request.ticker}, got {ticker or '<empty>'}"
                )
            fiscal_year, quarter = _parse_mops_period(cells[positions["資料年度"]])
            expected_year = request.roc_year + 1911
            if request.roc_year == 0 and fiscal_year - 1911 not in ROC_YEARS:
                continue
            if request.roc_year and request.season and (fiscal_year, quarter) != (expected_year, request.season):
                period_mismatch = (fiscal_year, quarter)
                continue
            publication_date, publication_timestamp = _parse_mops_timestamp(cells[positions["上傳日期"]])
            available_date, availability_rule = available_date_for_publication(
                publication_date, publication_timestamp, trading_dates
            )
            filename = cells[positions.get("電子檔案", 0)]
            source_record_id = (
                f"mops:t57sb01:{ticker}:{request.roc_year}:Q{request.season}:"
                f"{filename or len(records)}"
            )
            records.append(
                PublicationRecord(
                    ticker=ticker,
                    fiscal_year=fiscal_year,
                    quarter=quarter,
                    statement_type=request.statement_type,
                    period_end=_period_end(fiscal_year, quarter),
                    publication_date=publication_date,
                    publication_timestamp=publication_timestamp,
                    available_date=available_date,
                    availability_rule=availability_rule,
                    source_system="MOPS",
                    source_record_id=source_record_id,
                    request_key=publication_request_key(
                        request.ticker,
                        fiscal_year - 1911,
                        quarter,
                        statement_type=request.statement_type,
                    ),
                    retrieved_at=retrieved,
                )
            )
    if not records:
        if period_mismatch is not None:
            raise PublicationParseError(
                f"period mismatch: expected {expected_year}Q{request.season}, got {period_mismatch[0]}Q{period_mismatch[1]}"
            )
        raise PublicationParseError("MOPS real-data page contains no filing rows")
    return tuple(records)


def validate_publication_record(record: PublicationRecord) -> None:
    if not record.period_end < record.publication_date:
        raise PublicationParseError("period_end must be earlier than publication_date")
    if record.publication_date > record.available_date:
        raise PublicationParseError("available_date must be on or after publication_date")


def load_research_trading_calendar(
    source: str | Path = "data/processed/research_universe.parquet",
) -> tuple[date_type, ...]:
    """Load the canonical research dates; never synthesize calendar days."""

    frame = pd.read_parquet(source)
    if "trade_date" not in frame.columns:
        raise PublicationParseError("canonical research calendar lacks trade_date")
    return _calendar_dates(frame["trade_date"].dropna())


def fetch_mops_publication_response(
    request: PublicationRequest,
    cache_root: str | Path,
    *,
    transport: Callable[[PublicationRequest], tuple[bytes | str, int, Mapping[str, object]]] | None = None,
    session: object | None = None,
    bootstrap_session: bool = True,
    timeout_seconds: float = 30.0,
) -> dict[str, object]:
    """Fetch/cache the official HTML response, keyed by request and source."""

    cache_dir = Path(cache_root) / MOPS_PUBLICATION_CACHE_DIR
    key = publication_request_key(
        request.ticker,
        request.roc_year,
        request.season,
        statement_type=request.statement_type,
    )
    digest = hashlib.sha256(key.encode("utf-8")).hexdigest()
    html_path = cache_dir / f"{digest}.html"
    metadata_path = cache_dir / f"{digest}.json"
    if html_path.exists() and metadata_path.exists():
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        if metadata.get("classification") == MOPS_REAL_DATA_PAGE:
            return {"html": html_path.read_bytes(), "cache_hit": True, **metadata}
    if transport is not None:
        raw, http_status, headers = transport(request)
    else:
        import requests

        client = session or requests.Session()
        if bootstrap_session:
            client.get(PUBLICATION_ENDPOINT, timeout=timeout_seconds)
        response = client.post(
            PUBLICATION_ENDPOINT,
            data=mops_publication_form(request),
            timeout=timeout_seconds,
        )
        raw = response.content
        http_status = response.status_code
        headers = {"content_type": response.headers.get("content-type", "")}
    raw_bytes = raw if isinstance(raw, bytes) else str(raw).encode("big5", errors="replace")
    classification = classify_mops_html(raw_bytes)
    metadata = {
        "request_key": key,
        "source_system": "MOPS",
        "http_status": int(http_status),
        "content_type": str(headers.get("content_type", "")),
        "classification": classification,
        "retrieved_at": datetime.now(timezone.utc).isoformat(),
    }
    cache_dir.mkdir(parents=True, exist_ok=True)
    _atomic_write(html_path, raw_bytes)
    _atomic_json(metadata_path, metadata)
    return {"html": raw_bytes, "cache_hit": False, **metadata}


def fetch_mops_publication_records(
    request: PublicationRequest,
    cache_root: str | Path,
    trading_dates: Iterable[object],
    **kwargs: object,
) -> dict[str, object]:
    response = fetch_mops_publication_response(request, cache_root, **kwargs)
    if response["classification"] != MOPS_REAL_DATA_PAGE:
        return {
            **response,
            "records": (),
            "parse_status": "QUARANTINED" if response["classification"] in {MOPS_RATE_LIMIT_PAGE, MOPS_LOGIN_BLOCK_PAGE, MOPS_ERROR_PAGE} else "EMPTY_RESULT",
            "evidence_status": "UNVERIFIED",
        }
    try:
        records = parse_mops_publication_html(
            response["html"],
            request,
            trading_dates,
            retrieved_at=datetime.fromisoformat(str(response["retrieved_at"])),
        )
    except PublicationParseError as exc:
        return {
            **response,
            "records": (),
            "parse_status": "INVALID_RESPONSE",
            "evidence_status": "UNVERIFIED",
            "failure_reason": str(exc),
        }
    for record in records:
        validate_publication_record(record)
    return {**response, "records": records, "parse_status": "PASS", "evidence_status": "VERIFIED"}


def plan_requests(tickers: Iterable[object]) -> tuple[PublicationRequest, ...]:
    normalized = tuple(sorted({clean_ticker(v) for v in tickers if clean_ticker(v)}))
    return tuple(
        PublicationRequest(ticker, year, season, make_request_key(ticker, year, season))
        for ticker, year, season in product(normalized, ROC_YEARS, SEASONS)
    )


def _request_cache_path(cache_root: Path, request_key: str) -> Path:
    digest = hashlib.sha256(request_key.encode("utf-8")).hexdigest()
    return cache_root / "requests" / f"{digest}.json"


def _index_cached_payloads(cache_root: Path) -> dict[str, object]:
    indexed: dict[str, object] = {}
    if not cache_root.exists():
        return indexed
    for path in cache_root.rglob("*.json"):
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not isinstance(value, Mapping):
            continue
        key = value.get("request_key")
        if not key and {"ticker", "roc_year", "season"}.issubset(value):
            try:
                key = make_request_key(value["ticker"], int(value["roc_year"]), int(value["season"]))
            except (TypeError, ValueError):
                key = None
        if key:
            indexed[str(key)] = value
    return indexed


def cached_publication_tickers(cache_root: str | Path) -> set[str]:
    """Discover cache contents for reporting only; never use this as a target."""

    root = Path(cache_root)
    tickers: set[str] = set()
    if not root.exists():
        return tickers
    for path in root.rglob("*.json"):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            payload = None
        candidates: list[object] = [path.stem]
        if isinstance(payload, Mapping):
            candidates.extend(payload.get(key) for key in ("ticker", "stock_id", "security_code"))
            request_key = payload.get("request_key")
            if request_key:
                match = re.search(r"ticker=([^|]+)", str(request_key))
                candidates.append(match.group(1) if match else None)
        for candidate in candidates:
            ticker = clean_ticker(candidate)
            if re.fullmatch(r"[1-9]\d{3}|\d{6}", ticker):
                tickers.add(ticker)
    return tickers


Fetcher = Callable[[PublicationRequest], Mapping[str, object] | object]


class ExpansionError(RuntimeError):
    """Raised only for invalid runner configuration, not upstream failures."""


def _truthy(value: object) -> bool:
    return value is True or str(value).strip().lower() in {"1", "true", "yes", "y"}


def _git_head() -> str | None:
    return subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True, check=False).stdout.strip() or None


def _git_status() -> str:
    return subprocess.run(["git", "status", "--porcelain"], capture_output=True, text=True, check=False).stdout


def _file_sha256(path: Path) -> str | None:
    try:
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()
    except OSError:
        return None


def fetch_publication_request(request: PublicationRequest, *, timeout_seconds: float = 30.0) -> Mapping[str, object]:
    """Fetch one TWSE publication slot; transport and parse failures stay explicit."""

    import requests

    response = requests.get(PUBLICATION_ENDPOINT, params=publication_request_parameters(request), timeout=timeout_seconds)
    response.raise_for_status()
    try:
        payload = response.json()
    except ValueError:
        classification = classify_mops_html(response.content)
        if classification == MOPS_REAL_DATA_PAGE:
            return {
                "payload": {"publication_found": True, "html": response.content.decode("big5", errors="replace")},
                "http_status": response.status_code,
                "parse_status": "PASS",
                "format": "HTML",
            }
        quarantined = classification in {MOPS_RATE_LIMIT_PAGE, MOPS_LOGIN_BLOCK_PAGE, MOPS_ERROR_PAGE}
        return {
            "payload": None,
            "http_status": response.status_code,
            "parse_status": "QUARANTINED" if quarantined else "INVALID_RESPONSE",
            "failure_reason": f"MOPS HTML classified as {classification}",
        }
    return {"payload": payload, "http_status": response.status_code, "parse_status": "PASS"}


def _safe_json(value: object) -> object:
    json.dumps(value, ensure_ascii=False)
    return value


def _extract_response(response: object) -> tuple[object, int | None, str, str]:
    if not isinstance(response, Mapping):
        return None, None, "INVALID_RESPONSE", "response is not a mapping"
    payload = response.get("payload", response)
    status = response.get("http_status")
    parse_status = str(response.get("parse_status", "PASS"))
    reason = str(response.get("failure_reason", ""))
    if payload is None:
        return None, _optional_int(status), "INVALID_RESPONSE", reason or "null payload"
    if parse_status.upper() not in {"PASS", "OK", "PARSED"}:
        return payload, _optional_int(status), "INVALID_RESPONSE", reason or "invalid response"
    return payload, _optional_int(status), "PASS", reason


def _publication_dates(payload: object) -> list[str]:
    if isinstance(payload, Mapping):
        values: object = payload.get("publication_dates", payload.get("publications", payload.get("records", payload.get("data", []))))
        if payload.get("publication_date"):
            values = [payload["publication_date"]]
        if isinstance(values, Mapping):
            values = [values]
        if isinstance(values, Sequence) and not isinstance(values, (str, bytes)):
            dates = []
            for item in values:
                value = item.get("publication_date") if isinstance(item, Mapping) else item
                if value:
                    dates.append(str(value))
            return sorted(set(dates))
    return []


def _publication_found(payload: object) -> bool:
    if isinstance(payload, Mapping):
        if "publication_found" in payload:
            return bool(payload["publication_found"])
        for key in ("publication_dates", "records", "data", "publications"):
            if key in payload:
                return bool(payload[key])
        return bool(payload.get("publication_date")) or bool(_publication_dates(payload))
    return bool(payload)


def _atomic_write(path: Path, data: bytes | str) -> None:
    """Write one cache file through a unique, fsynced temporary path."""

    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent)
    )
    temporary = Path(temporary_name)
    try:
        mode = "wb" if isinstance(data, bytes) else "w"
        with os.fdopen(fd, mode, encoding=None if mode == "wb" else "utf-8") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except Exception:
        try:
            temporary.unlink(missing_ok=True)
        finally:
            raise


def _atomic_json(path: Path, payload: object) -> None:
    _atomic_write(
        path,
        json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str),
    )


def _atomic_link_or_copy(source: Path, destination: Path) -> None:
    """Install a cache alias without a shared temporary filename."""

    destination.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary_name = tempfile.mkstemp(
        prefix=f".{destination.name}.", suffix=".tmp", dir=str(destination.parent)
    )
    os.close(fd)
    temporary = Path(temporary_name)
    try:
        temporary.unlink()
        try:
            os.link(source, temporary)
        except OSError:
            shutil.copyfile(source, temporary)
        with temporary.open("rb+") as handle:
            os.fsync(handle.fileno())
        os.replace(temporary, destination)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise


class PublicationExpansionRunner:
    """Incremental request executor with a durable, resume-safe ledger."""

    def __init__(
        self,
        target_tickers: Iterable[object],
        cache_root: str | Path,
        output_dir: str | Path,
        *,
        fetcher: Fetcher | None = None,
        cache_only: bool = False,
        max_attempts: int = 2,
        batch_size: int = 100,
        rate_limit_seconds: float = 0.0,
        timeout_seconds: float = 30.0,
        target_universe: TargetUniverse | None = None,
    ) -> None:
        self.target_tickers = tuple(sorted({clean_ticker(v) for v in target_tickers if clean_ticker(v)}))
        self.cache_root = Path(cache_root)
        self.output_dir = Path(output_dir)
        self.fetcher = fetcher
        self.cache_only = cache_only
        self.max_attempts = max(1, int(max_attempts))
        self.batch_size = max(1, int(batch_size))
        self.rate_limit_seconds = max(0.0, float(rate_limit_seconds))
        self.timeout_seconds = max(0.1, float(timeout_seconds))
        self.target_universe = target_universe
        self.ledger_path = self.output_dir / "publication_expansion_stats.csv"
        self._legacy_cache = _index_cached_payloads(self.cache_root)

    def run(self) -> dict[str, object]:
        requests = plan_requests(self.target_tickers)
        previous = self._load_ledger()
        cached_before = cached_publication_tickers(self.cache_root) & set(self.target_tickers)
        rows: list[dict[str, object]] = []
        for offset in range(0, len(requests), self.batch_size):
            for request in requests[offset:offset + self.batch_size]:
                old = previous.get(request.request_key)
                row = self._execute(request, old)
                rows.append(row)
                self._write_ledger(rows)
                if self.rate_limit_seconds and request is not requests[-1]:
                    time.sleep(self.rate_limit_seconds)
        rows.sort(key=lambda row: row["request_key"])
        self._write_ledger(rows)
        frame = pd.DataFrame(rows, columns=STAT_FIELDS)
        final_tickers = {str(row["ticker"]) for row in rows if row["publication_found"]}
        fetched_tickers = {str(row["ticker"]) for row in rows if row["request_status"] == "FETCHED" and row["publication_found"]}
        failed_tickers = {str(row["ticker"]) for row in rows if row["request_status"] in {"FAILED", "INVALID_RESPONSE", "QUARANTINED"}}
        final_digest = _sha256_text(sorted(final_tickers))
        manifest = {
            "change_id": "expand-fundamental-pit-coverage-v1",
            "dataset_version": "fundamental_pit_coverage_v2",
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "target_ticker_count": len(self.target_tickers),
            "target_ticker_sha256": _sha256_text(self.target_tickers),
            "target_universe_status": self.target_universe.status if self.target_universe else "PASS",
            "TARGET_UNIVERSE_STATUS": self.target_universe.status if self.target_universe else "PASS",
            "target_universe_baseline_count": self.target_universe.baseline_count if self.target_universe else None,
            "target_universe_baseline_sha256": self.target_universe.baseline_sha256 if self.target_universe else None,
            "planned_request_count": len(requests),
            "cached_request_count": int((frame["request_status"] == "CACHED").sum()),
            "fetched_request_count": int((frame["request_status"] == "FETCHED").sum()),
            "failed_request_count": int(frame["request_status"].isin(["FAILED", "INVALID_RESPONSE", "QUARANTINED"]).sum()),
            "existing_publication_ticker_count": len(cached_before),
            "missing_before_run_count": len(set(self.target_tickers) - cached_before),
            "newly_fetched_ticker_count": len(fetched_tickers),
            "failed_ticker_count": len(failed_tickers),
            "final_publication_ticker_count": len(final_tickers),
            "final_publication_ticker_sha256": final_digest,
            "target_publication_coverage_ratio": len(final_tickers) / len(self.target_tickers) if self.target_tickers else None,
            "network_fetch_count": int(frame["http_attempted"].sum()),
            "cache_only": self.cache_only,
            "git_head": _git_head(),
            "git_dirty": bool(_git_status()),
            "python_version": platform.python_version(),
            "git_version": dependency_metadata()["git_version"],
            "pandas_version": pd.__version__,
            "normalized_record_count": None,
            "factor_matrix_ticker_count": None,
            "input_sha256": None,
            "code_sha256": _file_sha256(Path(__file__)),
            "artifact_sha256": {"publication_expansion_stats.csv": _file_sha256(self.ledger_path)},
        }
        self.output_dir.mkdir(parents=True, exist_ok=True)
        mapping = rebuild_publication_mapping(frame, self.output_dir / "publication_dates_expanded.csv")
        manifest["artifact_sha256"] = {
            "publication_expansion_stats.csv": _file_sha256(self.ledger_path),
            "publication_dates_expanded.csv": _file_sha256(self.output_dir / "publication_dates_expanded.csv"),
        }
        manifest.update({
            "nonempty_publication_mapping_ticker_count": int(mapping["publication_found"].sum()) if not mapping.empty else 0,
            "missing_publication_ticker_count": int((~mapping["publication_found"]).sum()) if not mapping.empty else 0,
            "final_publication_ticker_count": int(mapping["publication_found"].sum()) if not mapping.empty else 0,
        })
        (self.output_dir / "run_manifest.json").write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
        return {"rows": frame, "manifest": manifest}

    def _load_ledger(self) -> dict[str, dict[str, object]]:
        if not self.ledger_path.exists():
            return {}
        return {str(row["request_key"]): row for row in csv.DictReader(self.ledger_path.open(encoding="utf-8", newline=""))}

    def _execute(self, request: PublicationRequest, old: Mapping[str, object] | None) -> dict[str, object]:
        cache_path = _request_cache_path(self.cache_root, request.request_key)
        cached_payload = self._cached_payload(request)
        if old and old.get("request_status") in {"CACHED", "FETCHED"} and cached_payload is not None:
            row = dict(old)
            row.update({"ticker": request.ticker, "roc_year": request.roc_year, "season": request.season, "request_key": request.request_key, "request_status": "CACHED", "cache_hit": True, "http_attempted": False})
            return self._coerce_row(row)
        if not re.fullmatch(r"[1-9]\d{3}", request.ticker):
            return self._row(request, "NOT_APPLICABLE", False, False, None, "NOT_APPLICABLE", False, "NONSTANDARD_IDENTIFIER")
        if cached_payload is not None:
            try:
                payload, status, parse_status, reason = _extract_response(cached_payload)
                if parse_status == "PASS":
                    found = _publication_found(payload)
                    return self._row(request, "CACHED", True, False, status, "PASS", found, reason or ("" if found else "SOURCE_NO_RECORD"), _publication_dates(payload)[0] if _publication_dates(payload) else None)
            except (OSError, ValueError):
                pass
        if self.cache_only:
            return self._row(request, "FAILED", False, False, None, "NOT_ATTEMPTED", False, "CACHE_MISS_CACHE_ONLY" if self.cache_only else "NO_FETCHER")
        fetcher = self.fetcher or (lambda item: fetch_publication_request(item, timeout_seconds=self.timeout_seconds))
        last_reason = "fetch failed"
        for _ in range(self.max_attempts):
            try:
                response = fetcher(request)
                payload, status, parse_status, reason = _extract_response(response)
                if parse_status != "PASS":
                    status_name = "QUARANTINED" if parse_status.upper() == "QUARANTINED" else "INVALID_RESPONSE"
                    return self._row(request, status_name, False, True, status, parse_status, False, reason)
                _safe_json(payload)
                found = _publication_found(payload)
                _atomic_json(cache_path, {"request_key": request.request_key, "ticker": request.ticker, "payload": payload, "publication_found": found, "http_status": status})
                dates = _publication_dates(payload)
                return self._row(request, "FETCHED", False, True, status, "PASS", found, reason or ("" if found else "SOURCE_NO_RECORD"), dates[0] if dates else None)
            except Exception as exc:  # upstream errors become ledger rows
                last_reason = str(exc) or exc.__class__.__name__
        return self._row(request, "FAILED", False, True, None, "FAILED", False, last_reason)

    def _cached_payload(self, request: PublicationRequest) -> object | None:
        path = _request_cache_path(self.cache_root, request.request_key)
        if path.exists():
            try:
                return json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                return None
        return self._legacy_cache.get(request.request_key)

    @staticmethod
    def _coerce_row(row: Mapping[str, object]) -> dict[str, object]:
        result = {field: row.get(field, "") for field in STAT_FIELDS}
        result["roc_year"] = int(result["roc_year"])
        result["season"] = int(result["season"])
        result["cache_hit"] = str(result["cache_hit"]).lower() == "true"
        result["http_attempted"] = str(result["http_attempted"]).lower() == "true"
        result["publication_found"] = _truthy(result["publication_found"])
        return result

    @staticmethod
    def _row(request: PublicationRequest, status: str, cache_hit: bool, attempted: bool, http_status: int | None, parse_status: str, found: bool, reason: str, publication_date: str | None = None) -> dict[str, object]:
        if status not in REQUEST_STATUSES:
            raise ExpansionError(f"unknown request status: {status}")
        return {"ticker": request.ticker, "roc_year": request.roc_year, "season": request.season, "request_key": request.request_key, "request_status": status, "cache_hit": cache_hit, "http_attempted": attempted, "http_status": http_status, "parse_status": parse_status, "publication_found": found, "publication_date": publication_date, "failure_reason": reason}

    def _write_ledger(self, rows: Sequence[Mapping[str, object]]) -> None:
        self.output_dir.mkdir(parents=True, exist_ok=True)
        temporary = self.ledger_path.with_suffix(".csv.tmp")
        with temporary.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=STAT_FIELDS)
            writer.writeheader()
            writer.writerows(rows)
        temporary.replace(self.ledger_path)


def run_expansion(target_tickers: Iterable[object], cache_root: str | Path, output_dir: str | Path, **kwargs: object) -> dict[str, object]:
    """Small functional entry point for CLI/jobs and offline callers."""

    return PublicationExpansionRunner(target_tickers, cache_root, output_dir, **kwargs).run()


def run_full_expansion(
    universe_path: str | Path,
    acceptance_tickers: Iterable[object],
    cache_root: str | Path,
    output_dir: str | Path = AUTHORITATIVE_OUTPUT_ROOT,
    *,
    baseline: Mapping[str, object] | str | Path | None = None,
    **kwargs: object,
) -> dict[str, object]:
    """Wire the canonical target source to the incremental publication runner."""

    target = build_target_universe(universe_path, acceptance_tickers, baseline=baseline)
    if target.status == "BLOCKED":
        output = Path(output_dir)
        output.mkdir(parents=True, exist_ok=True)
        manifest = {
            "change_id": "expand-fundamental-pit-coverage-v1",
            "dataset_version": "fundamental_pit_coverage_v2",
            "target_universe_status": "BLOCKED",
            "TARGET_UNIVERSE_STATUS": "BLOCKED",
            "target_ticker_count": target.count,
            "target_ticker_sha256": target.sha256,
            "UNBLOCK_STATUS": "BLOCKED",
            "FULL_EXPANSION_UNLOCKED": "NO",
            "READY_FOR_FULL_EXPANSION": "NO",
            "PRE_RUN_SNAPSHOT": "NOT_RUN",
            "LEGACY_BASELINE_STATUS": "NOT_ESTABLISHED",
            "network_fetch_count": 0,
            "full_publication_expansion": "FAILED",
            "failure_reason": "canonical target universe or review baseline unavailable",
        }
        (output / "run_manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        return {"target": target, "manifest": manifest, "status": "BLOCKED"}
    # Snapshot cache state before the first request.  Import lazily to keep
    # this module usable on its own and avoid a module-level cycle.
    from core.research.universe_recovery import create_pre_run_snapshot

    snapshot = create_pre_run_snapshot(
        target.tickers,
        cache_root,
        Path(output_dir) / "pre_run_snapshot",
        existing_publication_tickers=cached_publication_tickers(cache_root),
        target_source=universe_path,
    )
    result = PublicationExpansionRunner(target.tickers, cache_root, output_dir, target_universe=target, **kwargs).run()
    result["manifest"]["PRE_RUN_SNAPSHOT"] = "PASS"
    result["manifest"]["pre_run_snapshot"] = snapshot
    Path(output_dir).joinpath("run_manifest.json").write_text(json.dumps(result["manifest"], indent=2, ensure_ascii=False), encoding="utf-8")
    result["target"] = target
    return result


def validate_complete_ledger(ledger: pd.DataFrame, target_tickers: Iterable[object]) -> tuple[bool, str]:
    """Check that the ledger is a one-to-one complete request space."""

    required = {"request_key", "request_status"}
    missing = sorted(required - set(ledger.columns))
    if missing:
        return False, f"ledger missing columns: {missing}"
    expected = {request.request_key for request in plan_requests(target_tickers)}
    if set(ledger.get("request_key", pd.Series(dtype=object))) != expected:
        return False, "request-key set does not equal target × ROC years × seasons"
    if ledger["request_key"].duplicated().any():
        return False, "duplicate request_key"
    if not ledger["request_status"].isin(REQUEST_STATUSES).all():
        return False, "unknown request status"
    return True, ""


def validate_missingness_reconciliation(matrices: Mapping[str, pd.DataFrame], report: pd.DataFrame, target_tickers: Iterable[object]) -> tuple[bool, str]:
    """Ensure each and only each missing matrix cell has one allowed reason."""

    required_report = {"asof_date", "ticker", "metric", "missing_reason"}
    missing_report = sorted(required_report - set(report.columns))
    if missing_report and not report.empty:
        return False, f"missingness report missing columns: {missing_report}"
    target = tuple(sorted({clean_ticker(v) for v in target_tickers}))
    expected = {(day, ticker, metric) for metric, matrix in matrices.items() for day in matrix.index for ticker in target if pd.isna(matrix.reindex(columns=target).loc[day, ticker])}
    actual = {(row.asof_date, row.ticker, row.metric) for row in report.itertuples(index=False)} if not report.empty else set()
    if expected != actual:
        return False, "missingness cells do not reconcile"
    if report.duplicated(["asof_date", "ticker", "metric"]).any():
        return False, "duplicate missingness cell"
    if not report.empty and not report["missing_reason"].isin(MISSINGNESS_CATEGORIES).all():
        return False, "unknown missingness category"
    return True, ""


def rebuild_publication_mapping(ledger: pd.DataFrame, output_path: str | Path) -> pd.DataFrame:
    """Emit one explainable mapping row per target ticker from current evidence."""

    required = set(REQUIRED_STAT_FIELDS)
    if not required.issubset(ledger.columns):
        raise ValueError(f"ledger missing columns: {sorted(required - set(ledger.columns))}")
    rows: list[dict[str, object]] = []
    for ticker, group in ledger.groupby("ticker", sort=True):
        found = group.loc[group["publication_found"].map(_truthy)]
        reason = "" if not found.empty else next((str(v) for v in group["failure_reason"] if str(v).strip().lower() not in {"", "none", "nan"}), "NO_SOURCE_RECORD")
        publication_date = next((str(v) for v in found.get("publication_date", pd.Series(dtype=object)) if str(v) not in {"", "None", "nan"}), None)
        rows.append({"ticker": ticker, "publication_found": not found.empty, "publication_date": publication_date, "mapping_status": "MAPPED" if not found.empty else "MISSING", "missing_reason": reason})
    result = pd.DataFrame(rows, columns=["ticker", "publication_found", "publication_date", "mapping_status", "missing_reason"])
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    result.to_csv(path, index=False)
    return result


def validate_pit_order(records: pd.DataFrame) -> tuple[bool, pd.DataFrame]:
    """Validate conservative period/publication/availability ordering."""

    required = {"period_end", "publication_date", "available_date"}
    if not required.issubset(records.columns):
        missing = sorted(required - set(records.columns))
        return False, pd.DataFrame([{"code": "MISSING_COLUMNS", "detail": ",".join(missing)}])
    work = records.copy()
    for column in required | ({"asof_date"} if "asof_date" in work else set()):
        work[column] = pd.to_datetime(work[column], errors="coerce")
    invalid = work[work["period_end"].isna() | work["publication_date"].isna() | work["available_date"].isna() | ~(work["period_end"] < work["publication_date"]) | ~(work["publication_date"] <= work["available_date"])]
    if "asof_date" in work:
        invalid = pd.concat([invalid, work[work["publication_date"] > work["asof_date"]]]).drop_duplicates()
    diagnostics = pd.DataFrame({"code": "PIT_ORDERING", "detail": "period_end < publication_date <= available_date"}, index=invalid.index)
    return invalid.empty, diagnostics.reset_index(drop=True)


def normalize_pit_records(records: pd.DataFrame) -> pd.DataFrame:
    """Normalize dates and preserve every source revision for downstream joins."""

    ok, diagnostics = validate_pit_order(records)
    if not ok:
        raise ValueError(f"PIT_INTEGRITY failed: {diagnostics.to_dict('records')}")
    work = records.copy()
    for column in ("period_end", "publication_date", "available_date"):
        work[column] = pd.to_datetime(work[column])
    if "revision_status" not in work:
        work["revision_status"] = "SOURCE_UNAVAILABLE"
    else:
        work["revision_status"] = work["revision_status"].fillna("SOURCE_UNAVAILABLE")
    id_column = "ticker" if "ticker" in work else "stock_id"
    work[id_column] = work[id_column].map(clean_ticker)
    return work.sort_values([id_column, "period_end", "publication_date"], kind="stable").reset_index(drop=True)


def build_metric_matrices(records: pd.DataFrame, target_tickers: Iterable[str], asof_dates: Iterable[object]) -> dict[str, pd.DataFrame]:
    """Build EPS and ROE matrices using only publications available by each as-of date."""

    work = normalize_pit_records(records)
    id_column = "ticker" if "ticker" in work else "stock_id"
    target = tuple(sorted({clean_ticker(v) for v in target_tickers}))
    dates = pd.DatetimeIndex(sorted(pd.to_datetime(list(asof_dates))))
    eps = pd.DataFrame(index=dates, columns=target, dtype=float)
    roe = pd.DataFrame(index=dates, columns=target, dtype=float)
    for ticker in target:
        right = work[work[id_column].eq(ticker)].copy()
        asof_column = "available_date" if "available_date" in right.columns else "publication_date"
        right = right.sort_values(asof_column, kind="stable")
        left = pd.DataFrame({"asof_date": dates})
        if right.empty:
            continue
        joined = pd.merge_asof(left, right, left_on="asof_date", right_on=asof_column, direction="backward", allow_exact_matches=True)
        for _, row in joined.iterrows():
            day = row["asof_date"]
            if pd.isna(row.get("publication_date")) or row["period_end"] >= row["publication_date"]:
                continue
            if "eps" in row and pd.notna(row["eps"]):
                eps.loc[day, ticker] = float(row["eps"])
            if "net_income" in row and "equity" in row and pd.notna(row["net_income"]) and pd.notna(row["equity"]) and float(row["equity"]) != 0:
                roe.loc[day, ticker] = float(row["net_income"]) / float(row["equity"])
    return {"eps": eps, "roe": roe}


def build_metric_matrices_v3(
    records: pd.DataFrame,
    target_tickers: Iterable[str],
    asof_dates: Iterable[object],
    metrics: Iterable[str] = ("revenue", "operating_profit", "eps"),
) -> dict[str, pd.DataFrame]:
    """Build raw-metric PIT matrices keyed by conservative availability date."""

    work = normalize_pit_records(records)
    id_column = "ticker" if "ticker" in work else "stock_id"
    target = tuple(sorted({clean_ticker(v) for v in target_tickers if clean_ticker(v)}))
    dates = pd.DatetimeIndex(sorted(pd.to_datetime(list(asof_dates))))
    selected = tuple(dict.fromkeys(metric for metric in metrics if metric in work.columns))
    matrices = {metric: pd.DataFrame(index=dates, columns=target, dtype=float) for metric in selected}
    for ticker in target:
        right = work[work[id_column].eq(ticker)].copy()
        if right.empty:
            continue
        asof_column = "available_date" if "available_date" in right.columns else "publication_date"
        right = right.sort_values(asof_column, kind="stable")
        joined = pd.merge_asof(
            pd.DataFrame({"asof_date": dates}),
            right,
            left_on="asof_date",
            right_on=asof_column,
            direction="backward",
            allow_exact_matches=True,
        )
        for _, row in joined.iterrows():
            day = row["asof_date"]
            if pd.isna(row.get("publication_date")) or row["period_end"] >= row["publication_date"]:
                continue
            for metric, matrix in matrices.items():
                if pd.notna(row.get(metric)):
                    matrix.loc[day, ticker] = float(row[metric])
    return matrices


MISSINGNESS_CATEGORIES = frozenset({"NO_SOURCE_RECORD", "NOT_YET_PUBLISHED", "INVALID_RESPONSE", "MAPPING_FAILURE", "UNSUPPORTED_IDENTIFIER", "OTHER"})


def missingness_report(matrices: Mapping[str, pd.DataFrame], target_tickers: Iterable[str], mapping: pd.DataFrame | None = None) -> pd.DataFrame:
    mapping_reasons = {} if mapping is None else mapping.set_index("ticker").get("missing_reason", pd.Series(dtype=object)).to_dict()
    rows: list[dict[str, object]] = []
    target = tuple(sorted({clean_ticker(v) for v in target_tickers}))
    for metric, matrix in matrices.items():
        work = matrix.reindex(columns=target)
        for day in work.index:
            for ticker in target:
                if pd.notna(work.loc[day, ticker]):
                    continue
                reason = {"SOURCE_NO_RECORD": "NO_SOURCE_RECORD", "NONSTANDARD_IDENTIFIER": "UNSUPPORTED_IDENTIFIER"}.get(str(mapping_reasons.get(ticker, "NO_SOURCE_RECORD")), str(mapping_reasons.get(ticker, "NO_SOURCE_RECORD")))
                if reason not in MISSINGNESS_CATEGORIES:
                    reason = "OTHER"
                rows.append({"asof_date": day, "ticker": ticker, "metric": metric, "missing_reason": reason})
    return pd.DataFrame(rows, columns=["asof_date", "ticker", "metric", "missing_reason"])


def coverage_report(matrices: Mapping[str, pd.DataFrame], target_tickers: Iterable[str], factor_matrix_tickers: Iterable[str]) -> pd.DataFrame:
    target = tuple(sorted({clean_ticker(v) for v in target_tickers}))
    factor = tuple(sorted({clean_ticker(v) for v in factor_matrix_tickers}))
    rows = []
    paired_matrix = None
    if {"eps", "roe"}.issubset(matrices):
        paired_matrix = matrices["eps"].notna() & matrices["roe"].notna()
    for metric, matrix in matrices.items():
        usable = matrix.notna().any(axis=0)
        target_numerator = int(usable.reindex(target, fill_value=False).sum())
        paired = matrix.reindex(columns=factor).notna().all(axis=1).any() if factor else False
        factor_numerator = int(matrix.reindex(columns=factor).notna().any(axis=0).sum())
        paired_numerator = int(paired_matrix.reindex(columns=factor, fill_value=False).any(axis=0).sum()) if paired_matrix is not None and factor else 0
        rows.append({"metric": metric, "factor_matrix_numerator": factor_numerator, "factor_matrix_denominator": len(factor), "factor_matrix_coverage": factor_numerator / len(factor) if factor else None, "paired_observation_numerator": paired_numerator, "paired_observation_denominator": len(factor), "paired_observation_coverage": paired_numerator / len(factor) if factor else None, "target_universe_numerator": target_numerator, "target_universe_denominator": len(target), "target_universe_coverage": target_numerator / len(target) if target else None, "paired_observation_present": bool(paired)})
    return pd.DataFrame(rows)


def readiness(*, pipeline_integrity: bool, factor_matrix_coverage: float | None, full_expansion: bool | str) -> dict[str, object]:
    factor_ready = factor_matrix_coverage is not None and factor_matrix_coverage >= 0.20
    expansion_status = str(full_expansion).upper() if isinstance(full_expansion, str) else ("COMPLETE" if full_expansion else "PARTIAL")
    if expansion_status not in {"COMPLETE", "PARTIAL", "FAILED"}:
        raise ValueError("full_expansion must be COMPLETE, PARTIAL, FAILED, or bool")
    return {"PIPELINE_INTEGRITY_READY": "YES" if pipeline_integrity else "NO", "FACTOR_MATRIX_RESEARCH_READY": "YES" if factor_ready else "NO", "FULL_PUBLICATION_EXPANSION": expansion_status, "READY_FOR_RESEARCH_CYCLE_V3": "NO", "READY_FOR_FACTOR_GATE": "NO", "READY_FOR_BACKTEST": "NO", "READY_FOR_REVIEW": "YES" if pipeline_integrity and expansion_status == "COMPLETE" else "NO"}


def dependency_metadata() -> dict[str, str]:
    git = subprocess.run(["git", "--version"], capture_output=True, text=True, check=False).stdout.strip()
    return {"python_version": platform.python_version() or sys.version, "git_version": git}


def artifact_hashes(root: str | Path) -> dict[str, str]:
    root = Path(root)
    return {str(path.relative_to(root)).replace("\\", "/"): digest for path in sorted(root.rglob("*")) if path.is_file() and not path.name.endswith(".tmp") if (digest := _file_sha256(path))}


def reproducibility_check(first_root: str | Path, second_root: str | Path, names: Iterable[str] = ("publication_dates_expanded.csv", "fundamental_records.parquet", "fundamental_matrix.parquet", "coverage_report.csv")) -> bool:
    first, second = Path(first_root), Path(second_root)
    return all(_file_sha256(first / name) == _file_sha256(second / name) and _file_sha256(first / name) is not None for name in names)


def write_authoritative_artifacts(output_dir: str | Path, records: pd.DataFrame, matrices: Mapping[str, pd.DataFrame], missingness: pd.DataFrame, coverage: pd.DataFrame, manifest: Mapping[str, object]) -> dict[str, Path]:
    root = Path(output_dir)
    if "fundamental_pit_coverage_v1" in root.parts:
        raise ExpansionError("v1 fundamental evidence is immutable; write to the v2 output root")
    root.mkdir(parents=True, exist_ok=True)
    records_path = root / "fundamental_records.parquet"
    records.to_parquet(records_path, index=False)
    matrix: pd.DataFrame | None = None
    for metric, frame in matrices.items():
        rows = [{"asof_date": day, "ticker": ticker, metric: frame.loc[day, ticker]} for day in frame.index for ticker in frame.columns]
        part = pd.DataFrame(rows, columns=["asof_date", "ticker", metric])
        matrix = part if matrix is None else matrix.merge(part, on=["asof_date", "ticker"], how="outer")
    matrix = matrix if matrix is not None else pd.DataFrame(columns=["asof_date", "ticker"])
    matrix_path = root / "fundamental_matrix.parquet"
    matrix.to_parquet(matrix_path, index=False)
    missing_path = root / "fundamental_missingness_report.csv"
    missingness.to_csv(missing_path, index=False)
    coverage_path = root / "coverage_report.csv"
    coverage.to_csv(coverage_path, index=False)
    manifest_path = root / "fundamental_manifest.json"
    manifest_payload = {
        "change_id": "expand-fundamental-pit-coverage-v1",
        "dataset_version": "fundamental_pit_coverage_v2",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "git_head": _git_head(),
        "git_dirty": bool(_git_status()),
        "python_version": platform.python_version(),
        "git_version": dependency_metadata()["git_version"],
        "pandas_version": pd.__version__,
        "normalized_record_count": len(records),
        "factor_matrix_ticker_count": len(next(iter(matrices.values())).columns) if matrices else 0,
        "input_sha256": None,
        "code_sha256": _file_sha256(Path(__file__)),
        "test_commands": [],
        "test_results": [],
        **dict(manifest),
    }
    manifest_payload["artifact_sha256"] = {key: _file_sha256(path) for key, path in {"fundamental_records.parquet": records_path, "fundamental_matrix.parquet": matrix_path, "fundamental_missingness_report.csv": missing_path, "coverage_report.csv": coverage_path}.items()}
    manifest_path.write_text(json.dumps(manifest_payload, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    return {"records": records_path, "matrix": matrix_path, "missingness": missing_path, "coverage": coverage_path, "manifest": manifest_path}


__all__ = [
    "MISSINGNESS_CATEGORIES", "AUTHORITATIVE_OUTPUT_ROOT", "ExpansionError", "PUBLICATION_ENDPOINT", "PublicationExpansionRunner", "PublicationRequest", "REQUEST_STATUSES", "ROC_YEARS", "SEASONS", "TargetUniverse", "artifact_hashes", "build_metric_matrices", "build_target_universe", "cached_publication_tickers", "clean_ticker", "coverage_report", "dependency_metadata", "fetch_publication_request", "make_request_key", "missingness_report", "normalize_pit_records", "plan_requests", "publication_request_parameters", "readiness", "rebuild_publication_mapping", "reproducibility_check", "run_expansion", "run_full_expansion", "validate_complete_ledger", "validate_missingness_reconciliation", "validate_pit_order", "write_authoritative_artifacts",
]
