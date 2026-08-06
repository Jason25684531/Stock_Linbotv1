import pandas as pd

from core.research.validation import validate_research_dataset


def test_research_leakage_validator_makes_date_violations_fatal():
    rows = pd.DataFrame(
        {
            "factor_asof_date": ["2025-01-02", "2025-01-02"],
            "source_max_trade_date": ["2025-01-03", "2025-01-02"],
            "execution_date": ["2025-01-03", "2025-01-02"],
        }
    )

    diagnostics = validate_research_dataset(rows)

    assert {(item.code, item.severity) for item in diagnostics} == {
        ("F101_source_after_asof", "FATAL"),
        ("F102_execution_not_after_asof", "FATAL"),
    }


def test_research_leakage_validator_accepts_null_instants_and_compliant_dates():
    rows = pd.DataFrame(
        {
            "factor_asof_date": ["2025-01-02"], "source_max_trade_date": ["2025-01-02"], "execution_date": ["2025-01-03"],
            "factor_asof_time": [pd.NaT], "source_max_available_at": [pd.NaT], "execution_time": [pd.NaT],
        }
    )

    assert validate_research_dataset(rows) == []
