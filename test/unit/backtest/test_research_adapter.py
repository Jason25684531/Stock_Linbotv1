import pandas as pd
import pytest

from core.backtest.costs import CostModel
from core.backtest.research_adapter import replay_config


COST = CostModel(0.001425, 0.003, 20)


def _targets(rows):
    return pd.DataFrame(rows, columns=["asof_date", "execution_date", "asset_id", "config_id", "target_weight"])


def _prices(index, columns_values):
    return pd.DataFrame(columns_values, index=pd.to_datetime(index))


def test_replay_consumes_frozen_weights_without_reranking():
    targets = _targets([
        ("2023-01-02", "2023-01-03", "A", "cfg", 0.6),
        ("2023-01-02", "2023-01-03", "B", "cfg", 0.4),
    ])
    prices = _prices(["2023-01-03", "2023-01-04"], {"A": [100.0, 100.0], "B": [50.0, 50.0]})

    result = replay_config(targets, prices, COST, config_id="cfg", source_run_id="d5_run", initial_capital=1_000_000.0)

    held = {row["asset_id"]: row for row in result.positions.to_dict("records") if row["execution_date"] == pd.Timestamp("2023-01-03")}
    assert set(held) == {"A", "B"}
    # frozen weights are consumed as-is: no third asset introduced, no reranking
    assert result.transactions["asset_id"].isin(["A", "B"]).all()
    assert result.transactions["execution_date"].eq(pd.Timestamp("2023-01-03")).all()


def test_execution_date_must_be_after_asof_date():
    targets = _targets([("2023-01-03", "2023-01-03", "A", "cfg", 1.0)])
    prices = _prices(["2023-01-03"], {"A": [100.0]})
    with pytest.raises(ValueError, match="execution_date"):
        replay_config(targets, prices, COST, config_id="cfg", source_run_id="d5_run")


def test_cost_model_applies_minimum_fee_and_sell_tax():
    targets = _targets([
        ("2023-01-02", "2023-01-03", "A", "cfg", 1.0),
        ("2023-01-03", "2023-01-04", "A", "cfg", 0.0),
    ])
    prices = _prices(["2023-01-03", "2023-01-04"], {"A": [100.0, 100.0]})

    result = replay_config(targets, prices, COST, config_id="cfg", source_run_id="d5_run", initial_capital=1_000_000.0)

    sell = result.transactions.loc[result.transactions["side"].eq("sell")].iloc[0]
    expected_fee = COST.sell_cost(sell["notional"])
    assert sell["fee"] == expected_fee
    buy = result.transactions.loc[result.transactions["side"].eq("buy")].iloc[0]
    assert buy["fee"] == COST.buy_cost(buy["notional"])


def test_unfilled_execution_is_logged_not_shifted():
    targets = _targets([("2023-01-02", "2023-01-03", "A", "cfg", 1.0)])
    # No price at all for "A" on 2023-01-03: the adapter must not search for a later date.
    prices = _prices(["2023-01-04"], {"A": [100.0]})

    result = replay_config(targets, prices, COST, config_id="cfg", source_run_id="d5_run")

    assert (result.execution_log["status"] == "UNFILLED").all()
    assert (result.execution_log["execution_date"] == pd.Timestamp("2023-01-03")).all()
    assert result.transactions.empty


def test_missing_execution_day_price_is_unfilled():
    targets = _targets([("2023-01-02", "2023-01-03", "A", "cfg", 1.0)])
    prices = _prices(["2023-01-02", "2023-01-03"], {"A": [100.0, float("nan")]})
    result = replay_config(targets, prices, COST, config_id="cfg", source_run_id="d5_run")
    assert result.transactions.empty
    assert result.execution_log.iloc[0]["reason"] == "MISSING_EXECUTION_PRICE"


def test_future_price_never_used_for_execution():
    targets = _targets([("2023-01-02", "2023-01-03", "A", "cfg", 1.0)])
    prices = _prices(["2023-01-02", "2023-01-03", "2023-01-04"], {"A": [100.0, float("nan"), 110.0]})
    result = replay_config(targets, prices, COST, config_id="cfg", source_run_id="d5_run")
    assert result.transactions.empty
    assert result.execution_log.iloc[0]["execution_date"] == pd.Timestamp("2023-01-03")


def test_buy_sizing_respects_minimum_fee():
    targets = _targets([("2023-01-02", "2023-01-03", "A", "cfg", 1.0)])
    result = replay_config(targets, _prices(["2023-01-03"], {"A": [100.0]}), COST, config_id="cfg", source_run_id="d5_run", initial_capital=101.0)
    assert result.transactions.empty


def test_buy_sizing_never_produces_negative_cash():
    targets = _targets([("2023-01-02", "2023-01-03", "A", "cfg", 1.0)])
    result = replay_config(targets, _prices(["2023-01-03"], {"A": [100.0]}), COST, config_id="cfg", source_run_id="d5_run", initial_capital=119.0)
    assert result.transactions.empty


def test_zero_affordable_shares_does_not_create_invalid_trade():
    targets = _targets([("2023-01-02", "2023-01-03", "A", "cfg", 1.0)])
    result = replay_config(targets, _prices(["2023-01-03"], {"A": [100.0]}), COST, config_id="cfg", source_run_id="d5_run", initial_capital=20.0)
    assert result.transactions.empty


def test_replay_is_deterministic():
    targets = _targets([
        ("2023-01-02", "2023-01-03", "A", "cfg", 0.5),
        ("2023-01-02", "2023-01-03", "B", "cfg", 0.5),
        ("2023-01-16", "2023-01-17", "A", "cfg", 1.0),
    ])
    prices = _prices(
        ["2023-01-03", "2023-01-10", "2023-01-17"],
        {"A": [100.0, 105.0, 110.0], "B": [50.0, 52.0, 53.0]},
    )

    first = replay_config(targets, prices, COST, config_id="cfg", source_run_id="d5_run", initial_capital=1_000_000.0)
    second = replay_config(targets, prices, COST, config_id="cfg", source_run_id="d5_run", initial_capital=1_000_000.0)

    pd.testing.assert_series_equal(first.portfolio_value, second.portfolio_value)
    pd.testing.assert_frame_equal(first.transactions, second.transactions)
