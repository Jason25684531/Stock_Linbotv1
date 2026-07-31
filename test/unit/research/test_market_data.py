from pathlib import Path

import pandas as pd
import pytest

from core.research.market_data import CANONICAL_QUOTE_COLUMNS, SchemaError, validate_canonical_quotes


def _quotes():
    return pd.DataFrame({column: [None] for column in CANONICAL_QUOTE_COLUMNS})


def test_canonical_quote_schema_rejects_missing_required_columns_and_tolerates_extras():
    quotes = _quotes().assign(extra_column="allowed")

    validate_canonical_quotes(quotes)
    with pytest.raises(SchemaError, match="F001"):
        validate_canonical_quotes(quotes.drop(columns="raw_close"))


def test_canonical_quote_schema_carries_provenance_and_time_fields_only():
    source = Path(validate_canonical_quotes.__code__.co_filename).read_text(encoding="utf-8")

    assert {"market_closed_at", "retrieved_at", "adjustment_as_of", "liquidity_basis", "quality_status"} <= set(CANONICAL_QUOTE_COLUMNS)
    assert "cash_dividend" not in source
    assert "stock_split_ratio" not in source
    assert "available_at" not in source
