"""Source-value normalization for research data."""

from dataclasses import dataclass
from datetime import date, datetime
from math import nan
from typing import Mapping

import pandas as pd


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

    valid_actions = actions.loc[actions["pre_ex_close"] > 0]
    warnings = ["W007_adjustment_unavailable"] if len(valid_actions) != len(actions) else []
    adjusted["adjustment_factor"] = [
        valid_actions.loc[
            (valid_actions["stock_id"] == row.stock_id) & (valid_actions["ex_date"] > row.trade_date),
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
