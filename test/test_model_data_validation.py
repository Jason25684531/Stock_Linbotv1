"""Decision and estimate logic of scripts/validate_model_data.py (no DB, no network)."""
import pandas as pd

from scripts.validate_model_data import clean_sessions, date_after_sessions, decide, needed

BASE = {k: {'grade': 'NOT_OBSERVED'} for k in ('wrong_flows_in_fit', 'wrong_flows_in_evaluation', 'definition_changed',
                                               'ranking_impact', 'leakage', 'distribution_shift')}


def criteria(**grades):
    return {**BASE, 'cutoff_provable': {'grade': 'PROVEN'}, **{k: {'grade': g} for k, g in grades.items()}}


def test_proven_wrong_training_flows_require_retraining():
    assert decide(criteria(wrong_flows_in_fit='PROVEN')) == 'RETRAIN_REQUIRED'


def test_changed_definition_requires_retraining_only_with_ranking_impact():
    assert decide(criteria(definition_changed='PROVEN', ranking_impact='PROVEN')) == 'RETRAIN_REQUIRED'
    assert decide(criteria(definition_changed='PROVEN', ranking_impact='NOT_OBSERVED')) == 'RETRAIN_RECOMMENDED'


def test_unknown_cutoff_is_never_not_required():
    assert decide(criteria(cutoff_provable='UNKNOWN')) == 'UNDETERMINED'
    assert decide(criteria()) == 'RETRAIN_NOT_REQUIRED'  # only with a proven cutoff and no risk at all


def test_cached_sessions_are_not_requested_again():
    assert needed(['2026-01-02', '2026-01-05', '2026-01-06'], {'2026-01-05'}) == ['2026-01-02', '2026-01-06']


def test_read_periods_never_count_as_oos():
    days = ['2026-07-27', '2026-07-28', '2026-07-29', '2026-10-13', '2026-10-21']
    periods = [('2026-05-21', '2026-07-28', 'native7'), ('2026-10-13', '2026-10-20', 'shadow')]
    assert clean_sessions(days, periods) == ['2026-07-29', '2026-10-21']


def test_session_date_uses_known_calendar_then_weekday_estimate():
    known = ['2026-01-02', '2026-01-05', '2026-01-06']
    assert date_after_sessions('2026-01-01', 2, known, 0.93) == ('2026-01-05', 'official calendar')
    day, basis = date_after_sessions('2026-01-01', 10, known, 1.0)
    assert basis.startswith('estimate') and pd.Timestamp(day) > pd.Timestamp('2026-01-06')
