"""Async-first MCP transport boundary for TWSE modernization.

This module is the only covered-data HTTP client used by the bot process.
It talks to ``Config.MCP_BASE_URL`` and exposes sync/async helpers for:
- stock basic snapshot
- foreign investor flow
- historical financial statements

The same client is shared by ``1_update_database.py``, financial updaters,
and the MCP-backed context tools in ``tool/news_agent.py``.
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
    "stock_basic_snapshot",
    "foreign_investor_flow",
    "historical_financial_statements",
]
DateValue: TypeAlias = date | datetime | str
JSONDict: TypeAlias = dict[str, Any]

_T = TypeVar("_T")
_LOGGER = logging.getLogger(__name__)
_RETRYABLE_STATUS_CODES = {408, 425, 429, 500, 502, 503, 504}


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
]
