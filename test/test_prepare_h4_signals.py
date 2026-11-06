import pandas as pd
import pytest

from scripts.prepare_h4_signals import scores


def test_growth_momentum_score_follows_the_registered_rule():
    day = pd.Timestamp('2025-03-03')
    eligible = pd.DataFrame({'asof_date': [day] * 4, 'asset_id': ['A', 'B', 'C', 'D']})
    growth = {'operating_income_yoy': pd.DataFrame({'A': [.1], 'B': [.3], 'C': [None], 'D': [.2]}, index=[day]),
              'eps_yoy': pd.DataFrame({'A': [.4], 'B': [.1], 'C': [None], 'D': [None]}, index=[day])}
    momentum = pd.DataFrame({'A': [-.2], 'B': [-.1], 'C': [-.05], 'D': [None]}, index=[day])
    out = scores(eligible, growth, momentum, ['operating_income_yoy', 'eps_yoy']).set_index('asset_id')['composite_score']
    assert set(out.index) == {'A', 'B'}  # C has no growth, D has no momentum
    g2 = pd.Series({'A': .1, 'B': .3, 'D': .2}).rank(pct=True)
    g3 = pd.Series({'A': .4, 'B': .1}).rank(pct=True)
    mom = pd.Series({'A': -.2, 'B': -.1, 'C': -.05}).rank(pct=True)
    assert out['A'] == pytest.approx(.5 * (g2['A'] + g3['A']) / 2 + .5 * mom['A'])
    assert out['B'] == pytest.approx(.5 * (g2['B'] + g3['B']) / 2 + .5 * mom['B'])
