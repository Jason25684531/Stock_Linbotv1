import numpy as np
import pandas as pd

from core.research.pipeline import RunConfig, run
from core.research.research_dataset import build_research_dataset


def test_research_dataset_is_unique_explainable_and_has_no_infinities():
    dates = pd.bdate_range("2025-01-01", periods=4)
    factors = pd.DataFrame(
        {"asof_date": [dates[0], dates[0]], "asset_id": ["A", "B"], "factor_id": ["f", "f"], "raw_value": [1.0, np.nan], "factor_version": ["1", "1"]}
    )
    membership = pd.DataFrame(
        {"trade_date": dates.tolist() * 2, "stock_id": ["A"] * 4 + ["B"] * 4, "member": [True] * 4 + [False] * 4,
         "listing_date": [dates[0]] * 8, "listing_age_trading_days": list(range(1, 5)) * 2,
         "available_history_count": list(range(1, 5)) * 2, "listing_history_sufficient": [True] * 8,
         "factor_history_sufficient": [pd.NA] * 8, "is_tradable_t": [True] * 8, "is_tradable_t1": [True, True, True, False] * 2,
         "universe_rule_id": ["twse_research_v2"] * 8, "exclusion_reason": [pd.NA] * 4 + ["not_in_universe"] * 4}
    )
    quotes = pd.DataFrame(
        {"trade_date": dates.tolist() * 2, "stock_id": ["A"] * 4 + ["B"] * 4, "adjusted_open": [10.0, 11.0, 12.0, 13.0] * 2}
    )

    dataset, diagnostics = build_research_dataset(factors, membership, quotes, {"f": 1}, run_id="run")

    assert not dataset.duplicated(["asof_date", "asset_id", "factor_id"]).any()
    assert not np.isinf(dataset.select_dtypes("number").to_numpy()).any()
    assert dataset.loc[dataset.asset_id.eq("B"), "factor_missing_reason"].eq("not_in_universe").all()
    assert dataset[["factor_asof_time", "source_max_available_at", "execution_time", "entry_price_time"]].isna().all().all()
    assert diagnostics == []


def test_pipeline_dataset_stage_is_opt_in_and_offline(tmp_path):
    dates = pd.bdate_range("2024-01-01", periods=320)
    quotes = pd.concat(
        [
            pd.DataFrame({"trade_date": dates, "stock_id": stock_id, "market": "TWSE", "currency": "TWD", "raw_open": 20.0, "raw_high": 21.0, "raw_low": 19.0, "raw_close": 20.0, "volume": volume, "amount": 25_000_000.0, "transaction_count": 1, "liquidity_basis": "official_amount"})
            for stock_id, volume in (("A", 1_000_000.0), ("B", 1_000_000.0), ("C", 0.0))
        ],
        ignore_index=True,
    )
    config = RunConfig(
        run_id="d3", generated_at="2026-08-06T00:00:00Z", adjustment_as_of="2026-08-06",
        requested_start=dates[0], requested_end=dates[-1], output_dir=tmp_path, quotes=quotes,
        actions=pd.DataFrame(columns=["ex_date", "stock_id", "pre_ex_close", "event_factor"]),
        listing_dates={"A": dates[0], "B": dates[100], "C": dates[0]}, no_fetch=True,
    )

    result = run(config)

    assert result.status == "success"
    assert (tmp_path / "universe_membership.csv").exists()
    assert (tmp_path / "research_dataset").exists()
    assert not pd.read_csv(tmp_path / "universe_membership.csv").query("stock_id == 'B'")["member"].any()
