"""Legacy baseline runner (round 2026-11-06-msv): window refusal, blocked ids, PIT and chip checks, synthetic end-to-end run.

Synthetic data only; never investment evidence and never a governed trial.
"""
import numpy as np
import pandas as pd
import pytest

from core.research.strategy_signal import ResearchOnlyViolation
from scripts import legacy_baseline_run as lb

DAYS = pd.bdate_range('2022-08-01', '2023-04-28')
STOCKS = ('2330', '1101', '1102', '1216', '2002', '2105')


def synthetic_inputs(start=None, end=None):
    rng = np.random.default_rng(11)
    rows = []
    for k, sid in enumerate(STOCKS):
        close = 30 * np.exp(np.cumsum(rng.normal(0.0015, 0.008, len(DAYS)))) + k
        for i, day in enumerate(DAYS):
            prev = close[i - 1] if i else close[i]
            volume = float(rng.integers(1_500_000, 3_000_000))
            rows.append({'trade_date': day, 'stock_id': sid, 'open_price': prev * (1 + rng.normal(0, 0.002)), 'high_price': max(prev, close[i]) * 1.004,
                         'low_price': min(prev, close[i]) * 0.996, 'close_price': close[i], 'volume': volume, 'amount': volume * close[i],
                         'foreign_buy': float(rng.integers(-200, 400)), 'trust_buy': float(rng.integers(-20, 60)), 'dealer_buy': float(rng.integers(-10, 10)),
                         'margin_balance': float(rng.integers(1000, 2000)), 'short_balance': np.nan})
    fin = pd.DataFrame([{'stock_id': s, 'year': y, 'quarter': q, 'revenue': 100.0, 'rd_expense': 1.0, 'operating_profit': 15.0, 'eps': 2.0}
                        for s in STOCKS for y, q in ((2022, 2), (2022, 3), (2023, 1))])
    pubs = pd.DataFrame([{'stock_id': s, 'year': 2022, 'quarter': q, 'publication_date': pd.Timestamp(d)}
                         for s in STOCKS[:-1] for q, d in ((2, '2022-08-10'), (3, '2022-11-10'))])  # the last stock is dated by deadline only
    rev = pd.DataFrame([{'stock_id': s, 'year': 2022, 'month': m, 'revenue': 1.0, 'revenue_yoy': 5.0} for s in STOCKS for m in (10, 11)])
    snap = pd.DataFrame(rows)
    bench = snap[snap.stock_id == '2330'].set_index('trade_date').close_price.rename('taiex_price')
    gone = pd.DataFrame({'stock_id': ['9999', '8888'], 'last_traded': ['2023-02-01', '2021-01-04']})
    return {'snapshot': snap, 'financials': fin, 'revenue': rev, 'publications': pubs, 'settings': {}, 'benchmark': bench, 'delistings': gone}


def test_2026_end_is_refused_before_any_input_is_read():
    calls = []
    with pytest.raises(ResearchOnlyViolation):
        lb.run_legacy('mean_reversion', end='2026-01-05', snapshot_builder=lambda s, e: calls.append((s, e)))
    assert calls == []


@pytest.mark.parametrize('strategy_id', sorted(lb.BLOCKED))
def test_blocked_strategies_raise_with_reason(strategy_id):
    calls = []
    with pytest.raises(ValueError, match='MONTHLY_REVENUE_NO_ANNOUNCEMENT_TIME'):
        lb.run_legacy(strategy_id, snapshot_builder=lambda s, e: calls.append(1))
    assert calls == []


def test_pit_check_flags_report_published_on_signal_date():
    rows = pd.DataFrame({'fundamental_availability_source': ['mops_publication', 'mops_publication', 'statutory_deadline', None],
                         'fundamental_due': pd.to_datetime(['2023-03-10', '2023-03-09', '2023-03-01', None])})
    got = lb.pit_check(rows, '2023-03-10')
    assert got == {'checks': 3, 'violations': 1, 'unverified': 1}
    assert lb.pit_status(got) == 'PIT_VIOLATION'
    assert lb.pit_status(lb.pit_check(rows.iloc[[1]], '2023-03-10')) == 'PIT_VERIFIED'
    assert lb.pit_status(lb.pit_check(rows.iloc[[1, 2]], '2023-03-10')) == 'PIT_UNVERIFIED'
    assert lb.pit_status(lb.pit_check(pd.DataFrame({'eps': [1.0]}), '2023-03-10')) == 'PIT_UNVERIFIED'  # no per-row dates


