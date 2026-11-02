import numpy as np
import pandas as pd
from pandas.testing import assert_frame_equal
from core.research.d5_research_variant import D5_POLICY_V1, fit_at, policy_sha


def test_d5_fit_ignores_uncompleted_labels_and_future_observations():
    dates = pd.bdate_range('2023-01-02', periods=260)
    names = D5_POLICY_V1['factor_universe']
    ic = pd.DataFrame({name: .03 + .005 * np.sin(np.arange(len(dates)) + i) for i, name in enumerate(names)}, index=dates)
    ranks = {name: pd.DataFrame({'A': np.arange(len(dates)) + i, 'B': np.arange(len(dates)) * .5 + i,
                                 'C': np.arange(len(dates)) * .2 + i}, index=dates) for i, name in enumerate(names)}
    signal = dates[220]
    first = fit_at(signal, ic, ranks)
    assert first is not None and len(first['candidates']) == 5 and len(policy_sha()) == 64
    altered = ic.copy()
    altered.iloc[161:] = -999
    changed = {name: frame.copy() for name, frame in ranks.items()}
    for frame in changed.values():
        frame.iloc[221:] = -999
    second = fit_at(signal, altered, changed)
    assert_frame_equal(first['candidates'], second['candidates'])
    assert_frame_equal(first['correlation'], second['correlation'])
