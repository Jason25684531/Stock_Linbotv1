import math

import pandas as pd

from core.research.factor_operators import rank_ts


def test_rank_ts_ranks_a_security_within_its_own_trailing_window():
    values = pd.DataFrame({"2330": [10.0, 20.0, 30.0]})

    ranked = rank_ts(values, window=3)

    assert ranked["2330"].iloc[2] == 3.0


def test_rank_ts_never_compares_across_securities():
    values = pd.DataFrame({"2330": [10.0, 20.0, 30.0], "2317": [1000.0, 2000.0, 3000.0]})

    ranked = rank_ts(values, window=3)

    # Despite 2317's values dwarfing 2330's, each column ranks only against its own history.
    assert ranked["2330"].iloc[2] == ranked["2317"].iloc[2] == 3.0


def test_rank_ts_is_null_for_an_incomplete_window():
    values = pd.DataFrame({"2330": [10.0, 20.0]})

    assert math.isnan(rank_ts(values, window=3)["2330"].iloc[-1])
