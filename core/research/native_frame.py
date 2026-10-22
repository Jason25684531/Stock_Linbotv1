"""Research data provider: Daily-equivalent cross-sections built from the official snapshot.

Indicators, chip features and the z-score matrix use the same functions and window as
jobs/run_daily.py (150 calendar days, one-day cross-section). Missing inputs stay NaN and the
stock is excluded with a recorded reason; nothing is zero-filled.
"""
from __future__ import annotations

import inspect
import re

import pandas as pd

from core.calc_indicators import (
    build_multi_factor_matrix, calculate_atr, calculate_bb_width, calculate_chip_score, calculate_consec_days,
    calculate_kd, calculate_macd, calculate_margin_change_pct, calculate_natr, calculate_ratio_features,
    calculate_rsi, calculate_std_20)

LOOKBACK_DAYS = 150  # run_daily.compute_indicators_from_history window
# Statutory filing deadlines (month, day, year offset); the latest legally public period, never later data.
QUARTER_DEADLINES = {1: (5, 15, 0), 2: (8, 14, 0), 3: (11, 14, 0), 4: (3, 31, 1)}
FRAME_INPUTS = frozenset({
    "ma5", "ma20", "ma60", "bias", "rsi", "macd_hist", "kd_k", "bb_width", "atr", "natr", "std_20", "volume_ratio",
    "foreign_ratio", "trust_ratio", "dealer_ratio", "foreign_consec_days", "trust_consec_days", "margin_change_pct",
    "chip_score", "foreign_buy", "trust_buy", "dealer_buy", "margin_balance", "revenue_yoy", "op_profit_margin",
    "eps", "rd_ratio"})


def rule_inputs(strategy) -> set[str]:
    """Frame columns named in the strategy module (rules and its declared model features)."""
    source = inspect.getsource(inspect.getmodule(type(strategy)))
    return {t for t in re.findall(r"""['"]([a-z_0-9]+)['"]""", source) if t in FRAME_INPUTS}


def _due(year, month, day) -> pd.Series:
    return pd.to_datetime(pd.DataFrame({"year": year, "month": month, "day": day}))


