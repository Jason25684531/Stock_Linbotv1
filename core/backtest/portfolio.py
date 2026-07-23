"""Minimal portfolio state used by the runner."""
from dataclasses import dataclass, field
from types import MappingProxyType


@dataclass
class Portfolio:
    _cash: float
    _positions: dict[str, dict] = field(default_factory=dict)

    @property
    def cash(self) -> float:
        return self._cash

    @property
    def positions(self):
        return MappingProxyType(self._positions)

    def debit(self, amount: float) -> None:
        if amount > self._cash:
            raise ValueError("insufficient cash")
        self._cash -= amount

    def credit(self, amount: float) -> None:
        self._cash += amount

    def open_position(self, stock_id: str, position: dict) -> None:
        self._positions[stock_id] = position

    def close_position(self, stock_id: str) -> dict:
        return self._positions.pop(stock_id)

    def replace_positions(self, positions: dict[str, dict]) -> None:
        self._positions = positions
