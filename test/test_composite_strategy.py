import numpy as np
import pandas as pd
import pytest

from core.research.strategy_signal import ResearchOnlyViolation
from core.strategy import composite as cs
from core.strategy.contracts import StrategyContext, StrategyExecutor
from scripts import research_workflow as workflow

TICKERS = [f'9{i:03d}' for i in range(12)]


def scorer(scores, passes=None):
    frame = pd.DataFrame({'score': scores, 'passes': True if passes is None else passes}, index=TICKERS[:len(scores)])
    return lambda context, signal_date: frame


def register(tmp_path, hid, mode, parts, top_n=10, min_names=5):
    registry = tmp_path / 'registry.csv'
    sha = cs.config_sha256(mode, parts, top_n, min_names)
    workflow.register({'hypothesis_id': hid, 'strategy': 'composite', 'hypothesis': f'fixed composite config_sha256={sha}',
                       'falsification_test': 'synthetic', 'data_window': '2023-2025', 'trial_budget': '1', 'source': 'test'},
                      path=registry, today='2026-11-06')
    return registry


def ctx(day='2025-06-30'):
    return StrategyContext(asof_date=day, data={'data_version': 'synthetic', 'digests': {'s': '1'}})


def test_unregistered_id_refused(tmp_path):
    parts = (('A', scorer([1.0]), 1.0),)
    registry = register(tmp_path, 'H:X', cs.FACTOR_WEIGHTED, parts)
    with pytest.raises(ValueError, match='NOT_REGISTERED'):
        cs.CompositeExecutor(cs.FACTOR_WEIGHTED, parts, 'H:OTHER', registry=registry)
    with pytest.raises(ValueError, match='REGISTRY_UNVERIFIED'):
        cs.CompositeExecutor(cs.FACTOR_WEIGHTED, parts, 'H:X', registry=tmp_path / 'none.csv')


def test_config_sha_mismatch_refused(tmp_path):
    registry = register(tmp_path, 'H:X', cs.FACTOR_WEIGHTED, (('A', .5), ('B', .5)))
    other = (('A', scorer([1.0]), .6), ('B', scorer([1.0]), .4))
    with pytest.raises(ValueError, match='CONFIG_NOT_REGISTERED'):
        cs.CompositeExecutor(cs.FACTOR_WEIGHTED, other, 'H:X', registry=registry)
    same = (('A', scorer([1.0]), .5), ('B', scorer([1.0]), .5))
    with pytest.raises(ValueError, match='CONFIG_NOT_REGISTERED'):
        cs.CompositeExecutor(cs.FACTOR_WEIGHTED, same, 'H:X', top_n=5, registry=registry)
    assert isinstance(cs.CompositeExecutor(cs.FACTOR_WEIGHTED, same, 'H:X', registry=registry), StrategyExecutor)


def test_weights_not_summing_to_one_refused(tmp_path):
    parts = (('A', scorer([1.0]), .5), ('B', scorer([1.0]), .6))
    with pytest.raises(ValueError, match='WEIGHTS_INVALID'):
        cs.CompositeExecutor(cs.FACTOR_WEIGHTED, parts, 'H:X', registry=tmp_path / 'r.csv')
    with pytest.raises(ValueError, match='WEIGHTS_INVALID'):
        cs.CompositeExecutor(cs.CONDITION_INTERSECTION, (('A', scorer([1.0]), None),), 'H:X', registry=tmp_path / 'r.csv')


def test_empty_intersection_is_cash(tmp_path):
    parts = (('A', scorer([1.0] * 6, [True] * 3 + [False] * 3), 0), ('B', scorer([1.0] * 6, [False] * 3 + [True] * 3), 0))
    registry = register(tmp_path, 'H:I', cs.CONDITION_INTERSECTION, parts)
    result = cs.CompositeExecutor(cs.CONDITION_INTERSECTION, parts, 'H:I', registry=registry).execute(ctx())
    assert result.rows == () and result.metadata['status'] == 'NO_ELIGIBLE_STOCKS'
    assert result.metadata['signals'].empty and result.metadata['strategy_type'] == cs.CONDITION_INTERSECTION


def test_three_eligible_is_insufficient_diversification(tmp_path):
    passes = [True] * 3 + [False] * 5
    parts = (('A', scorer(list(range(8)), passes), 0), ('B', scorer(list(range(8, 0, -1)), [True] * 8), 0))
    registry = register(tmp_path, 'H:I', cs.CONDITION_INTERSECTION, parts)
    result = cs.CompositeExecutor(cs.CONDITION_INTERSECTION, parts, 'H:I', registry=registry).execute(ctx())
    assert result.metadata['status'] == 'INSUFFICIENT_DIVERSIFICATION'
    # mean percentile is 4.5/8 for all three, so ticker order decides
    assert [r.stock_id for r in result.rows] == TICKERS[:3] and all(r.target_weight == pytest.approx(1 / 3) for r in result.rows)


def test_weighted_scores_order_and_weights(tmp_path):
    rng = np.random.default_rng(7)
    a, b = rng.normal(size=12), rng.normal(size=12)
    parts = (('A', scorer(a), .7), ('B', scorer(b), .3))
    registry = register(tmp_path, 'H:W', cs.FACTOR_WEIGHTED, parts, top_n=5)
    result = cs.CompositeExecutor(cs.FACTOR_WEIGHTED, parts, 'H:W', top_n=5, registry=registry).execute(ctx())
    expected = .7 * pd.Series(a, index=TICKERS).rank(pct=True) + .3 * pd.Series(b, index=TICKERS).rank(pct=True)
    signals = result.metadata['signals']
    np.testing.assert_allclose(signals.set_index('ticker')['final_score'].reindex(TICKERS), expected, rtol=1e-9, atol=1e-12)
    order = expected.reset_index().set_axis(['ticker', 's'], axis=1).sort_values(['s', 'ticker'], ascending=[False, True])['ticker'].tolist()
    assert signals['ticker'].tolist() == order
    assert [r.stock_id for r in result.rows] == order[:5] and all(r.target_weight == pytest.approx(.2) for r in result.rows)
    assert result.metadata['status'] == 'OK' and signals['strategy_version'].iloc[0] == f'H:W:{cs.FACTOR_WEIGHTED}'


def test_2026_signal_date_blocked(tmp_path):
    parts = (('A', scorer([1.0]), 1.0),)
    registry = register(tmp_path, 'H:X', cs.FACTOR_WEIGHTED, parts)
    with pytest.raises(ResearchOnlyViolation, match='OOS_OR_INPUT_WINDOW_BLOCKED'):
        cs.CompositeExecutor(cs.FACTOR_WEIGHTED, parts, 'H:X', registry=registry).execute(ctx('2026-01-05'))


def test_no_composite_in_production_registries():
    from core.strategy.adapters.legacy import build_legacy_registry
    from core.strategy_manager import StrategyManager
    ids = list(StrategyManager().list_strategies()) + list(build_legacy_registry().canonical_ids())
    assert not [i for i in ids if 'COMPOSITE' in i.upper() or cs.CONDITION_INTERSECTION in i or cs.FACTOR_WEIGHTED in i]
