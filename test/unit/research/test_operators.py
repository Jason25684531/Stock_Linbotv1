import math

import pandas as pd

from core.research.factors import pct_change, ts_corr, ts_std, wilder_atr


def test_operators_keep_incomplete_windows_nan_and_constant_full_windows_zero():
    values = pd.DataFrame({"2330": [1.0, 1.0, 1.0]})

    result = ts_std(values, 2)

    assert math.isnan(result.iloc[0, 0])
    assert result.iloc[1:, 0].tolist() == [0.0, 0.0]
    assert math.isnan(ts_corr(values, values, 2).iloc[1, 0])


def test_pct_change_does_not_bridge_gaps_and_atr_seeds_on_the_fourteenth_bar():
    values = pd.DataFrame({"2330": [10.0, float("nan"), 12.0]})
    high = pd.DataFrame({"2330": range(2, 16)})
    low = pd.DataFrame({"2330": range(1, 15)})
    close = high.copy()

    assert pct_change(values).iloc[:, 0].isna().all()
    assert math.isnan(wilder_atr(high, low, close, 14).iloc[12, 0])
    assert wilder_atr(high, low, close, 14).iloc[13, 0] == 1.0
