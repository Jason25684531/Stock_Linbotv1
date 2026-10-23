"""Statistics rules of scripts/track_b_stats.py on synthetic series."""
import math

import numpy as np
import pandas as pd
import pytest

from scripts import track_b_stats as st


def noise(n=750, mean=0.0, sd=0.01, seed=1):
    return np.random.default_rng(seed).normal(mean, sd, n)


def test_sharpe_and_drawdown_known_values():
    assert st.sharpe([0.01, -0.01] * 50) == pytest.approx(0.0, abs=1e-9)
    assert math.isnan(st.sharpe([0.01] * 10))  # no variance: undefined, not infinite
    assert st.max_drawdown([0.10, -0.50, 0.0]) == pytest.approx(-0.5)
    assert st.cagr([0.0] * 252) == pytest.approx(0.0)


def test_bootstrap_interval_contains_zero_for_noise_and_excludes_it_for_a_real_edge():
    pure_noise = st.block_bootstrap_sharpe(noise())
    assert pure_noise['lower'] < 0 < pure_noise['upper'] and pure_noise['draws'] == 2000
    edge = st.block_bootstrap_sharpe(noise(mean=0.004, sd=0.005))
    assert edge['lower'] > 0
    assert st.block_bootstrap_sharpe(noise(30))['draws'] == 0  # too short for 20-session blocks: no interval is claimed
    assert st.block_bootstrap_sharpe(noise()) == pure_noise  # fixed seed: reproducible


def test_expected_maximum_sharpe_grows_with_the_number_of_trials():
    small, large = st.expected_max_sharpe(10, 0.0001), st.expected_max_sharpe(1000, 0.0001)
    assert 0 < small < large and st.expected_max_sharpe(1, 0.0001) == 0.0


def test_deflated_sharpe_falls_when_more_trials_were_run():
    x = noise(mean=0.0008, sd=0.01)
    few, many = st.deflated_sharpe(x, 5, 0.0001), st.deflated_sharpe(x, 5000, 0.0001)
    assert 0 <= many < few <= 1


def test_concentration_is_none_when_the_total_is_not_positive():
    assert st.best_days_share([-0.01] * 20) is None
    assert st.best_days_share([0.01] * 100) == pytest.approx(0.05)
    assert st.top_assets_share({'a': 60, 'b': 30, 'c': 10}, 1) == pytest.approx(0.6)
    assert st.top_assets_share({'a': 10, 'b': -30}, 1) is None


def test_regime_uses_the_previous_session_and_flags_short_regimes():
    index = pd.Series(np.r_[np.linspace(100, 200, 260), np.linspace(200, 90, 40)], index=pd.bdate_range('2024-01-01', periods=300))
    regime = st.regimes(index)
    assert regime.iloc[:199].isna().all() and regime.iloc[210] == 'bull'
    excess = pd.Series(0.001, index=index.index)
    result = st.regime_excess(excess, regime)
    assert result['bull']['sessions'] >= st.MIN_REGIME_SESSIONS and result['bull']['status'] == 'POSITIVE'
    assert result['bear']['status'] == 'UNDETERMINED_SAMPLE_TOO_SHORT'  # a short bear sample is not "stable"


def test_paired_difference_interval_separates_a_real_gap_from_noise():
    base = noise(seed=3)
    same = st.paired_block_bootstrap_sharpe_difference(base + noise(sd=0.0005, seed=4), base)
    assert same['lower'] < 0 < same['upper']  # two nearly identical series cannot be told apart
    better = st.paired_block_bootstrap_sharpe_difference(noise(mean=0.004, sd=0.005, seed=5), noise(seed=6))
    assert better['lower'] > 0 and better['difference'] > 0


# ---- the candidate decision itself (the real results contain no candidate, so the rule is proven on constructed rows) ----
def candidate_row(**override):
    regime = {'bull': {'sessions': 300, 'annualized_excess': 0.1, 'status': 'POSITIVE'}, 'bear': {'sessions': 200, 'annualized_excess': 0.05, 'status': 'POSITIVE'}}
    row = {'family': 'D5', 'config_id': 'c1', 'sharpe_net_2x_cost': 2.0, 'excess_sharpe_2x_cost': 1.5, 'top5_assets_profit_share': 0.3, 'best5pct_days_return_share': 0.4,
           'regime': regime, 'execution_audit': 'PASS_FLAG_ONLY', 'held_on_nonofficial_prices': 0, 'delisting_status': 'NO_DELISTED_HOLDING', 'beyond_boundary': False,
           'total_return_status': 'TOTAL_RETURN_PURE_CASH_ONLY'}
    return {**row, **override}


def decide(**override):
    from scripts import track_b_evaluate as ev

    x = pd.Series(np.random.default_rng(1).normal(0.004, 0.004, 700), index=pd.bdate_range('2023-01-02', periods=700))
    others = {f'o{i}': pd.Series(np.random.default_rng(10 + i).normal(0.0, 0.01, 700), index=x.index) for i in range(60)}  # many comparable trials, as in the real study
    frame, _ = ev.apply_statistics(pd.DataFrame([candidate_row(**override)]), {'c1': x, **others})
    return frame.iloc[0]


def test_every_condition_met_gives_a_candidate_and_never_an_oos_or_production_label():
    row = decide()
    assert row.statistical_gates_pass and row.BACKTEST == 'DEVELOPMENT' and row.EVIDENCE == 'CANDIDATE_FOR_INDEPENDENT_VALIDATION'
    text = ' '.join(str(v) for v in row.to_dict().values())
    assert not {'OOS_PASS', 'PROFITABLE_PROVEN', 'PRODUCTION_READY'} & set(text.split())


@pytest.mark.parametrize('override', [{'total_return_status': 'TOTAL_RETURN_BLOCKED'}, {'delisting_status': 'DELISTING_EXIT_UNVERIFIED'}, {'execution_audit': 'FLAGGED_MATERIAL'},
                                      {'held_on_nonofficial_prices': 1}, {'beyond_boundary': True}, {'family': 'G2G3'}, {'top5_assets_profit_share': 0.7},
                                      {'best5pct_days_return_share': None}, {'excess_sharpe_2x_cost': -0.1}])
def test_any_missing_certification_or_failed_gate_blocks_the_candidate(override):
    assert decide(**override).EVIDENCE == 'INSUFFICIENT'


def test_a_short_regime_is_undetermined_not_stable():
    regime = {'bull': {'sessions': 500, 'annualized_excess': 0.1, 'status': 'POSITIVE'}, 'bear': {'sessions': 30, 'annualized_excess': 0.2, 'status': 'UNDETERMINED_SAMPLE_TOO_SHORT'}}
    row = decide(regime=regime)
    assert row.g5_regimes == 'UNDETERMINED' and row.EVIDENCE == 'INSUFFICIENT'
