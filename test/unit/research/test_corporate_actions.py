from datetime import date, datetime
from pathlib import Path

import pandas as pd
import pytest

from core.research.normalize import DuplicateKeyError, normalize_corporate_actions


def test_normalize_corporate_actions_builds_the_separate_official_contract():
    actions = normalize_corporate_actions(
        {
            "fields": ["資料日期", "股票代號", "權/息", "除權息前收盤價", "除權息參考價"],
            "data": [["112年01月04日", "2330", "息", "500", "490"]],
        },
        datetime(2026, 7, 31, 9),
    )

    assert list(actions.columns) == [
        "ex_date", "stock_id", "action_type", "pre_ex_close", "ex_reference_price",
        "event_factor", "source", "retrieved_at",
    ]
    assert actions.iloc[0].to_dict() == {
        "ex_date": date(2023, 1, 4),
        "stock_id": "2330",
        "action_type": "息",
        "pre_ex_close": 500.0,
        "ex_reference_price": 490.0,
        "event_factor": 0.98,
        "source": "twse_twt49u",
        "retrieved_at": pd.Timestamp("2026-07-31 09:00:00"),
    }


def test_normalize_corporate_actions_rejects_a_duplicate_natural_key():
    payload = {
        "fields": ["資料日期", "股票代號", "權/息", "除權息前收盤價", "除權息參考價"],
        "data": [["112年01月04日", "2330", "息", "500", "490"]] * 2,
    }

    with pytest.raises(DuplicateKeyError, match="F002"):
        normalize_corporate_actions(payload, datetime(2026, 7, 31, 9))


def test_corporate_action_contract_does_not_claim_unavailable_components():
    source = Path(normalize_corporate_actions.__code__.co_filename).read_text(encoding="utf-8")

    assert "cash_dividend" not in source
    assert "stock_split_ratio" not in source
