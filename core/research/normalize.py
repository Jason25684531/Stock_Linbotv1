"""Source-value normalization for research data."""

from math import nan


def parse_number(value: object) -> float:
    """Convert TWSE numeric text without turning missing values into zero."""

    if value is None or str(value).strip() in {"", "--"}:
        return nan
    return float(str(value).replace(",", ""))
