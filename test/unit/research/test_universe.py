from pathlib import Path

import pandas as pd

from core.research.universe import build_mask, universe_counts


def test_universe_is_twse_ordinary_shares_only_and_uses_raw_prices():
    dates = pd.date_range("2023-01-01", periods=20)
    quotes = pd.DataFrame(
        {
            "trade_date": dates.tolist() * 3,
            "stock_id": ["2330"] * 20 + ["0050"] * 20 + ["1234"] * 20,
            "market": ["TWSE"] * 40 + ["TPEx"] * 20,
            "raw_close": [20.0] * 60,
            "volume": [2_000_000] * 60,
            "amount": [None] * 60,
            "adjusted_close": [1.0] * 60,
        }
    )

    mask, warnings = build_mask(quotes)

    assert mask.loc[:, "2330"].iloc[-1]
    assert not mask.loc[:, "0050"].any()
    assert not mask.loc[:, "1234"].any()
    assert warnings == ["W006_liquidity_proxy"]
    assert "adjusted_" not in Path(build_mask.__code__.co_filename).read_text(encoding="utf-8")


def test_universe_counts_split_official_and_proxy_liquidity():
    index = pd.to_datetime(["2023-01-02", "2023-01-03"])
    mask = pd.DataFrame([[True, False], [True, True]], index=index, columns=["2330", "2317"])
    basis = pd.DataFrame([["official_amount", "close_times_volume_proxy"], ["official_amount", "official_amount"]], index=index, columns=mask.columns)

    assert universe_counts(mask, basis).to_dict("records") == [
        {"trade_date": pd.Timestamp("2023-01-02"), "count": 1, "liquidity_basis_official": 1, "liquidity_basis_proxy": 0},
        {"trade_date": pd.Timestamp("2023-01-03"), "count": 2, "liquidity_basis_official": 2, "liquidity_basis_proxy": 0},
    ]