def test_reports_published_after_end_are_dropped():
    inputs = synthetic_inputs()
    fin = inputs['financials']
    available = lb.report_availability(fin, inputs['publications'])
    assert available[(fin.stock_id == '1101') & (fin.quarter == 3)].iloc[0] == pd.Timestamp('2022-11-10')
    assert available[(fin.stock_id == '2105') & (fin.quarter == 3)].iloc[0] == pd.Timestamp('2022-11-14')  # statutory deadline
    prep = lb._prepare(inputs, '2023-01-03', '2023-04-28')
    assert prep.data._fin.fundamental_due.max() <= pd.Timestamp('2023-04-28')  # 2023Q1 (due 05-15) is excluded
    assert prep.data.snapshot.trade_date.min() >= pd.Timestamp('2022-07-26') and prep.data.snapshot.trade_date.max() <= pd.Timestamp('2023-04-28')


def test_chip_degeneracy():
    varied = [lb.chip_distribution([10, 20, 30]), lb.chip_distribution([0, 40])]
    assert lb.chip_degeneracy(varied) == (False, None)
    assert lb.chip_degeneracy([lb.chip_distribution([7, 7]), lb.chip_distribution([3])])[0]
    zeros = [lb.chip_distribution([0] * 96 + [1, 2, 3, 4])]
    assert lb.chip_degeneracy(zeros) == (True, 'ZERO_SHARE_0.9600')
    assert lb.chip_degeneracy([lb.chip_distribution([0] * 94 + list(range(1, 7)))]) == (False, None)
    assert lb.chip_degeneracy([lb.chip_distribution([])]) == (True, 'NO_CHIP_SCORE_VALUES')


def test_config_for_is_deterministic():
    a = lb.config_for('mean_reversion', {'V37_MAX_HOLD_DAYS': 16})
    assert a == lb.config_for('mean_reversion', {'V37_MAX_HOLD_DAYS': 16})
    assert a['round'] == '2026-11-06-msv' and a['valuation_policy'] == 'finite_positive_stale'
    assert a['seed'] == 20261023 and a['stress_multiple'] == 2
    assert (a['fee_rate'], a['tax_rate'], a['minimum_fee'], a['slippage_max']) == (0.001425, 0.003, 20, 0.001)
    assert a != lb.config_for('mean_reversion')


def test_injectable_only_where_the_strategy_reads_the_key():
    assert lb.check_injectable('mean_reversion', lb.SENSITIVITY['mean_reversion']) is True
    assert lb.check_injectable('quality_value_low_volatility', lb.SENSITIVITY['quality_value_low_volatility']) is True
    assert lb.check_injectable('institutional_flow_confirmation', lb.SENSITIVITY['institutional_flow_confirmation']) is True
    assert lb.check_injectable('hybrid_trend_rank', lb.SENSITIVITY['hybrid_trend_rank']).startswith('NOT_INJECTABLE')
    assert lb.check_injectable('defensive_low_volatility', lb.SENSITIVITY['defensive_low_volatility']).startswith('NOT_INJECTABLE')
    assert lb.check_injectable('quality_growth', {}).startswith('DATA_BLOCKED')
    with pytest.raises(ValueError, match='NOT_INJECTABLE'):
        lb.run_legacy('hybrid_trend_rank', {'MAX_HOLD_DAYS': 20}, end='2023-04-28', snapshot_builder=lambda s, e: pytest.fail('read'))


