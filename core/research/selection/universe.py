"""Daily TWSE ordinary-share universe selection."""

import re
from dataclasses import asdict, dataclass
from typing import Iterable, Mapping

import pandas as pd


@dataclass(frozen=True)
class UniverseRuleV2:
    rule_id: str = "twse_research_v2"
    market: str = "TWSE"
    code_regex: str = r"[1-9]\d{3}"
    minimum_listing_trading_days: int = 252
    minimum_raw_close: float = 10.0
    liquidity_window: int = 60
    liquidity_min_periods: int = 60
    minimum_average_amount: float = 20_000_000.0
    allow_liquidity_proxy: bool = False
    require_tradable: bool = True
    required_history_policy: str = "listing_gate_only"


UNIVERSE_RULE_V2 = UniverseRuleV2()


def universe_rule_v2_parameters() -> dict[str, object]:
    return asdict(UNIVERSE_RULE_V2)


def build_mask(quotes: pd.DataFrame) -> tuple[pd.DataFrame, list[str]]:
    """Build a date-by-security membership mask from point-in-time quote facts."""

    work = quotes.sort_values(["stock_id", "trade_date"]).copy()
    missing_amount = work["amount"].isna()
    work["liquidity"] = work["amount"].where(~missing_amount, work["raw_close"] * work["volume"])
    work["mean_liquidity"] = work.groupby("stock_id")["liquidity"].transform(lambda values: values.rolling(20, min_periods=1).mean())
    work["member"] = (
        work["stock_id"].astype(str).str.fullmatch(r"[1-9]\d{3}")
        & work["market"].eq("TWSE")
        & work["raw_close"].ge(10)
        & work["mean_liquidity"].ge(20_000_000)
        & work["volume"].gt(0)
    )
    index = pd.Index(sorted(work["trade_date"].unique()), name="trade_date")
    columns = pd.Index(sorted(work["stock_id"].unique()), name="stock_id")
    mask = work.pivot(index="trade_date", columns="stock_id", values="member").reindex(index=index, columns=columns, fill_value=False)
    return mask.astype(bool), ["W006_liquidity_proxy"] if missing_amount.any() else []


def universe_counts(mask: pd.DataFrame, liquidity_basis: pd.DataFrame) -> pd.DataFrame:
    """Summarize daily membership and the liquidity source used by members."""

    return pd.DataFrame(
        {
            "trade_date": mask.index,
            "count": mask.sum(axis=1).astype(int),
            "liquidity_basis_official": (mask & liquidity_basis.eq("official_amount")).sum(axis=1).astype(int),
            "liquidity_basis_proxy": (mask & liquidity_basis.eq("close_times_volume_proxy")).sum(axis=1).astype(int),
        }
    )


def build_membership_v2(
    quotes: pd.DataFrame, listing_dates: Mapping[str, object], rule: UniverseRuleV2 = UNIVERSE_RULE_V2,
    *, trading_calendar: Iterable[object] | None = None,
) -> pd.DataFrame:
    """Build explainable, point-in-time version-two membership rows."""

    work = quotes.sort_values(["stock_id", "trade_date"]).copy()
    work["trade_date"] = pd.to_datetime(work["trade_date"])
    work["listing_date"] = work["stock_id"].astype(str).map(listing_dates)
    work["listing_date"] = pd.to_datetime(work["listing_date"], errors="coerce")
    if trading_calendar is None:
        raise ValueError("trading_calendar is required for universe v2")
    calendar = pd.DatetimeIndex(sorted(pd.to_datetime(trading_calendar)))
    positions = pd.Series(range(len(calendar)), index=calendar)
    listing_positions = work["listing_date"].map(lambda value: calendar.searchsorted(value, side="left") if pd.notna(value) else pd.NA)
    work["listing_age_trading_days"] = [
        int(positions[day] - position + 1) if pd.notna(position) and position <= positions[day] else 0
        for day, position in zip(work["trade_date"], listing_positions)
    ]
    work["available_history_count"] = work.groupby("stock_id").cumcount() + 1
    work["listing_history_sufficient"] = work["listing_age_trading_days"].ge(rule.minimum_listing_trading_days)
    work["factor_history_sufficient"] = pd.NA
    work["liquidity_mean"] = work.groupby("stock_id")["amount"].transform(
        lambda values: values.rolling(rule.liquidity_window, min_periods=rule.liquidity_min_periods).mean()
    )
    work["liquidity_sufficient"] = work["liquidity_mean"].ge(rule.minimum_average_amount)
    work["is_tradable_t"] = work["volume"].gt(0)
    next_dates = pd.Series(calendar, index=calendar).shift(-1)
    tradable = work.set_index(["trade_date", "stock_id"])["volume"].gt(0)
    work["is_tradable_t1"] = [bool(tradable.get((next_dates.get(day), stock_id), False)) for day, stock_id in zip(work["trade_date"], work["stock_id"])]
    eligible = (
        work["stock_id"].astype(str).str.fullmatch(rule.code_regex)
        & work["market"].eq(rule.market)
        & work["raw_close"].ge(rule.minimum_raw_close)
        & work["listing_history_sufficient"]
        & work["liquidity_sufficient"]
        & work["is_tradable_t"]
    )
    work["member"] = eligible.fillna(False)
    work["exclusion_reason"] = pd.NA
    for condition, reason in (
        (~work["stock_id"].astype(str).str.fullmatch(rule.code_regex), "invalid_code"),
        (~work["market"].eq(rule.market), "wrong_market"),
        (work["listing_date"].isna(), "listing_date_unavailable"),
        (~work["listing_history_sufficient"], "listing_history_insufficient"),
        (~work["liquidity_sufficient"], "liquidity_insufficient"),
        (~work["is_tradable_t"], "t_untradable"),
        (~work["raw_close"].ge(rule.minimum_raw_close), "below_price_threshold"),
    ):
        work.loc[~work["member"] & condition & work["exclusion_reason"].isna(), "exclusion_reason"] = reason
    work.loc[work["member"], "exclusion_reason"] = pd.NA
    columns = [
        "trade_date", "stock_id", "listing_date", "listing_age_trading_days", "available_history_count",
        "listing_history_sufficient", "factor_history_sufficient", "liquidity_sufficient", "is_tradable_t",
        "is_tradable_t1", "member", "exclusion_reason",
    ]
    return work.assign(universe_rule_id=rule.rule_id).loc[:, ["universe_rule_id", *columns]].reset_index(drop=True)
