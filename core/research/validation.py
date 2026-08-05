"""Fixed-severity validation of canonical research data."""

from dataclasses import dataclass

import numpy as np
import pandas as pd


@dataclass(frozen=True)
class Diagnostic:
    stage: str
    code: str
    severity: str
    trade_date: object | None = None
    stock_id: str | None = None
    detail: str = ""


REQUIRED_COLUMNS = frozenset({"trade_date", "stock_id", "raw_open", "raw_high", "raw_low", "raw_close", "volume", "amount"})
REQUIRED_NUMERIC_COLUMNS = ("raw_open", "raw_high", "raw_low", "raw_close", "volume", "amount")


def validate(quotes: pd.DataFrame) -> list[Diagnostic]:
    """Return contract diagnostics with severity fixed in code. Never mutates `quotes`."""

    missing = REQUIRED_COLUMNS - set(quotes.columns)
    if missing:
        detail = ", ".join(f"{name} (aka close)" if name == "raw_close" else name for name in sorted(missing))
        return [_fatal("F001_missing_required_column", detail=detail)]
    diagnostics = []
    if quotes.duplicated(["trade_date", "stock_id"]).any():
        diagnostics.append(_fatal("F002_duplicate_key"))
    checks = (
        ("F004_nonpositive_price", (quotes[["raw_open", "raw_high", "raw_low", "raw_close"]] <= 0).any(axis=1)),
        ("F005_high_lt_low", quotes["raw_high"] < quotes["raw_low"]),
        ("F006_ohlc_out_of_range", (quotes["raw_open"] < quotes["raw_low"]) | (quotes["raw_open"] > quotes["raw_high"]) | (quotes["raw_close"] < quotes["raw_low"]) | (quotes["raw_close"] > quotes["raw_high"])),
        ("F007_negative_volume", quotes["volume"] < 0),
        ("F010_negative_amount", quotes["amount"] < 0),
        ("F012_blank_stock_id", quotes["stock_id"].astype(object).eq("")),
        ("F013_null_stock_id", quotes["stock_id"].isna()),
        ("F014_infinite_value", pd.concat([np.isinf(pd.to_numeric(quotes[column], errors="coerce")) for column in REQUIRED_NUMERIC_COLUMNS], axis=1).any(axis=1)),
    )
    for code, matches in checks:
        if matches.any():
            row = quotes.loc[matches].iloc[0]
            diagnostics.append(_fatal(code, row.trade_date, row.stock_id))
    warnings = (
        ("W002_zero_volume", quotes["volume"].eq(0)),
        ("W006_liquidity_proxy", quotes.get("liquidity_basis", pd.Series(dtype=str)).eq("close_times_volume_proxy")),
        ("W007_adjustment_unavailable", quotes.get("adjustment_source", pd.Series(dtype=str)).eq("unavailable")),
        ("W008_fallback_used", quotes.get("is_fallback", pd.Series(dtype=bool)).eq(True)),
    )
    for code, matches in warnings:
        if matches.any():
            row = quotes.loc[matches].iloc[0]
            diagnostics.append(_warn(code, row.trade_date, row.stock_id))
    if _has_unsorted_dates(quotes):
        diagnostics.append(_warn("W014_unsorted_dates"))
    return diagnostics


def _has_unsorted_dates(quotes: pd.DataFrame) -> bool:
    """Detect only; sorting itself is normalize.py's sole responsibility."""

    trade_date = pd.to_datetime(quotes["trade_date"], errors="coerce")
    return bool((trade_date.groupby(quotes["stock_id"]).apply(lambda group: not group.is_monotonic_increasing)).any())


def _fatal(code: str, trade_date: object | None = None, stock_id: str | None = None, detail: str = "") -> Diagnostic:
    return Diagnostic("contract", code, "FATAL", trade_date, stock_id, detail)


def _warn(code: str, trade_date: object | None = None, stock_id: str | None = None, detail: str = "") -> Diagnostic:
    return Diagnostic("contract", code, "WARN", trade_date, stock_id, detail)
