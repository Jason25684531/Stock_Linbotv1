import pandas as pd
from pandas.testing import assert_frame_equal
import pytest

from core.backtest.costs import CostModel
from core.research.repair_accounting import simulate
from core.research.repair_targets import build_targets
from core.research.selection.target_weights import build_target_weights as legacy_targets


def inputs():
    signal = '2025-09-05T15:00:00+08:00'
    scores = pd.DataFrame([{'asof_date': '2025-09-05', 'asset_id': asset, 'composite_score': score, 'signal_at': signal,
                            'score_available_at': '2025-09-05T14:00:00+08:00', 'score_source_sha256': 'a' * 64}
                           for asset, score in [('AAA', 100), ('BBB', 50)]])
    universe = pd.DataFrame([{'asof_date': '2025-09-05', 'asset_id': asset, 'member': True, 'execution_date': '2025-09-08',
                              'is_tradable_t1': tradable, 'membership_available_at': '2025-09-05T14:00:00+08:00',
                              'membership_source_sha256': 'b' * 64, 'calendar_available_at': '2025-01-01T12:00:00+08:00',
                              'calendar_source_sha256': 'c' * 64} for asset, tradable in [('AAA', False), ('BBB', True)]])
    return scores, universe


def build(scores, universe):
    return build_targets(scores, universe, config_id='fixture', top_n=1, stock_weighting='equal')


def test_next_session_tradability_is_not_a_selection_input():
    scores, universe = inputs()
    old = legacy_targets(scores, universe, config_id='fixture', top_n=1, stock_weighting='equal')
    assert old.asset_id.tolist() == ['BBB']  # proves the legacy look-ahead path
    selected = build(scores, universe)
    assert selected.asset_id.tolist() == ['AAA']
    changed = universe.copy()
    changed['is_tradable_t1'] = ~changed['is_tradable_t1']
    assert_frame_equal(selected, build(scores, changed))
    assert_frame_equal(selected, build(scores, universe.drop(columns='is_tradable_t1')))
    assert selected.point_in_time_status.tolist() == ['BLOCKED_UNVERIFIED_SOURCES']

    # Selection is unchanged; actual next-session no-trade is handled by the fill engine.
    bar = {'open': 10, 'high': 10, 'low': 10, 'close': 10, 'volume': 0, 'amount': 0,
           'prior_amount': 100_000, 'limit_up': 11, 'limit_down': 9}
    order = {'id': 'x', 'date': '2025-09-08', 'stock_id': 'AAA', 'side': 'buy', 'shares': 10,
             'signal_at': scores.signal_at.iloc[0], 'latest_input_available_at': scores.score_available_at.iloc[0]}
    result = simulate(bars={'2025-09-08': {'AAA': bar}}, orders=[order], actions=[], action_coverage={'AAA': ['2025-09-08']},
                      initial_cash=10000, cost=CostModel(.001425, .003, 20), slippage=.001)
    assert result['ledger'][0]['status'] == 'UNFILLED' and result['ledger'][0]['reason'] == 'REJECTED_NO_TRADE'


@pytest.mark.parametrize('column,value', [
    ('score_available_at', '2025-09-05T16:00:00+08:00'),
    ('membership_available_at', '2025-09-05T16:00:00+08:00'),
    ('calendar_available_at', '2025-09-05T16:00:00+08:00'),
    ('score_source_sha256', 'unknown'),
    ('execution_date', '2026-01-02'),
])
def test_late_or_missing_evidence_and_oos_are_rejected(column, value):
    scores, universe = inputs()
    (scores if column in scores else universe).loc[0, column] = value
    with pytest.raises(ValueError, match='PIT_LOOKAHEAD_OR_EVIDENCE_INVALID'):
        build(scores, universe)


def test_missing_member_score_and_naive_time_fail_closed():
    scores, universe = inputs()
    with pytest.raises(ValueError, match='PIT_MEMBER_SCORE_MISSING'):
        build(scores.iloc[:1], universe)
    scores.loc[0, 'signal_at'] = '2025-09-05T15:00:00'
    with pytest.raises(ValueError, match='INVALID_SIGNAL_OR_AVAILABILITY_TIMESTAMP'):
        build(scores, universe)


def test_late_nonmember_evidence_is_not_used_to_exclude_a_stock():
    scores, universe = inputs()
    universe.loc[1, 'member'] = False
    universe.loc[1, 'membership_available_at'] = '2025-09-08T15:00:00+08:00'
    with pytest.raises(ValueError, match='PIT_LOOKAHEAD_OR_EVIDENCE_INVALID'):
        build(scores, universe)
    universe['member'] = universe['member'].astype('boolean')
    universe.loc[1, 'member'] = pd.NA
    with pytest.raises(ValueError, match='PIT_MEMBERSHIP_UNKNOWN'):
        build(scores, universe)


def test_execution_date_cannot_be_chosen_from_stock_specific_future_data():
    scores, universe = inputs()
    universe.loc[1, 'execution_date'] = '2025-09-09'
    with pytest.raises(ValueError, match='PIT_EXECUTION_CALENDAR_MISMATCH'):
        build(scores, universe)


def test_each_signal_date_requires_explicit_eligible_universe():
    scores, universe = inputs()
    second_scores, second_universe = inputs()
    second_scores['asof_date'] = '2025-09-08'
    second_scores['signal_at'] = '2025-09-08T15:00:00+08:00'
    second_scores['score_available_at'] = '2025-09-08T14:00:00+08:00'
    second_universe['asof_date'] = '2025-09-08'
    second_universe['execution_date'] = '2025-09-09'
    second_universe['membership_available_at'] = '2025-09-08T14:00:00+08:00'
    second_universe['member'] = False
    with pytest.raises(ValueError, match='PIT_NO_ELIGIBLE_UNIVERSE'):
        build(pd.concat([scores, second_scores], ignore_index=True), pd.concat([universe, second_universe], ignore_index=True))


@pytest.mark.parametrize('invalid', [None, '', '  ', 2330])
def test_unknown_or_non_string_asset_and_config_id_are_rejected(invalid):
    scores, universe = inputs()
    scores.loc[0, 'asset_id'] = invalid
    universe.loc[0, 'asset_id'] = invalid
    with pytest.raises(ValueError, match='PIT_ASSET_ID_INVALID'):
        build(scores, universe)
    scores, universe = inputs()
    with pytest.raises(ValueError, match='INVALID_TARGET_CONFIGURATION'):
        build_targets(scores, universe, config_id=invalid, top_n=1, stock_weighting='equal')


def test_large_finite_scores_produce_finite_normalized_weights():
    scores, universe = inputs()
    scores['composite_score'] = 1e308
    result = build_targets(scores, universe, config_id='fixture', top_n=2, stock_weighting='score_weighted')
    assert result.target_weight.tolist() == pytest.approx([.5, .5])
    assert result.target_weight.sum() == pytest.approx(1.0)
