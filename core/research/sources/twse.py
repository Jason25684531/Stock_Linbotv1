"""TWSE official-source adapters."""

import json
from dataclasses import dataclass
from datetime import date, datetime
from enum import Enum
from pathlib import Path
from typing import Mapping

import requests

from core.research.sources import RawResponse


MI_INDEX_URL = "https://www.twse.com.tw/rwd/zh/afterTrading/MI_INDEX"
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


def fetch_daily_quotes(trade_date: date, cache_dir: Path) -> RawResponse:
    """Fetch and cache one official daily closing-report response."""

    day = trade_date.strftime("%Y%m%d")
    parameters = {"date": day, "type": "ALL", "response": "json"}
    cache_path = Path(cache_dir) / f"MI_INDEX_{day}.json"
    if cache_path.exists():
        payload = json.loads(cache_path.read_text(encoding="utf-8"))
    else:
        response = requests.get(MI_INDEX_URL, params=parameters, timeout=30)
        response.raise_for_status()
        payload = response.json()
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
