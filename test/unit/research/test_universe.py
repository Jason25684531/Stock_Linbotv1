from pathlib import Path

import pandas as pd

from core.research.universe import build_mask


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
