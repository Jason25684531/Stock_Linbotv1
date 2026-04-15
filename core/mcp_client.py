"""Async-first MCP transport boundary for TWSE modernization.

This module is the only covered-data HTTP client used by the bot process.
It talks to ``Config.MCP_BASE_URL`` and exposes sync/async helpers for:
- stock basic snapshot
- foreign investor flow
- historical financial statements

The same client is shared by ``1_update_database.py``, financial updaters,
and the MCP-backed context tools in ``core/news_agent.py``.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import (
    Any,
    Awaitable,
    Callable,
    Literal,
    Mapping,
    Sequence,
    TypeAlias,
    TypeVar,
)
from uuid import uuid4

import httpx
import pandas as pd

from config import Config

MarketCode: TypeAlias = Literal["TWSE", "TPEx", "ALL"]
DatasetName: TypeAlias = Literal[
    "company_basic_info",
    "stock_basic_snapshot",
    "foreign_investor_flow",
    "historical_financial_statements",
]
DateValue: TypeAlias = date | datetime | str
JSONDict: TypeAlias = dict[str, Any]

_T = TypeVar("_T")
_LOGGER = logging.getLogger(__name__)
_RETRYABLE_STATUS_CODES = {408, 425, 429, 500, 502, 503, 504}

_TOOL_ROUTE_ENDPOINTS: dict[str, str] = {
    "company_basic_info": "/v1/tools/get_company_basic_info",
    "market_statistics": "/v1/tools/get_market_statistics",
    "foreign_investment": "/v1/tools/get_foreign_investment",
}

_COMPANY_BASIC_INFO_ALIASES: dict[str, str] = {
    "stock_id": "stock_id",
    "code": "stock_id",
    "symbol": "stock_id",
    "stock_code": "stock_id",
    "ticker": "stock_id",
    "stock_name": "stock_name",
    "company_name": "stock_name",
    "name": "stock_name",
    "market": "market",
    "exchange": "market",
    "industry": "industry_category",
    "industry_category": "industry_category",
    "trade_date": "trade_date",
    "date": "trade_date",
    "as_of_date": "trade_date",
    "close_price": "close_price",
    "close": "close_price",
    "open_price": "open_price",
    "open": "open_price",
    "high_price": "high_price",
    "high": "high_price",
    "low_price": "low_price",
    "low": "low_price",
    "volume": "volume",
    "trading_volume": "volume",
    "pe_ratio": "pe_ratio",
    "pe": "pe_ratio",
    "security_type": "security_type",
    "listing_date": "listing_date",
    "listed_date": "listing_date",
}

_MARKET_STATISTICS_ALIASES: dict[str, str] = {
    "stock_id": "stock_id",
    "code": "stock_id",
    "symbol": "stock_id",
    "stock_code": "stock_id",
    "ticker": "stock_id",
    "trade_date": "trade_date",
    "date": "trade_date",
    "as_of_date": "trade_date",
    "open_price": "open_price",
    "open": "open_price",
    "high_price": "high_price",
    "high": "high_price",
    "low_price": "low_price",
    "low": "low_price",
    "close_price": "close_price",
    "close": "close_price",
    "volume": "volume",
    "trading_volume": "volume",
    "成交股數": "volume",
    "pe_ratio": "pe_ratio",
    "pe": "pe_ratio",
    "stock_name": "stock_name",
    "company_name": "stock_name",
    "name": "stock_name",
    "security_type": "security_type",
}

_FOREIGN_INVESTMENT_ALIASES: dict[str, str] = {
    "stock_id": "stock_id",
    "code": "stock_id",
    "symbol": "stock_id",
    "stock_code": "stock_id",
    "ticker": "stock_id",
    "trade_date": "trade_date",
    "date": "trade_date",
    "as_of_date": "trade_date",
    "foreign_buy": "foreign_buy",
    "foreign_net_buy": "foreign_buy",
    "foreign_investment": "foreign_buy",
    "trust_buy": "trust_buy",
    "trust_net_buy": "trust_buy",
    "dealer_buy": "dealer_buy",
    "dealer_net_buy": "dealer_buy",
}

_FINANCIAL_STATEMENT_ALIASES: dict[str, str] = {
    "stock_id": "stock_id",
    "code": "stock_id",
    "symbol": "stock_id",
    "stock_code": "stock_id",
    "ticker": "stock_id",
    "revenue": "revenue",
    "rd_expense": "rd_expense",
    "r_and_d_expense": "rd_expense",
    "operating_expense": "operating_expense",
    "op_expense": "operating_expense",
    "operating_profit": "operating_profit",
    "op_profit": "operating_profit",
    "eps": "eps",
}


def _generate_correlation_id(prefix: str = "mcp") -> str:
    return f"{prefix}-{uuid4().hex[:12]}"


def _normalize_market(market: str) -> MarketCode:
    normalized = str(market).strip().upper()
    mapping: dict[str, MarketCode] = {
        "TWSE": "TWSE",
        "TPEX": "TPEx",
        "ALL": "ALL",
    }
    if normalized not in mapping:
        raise ValueError(f"Unsupported market: {market}")
    return mapping[normalized]


def _normalize_trade_date(trade_date: DateValue) -> str:
    if isinstance(trade_date, datetime):
        return trade_date.date().isoformat()
    if isinstance(trade_date, date):
        return trade_date.isoformat()

    normalized = str(trade_date).strip()
    if not normalized:
        raise ValueError("trade_date is required")

    try:
        return date.fromisoformat(normalized[:10]).isoformat()
    except ValueError as exc:
        raise ValueError(
            f"trade_date must be ISO-8601, got: {trade_date}"
        ) from exc


class MCPClientError(RuntimeError):
    """Base error raised by the MCP transport boundary."""

    def __init__(
        self,
        message: str,
        *,
        endpoint: str,
        correlation_id: str,
        retryable: bool = False,
        status_code: int | None = None,
        details: Mapping[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.endpoint = endpoint
        self.correlation_id = correlation_id
        self.retryable = retryable
        self.status_code = status_code
        self.details = dict(details or {})


class MCPConfigurationError(MCPClientError):
    """Raised when local MCP client configuration is invalid."""


class MCPTransportError(MCPClientError):
    """Raised when the MCP service cannot be reached reliably."""


class MCPServiceError(MCPClientError):
    """Raised when the MCP service returns a structured error."""


class MCPResponseError(MCPClientError):
    """Raised when the MCP service returns an invalid payload."""


@dataclass(frozen=True, slots=True)
class MCPRequestContext:
    """Shared request metadata carried across MCP datasets."""

    market: MarketCode | str = "ALL"
    correlation_id: str = field(default_factory=_generate_correlation_id)
    include_etfs: bool = True

    def __post_init__(self) -> None:
        object.__setattr__(self, "market", _normalize_market(self.market))
        if not self.correlation_id.strip():
            raise ValueError("correlation_id is required")


def _default_context() -> MCPRequestContext:
    return MCPRequestContext(market=Config.MCP_DEFAULT_MARKET)


@dataclass(frozen=True, slots=True)
class StockBasicSnapshotRequest:
    """DTO for the market snapshot MCP contract."""

    trade_date: DateValue
    context: MCPRequestContext = field(default_factory=_default_context)

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "trade_date",
            _normalize_trade_date(self.trade_date),
        )

    def to_payload(self) -> JSONDict:
        return {
            "trade_date": self.trade_date,
            "market": self.context.market,
            "correlation_id": self.context.correlation_id,
            "include_etfs": self.context.include_etfs,
        }


@dataclass(frozen=True, slots=True)
class ForeignInvestorFlowRequest:
    """DTO for the institutional net-buy MCP contract."""

    trade_date: DateValue
    context: MCPRequestContext = field(default_factory=_default_context)

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "trade_date",
            _normalize_trade_date(self.trade_date),
        )

    def to_payload(self) -> JSONDict:
        return {
            "trade_date": self.trade_date,
            "market": self.context.market,
            "correlation_id": self.context.correlation_id,
            "include_etfs": self.context.include_etfs,
        }


@dataclass(frozen=True, slots=True)
class HistoricalFinancialStatementsRequest:
    """DTO for the financial statements MCP contract."""

    year: int
    quarter: int
    context: MCPRequestContext = field(default_factory=_default_context)

    def __post_init__(self) -> None:
        if int(self.year) < 2000:
            raise ValueError(f"year must be >= 2000, got: {self.year}")
        if int(self.quarter) not in {1, 2, 3, 4}:
            raise ValueError(f"quarter must be 1-4, got: {self.quarter}")

    def to_payload(self) -> JSONDict:
        return {
            "year": int(self.year),
            "quarter": int(self.quarter),
            "market": self.context.market,
            "correlation_id": self.context.correlation_id,
        }


RequestPayload: TypeAlias = (
    StockBasicSnapshotRequest
    | ForeignInvestorFlowRequest
    | HistoricalFinancialStatementsRequest
)


def _iter_payload_mappings(payload: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    mappings: list[Mapping[str, Any]] = [payload]
    for key in ("result", "data", "payload"):
        value = payload.get(key)
        if isinstance(value, Mapping):
            mappings.append(value)
    return mappings


def _extract_first_value(
    payload: Mapping[str, Any],
    keys: Sequence[str],
) -> Any | None:
    for mapping in _iter_payload_mappings(payload):
        for key in keys:
            value = mapping.get(key)
            if value not in (None, "", [], {}):
                return value
    return None


def _extract_records(payload: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    for mapping in _iter_payload_mappings(payload):
        for key in ("records", "items", "rows", "list", "data"):
            value = mapping.get(key)
            if isinstance(value, list):
                return [item for item in value if isinstance(item, Mapping)]

    direct_data = payload.get("data")
    if isinstance(direct_data, list):
        return [item for item in direct_data if isinstance(item, Mapping)]
    return []


def _extract_single_record(payload: Mapping[str, Any]) -> Mapping[str, Any] | None:
    for mapping in _iter_payload_mappings(payload):
        for key in ("record", "company", "item"):
            value = mapping.get(key)
            if isinstance(value, Mapping):
                return value
    records = _extract_records(payload)
    return records[0] if records else None


def _canonicalize_record(
    record: Mapping[str, Any],
    aliases: Mapping[str, str],
) -> JSONDict:
    normalized: JSONDict = {}
    for key, value in record.items():
        key_text = str(key)
        canonical_key = aliases.get(key_text, key_text)
        if canonical_key not in normalized or normalized[canonical_key] in (None, ""):
            normalized[canonical_key] = value
    return normalized


def _coerce_iso_date(value: Any, fallback: str) -> str:
    if value in (None, ""):
        return fallback
    try:
        return _normalize_trade_date(value)
    except ValueError:
        return fallback


def _normalize_market_statistics_payload(
    payload: Mapping[str, Any],
    *,
    trade_date: str,
    market: MarketCode,
) -> JSONDict:
    records: list[JSONDict] = []
    for raw_record in _extract_records(payload):
        record = _canonicalize_record(raw_record, _MARKET_STATISTICS_ALIASES)
        record["trade_date"] = _coerce_iso_date(
            record.get("trade_date") or record.get("as_of_date"),
            trade_date,
        )
        record.setdefault("pe_ratio", 0)
        records.append(record)

    return {
        "dataset": "stock_basic_snapshot",
        "as_of_date": _coerce_iso_date(
            _extract_first_value(payload, ("as_of_date", "trade_date", "date")),
            trade_date,
        ),
        "market": str(
            _extract_first_value(payload, ("market", "exchange", "market_type"))
            or market
        ),
        "records": records,
        "meta": {"record_count": len(records)},
    }


def _normalize_foreign_investment_payload(
    payload: Mapping[str, Any],
    *,
    trade_date: str,
    market: MarketCode,
) -> JSONDict:
    records: list[JSONDict] = []
    for raw_record in _extract_records(payload):
        record = _canonicalize_record(raw_record, _FOREIGN_INVESTMENT_ALIASES)
        record["trade_date"] = _coerce_iso_date(
            record.get("trade_date") or record.get("as_of_date"),
            trade_date,
        )
        record.setdefault("trust_buy", 0)
        record.setdefault("dealer_buy", 0)
        records.append(record)

    return {
        "dataset": "foreign_investor_flow",
        "as_of_date": _coerce_iso_date(
            _extract_first_value(payload, ("as_of_date", "trade_date", "date")),
            trade_date,
        ),
        "market": str(
            _extract_first_value(payload, ("market", "exchange", "market_type"))
            or market
        ),
        "records": records,
        "meta": {"record_count": len(records)},
    }


def _normalize_company_basic_info_payload(
    payload: Mapping[str, Any],
    *,
    stock_id: str,
    trade_date: str,
    market: MarketCode,
) -> JSONDict:
    direct_record = _extract_single_record(payload)
    if direct_record is not None:
        record = _canonicalize_record(direct_record, _COMPANY_BASIC_INFO_ALIASES)
    else:
        record = {}
        for raw_record in _extract_records(payload):
            candidate = _canonicalize_record(raw_record, _COMPANY_BASIC_INFO_ALIASES)
            if str(candidate.get("stock_id", "")).strip() == stock_id:
                record = candidate
                break

    if not record:
        raise MCPResponseError(
            "company_basic_info response does not contain the requested stock",
            endpoint="/v1/tools/get_company_basic_info",
            correlation_id=str(
                _extract_first_value(payload, ("correlation_id",)) or "unknown"
            ),
            details={"stock_id": stock_id},
        )

    record["stock_id"] = str(record.get("stock_id") or stock_id).strip()
    record["trade_date"] = _coerce_iso_date(
        record.get("trade_date") or record.get("as_of_date"),
        trade_date,
    )
    record.setdefault("market", str(market))

    return {
        "dataset": "company_basic_info",
        "as_of_date": _coerce_iso_date(
            _extract_first_value(payload, ("as_of_date", "trade_date", "date")),
            trade_date,
        ),
        "market": str(
            _extract_first_value(payload, ("market", "exchange", "market_type"))
            or market
        ),
        "record": record,
        "records": [record],
        "meta": {"record_count": 1},
    }


def _normalize_financial_statements_payload(
    payload: Mapping[str, Any],
    *,
    year: int,
    quarter: int,
    market: MarketCode,
) -> JSONDict:
    records = [
        _canonicalize_record(raw_record, _FINANCIAL_STATEMENT_ALIASES)
        for raw_record in _extract_records(payload)
    ]
    period_value = _extract_first_value(payload, ("period",))
    period: Mapping[str, Any]
    if isinstance(period_value, Mapping):
        period = period_value
    else:
        period = {"year": year, "quarter": quarter}

    return {
        "dataset": "historical_financial_statements",
        "period": {
            "year": int(period.get("year") or year),
            "quarter": int(period.get("quarter") or quarter),
        },
        "unit": str(_extract_first_value(payload, ("unit",)) or "TWD"),
        "market": str(
            _extract_first_value(payload, ("market", "exchange", "market_type"))
            or market
        ),
        "records": records,
        "meta": {"record_count": len(records)},
    }


@dataclass(frozen=True, slots=True)
class MCPFetchJob:
    """Describes one dataset request used by the fetch_many helper."""

    name: str
    dataset: DatasetName
    request: RequestPayload

    def __post_init__(self) -> None:
        if not self.name.strip():
            raise ValueError("job name is required")


class MCPClient:
    """Async-first client that encapsulates all MCP HTTP calls."""

    def __init__(
        self,
        *,
        base_url: str | None = None,
        timeout_seconds: float | None = None,
        connect_timeout_seconds: float | None = None,
        max_retries: int | None = None,
        backoff_base_seconds: float | None = None,
        max_backoff_seconds: float | None = None,
        logger: logging.Logger | None = None,
    ) -> None:
        resolved_base_url = (
            (base_url or Config.MCP_BASE_URL).strip().rstrip("/")
        )
        if not resolved_base_url:
            raise MCPConfigurationError(
                "MCP_BASE_URL is required",
                endpoint="__init__",
                correlation_id=_generate_correlation_id("config"),
            )

        resolved_timeout = (
            timeout_seconds
            if timeout_seconds is not None
            else Config.MCP_HTTP_TIMEOUT_SECONDS
        )
        resolved_connect_timeout = (
            connect_timeout_seconds
            if connect_timeout_seconds is not None
            else Config.MCP_CONNECT_TIMEOUT_SECONDS
        )
        resolved_retries = (
            max_retries if max_retries is not None else Config.MCP_MAX_RETRIES
        )
        resolved_backoff = (
            backoff_base_seconds
            if backoff_base_seconds is not None
            else Config.MCP_BACKOFF_BASE_SECONDS
        )
        resolved_max_backoff = (
            max_backoff_seconds
            if max_backoff_seconds is not None
            else Config.MCP_MAX_BACKOFF_SECONDS
        )

        self.base_url = resolved_base_url
        self.timeout = httpx.Timeout(
            timeout=resolved_timeout,
            connect=resolved_connect_timeout,
        )
        self.max_retries = max(1, int(resolved_retries))
        self.backoff_base_seconds = max(0.0, float(resolved_backoff))
        self.max_backoff_seconds = max(0.0, float(resolved_max_backoff))
        self._logger = logger or _LOGGER
        self._client: httpx.AsyncClient | None = None

    async def __aenter__(self) -> "MCPClient":
        await self._ensure_client()
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: Any,
    ) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def fetch_stock_basic_snapshot(
        self,
        request: StockBasicSnapshotRequest,
    ) -> JSONDict:
        return await self._run_managed(
            lambda: self._fetch_dataset(
                endpoint="/v1/stock-basic-snapshot",
                payload=request.to_payload(),
                dataset="stock_basic_snapshot",
                correlation_id=request.context.correlation_id,
                required_fields=(
                    "dataset",
                    "as_of_date",
                    "market",
                    "records",
                ),
                required_record_fields=(
                    "stock_id",
                    "trade_date",
                    "open_price",
                    "high_price",
                    "low_price",
                    "close_price",
                    "volume",
                ),
            )
        )

    async def fetch_foreign_investor_flow(
        self,
        request: ForeignInvestorFlowRequest,
    ) -> JSONDict:
        return await self._run_managed(
            lambda: self._fetch_dataset(
                endpoint="/v1/foreign-investor-flow",
                payload=request.to_payload(),
                dataset="foreign_investor_flow",
                correlation_id=request.context.correlation_id,
                required_fields=(
                    "dataset",
                    "as_of_date",
                    "market",
                    "records",
                ),
                required_record_fields=(
                    "stock_id",
                    "trade_date",
                    "foreign_buy",
                ),
            )
        )

    async def fetch_historical_financial_statements(
        self,
        request: HistoricalFinancialStatementsRequest,
    ) -> JSONDict:
        return await self._run_managed(
            lambda: self._fetch_dataset(
                endpoint="/v1/historical-financial-statements",
                payload=request.to_payload(),
                dataset="historical_financial_statements",
                correlation_id=request.context.correlation_id,
                required_fields=("dataset", "period", "unit", "records"),
                required_record_fields=(
                    "stock_id",
                    "revenue",
                    "operating_expense",
                    "operating_profit",
                ),
            )
        )

    async def fetch_many(
        self,
        jobs: Sequence[MCPFetchJob],
        *,
        return_exceptions: bool = False,
    ) -> dict[str, JSONDict | Exception]:
        async def _operation() -> dict[str, JSONDict | Exception]:
            if not jobs:
                return {}

            results = await asyncio.gather(
                *(self._dispatch_job(job) for job in jobs),
                return_exceptions=return_exceptions,
            )
            return {
                job.name: result
                for job, result in zip(jobs, results)
            }

        return await self._run_managed(_operation)

    def fetch_stock_basic_snapshot_sync(
        self,
        trade_date: DateValue,
        *,
        market: MarketCode | str = "ALL",
        include_etfs: bool = True,
        correlation_id: str | None = None,
    ) -> JSONDict:
        context = MCPRequestContext(
            market=market,
            include_etfs=include_etfs,
            correlation_id=(
                correlation_id
                or _generate_correlation_id("snapshot")
            ),
        )
        request = StockBasicSnapshotRequest(
            trade_date=trade_date,
            context=context,
        )
        return self._run_sync(
            lambda: self.fetch_stock_basic_snapshot(request),
            correlation_id=context.correlation_id,
        )

    def fetch_foreign_investor_flow_sync(
        self,
        trade_date: DateValue,
        *,
        market: MarketCode | str = "ALL",
        include_etfs: bool = True,
        correlation_id: str | None = None,
    ) -> JSONDict:
        context = MCPRequestContext(
            market=market,
            include_etfs=include_etfs,
            correlation_id=(
                correlation_id
                or _generate_correlation_id("flow")
            ),
        )
        request = ForeignInvestorFlowRequest(
            trade_date=trade_date,
            context=context,
        )
        return self._run_sync(
            lambda: self.fetch_foreign_investor_flow(request),
            correlation_id=context.correlation_id,
        )

    def fetch_historical_financial_statements_sync(
        self,
        year: int,
        quarter: int,
        *,
        market: MarketCode | str = "ALL",
        correlation_id: str | None = None,
    ) -> JSONDict:
        context = MCPRequestContext(
            market=market,
            correlation_id=correlation_id
            or _generate_correlation_id("financials"),
        )
        request = HistoricalFinancialStatementsRequest(
            year=year,
            quarter=quarter,
            context=context,
        )
        return self._run_sync(
            lambda: self.fetch_historical_financial_statements(request),
            correlation_id=context.correlation_id,
        )

    def fetch_many_sync(
        self,
        jobs: Sequence[MCPFetchJob],
        *,
        return_exceptions: bool = False,
        correlation_id: str | None = None,
    ) -> dict[str, JSONDict | Exception]:
        return self._run_sync(
            lambda: self.fetch_many(
                jobs,
                return_exceptions=return_exceptions,
            ),
            correlation_id=correlation_id or _generate_correlation_id("many"),
        )

    @staticmethod
    def stock_basic_snapshot_to_frame(
        payload: Mapping[str, Any],
    ) -> pd.DataFrame:
        frame = pd.DataFrame.from_records(payload.get("records", []))
        if frame.empty:
            return pd.DataFrame(
                columns=[
                    "stock_id",
                    "trade_date",
                    "open_price",
                    "high_price",
                    "low_price",
                    "close_price",
                    "volume",
                    "pe_ratio",
                    "stock_name",
                    "security_type",
                ]
            )

        if "trade_date" not in frame.columns:
            frame["trade_date"] = str(payload.get("as_of_date", ""))

        for column in (
            "open_price",
            "high_price",
            "low_price",
            "close_price",
            "volume",
            "pe_ratio",
        ):
            if column in frame.columns:
                frame[column] = pd.to_numeric(frame[column], errors="coerce")

        frame["stock_id"] = frame["stock_id"].astype(str).str.strip()
        return frame

    @staticmethod
    def foreign_investor_flow_to_frame(
        payload: Mapping[str, Any],
    ) -> pd.DataFrame:
        frame = pd.DataFrame.from_records(payload.get("records", []))
        if frame.empty:
            return pd.DataFrame(
                columns=[
                    "stock_id",
                    "trade_date",
                    "foreign_buy",
                    "trust_buy",
                    "dealer_buy",
                ]
            )

        if "trade_date" not in frame.columns:
            frame["trade_date"] = str(payload.get("as_of_date", ""))

        for column in ("foreign_buy", "trust_buy", "dealer_buy"):
            if column not in frame.columns:
                frame[column] = 0
            frame[column] = pd.to_numeric(frame[column], errors="coerce")

        frame["stock_id"] = frame["stock_id"].astype(str).str.strip()
        return frame

    @staticmethod
    def historical_financial_statements_to_frame(
        payload: Mapping[str, Any],
    ) -> pd.DataFrame:
        frame = pd.DataFrame.from_records(payload.get("records", []))
        if frame.empty:
            return pd.DataFrame(
                columns=[
                    "stock_id",
                    "revenue",
                    "rd_expense",
                    "operating_expense",
                    "operating_profit",
                    "eps",
                    "year",
                    "quarter",
                    "unit",
                ]
            )

        period = payload.get("period", {})
        if not isinstance(period, Mapping):
            period = {}

        for column in (
            "revenue",
            "rd_expense",
            "operating_expense",
            "operating_profit",
            "eps",
        ):
            if column not in frame.columns:
                frame[column] = 0
            frame[column] = pd.to_numeric(frame[column], errors="coerce")

        frame["stock_id"] = frame["stock_id"].astype(str).str.strip()
        frame["year"] = int(period.get("year", 0))
        frame["quarter"] = int(period.get("quarter", 0))
        frame["unit"] = str(payload.get("unit", ""))
        return frame

    async def _dispatch_job(self, job: MCPFetchJob) -> JSONDict:
        if job.dataset == "stock_basic_snapshot":
            if not isinstance(job.request, StockBasicSnapshotRequest):
                raise ValueError(
                    "stock_basic_snapshot jobs require "
                    "StockBasicSnapshotRequest"
                )
            return await self.fetch_stock_basic_snapshot(job.request)

        if job.dataset == "foreign_investor_flow":
            if not isinstance(job.request, ForeignInvestorFlowRequest):
                raise ValueError(
                    "foreign_investor_flow jobs require "
                    "ForeignInvestorFlowRequest"
                )
            return await self.fetch_foreign_investor_flow(job.request)

        if not isinstance(job.request, HistoricalFinancialStatementsRequest):
            raise ValueError(
                "historical_financial_statements jobs require "
                "HistoricalFinancialStatementsRequest"
            )
        return await self.fetch_historical_financial_statements(job.request)

    async def _fetch_dataset(
        self,
        *,
        endpoint: str,
        payload: Mapping[str, Any],
        dataset: DatasetName,
        correlation_id: str,
        required_fields: Sequence[str],
        required_record_fields: Sequence[str],
    ) -> JSONDict:
        response_payload = await self._post_json(
            endpoint=endpoint,
            payload=payload,
            correlation_id=correlation_id,
        )
        return self._validate_payload(
            payload=response_payload,
            endpoint=endpoint,
            correlation_id=correlation_id,
            dataset=dataset,
            required_fields=required_fields,
            required_record_fields=required_record_fields,
        )

    async def _post_json(
        self,
        *,
        endpoint: str,
        payload: Mapping[str, Any],
        correlation_id: str,
    ) -> JSONDict:
        client = await self._ensure_client()
        last_error: MCPClientError | None = None

        for attempt in range(1, self.max_retries + 1):
            self._log(
                logging.INFO,
                "MCP request started",
                correlation_id=correlation_id,
                endpoint=endpoint,
                attempt=attempt,
            )

            try:
                response = await client.post(endpoint, json=dict(payload))
            except httpx.RequestError as exc:
                last_error = MCPTransportError(
                    f"MCP request transport failure: {exc}",
                    endpoint=endpoint,
                    correlation_id=correlation_id,
                    retryable=True,
                    details={"attempt": attempt},
                )
                if attempt >= self.max_retries:
                    raise last_error from exc
                await self._sleep_backoff(
                    endpoint=endpoint,
                    correlation_id=correlation_id,
                    attempt=attempt,
                    reason="transport_error",
                )
                continue

            if response.is_error:
                last_error = self._build_service_error(
                    response=response,
                    endpoint=endpoint,
                    correlation_id=correlation_id,
                )
                if attempt >= self.max_retries or not last_error.retryable:
                    raise last_error
                await self._sleep_backoff(
                    endpoint=endpoint,
                    correlation_id=correlation_id,
                    attempt=attempt,
                    reason="service_error",
                )
                continue

            try:
                response_payload = response.json()
            except ValueError as exc:
                raise MCPResponseError(
                    "MCP response is not valid JSON",
                    endpoint=endpoint,
                    correlation_id=correlation_id,
                    details={"body": response.text[:500]},
                ) from exc

            if not isinstance(response_payload, dict):
                raise MCPResponseError(
                    "MCP response must be a JSON object",
                    endpoint=endpoint,
                    correlation_id=correlation_id,
                    details={"response_type": type(response_payload).__name__},
                )

            self._log(
                logging.INFO,
                "MCP request succeeded",
                correlation_id=correlation_id,
                endpoint=endpoint,
                attempt=attempt,
                extra={"status_code": response.status_code},
            )
            return response_payload

        if last_error is None:
            raise MCPTransportError(
                "MCP request failed without a captured error",
                endpoint=endpoint,
                correlation_id=correlation_id,
                retryable=False,
            )
        raise last_error

    async def _ensure_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                base_url=self.base_url,
                timeout=self.timeout,
                follow_redirects=False,
                headers={
                    "Accept": "application/json",
                    "Content-Type": "application/json",
                    "User-Agent": "stock-linbot-mcp-client/0.1",
                },
            )
        return self._client

    async def _run_managed(
        self,
        operation: Callable[[], Awaitable[_T]],
    ) -> _T:
        owns_client = self._client is None
        if owns_client:
            await self._ensure_client()

        try:
            return await operation()
        finally:
            if owns_client:
                await self.aclose()

    def _run_sync(
        self,
        operation: Callable[[], Awaitable[_T]],
        *,
        correlation_id: str,
    ) -> _T:
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return asyncio.run(operation())

        raise MCPConfigurationError(
            "Sync facade cannot run inside an active event loop",
            endpoint="sync-facade",
            correlation_id=correlation_id,
        )

    def _build_service_error(
        self,
        *,
        response: httpx.Response,
        endpoint: str,
        correlation_id: str,
    ) -> MCPServiceError:
        payload: Mapping[str, Any] = {}
        try:
            raw_payload = response.json()
            if isinstance(raw_payload, dict):
                payload = raw_payload
        except ValueError:
            payload = {}

        retryable = bool(payload.get("retryable"))
        if "retryable" not in payload:
            retryable = response.status_code in _RETRYABLE_STATUS_CODES

        message = str(
            payload.get("message")
            or f"MCP service returned HTTP {response.status_code}"
        )
        error_code = str(
            payload.get("error_code")
            or f"HTTP_{response.status_code}"
        )
        details = payload.get("details")
        if not isinstance(details, Mapping):
            details = {"body": response.text[:500]}

        self._log(
            logging.WARNING,
            "MCP request failed",
            correlation_id=correlation_id,
            endpoint=endpoint,
            attempt=1,
            extra={
                "status_code": response.status_code,
                "error_code": error_code,
                "retryable": retryable,
            },
        )
        return MCPServiceError(
            message,
            endpoint=endpoint,
            correlation_id=correlation_id,
            retryable=retryable,
            status_code=response.status_code,
            details=details,
        )

    async def _sleep_backoff(
        self,
        *,
        endpoint: str,
        correlation_id: str,
        attempt: int,
        reason: str,
    ) -> None:
        delay = min(
            self.backoff_base_seconds * (2 ** (attempt - 1)),
            self.max_backoff_seconds,
        )
        self._log(
            logging.WARNING,
            "MCP request retry scheduled",
            correlation_id=correlation_id,
            endpoint=endpoint,
            attempt=attempt,
            extra={
                "backoff_seconds": delay,
                "reason": reason,
            },
        )
        await asyncio.sleep(delay)

    def _validate_payload(
        self,
        *,
        payload: JSONDict,
        endpoint: str,
        correlation_id: str,
        dataset: DatasetName,
        required_fields: Sequence[str],
        required_record_fields: Sequence[str],
    ) -> JSONDict:
        missing_fields = [
            field
            for field in required_fields
            if field not in payload
        ]
        if missing_fields:
            raise MCPResponseError(
                "MCP response is missing required fields",
                endpoint=endpoint,
                correlation_id=correlation_id,
                details={"missing_fields": missing_fields},
            )

        if payload.get("dataset") != dataset:
            raise MCPResponseError(
                "MCP response dataset does not match request",
                endpoint=endpoint,
                correlation_id=correlation_id,
                details={
                    "expected_dataset": dataset,
                    "actual_dataset": payload.get("dataset"),
                },
            )

        if dataset == "historical_financial_statements":
            period = payload.get("period")
            if not isinstance(period, Mapping):
                raise MCPResponseError(
                    "historical_financial_statements requires "
                    "period metadata",
                    endpoint=endpoint,
                    correlation_id=correlation_id,
                )
            period_missing = [
                field
                for field in ("year", "quarter")
                if field not in period
            ]
            if period_missing:
                raise MCPResponseError(
                    "Financial period metadata is incomplete",
                    endpoint=endpoint,
                    correlation_id=correlation_id,
                    details={"missing_fields": period_missing},
                )

        records = payload.get("records")
        if not isinstance(records, list):
            raise MCPResponseError(
                "MCP response records must be a list",
                endpoint=endpoint,
                correlation_id=correlation_id,
                details={"records_type": type(records).__name__},
            )

        for index, record in enumerate(records):
            if not isinstance(record, Mapping):
                raise MCPResponseError(
                    "MCP response record must be an object",
                    endpoint=endpoint,
                    correlation_id=correlation_id,
                    details={
                        "record_index": index,
                        "record_type": type(record).__name__,
                    },
                )
            missing_record_fields = [
                field
                for field in required_record_fields
                if field not in record
            ]
            if missing_record_fields:
                raise MCPResponseError(
                    "MCP response record is missing "
                    "required fields",
                    endpoint=endpoint,
                    correlation_id=correlation_id,
                    details={
                        "record_index": index,
                        "missing_fields": missing_record_fields,
                    },
                )

        return payload

    def _log(
        self,
        level: int,
        message: str,
        *,
        correlation_id: str,
        endpoint: str,
        attempt: int,
        extra: Mapping[str, Any] | None = None,
    ) -> None:
        message_parts = [
            message,
            f"correlation_id={correlation_id}",
            f"endpoint={endpoint}",
            f"attempt={attempt}",
        ]
        if extra:
            message_parts.extend(
                f"{key}={value}"
                for key, value in extra.items()
            )
        self._logger.log(level, " | ".join(message_parts))


class TWSEMCPClient(MCPClient):
    """Canonical MCP client aligned with the `/v1/tools/*` feature contract.

    This subclass keeps the existing strict `MCPClient` APIs intact while adding
    caller-safe sync/async methods that:
    - call the canonical `/v1/tools/*` endpoints directly;
    - normalize field names to the canonical payloads already consumed by DB and
      LINE/agent call sites;
    - return `None` on HTTP/service failures so interactive callers can degrade
      gracefully instead of crashing.
    """

    async def get_company_basic_info(
        self,
        stock_id: str,
        *,
        trade_date: DateValue | None = None,
        market: MarketCode | str = "ALL",
        correlation_id: str | None = None,
    ) -> JSONDict | None:
        normalized_stock_id = str(stock_id).strip()
        if not normalized_stock_id:
            return None

        normalized_trade_date = _normalize_trade_date(
            trade_date or date.today().isoformat()
        )
        context = MCPRequestContext(
            market=market,
            correlation_id=correlation_id or _generate_correlation_id("company"),
        )
        try:
            return await self._request_tool_payload(
                endpoint=_TOOL_ROUTE_ENDPOINTS["company_basic_info"],
                payload={
                    "stock_id": normalized_stock_id,
                    "trade_date": normalized_trade_date,
                    "market": context.market,
                    "correlation_id": context.correlation_id,
                    "include_etfs": True,
                },
                correlation_id=context.correlation_id,
                dataset="company_basic_info",
                required_fields=("dataset", "as_of_date", "market", "records"),
                required_record_fields=("stock_id",),
                normalize=lambda raw_payload: _normalize_company_basic_info_payload(
                    raw_payload,
                    stock_id=normalized_stock_id,
                    trade_date=normalized_trade_date,
                    market=context.market,
                ),
            )
        except (MCPClientError, httpx.HTTPError) as exc:
            self._log_soft_failure(
                action="get_company_basic_info",
                correlation_id=context.correlation_id,
                exc=exc,
            )
            return None

    async def get_market_statistics(
        self,
        trade_date: DateValue,
        *,
        market: MarketCode | str = "ALL",
        include_etfs: bool = True,
        correlation_id: str | None = None,
    ) -> JSONDict | None:
        normalized_trade_date = _normalize_trade_date(trade_date)
        context = MCPRequestContext(
            market=market,
            include_etfs=include_etfs,
            correlation_id=correlation_id or _generate_correlation_id("market"),
        )
        try:
            return await self._request_tool_payload(
                endpoint=_TOOL_ROUTE_ENDPOINTS["market_statistics"],
                payload={
                    "trade_date": normalized_trade_date,
                    "market": context.market,
                    "correlation_id": context.correlation_id,
                    "include_etfs": context.include_etfs,
                },
                correlation_id=context.correlation_id,
                dataset="stock_basic_snapshot",
                required_fields=("dataset", "as_of_date", "market", "records"),
                required_record_fields=(
                    "stock_id",
                    "trade_date",
                    "open_price",
                    "high_price",
                    "low_price",
                    "close_price",
                    "volume",
                ),
                normalize=lambda raw_payload: _normalize_market_statistics_payload(
                    raw_payload,
                    trade_date=normalized_trade_date,
                    market=context.market,
                ),
            )
        except (MCPClientError, httpx.HTTPError) as exc:
            self._log_soft_failure(
                action="get_market_statistics",
                correlation_id=context.correlation_id,
                exc=exc,
            )
            return None

    async def get_foreign_investment(
        self,
        trade_date: DateValue,
        *,
        market: MarketCode | str = "ALL",
        include_etfs: bool = True,
        correlation_id: str | None = None,
    ) -> JSONDict | None:
        normalized_trade_date = _normalize_trade_date(trade_date)
        context = MCPRequestContext(
            market=market,
            include_etfs=include_etfs,
            correlation_id=correlation_id or _generate_correlation_id("foreign"),
        )
        try:
            return await self._request_tool_payload(
                endpoint=_TOOL_ROUTE_ENDPOINTS["foreign_investment"],
                payload={
                    "trade_date": normalized_trade_date,
                    "market": context.market,
                    "correlation_id": context.correlation_id,
                    "include_etfs": context.include_etfs,
                },
                correlation_id=context.correlation_id,
                dataset="foreign_investor_flow",
                required_fields=("dataset", "as_of_date", "market", "records"),
                required_record_fields=(
                    "stock_id",
                    "trade_date",
                    "foreign_buy",
                ),
                normalize=lambda raw_payload: _normalize_foreign_investment_payload(
                    raw_payload,
                    trade_date=normalized_trade_date,
                    market=context.market,
                ),
            )
        except (MCPClientError, httpx.HTTPError) as exc:
            self._log_soft_failure(
                action="get_foreign_investment",
                correlation_id=context.correlation_id,
                exc=exc,
            )
            return None

    async def get_historical_financial_statements(
        self,
        year: int,
        quarter: int,
        *,
        market: MarketCode | str = "ALL",
        correlation_id: str | None = None,
    ) -> JSONDict | None:
        context = MCPRequestContext(
            market=market,
            correlation_id=correlation_id or _generate_correlation_id("fin-safe"),
        )
        try:
            strict_payload = await self.fetch_historical_financial_statements(
                HistoricalFinancialStatementsRequest(
                    year=year,
                    quarter=quarter,
                    context=context,
                )
            )
            return _normalize_financial_statements_payload(
                strict_payload,
                year=year,
                quarter=quarter,
                market=context.market,
            )
        except (MCPClientError, httpx.HTTPError) as exc:
            self._log_soft_failure(
                action="get_historical_financial_statements",
                correlation_id=context.correlation_id,
                exc=exc,
            )
            return None

    def get_company_basic_info_sync(
        self,
        stock_id: str,
        *,
        trade_date: DateValue | None = None,
        market: MarketCode | str = "ALL",
        correlation_id: str | None = None,
    ) -> JSONDict | None:
        resolved_correlation_id = correlation_id or _generate_correlation_id("company")
        return self._run_sync(
            lambda: self.get_company_basic_info(
                stock_id,
                trade_date=trade_date,
                market=market,
                correlation_id=resolved_correlation_id,
            ),
            correlation_id=resolved_correlation_id,
        )

    def get_market_statistics_sync(
        self,
        trade_date: DateValue,
        *,
        market: MarketCode | str = "ALL",
        include_etfs: bool = True,
        correlation_id: str | None = None,
    ) -> JSONDict | None:
        resolved_correlation_id = correlation_id or _generate_correlation_id("market")
        return self._run_sync(
            lambda: self.get_market_statistics(
                trade_date,
                market=market,
                include_etfs=include_etfs,
                correlation_id=resolved_correlation_id,
            ),
            correlation_id=resolved_correlation_id,
        )

    def get_foreign_investment_sync(
        self,
        trade_date: DateValue,
        *,
        market: MarketCode | str = "ALL",
        include_etfs: bool = True,
        correlation_id: str | None = None,
    ) -> JSONDict | None:
        resolved_correlation_id = correlation_id or _generate_correlation_id("foreign")
        return self._run_sync(
            lambda: self.get_foreign_investment(
                trade_date,
                market=market,
                include_etfs=include_etfs,
                correlation_id=resolved_correlation_id,
            ),
            correlation_id=resolved_correlation_id,
        )

    def get_historical_financial_statements_sync(
        self,
        year: int,
        quarter: int,
        *,
        market: MarketCode | str = "ALL",
        correlation_id: str | None = None,
    ) -> JSONDict | None:
        resolved_correlation_id = correlation_id or _generate_correlation_id("fin-safe")
        return self._run_sync(
            lambda: self.get_historical_financial_statements(
                year,
                quarter,
                market=market,
                correlation_id=resolved_correlation_id,
            ),
            correlation_id=resolved_correlation_id,
        )

    @staticmethod
    def company_basic_info_to_record(payload: Mapping[str, Any]) -> JSONDict:
        record = payload.get("record")
        if isinstance(record, Mapping):
            return dict(record)
        records = payload.get("records")
        if isinstance(records, list) and records:
            first_record = records[0]
            if isinstance(first_record, Mapping):
                return dict(first_record)
        return {}

    async def _request_tool_payload(
        self,
        *,
        endpoint: str,
        payload: Mapping[str, Any],
        correlation_id: str,
        dataset: DatasetName,
        required_fields: Sequence[str],
        required_record_fields: Sequence[str],
        normalize: Callable[[Mapping[str, Any]], JSONDict],
    ) -> JSONDict:
        raw_payload = await self._post_json(
            endpoint=endpoint,
            payload=payload,
            correlation_id=correlation_id,
        )
        normalized_payload = normalize(raw_payload)
        return self._validate_payload(
            payload=normalized_payload,
            endpoint=endpoint,
            correlation_id=correlation_id,
            dataset=dataset,
            required_fields=required_fields,
            required_record_fields=required_record_fields,
        )

    def _log_soft_failure(
        self,
        *,
        action: str,
        correlation_id: str,
        exc: Exception,
    ) -> None:
        self._logger.warning(
            "TWSEMCPClient soft failure | action=%s | correlation_id=%s | error=%s",
            action,
            correlation_id,
            exc,
        )


__all__ = [
    "ForeignInvestorFlowRequest",
    "HistoricalFinancialStatementsRequest",
    "MCPClient",
    "MCPClientError",
    "MCPConfigurationError",
    "MCPFetchJob",
    "MCPRequestContext",
    "MCPResponseError",
    "MCPServiceError",
    "MCPTransportError",
    "StockBasicSnapshotRequest",
    "TWSEMCPClient",
]
