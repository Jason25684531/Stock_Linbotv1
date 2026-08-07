import pandas as pd
import pytest

from core.research.universe import build_membership_v2


def _quotes(days=320):
    dates = pd.bdate_range("2025-01-01", periods=days)
    return pd.DataFrame(
        {
            "trade_date": dates,
            "stock_id": "2330",
            "market": "TWSE",
            "raw_close": 20.0,
            "volume": 1_000_000.0,
            "amount": 25_000_000.0,
        }
    )


def test_membership_v2_observes_listing_and_liquidity_boundaries():
    quotes = _quotes()
    listing_date = quotes["trade_date"].iloc[0]

    result = build_membership_v2(quotes, {"2330": listing_date}, trading_calendar=quotes["trade_date"])

    assert not result.iloc[58]["liquidity_sufficient"]
    assert result.iloc[59]["liquidity_sufficient"]
    assert not result.iloc[250]["listing_history_sufficient"]
    assert result.iloc[251]["listing_history_sufficient"]
    assert result.iloc[251]["member"]


def test_membership_v2_keeps_t1_tradability_out_of_the_membership_gate():
    quotes = _quotes()
    quotes.loc[252, "volume"] = 0.0

    result = build_membership_v2(quotes, {"2330": quotes["trade_date"].iloc[0]}, trading_calendar=quotes["trade_date"])

    assert result.iloc[251]["member"]
    assert not result.iloc[251]["is_tradable_t1"]
    assert not result.iloc[252]["member"]


def test_membership_v2_excludes_missing_listing_dates_and_ignores_adjusted_prices():
    quotes = _quotes().assign(adjusted_close=1.0)

    missing = build_membership_v2(quotes, {}, trading_calendar=quotes["trade_date"])
    with_listing = build_membership_v2(quotes, {"2330": quotes["trade_date"].iloc[0]}, trading_calendar=quotes["trade_date"])

    assert not missing["member"].any()
    assert missing["exclusion_reason"].eq("listing_date_unavailable").all()
    assert with_listing["member"].equals(
        build_membership_v2(quotes.assign(adjusted_close=9999.0), {"2330": quotes["trade_date"].iloc[0]}, trading_calendar=quotes["trade_date"])["member"]
    )


def test_membership_v2_uses_the_injected_calendar_and_reasons_every_non_member():
    quotes = _quotes(days=252).drop(index=100).reset_index(drop=True)
    calendar = pd.bdate_range(quotes.trade_date.min(), periods=252)
    result = build_membership_v2(quotes, {"2330": quotes.trade_date.iloc[0]}, trading_calendar=calendar)
    invalid = build_membership_v2(quotes.assign(stock_id="A"), {"A": quotes.trade_date.iloc[0]}, trading_calendar=calendar)

    assert result.iloc[-1]["listing_history_sufficient"]
    assert invalid["exclusion_reason"].eq("invalid_code").all()


def test_membership_v2_requires_a_verified_trading_calendar():
    quotes = _quotes()

    with pytest.raises(ValueError, match="trading_calendar"):
        build_membership_v2(quotes, {"2330": quotes.trade_date.iloc[0]})
