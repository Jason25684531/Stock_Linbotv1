"""Transaction costs shared by single and portfolio backtests."""
from dataclasses import dataclass


@dataclass(frozen=True)
class CostModel:
    fee_rate: float
    tax_rate: float
    minimum_fee: float = 20.0

    def buy_cost(self, notional: float) -> float:
        return max(int(notional * self.fee_rate), self.minimum_fee)

    def sell_cost(self, notional: float) -> float:
        return max(int(notional * self.fee_rate), self.minimum_fee) + int(notional * self.tax_rate)
