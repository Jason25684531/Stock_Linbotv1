import pandas as pd
from pandas.testing import assert_frame_equal

from core.research.reconcile import reconcile, select_symbols


def _canonical():
    return pd.DataFrame(
        {
            "stock_id": ["2330", "2317", "2303"],
            "trade_date": ["2023-01-03"] * 3,
            "amount": [300.0, 200.0, 100.0],
            "raw_close": [100.0, 100.0, 100.0],
            "quality_status": ["unverified"] * 3,
        }
    )


def test_reconcile_reports_large_differences_without_mutating_canonical():
    canonical = _canonical()
    before = canonical.copy(deep=True)
    vendor = pd.DataFrame({"stock_id": ["2330"], "trade_date": ["2023-01-03"], "close": [102.0]})

    result = reconcile(canonical, vendor, reconciliation_seed=7, top_n=1, random_n=0)

    assert_frame_equal(canonical, before)
    assert result.diagnostics == ["W004_reconciliation_difference"]
    assert result.summary["stock_id"].tolist() == ["2330"]
    assert canonical.loc[canonical.stock_id != "2330", "quality_status"].eq("unverified").all()


def test_select_symbols_uses_mean_amount_not_market_cap_and_a_stable_seed():
    canonical = _canonical()

    assert select_symbols(canonical, reconciliation_seed=3, top_n=1, random_n=1) == select_symbols(
        canonical, reconciliation_seed=3, top_n=1, random_n=1
    )
    assert select_symbols(canonical, reconciliation_seed=3, top_n=1, random_n=0) == {"2330"}
