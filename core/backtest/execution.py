"""Deterministic-friendly execution pricing."""
import random


def apply_slippage(price: float, side: str, max_slippage: float, rng=None) -> float:
    if side not in {"buy", "sell"} or price < 0 or max_slippage < 0:
        raise ValueError("invalid execution input")
    slip = (rng or random).uniform(0, max_slippage)
    return price * (1 + slip if side == "buy" else 1 - slip)
