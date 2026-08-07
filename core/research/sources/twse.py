"""TWSE official-source adapters."""

import json
from dataclasses import dataclass, field
from datetime import date, datetime
from enum import Enum
from pathlib import Path
from time import monotonic, sleep as real_sleep
from typing import Callable, Mapping

import requests

from core.research.sources import RawResponse


MI_INDEX_URL = "https://www.twse.com.tw/rwd/zh/afterTrading/MI_INDEX"
TWT49U_URL = "https://www.twse.com.tw/rwd/zh/exRight/TWT49U"
OPENAPI_URL = "https://openapi.twse.com.tw/v1"
REQUIRED_ACTION_FIELDS = frozenset({"資料日期", "股票代號", "除權息前收盤價", "除權息參考價", "權/息"})
NON_TRADING_STAT = "很抱歉，沒有符合條件的資料!"
FUTURE_STAT = "查詢日期大於今日，請重新查詢!"
EARLY_STAT = "查詢日期小於93年2月11日，請重新查詢!"
REQUIRED_CLOSING_FIELDS = frozenset(
    {
        "證券代號",
        "成交股數",
        "成交筆數",
        "成交金額",
        "開盤價",
        "最高價",
        "最低價",
        "收盤價",
    }
)


class ResponseKind(str, Enum):
    TRADING_DAY = "TRADING_DAY"
    NON_TRADING_DAY = "NON_TRADING_DAY"
    OUT_OF_RANGE = "OUT_OF_RANGE"
    EMPTY_RESULT = "EMPTY_RESULT"
    SOURCE_ERROR = "SOURCE_ERROR"


class SchemaDriftError(ValueError):
    code = "F009_schema_drift"


@dataclass
class RequestGate:
    minimum_interval: float
    clock: Callable[[], float] = monotonic
    sleep: Callable[[float], None] = real_sleep
    _last_request_at: float | None = field(default=None, init=False)

    def wait(self) -> None:
        if self._last_request_at is not None:
            remaining = self.minimum_interval - (self.clock() - self._last_request_at)
            if remaining > 0:
                self.sleep(remaining)
        self._last_request_at = self.clock()


@dataclass(frozen=True)
class SourceClassification:
    kind: ResponseKind
    bound: str | None = None
    diagnostic_code: str | None = None
    severity: str | None = None
    detail: str = ""

    def coverage_row(self, trade_date: str) -> dict[str, str]:
        return {
            "trade_date": trade_date,
            "classification": self.kind.value,
            "bound": self.bound or "",
            "code": self.diagnostic_code or "",
            "severity": self.severity or "",
            "detail": self.detail,
        }


def classify(response: RawResponse) -> SourceClassification:
    """Classify a TWSE response using only Phase 0's exact status strings."""

    if response.error:
        return _source_error(response.error)
    if not isinstance(response.payload, Mapping):
        return _source_error("response payload is not an object")

    stat = response.payload.get("stat")
    if stat == "OK":
        table = find_closing_table(response.payload)
        if not table.get("data"):
            return SourceClassification(
                ResponseKind.EMPTY_RESULT,
                diagnostic_code="W010_source_empty",
                severity="WARN",
                detail="successful response has no rows",
            )
        return SourceClassification(ResponseKind.TRADING_DAY)
    if stat == NON_TRADING_STAT:
        return SourceClassification(ResponseKind.NON_TRADING_DAY)
    if stat == FUTURE_STAT:
        return SourceClassification(
            ResponseKind.OUT_OF_RANGE,
            bound="future",
            diagnostic_code="W013_request_out_of_range",
            severity="WARN",
            detail="requested date is after the source's current date",
        )
    if stat == EARLY_STAT:
        return SourceClassification(
            ResponseKind.OUT_OF_RANGE,
            bound="early",
            diagnostic_code="F011_window_before_source_start",
            severity="FATAL",
            detail="requested date precedes 2004-02-11 source coverage",
        )
    return _source_error(f"unrecognized status: {stat!r}")


def _source_error(detail: str) -> SourceClassification:
    return SourceClassification(
        ResponseKind.SOURCE_ERROR,
        diagnostic_code="W011_source_error",
        severity="WARN",
        detail=detail,
    )


def find_closing_table(payload: Mapping[str, object]) -> Mapping[str, object]:
    """Locate the sole closing-price table by its declared schema, not position."""

    tables = payload.get("tables")
    if not isinstance(tables, list):
        raise SchemaDriftError("daily closing table is missing")
    matches = [
        table
        for table in tables
        if isinstance(table, Mapping)
        and "每日收盤行情" in str(table.get("title", ""))
        and REQUIRED_CLOSING_FIELDS.issubset(set(table.get("fields", [])))
    ]
    if len(matches) != 1:
        raise SchemaDriftError(f"expected one daily closing table, found {len(matches)}")
    return matches[0]