def test_synthetic_end_to_end_run_returns_both_tracks():
    out = lb.run_legacy('quality_value_low_volatility', {'V38_MAX_HOLD_DAYS': 30}, start='2023-01-03', end='2023-04-28', snapshot_builder=synthetic_inputs)
    sessions = len([d for d in DAYS if d >= pd.Timestamp('2023-01-03')])
    assert set(out) >= {'strategy_id', 'overrides', 'tracks', 'picks', 'checks', 'data_version', 'benchmark'}
    for name in ('base', 'stress'):
        track = out['tracks'][name]
        assert track['valuation_policy'] == 'finite_positive_stale' and 'status' in track['valuation']
        assert len(track['nav']) == sessions and track['nav'][0][0] == '2023-01-03' and track['nav'][-1][0] == '2023-04-28'
        assert all(set(t) == {'stock_id', 'buy_date', 'buy_price', 'sell_date', 'sell_price', 'shares', 'cost', 'profit_pct', 'reason'} for t in track['trades'])
    base, stress = out['tracks']['base'], out['tracks']['stress']
    assert stress['costs']['fee_rate'] == 2 * base['costs']['fee_rate'] and stress['costs']['slippage_max'] == 2 * base['costs']['slippage_max']
    assert base['trades'], 'synthetic market should produce at least one trade'
    assert all(t['shares'] > 0 and t['cost'] > 0 and t['buy_date'] < t['sell_date'] for t in base['trades'])
    checks = out['checks']
    assert checks['max_hold_days'] == 30 and checks['trade_count'] == len(base['trades'])
    assert checks['pit_checks'] > 0 and checks['pit_violations'] == 0 and checks['pit_status'] in ('PIT_VERIFIED', 'PIT_UNVERIFIED')
    assert checks['lookahead_violations'] == 0 and checks['chip_score'] is None and checks['degenerate'] is False
    assert checks['delisted_in_window']['count'] == 1
    assert out['picks'] and all(r[2] in (1, 2, 3) for r in out['picks'])
    assert len(out['benchmark']) == sessions and len(out['data_version']) == 64
    again = lb.run_legacy('quality_value_low_volatility', {'V38_MAX_HOLD_DAYS': 30}, start='2023-01-03', end='2023-04-28', snapshot_builder=synthetic_inputs)
    assert again['tracks'] == out['tracks'] and again['data_version'] == out['data_version']  # same seed, same inputs


def test_synthetic_chip_strategy_records_distribution():
    out = lb.run_legacy('institutional_flow_confirmation', start='2023-01-03', end='2023-04-28', snapshot_builder=synthetic_inputs)
    chip = out['checks']['chip_score']
    assert chip is not None and isinstance(chip['degenerate'], bool) and out['checks']['pit_status'] == 'NOT_APPLICABLE'
    assert all(set(d) >= {'date', 'n', 'distinct', 'zero_share'} for d in chip['per_date'])


def test_runner_records_stale_marks_for_held_missing_closes():
    initial = lb.run_legacy('quality_value_low_volatility', start='2023-01-03', end='2023-04-28', snapshot_builder=synthetic_inputs)
    trade, missing_day = next((t, d) for t in initial['tracks']['base']['trades'] for d in DAYS
                              if pd.Timestamp(t['buy_date']) < d < pd.Timestamp(t['sell_date']))

    def missing(start, end):
        inputs = synthetic_inputs(start, end)
        bars = inputs['snapshot']
        bars.loc[(bars.stock_id == trade['stock_id']) & (bars.trade_date == missing_day), 'close_price'] = np.nan
        return inputs

    result = lb.run_legacy('quality_value_low_volatility', start='2023-01-03', end='2023-04-28', snapshot_builder=missing)
    track = result['tracks']['base']
    assert all(np.isfinite(value) and value > 0 for _, value in track['nav'])
    assert any(e['stock_id'] == trade['stock_id'] and e['date'] == missing_day.strftime('%Y-%m-%d') for e in track['valuation_events'])
    assert track['valuation']['status'] == 'VALUATION_BLOCKED_ACTIONS_UNKNOWN'


