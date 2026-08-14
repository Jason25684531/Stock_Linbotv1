"""Day 3 Custom Engine replay adapter for frozen D5 target weights.

Consumes a frozen target-weight artifact (asof_date, execution_date, asset_id,
config_id, target_weight) and replays it through the existing execution
components (Portfolio, CostModel, calculate_metrics). It never recomputes
signals, reranks assets, regenerates Top-N, or changes weights; execution
only happens at execution_date (T+1), never at asof_date close.

ponytail: rebalances by fully liquidating then rebuilding target positions
each execution date, rather than incrementally trading toward the target.
This is a declared EXPECTED_MODEL_DIFFERENCE vs VectorBT's daily re-target
(see design.md decision B "execution accounting"), not a bug.
"""
from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from .costs import CostModel
from .metrics import calculate_metrics
from .portfolio import Portfolio
from .result import PerformanceMetrics

REQUIRED_COLUMNS = ("asof_date", "execution_date", "asset_id", "config_id", "target_weight")


@dataclass
class ReplayResult:
    config_id: str
    source_run_id: str
    daily_returns: pd.Series
    portfolio_value: pd.Series
    positions: pd.DataFrame
    transactions: pd.DataFrame
    execution_log: pd.DataFrame
    performance_metrics: PerformanceMetrics


def _validate_targets(target_weights: pd.DataFrame) -> pd.DataFrame:
    missing = [column for column in REQUIRED_COLUMNS if column not in target_weights.columns]
    if missing:
        raise ValueError(f"target weights missing required columns: {missing}")
    targets = target_weights.copy()
    targets["asof_date"] = pd.to_datetime(targets["asof_date"])
    targets["execution_date"] = pd.to_datetime(targets["execution_date"])
    if (targets["execution_date"] <= targets["asof_date"]).any():
        raise ValueError("execution_date must be strictly after asof_date (no same-close execution)")
    if targets.duplicated(["execution_date", "asset_id"]).any():
        raise ValueError("duplicate (execution_date, asset_id) target rows")
    return targets


def replay_config(
    target_weights: pd.DataFrame,
    price_matrix: pd.DataFrame,
    cost_model: CostModel,
    *,
    config_id: str,
    source_run_id: str,
    initial_capital: float = 1_000_000.0,
    slippage: float = 0.0,
) -> ReplayResult:
    """Replay one config's frozen target weights through the existing execution components."""
    if slippage < 0:
        raise ValueError("slippage must be non-negative")
    targets = _validate_targets(target_weights)
    rebalance_dates = sorted(targets["execution_date"].unique())
    calendar = sorted(set(price_matrix.index) | set(rebalance_dates))

    portfolio = Portfolio(initial_capital)
    last_price: dict[object, float] = {}
    transactions: list[dict] = []
    execution_log: list[dict] = []
    positions_rows: list[dict] = []
    values: list[float] = []
    dates: list = []

    targets_by_date = {date: group for date, group in targets.groupby("execution_date")}

    for current_date in calendar:
        if current_date in price_matrix.index:
            for asset_id, price in price_matrix.loc[current_date].items():
                if pd.notna(price):
                    last_price[asset_id] = float(price)

        if current_date in targets_by_date:
            _execute_rebalance(
                portfolio=portfolio,
                targets=targets_by_date[current_date],
                last_price=last_price,
                cost_model=cost_model,
                slippage=slippage,
                current_date=current_date,
                config_id=config_id,
                transactions=transactions,
                execution_log=execution_log,
            )
            positions_rows.extend(
                {
                    "execution_date": current_date,
                    "config_id": config_id,
                    "asset_id": asset_id,
                    "shares": position["shares"],
                    "cost": position["cost"],
                    "market_value": position["shares"] * last_price.get(asset_id, position["cost"]),
                }
                for asset_id, position in portfolio.positions.items()
            )

        values.append(_mark_to_market(portfolio, last_price))
        dates.append(current_date)

    value_series = pd.Series(values, index=pd.DatetimeIndex(dates), name="portfolio_value")
    daily_returns = value_series.pct_change().dropna().rename("daily_return")
    transactions_df = pd.DataFrame(transactions)
    metrics = calculate_metrics(value_series.tolist(), transactions)

    return ReplayResult(
        config_id=config_id,
        source_run_id=source_run_id,
        daily_returns=daily_returns,
        portfolio_value=value_series,
        positions=pd.DataFrame(positions_rows),
        transactions=transactions_df,
        execution_log=pd.DataFrame(execution_log),
        performance_metrics=metrics,
    )


