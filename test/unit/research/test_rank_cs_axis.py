import pandas as pd

from core.research.factor_operators import rank_cs


def test_rank_cs_orders_distinct_securities_on_the_same_date():
    values = pd.DataFrame({"A": [10.0], "B": [20.0], "C": [30.0]})

    ranked = rank_cs(values)

    assert ranked.iloc[0]["A"] < ranked.iloc[0]["B"] < ranked.iloc[0]["C"]


def test_rank_cs_never_compares_a_security_across_dates():
    values = pd.DataFrame({"A": [10.0, 1000.0], "B": [20.0, 1.0]})

    ranked = rank_cs(values)

    # Day 1: A < B. Day 2: B < A (values flipped). Each day ranks independently.
    assert ranked.iloc[0]["A"] < ranked.iloc[0]["B"]
    assert ranked.iloc[1]["B"] < ranked.iloc[1]["A"]