def parse_roc_date(value: str) -> date:
    """Parse the TWT49U Republic-of-China calendar date format."""

    year, remainder = value.split("年", 1)
    month, day = remainder.removesuffix("日").split("月", 1)
    return date(int(year) + 1911, int(month), int(day))


def parse_twse_date(value: str) -> date:
    """Parse the four observed TWSE date encodings."""

    if "年" in value:
        return parse_roc_date(value)
    if "/" in value:
        year, month, day = value.split("/")
        return date(int(year) + 1911, int(month), int(day))
    if len(value) == 7:
        return date(int(value[:3]) + 1911, int(value[3:5]), int(value[5:]))
    return date.fromisoformat(f"{value[:4]}-{value[4:6]}-{value[6:]}")


def fetch_delisted() -> RawResponse:
    return _fetch_openapi("company/suspendListingCsvAndHtml")


def fetch_holidays() -> RawResponse:
    return _fetch_openapi("holidaySchedule/holidaySchedule")


def fetch_company_profile(cache_dir: Path | None = None) -> RawResponse:
    """Fetch or reuse the payload-only listed-company profile response."""

    if cache_dir is None:
        return _fetch_openapi("opendata/t187ap03_L")
    cache_path = Path(cache_dir) / "t187ap03_L.json"
    if cache_path.exists():
        payload = json.loads(cache_path.read_text(encoding="utf-8"))
        cache_used = True
    else:
        payload = _fetch_openapi("opendata/t187ap03_L").payload
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        cache_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        cache_used = False
    return RawResponse("twse_openapi", "opendata/t187ap03_L", {}, datetime.now(), None, payload, None, {"cache_used": cache_used})


def _fetch_openapi(endpoint: str) -> RawResponse:
    response = requests.get(f"{OPENAPI_URL}/{endpoint}", timeout=30)
    response.raise_for_status()
    return RawResponse("twse_openapi", endpoint, {}, datetime.now(), None, response.json(), None)


def fetch_corporate_actions(start: date, end: date, cache_dir: Path) -> RawResponse:
    """Fetch and cache the TWSE-listed corporate-action response."""

    start_text, end_text = start.strftime("%Y%m%d"), end.strftime("%Y%m%d")
    parameters = {"startDate": start_text, "endDate": end_text, "response": "json"}
    cache_path = Path(cache_dir) / f"TWT49U_{start_text}_{end_text}.json"
    if cache_path.exists():
        payload = json.loads(cache_path.read_text(encoding="utf-8"))
    else:
        response = requests.get(TWT49U_URL, params=parameters, timeout=30)
        response.raise_for_status()
        payload = response.json()
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        cache_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    validate_corporate_action_fields(payload)
    return RawResponse(
        source="twse_rwd",
        endpoint="TWT49U",
        request_parameters=parameters,
        retrieved_at=datetime.now(),
        source_revision=None,
        payload=payload,
        error=None,
    )


def validate_corporate_action_fields(payload: Mapping[str, object]) -> None:
    if not REQUIRED_ACTION_FIELDS.issubset(set(payload.get("fields", []))):
        raise SchemaDriftError("TWT49U required fields are missing")


def fetch_daily_quotes(
    trade_date: date,
    cache_dir: Path,
    *,
    max_attempts: int = 3,
    gate: RequestGate | None = None,
    sleep: Callable[[float], None] = real_sleep,
) -> RawResponse:
    """Fetch and cache one official daily closing-report response."""

    day = trade_date.strftime("%Y%m%d")
    parameters = {"date": day, "type": "ALL", "response": "json"}
    cache_path = Path(cache_dir) / f"MI_INDEX_{day}.json"
    if cache_path.exists():
        payload = json.loads(cache_path.read_text(encoding="utf-8"))
    else:
        gate = gate or RequestGate(0, sleep=sleep)
        payload = None
        error = None
        for _ in range(max_attempts):
            try:
                gate.wait()
                response = requests.get(MI_INDEX_URL, params=parameters, timeout=30)
                response.raise_for_status()
                payload = response.json()
                break
            except (requests.RequestException, ValueError) as caught:
                error = str(caught)
        if payload is None:
            return RawResponse(
                source="twse_rwd",
                endpoint="MI_INDEX",
                request_parameters=parameters,
                retrieved_at=datetime.now(),
                source_revision=None,
                payload=None,
                error=error or "request failed",
            )
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        cache_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    revision = payload.get("date") if isinstance(payload, dict) else None
    return RawResponse(
        source="twse_rwd",
        endpoint="MI_INDEX",
        request_parameters=parameters,
        retrieved_at=datetime.now(),
        source_revision=revision,
        payload=payload,
        error=None,
    )