def _mark_to_market(portfolio: Portfolio, last_price: dict) -> float:
    total = portfolio.cash
    for asset_id, position in portfolio.positions.items():
        total += position["shares"] * last_price.get(asset_id, position["cost"])
    return total


def _execute_rebalance(*, portfolio, targets, last_price, cost_model, slippage, current_date, config_id, transactions, execution_log) -> None:
    # Snapshot total value before touching positions; every buy sizes off this,
    # not off cash freed incrementally, so weights reflect the whole portfolio.
    portfolio_value = _mark_to_market(portfolio, last_price)
    target_weight_by_asset = dict(zip(targets["asset_id"], targets["target_weight"]))

    # Sell every currently held position first (full liquidate-and-rebuild); assets
    # missing a price on this execution date stay held and are logged as UNFILLED.
    for asset_id in sorted(portfolio.positions.keys(), key=str):
        price = last_price.get(asset_id)
        if price is None:
            execution_log.append({"execution_date": current_date, "config_id": config_id, "asset_id": asset_id, "side": "sell", "status": "UNFILLED", "reason": "no price on execution date"})
            continue
        _sell(portfolio, asset_id, price, slippage, cost_model, current_date, config_id, transactions)

    for asset_id, weight in target_weight_by_asset.items():
        price = last_price.get(asset_id)
        if price is None:
            execution_log.append({"execution_date": current_date, "config_id": config_id, "asset_id": asset_id, "side": "buy", "status": "UNFILLED", "reason": "no price on execution date"})
            continue
        _buy(portfolio, asset_id, price, weight, portfolio_value, slippage, cost_model, current_date, config_id, transactions)


def _sell(portfolio, asset_id, price, slippage, cost_model, current_date, config_id, transactions) -> None:
    position = portfolio.positions[asset_id]
    sell_price = price * (1 - slippage)
    proceeds = position["shares"] * sell_price
    fee = cost_model.sell_cost(proceeds)
    net = proceeds - fee
    portfolio.credit(net)
    portfolio.close_position(asset_id)
    profit_pct = (sell_price - position["cost"]) / position["cost"] * 100 if position["cost"] else 0.0
    transactions.append({
        "execution_date": current_date, "config_id": config_id, "asset_id": asset_id, "side": "sell",
        "shares": position["shares"], "price": sell_price, "notional": proceeds, "fee": fee,
        "profit_pct": profit_pct, "days": (current_date - position["opened_at"]).days if position.get("opened_at") is not None else 0,
        "status": "FILLED",
    })


def _buy(portfolio, asset_id, price, weight, portfolio_value, slippage, cost_model, current_date, config_id, transactions) -> None:
    buy_price = price * (1 + slippage)
    desired_notional = portfolio_value * weight
    shares = int(desired_notional // buy_price)
    if shares <= 0:
        return
    cost = shares * buy_price
    fee = cost_model.buy_cost(cost)
    if cost + fee > portfolio.cash:
        shares = int(portfolio.cash // (buy_price * (1 + cost_model.fee_rate)))
        if shares <= 0:
            return
        cost = shares * buy_price
        fee = cost_model.buy_cost(cost)
    portfolio.debit(cost + fee)
    portfolio.open_position(asset_id, {"shares": shares, "cost": buy_price, "total_cost": cost + fee, "opened_at": current_date})
    transactions.append({
        "execution_date": current_date, "config_id": config_id, "asset_id": asset_id, "side": "buy",
        "shares": shares, "price": buy_price, "notional": cost, "fee": fee, "profit_pct": 0.0, "days": 0,
        "status": "FILLED",
    })
