"""Resolve vendor symbols from the bundled runtime ticker mapping."""

import csv
from dataclasses import dataclass
from datetime import date
from pathlib import Path


@dataclass(frozen=True)
class TickerMapping:
    stock_id: str
    market: str
    twse_code: str
    yahoo_symbol: str
    valid_from: date | None
    valid_to: date | None
    mapping_status: str


def resource_path() -> Path:
    return Path(__file__).with_name("resources") / "ticker_map.csv"


def resolve(stock_id: str, on_date: date) -> TickerMapping | None:
    for row in _rows():
        if row.stock_id != stock_id:
            continue
        if row.valid_from and on_date < row.valid_from:
            continue
        if row.valid_to and on_date > row.valid_to:
            continue
        return row
    return None


def _rows() -> list[TickerMapping]:
    with resource_path().open(encoding="utf-8", newline="") as handle:
        return [
            TickerMapping(
                stock_id=row["stock_id"],
                market=row["market"],
                twse_code=row["twse_code"],
                yahoo_symbol=row["yahoo_symbol"],
                valid_from=_date_or_none(row["valid_from"]),
                valid_to=_date_or_none(row["valid_to"]),
                mapping_status=row["mapping_status"],
            )
            for row in csv.DictReader(handle)
        ]


def _date_or_none(value: str) -> date | None:
    return date.fromisoformat(value) if value else None
