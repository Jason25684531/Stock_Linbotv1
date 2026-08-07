"""Source-value normalization for research data."""

from dataclasses import dataclass
from datetime import date, datetime
import logging
from math import nan
import re
from typing import Mapping

import pandas as pd


LOGGER = logging.getLogger(__name__)


CORPORATE_ACTION_COLUMNS = (
    "ex_date", "stock_id", "action_type", "pre_ex_close", "ex_reference_price",
    "event_factor", "source", "retrieved_at",
)


class DuplicateKeyError(ValueError):
    code = "F002_duplicate_key"


@dataclass(frozen=True)
class AdjustmentResult:
    quotes: pd.DataFrame
    warnings: list[str]


def parse_number(value: object) -> float:
    """Convert TWSE numeric text without turning missing values into zero."""

    if value is None or str(value).strip() in {"", "--"}:
        return nan
    return float(str(value).replace(",", ""))


def quote_lineage(source: str, fallback_reason: str = "") -> dict[str, object]:
    """Describe one source for every OHLC value in a quote row."""

    is_fallback = source == "yfinance"
    if is_fallback != bool(fallback_reason):
        raise ValueError("fallback rows require yfinance and a reason")
    return {
        "raw_price_source": source,
        "is_fallback": is_fallback,
        "fallback_reason": fallback_reason,
        "quality_status": "degraded" if is_fallback else "unverified",
    }


def normalize_twse_closing_table(table: Mapping[str, object], trade_date: object, retrieved_at: object) -> pd.DataFrame:
    """Map the declared MI_INDEX closing-table columns into canonical rows."""

    rows = []
    for row in table.get("data", []):
        if len(row) >= 9:
            stock_id, _, volume, transactions, amount, open_, high, low, close = row[:9]
        else:
            stock_id, volume, transactions, amount, open_, high, low, close = row[:8]
        if not re.fullmatch(r"[1-9]\d{3}", str(stock_id)):
            continue
        rows.append({
            "trade_date": pd.Timestamp(trade_date), "stock_id": str(stock_id), "market": "TWSE", "currency": "TWD",
            "raw_open": parse_number(open_), "raw_high": parse_number(high), "raw_low": parse_number(low), "raw_close": parse_number(close),
            "volume": parse_number(volume), "amount": parse_number(amount), "transaction_count": parse_number(transactions),
            "liquidity_basis": "official_amount", "market_closed_at": None, "retrieved_at": retrieved_at, "ingested_at": retrieved_at,
            "source_revision": None, "quality_flags": "", **quote_lineage("twse_rwd"),
        })
    return pd.DataFrame(rows)


def sort_canonical_quotes(quotes: pd.DataFrame) -> pd.DataFrame:
    """Stably sort canonical rows by (stock_id, trade_date).

    This is the sole place in the pipeline that reorders canonical quotes;
    validation.py only detects unsorted input and never reorders it.
    """

    return quotes.sort_values(["stock_id", "trade_date"], kind="stable").reset_index(drop=True)


def normalize_company_profile_listing_dates(payload: object) -> dict[str, pd.Timestamp]:
    """Return unambiguous four-digit stock identifiers and their listed dates."""

    dates_by_stock: dict[str, set[pd.Timestamp | None]] = {}
    for row in payload if isinstance(payload, list) else []:
        if not isinstance(row, Mapping):
            continue
        stock_id = str(row.get("公司代號", "")).strip()
        if not re.fullmatch(r"[1-9]\d{3}", stock_id):
            continue
        parsed = pd.to_datetime(row.get("上市日期"), format="%Y%m%d", errors="coerce")
        dates_by_stock.setdefault(stock_id, set()).add(None if pd.isna(parsed) else pd.Timestamp(parsed))
    listing_dates = {}
    for stock_id, dates in dates_by_stock.items():
        if len(dates) == 1 and None not in dates:
            listing_dates[stock_id] = next(iter(dates))
        else:
            LOGGER.warning("listing date unavailable or conflicting for %s", stock_id)
    return listing_dates


def apply_adjustments(
    quotes: pd.DataFrame, actions: pd.DataFrame | None, adjustment_as_of: datetime
) -> AdjustmentResult:
    """Apply official event factors without overwriting raw prices."""

    adjusted = quotes.copy()
    adjusted["adjustment_as_of"] = adjustment_as_of
    if actions is None:
        adjusted["adjustment_factor"] = nan
        adjusted["adjustment_source"] = "unavailable"
        for price in ("open", "high", "low", "close"):
            adjusted[f"adjusted_{price}"] = nan
        return AdjustmentResult(adjusted, ["W007_adjustment_unavailable"])

    valid_actions = actions.loc[actions["pre_ex_close"] > 0].copy()
    valid_actions["ex_date"] = pd.to_datetime(valid_actions["ex_date"])
    warnings = ["W007_adjustment_unavailable"] if len(valid_actions) != len(actions) else []
    adjusted["adjustment_factor"] = [
        valid_actions.loc[
            (valid_actions["stock_id"] == row.stock_id)
            & (valid_actions["ex_date"] > pd.Timestamp(row.trade_date)),
            "event_factor",
        ].prod()
        for row in adjusted.itertuples()
    ]
    adjusted["adjustment_source"] = "local_twse_twt49u"
    for price in ("open", "high", "low", "close"):
        adjusted[f"adjusted_{price}"] = adjusted[f"raw_{price}"] * adjusted["adjustment_factor"]
    return AdjustmentResult(adjusted, warnings)


def normalize_corporate_actions(payload: Mapping[str, object], retrieved_at: datetime) -> pd.DataFrame:
    """Convert the official combined rights-and-dividend report to its own contract."""

    fields = payload["fields"]
    rows = [dict(zip(fields, row)) for row in payload["data"]]
    actions = pd.DataFrame(
        [
            {
                "ex_date": _roc_date(row["資料日期"]),
                "stock_id": str(row["股票代號"]),
                "action_type": str(row["權/息"]),
                "pre_ex_close": parse_number(row["除權息前收盤價"]),
                "ex_reference_price": parse_number(row["除權息參考價"]),
                "source": "twse_twt49u",
                "retrieved_at": retrieved_at,
            }
            for row in rows
        ],
        columns=[column for column in CORPORATE_ACTION_COLUMNS if column != "event_factor"],
    )
    actions.insert(5, "event_factor", actions["ex_reference_price"] / actions["pre_ex_close"])
    if actions.duplicated(["ex_date", "stock_id"]).any():
        raise DuplicateKeyError("F002_duplicate_key: duplicate (ex_date, stock_id)")
    return actions


def _roc_date(value: object) -> date:
    year, month_day = str(value).split("年", 1)
    month, day = month_day.removesuffix("日").split("月", 1)
    return date(int(year) + 1911, int(month), int(day))
