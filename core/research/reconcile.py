"""Read-only reconciliation of official and vendor quote samples."""

from dataclasses import dataclass
from random import Random

import pandas as pd


@dataclass(frozen=True)
class ReconciliationResult:
    summary: pd.DataFrame
    diagnostics: list[str]


def select_symbols(
    canonical: pd.DataFrame,
    reconciliation_seed: int,
    *,
    top_n: int = 20,
    random_n: int = 30,
    event_symbols: set[str] | None = None,
) -> set[str]:
    """Select deterministic liquidity, random, and event strata."""

    amounts = canonical.groupby("stock_id")["amount"].mean().sort_values(ascending=False)
    selected = set(amounts.head(top_n).index)
    remaining = sorted(set(amounts.index) - selected)
    selected.update(Random(reconciliation_seed).sample(remaining, min(random_n, len(remaining))))
    selected.update(event_symbols or set())
    return selected


def reconcile(
    canonical: pd.DataFrame,
    vendor: pd.DataFrame,
    reconciliation_seed: int,
    *,
    top_n: int = 20,
    random_n: int = 30,
    threshold: float = 0.01,
    event_symbols: set[str] | None = None,
) -> ReconciliationResult:
    """Compare a sampled vendor close without altering canonical data."""

    symbols = select_symbols(
        canonical, reconciliation_seed, top_n=top_n, random_n=random_n, event_symbols=event_symbols
    )
    summary = canonical.loc[canonical["stock_id"].isin(symbols), ["stock_id", "trade_date", "raw_close"]].merge(
        vendor[["stock_id", "trade_date", "close"]], on=["stock_id", "trade_date"], how="inner"
    )
    summary["relative_difference"] = (summary["raw_close"] - summary["close"]).abs() / summary["raw_close"]
    diagnostics = ["W004_reconciliation_difference"] if summary["relative_difference"].gt(threshold).any() else []
    return ReconciliationResult(summary, diagnostics)
