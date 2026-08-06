import ast
import math
from pathlib import Path

import numpy as np
import pandas as pd

from core.research.factor_operators import (
    delay,
    delta,
    rolling_corr,
    rolling_max,
    rolling_mean,
    rolling_min,
    rolling_std,
    winsorize_cs,
)
from core.research.factors import ts_corr, ts_max, ts_mean, ts_min, ts_std


FRAME = pd.DataFrame({"2330": [1.0, 2.0, 3.0, 4.0, 5.0], "2317": [5.0, 4.0, 3.0, 2.0, 1.0]})


def test_rolling_wrappers_match_the_underlying_ts_operators_numerically():
    pd.testing.assert_frame_equal(rolling_mean(FRAME, 2), ts_mean(FRAME, 2))
    pd.testing.assert_frame_equal(rolling_std(FRAME, 2), ts_std(FRAME, 2))
    pd.testing.assert_frame_equal(rolling_max(FRAME, 2), ts_max(FRAME, 2))
    pd.testing.assert_frame_equal(rolling_min(FRAME, 2), ts_min(FRAME, 2))
    pd.testing.assert_frame_equal(rolling_corr(FRAME, FRAME, 2), ts_corr(FRAME, FRAME, 2))


def test_rolling_wrappers_null_an_incomplete_window():
    assert rolling_mean(FRAME, 10).isna().all().all()


def test_delay_shifts_back_by_the_given_number_of_periods():
    assert delay(FRAME, 1)["2330"].tolist()[1:] == FRAME["2330"].tolist()[:-1]
    assert math.isnan(delay(FRAME, 1)["2330"].iloc[0])


def test_delay_never_uses_future_data():
    baseline = delay(FRAME, 1)
    perturbed = FRAME.copy()
    perturbed.iloc[-1] = 999.0

    result = delay(perturbed, 1)

    pd.testing.assert_series_equal(result.iloc[:-1]["2330"], baseline.iloc[:-1]["2330"])


def test_delta_returns_the_observed_difference_for_each_asset():
    actual = delta(FRAME, 2)
    expected = FRAME - FRAME.shift(2)

    pd.testing.assert_frame_equal(actual, expected)
    assert actual.iloc[:2].isna().all().all()
    np.testing.assert_allclose(
        actual.iloc[2:].to_numpy(),
        np.array([[2.0, -2.0], [2.0, -2.0], [2.0, -2.0]]),
        rtol=0,
        atol=0,
    )


def test_delta_nulls_its_first_periods_and_propagates_nulls_per_asset():
    values = FRAME.copy()
    values.loc[2, "2330"] = float("nan")

    actual = delta(values, 1)
    expected = pd.DataFrame(
        {"2330": [float("nan"), 1.0, float("nan"), float("nan"), 1.0],
         "2317": [float("nan"), -1.0, -1.0, -1.0, -1.0]},
        index=values.index,
    )

    pd.testing.assert_frame_equal(actual, expected)
    assert actual.iloc[:1].isna().all().all()
    assert actual.loc[3, "2317"] == -1.0
    assert actual.index.equals(values.index)
    assert actual.columns.equals(values.columns)


def test_winsorize_cs_needs_at_least_two_cross_sectional_observations():
    one_stock = pd.DataFrame({"2330": [10.0, 20.0]})

    assert winsorize_cs(one_stock, 0.1, 0.9).isna().all().all()


def test_winsorize_cs_leaves_an_all_nan_cross_section_untouched():
    all_nan = pd.DataFrame({"2330": [float("nan")], "2317": [float("nan")], "2454": [float("nan")]})

    assert winsorize_cs(all_nan, 0.1, 0.9).isna().all().all()


def test_winsorize_cs_leaves_a_constant_cross_section_unchanged():
    constant = pd.DataFrame({"2330": [5.0], "2317": [5.0], "2454": [5.0]})

    result = winsorize_cs(constant, 0.1, 0.9)

    assert result.iloc[0].tolist() == [5.0, 5.0, 5.0]


def test_winsorize_cs_treats_infinite_values_as_null_before_computing_bounds():
    row = pd.DataFrame({"2330": [float("inf")], "2317": [10.0], "2454": [20.0]})

    result = winsorize_cs(row, 0.1, 0.9)

    assert not result.isin([float("inf"), float("-inf")]).any().any()


def test_factor_operators_does_not_import_other_research_transform_modules():
    source_file = Path(delay.__code__.co_filename)
    tree = ast.parse(source_file.read_text(encoding="utf-8"))
    imports = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            imports.add(node.module)
        elif isinstance(node, ast.Import):
            imports.update(alias.name for alias in node.names)

    forbidden = {"core.research.sources", "core.research.normalize", "core.research.reconcile", "core.research.universe"}
    assert not any(imported == module or imported.startswith(f"{module}.") for imported in imports for module in forbidden)
