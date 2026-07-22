from random import Random

from core.backtest.costs import CostModel
from core.backtest.execution import apply_slippage
from core.backtest.portfolio import Portfolio


def test_costs_execution_and_portfolio_are_independent_components():
    costs = CostModel(.001425, .003, 20)
    assert costs.buy_cost(1_000) == 20
    assert costs.sell_cost(1_000) == 23
    assert apply_slippage(100, "buy", .01, rng=Random(1)) > 100
    portfolio = Portfolio(100)
    portfolio.debit(40)
    portfolio.credit(10)
    portfolio.open_position("2330", {"shares": 1})
    assert portfolio.cash == 70
    assert portfolio.positions["2330"]["shares"] == 1