def test_evidence_capture_changes_no_result(monkeypatch):
    """Design D5: the read-only wrappers leave trades and NAV identical (rtol 1e-9, atol 1e-12)."""
    args = dict(start='2023-01-03', end='2023-04-28', snapshot_builder=synthetic_inputs)
    captured = lb.run_legacy('quality_value_low_volatility', **args)
    monkeypatch.setattr(lb, 'CAPTURE', False)
    plain = lb.run_legacy('quality_value_low_volatility', **args)
    for name in ('base', 'stress'):
        assert captured['tracks'][name]['trades'] == plain['tracks'][name]['trades']
        np.testing.assert_allclose([v for _, v in captured['tracks'][name]['nav']], [v for _, v in plain['tracks'][name]['nav']], rtol=1e-9, atol=1e-12)
        assert 'positions_daily' not in plain['tracks'][name]
    track = captured['tracks']['base']
    by_day = pd.DataFrame(track['positions_daily']).groupby('date').value.sum()
    nav = pd.Series(dict(track['nav']))
    np.testing.assert_allclose(by_day.reindex(nav.index).to_numpy(), nav.to_numpy(), rtol=1e-9, atol=1e-6)
    assert track['cost_summary']['includes_open_positions'] and track['cost_summary']['fees_and_tax_paid'] >= sum(t['cost'] for t in track['trades'])
    assert captured['tracks']['stress']['trades'] and captured['tracks']['stress']['costs']['fee_rate'] == 2 * track['costs']['fee_rate']


def test_pit_detail_matches_the_aggregate_and_never_infers_publication():
    out = lb.run_legacy('quality_value_low_volatility', start='2023-01-03', end='2023-04-28', snapshot_builder=synthetic_inputs)
    detail = pd.DataFrame(out['pit_detail'])
    assert tuple(detail.columns) == lb.PIT_DETAIL_COLUMNS
    assert lb.pit_totals(out['pit_detail']) == {k: out['checks'][f'pit_{k}'] for k in ('checks', 'violations', 'unverified')}
    deadline = detail[detail.ticker == STOCKS[-1]]  # the stock without a MOPS date
    assert len(deadline) and (deadline.pit_result == 'PIT_UNVERIFIED').all() and deadline.publication_date.isna().all()
    assert (deadline.pit_failure_reason == 'DEADLINE_DATED_NOT_PUBLICATION').all()
    mops = detail[detail.source == 'mops_publication']
    assert len(mops) and (mops.pit_result == 'PIT_VERIFIED').all() and mops.publication_date.notna().all()
    assert mops.financial_period.str.fullmatch(r'\d{4}Q[1-4]').all() and detail.source_hash.notna().all()


def test_pit_detail_flags_filing_published_on_the_signal_date():
    rows = pd.DataFrame({'stock_id': ['1101'], 'fundamental_availability_source': ['mops_publication'],
                         'fundamental_due': [pd.Timestamp('2023-03-01')], 'fundamental_period': ['2022Q4'],
                         'fundamental_publication_date': [pd.Timestamp('2023-03-01')]})
    row = lb.pit_detail(rows, '2023-03-01', {'financials': 'f' * 64, 'publications': 'p' * 64})[0]
    assert row['pit_result'] == 'PIT_VIOLATION' and row['pit_failure_reason'] == 'PUBLISHED_ON_OR_AFTER_SIGNAL_DATE'
    assert lb.pit_check(rows, '2023-03-01')['violations'] == 1


def test_injected_action_table_reaches_the_verdict():
    initial = lb.run_legacy('quality_value_low_volatility', start='2023-01-03', end='2023-04-28', snapshot_builder=synthetic_inputs)
    trade, missing_day = next((t, d) for t in initial['tracks']['base']['trades'] for d in DAYS
                              if pd.Timestamp(t['buy_date']) < d < pd.Timestamp(t['sell_date']))
    day = missing_day.strftime('%Y-%m-%d')

    def with_actions(start, end):
        inputs = synthetic_inputs(start, end)
        bars = inputs['snapshot']
        bars.loc[(bars.stock_id == trade['stock_id']) & (bars.trade_date == missing_day), 'close_price'] = np.nan
        inputs['actions'] = {'events': [{'ticker': trade['stock_id'], 'ex_date': day}], 'coverage': {sid: ('2022-01-01', '2025-12-31') for sid in STOCKS}, 'status': 'PARTIALLY_VERIFIED'}
        return inputs

    verdict = lb.run_legacy('quality_value_low_volatility', start='2023-01-03', end='2023-04-28', snapshot_builder=with_actions)['tracks']['base']['valuation']
    assert verdict['status'] == 'VALUATION_BLOCKED_CORPORATE_ACTION' and verdict['coverage'] == 'PARTIALLY_VERIFIED'
    assert {'date': day, 'stock_id': trade['stock_id'], 'match': 'MATCHED_ACTION'} in verdict['event_match']