class NativeResearchData:
    available_inputs = FRAME_INPUTS

    # Sources that are NULL for most of the history; Daily and the z-score builder silently turn them into 0.
    NEVER_POPULATED = ("dealer_buy", "dealer_ratio", "margin_balance", "margin_change_pct", "chip_score")

    def __init__(self, snapshot: pd.DataFrame, financials: pd.DataFrame, revenue: pd.DataFrame, daily_equivalent_fill: bool = False):
        """daily_equivalent_fill=True reproduces Daily's NULL -> 0 for NEVER_POPULATED inputs (diagnostic only, never certified)."""
        self.daily_equivalent_fill = daily_equivalent_fill
        self.snapshot = snapshot.sort_values(["stock_id", "trade_date"]).reset_index(drop=True)
        self._bars = self.snapshot.set_index(["stock_id", "trade_date"])
        self._closes = {sid: g.set_index("trade_date").close_price for sid, g in self.snapshot.groupby("stock_id")}
        fin = financials.assign(stock_id=financials.stock_id.astype(str))
        deadline = fin.quarter.map(QUARTER_DEADLINES)
        fin["due"] = _due(fin.year + deadline.str[2], deadline.str[0], deadline.str[1])
        revenue_ok = fin.revenue.where(fin.revenue > 0)
        fin["op_profit_margin"] = fin.operating_profit / revenue_ok  # Daily: operating_profit / revenue
        fin["rd_ratio"] = fin.rd_expense / revenue_ok
        self._fin = fin.sort_values(["stock_id", "year", "quarter"])[["stock_id", "due", "op_profit_margin", "rd_ratio", "eps"]]
        rev = revenue.assign(stock_id=revenue.stock_id.astype(str))
        rev["due"] = _due(rev.year + (rev.month == 12), rev.month % 12 + 1, 10)  # monthly revenue is due on the 10th
        self._rev = rev.sort_values(["stock_id", "year", "month"])[["stock_id", "due", "revenue_yoy"]]
        self._cache: dict[pd.Timestamp, pd.DataFrame] = {}

    def _published(self, table: pd.DataFrame, date: pd.Timestamp) -> pd.DataFrame:
        return table[table.due < date].drop_duplicates("stock_id", keep="last").drop(columns="due")

    def cross_section(self, date: pd.Timestamp) -> pd.DataFrame:
        if date in self._cache:
            return self._cache[date]
        df = self.snapshot[(self.snapshot.trade_date >= date - pd.Timedelta(days=LOOKBACK_DAYS)) & (self.snapshot.trade_date <= date)].copy()
        if df.empty or df.trade_date.max() != date:
            raise ValueError(f"NO_OFFICIAL_SESSION_DATA: {date:%Y-%m-%d}")
        by_stock = df.groupby("stock_id")
        close = by_stock["close_price"]
        df["ma5"] = close.transform(lambda x: x.rolling(5, min_periods=3).mean())
        df["ma20"] = close.transform(lambda x: x.rolling(20, min_periods=10).mean())
        df["ma60"] = close.transform(lambda x: x.rolling(60, min_periods=30).mean())
        df["bias"] = (df.close_price - df.ma20) / df.ma20.where(df.ma20 != 0) * 100  # Daily fills 0 here; NaN is kept
        df["rsi"] = close.transform(calculate_rsi)
        df["macd_hist"] = close.transform(calculate_macd)
        df["kd_k"] = by_stock.apply(calculate_kd).reset_index(level=0, drop=True)
        df["bb_width"] = close.transform(calculate_bb_width)
        df["atr"] = by_stock.apply(calculate_atr).reset_index(level=0, drop=True)
        df["natr"] = by_stock.apply(calculate_natr).reset_index(level=0, drop=True)
        df["std_20"] = close.transform(calculate_std_20)
        vol_safe = df.volume.replace(0, 1)
        for side in ("dealer", "foreign", "trust"):
            df[f"{side}_ratio"] = (df[f"{side}_buy"] / vol_safe).clip(-0.5, 0.5)
        by_stock = df.groupby("stock_id")
        df["foreign_consec_days"] = by_stock["foreign_buy"].transform(calculate_consec_days)
        df["trust_consec_days"] = by_stock["trust_buy"].transform(calculate_consec_days)
        df["margin_change_pct"] = by_stock["margin_balance"].transform(calculate_margin_change_pct)
        df["chip_score"] = calculate_chip_score(df)
        if self.daily_equivalent_fill:
            df[list(self.NEVER_POPULATED)] = df[list(self.NEVER_POPULATED)].fillna(0)
        df = calculate_ratio_features(df)  # volume_ratio over the history, as in training
        latest = df[df.trade_date == date].merge(self._published(self._fin, date), on="stock_id", how="left")
        latest = latest.merge(self._published(self._rev, date), on="stock_id", how="left")
        self._cache[date] = latest  # shared by every strategy replayed on this provider
        return latest

    def unavailable_inputs(self, start: str, end: str) -> frozenset:
        """Inputs whose source is entirely NULL in the window (strict mode); empty when diagnostic filling is on."""
        if self.daily_equivalent_fill:
            return frozenset()
        window = self.snapshot[(self.snapshot.trade_date >= start) & (self.snapshot.trade_date <= end)]
        empty = {"dealer_buy", "dealer_ratio"} if window.dealer_buy.isna().all() else set()
        empty |= {"margin_balance", "margin_change_pct"} if window.margin_balance.isna().all() else set()
        return frozenset(empty | ({"chip_score"} if empty else set()))

    def frame(self, date_str: str, required) -> tuple[pd.DataFrame, dict]:
        """Daily-equivalent candidate frame; stocks missing any required input are excluded and counted."""
        full = self.cross_section(pd.Timestamp(date_str))
        absent = full[sorted(required)].isna()
        keep = ~absent.any(axis=1)
        note = {"date": date_str, "rows": len(full), "excluded": int((~keep).sum()),
                "missing_input_counts": {c: int(absent[c].sum()) for c in absent if absent[c].any()}}
        kept = full[keep]
        if kept.empty:
            return kept, note
        return build_multi_factor_matrix(kept, trade_date=date_str), note

    def bar(self, stock_id: str, date_str: str):
        try:
            return self._bars.loc[(str(stock_id), pd.Timestamp(date_str))].to_dict()
        except KeyError:
            return None

    def last_close(self, stock_id: str, date_str: str):
        closes = self._closes.get(str(stock_id))
        closes = None if closes is None else closes.loc[:pd.Timestamp(date_str)]
        return None if closes is None or closes.empty else float(closes.iloc[-1])

    def stock_data(self, symbol: str, date_str: str):
        """Stand-in for core.db_helper.get_stock_data (one row with indicators)."""
        full = self.cross_section(pd.Timestamp(date_str))
        return full[full.stock_id == str(symbol)].reset_index(drop=True), date_str
