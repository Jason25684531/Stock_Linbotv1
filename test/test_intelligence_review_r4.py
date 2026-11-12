"""Regression tests for independent review round 4: thin samples are never judged, flat (cash) tracks are visible. Synthetic data only."""
import json

import numpy as np
import pandas as pd

from core.research.intelligence import evaluate, performance as perf
from scripts import strategy_intelligence as si

PROFILE = json.loads(si.PROFILE.read_text(encoding='utf-8'))


def _summary(closed, **values):
    base = {'cagr': 0.001, 'cagr_stress': 0.0005, 'sharpe': 0.4, 'sortino': 0.5, 'profit_factor': 3.0, 'expectancy': 0.02, 'excess_cagr': 0.01,
            'max_drawdown': -0.01, 'benchmark_max_drawdown_total_return': -0.3}
    return {k: {'value': v, 'reason': None} for k, v in (base | values).items()} | {'closed_trades': closed}


def test_a_thin_verified_track_is_shown_but_never_judged():
    ctx = {'verified': _summary(4), 'provisional': _summary(6), 'primary_type': 'trend_momentum', 'scenarios_sample': {}}
    d = evaluate.dimensions(ctx, PROFILE)
    assert d['cost_tolerance']['status'] == 'INSUFFICIENT_DATA' and d['benchmark']['status'] == 'INSUFFICIENT_DATA'
    assert {c['status'] for c in d['type_edge']['checks']} == {'INSUFFICIENT_DATA'} and '4' in d['type_edge']['sample_note']
    assert d['return_target']['status'] == 'RETURN_TARGET_NOT_MET'  # the CAGR fact is still compared with the Owner target
    statuses = {i['status'] for items in evaluate.health(ctx, PROFILE).values() for i in items if i['name'] != 'CAGR（成本後）'}
    assert not statuses & {'GOOD', 'RISK', 'CAUTION'}  # only the CAGR-versus-target fact is still compared (with a sample note)


def test_enough_trades_are_judged_and_break_even_wording_is_neutral():
    ctx = {'verified': _summary(40), 'provisional': _summary(40), 'primary_type': 'trend_momentum', 'scenarios_sample': {}}
    sharpe = next(i for i in evaluate.health(ctx, PROFILE)['efficiency'] if i['name'] == 'Sharpe')
    assert sharpe['neutral'] == '平均日報酬為正' and '總報酬仍可能為負' in sharpe['explanation']
    mdd = next(i for i in evaluate.health(ctx, PROFILE)['drawdown'] if i['name'] == '最大回撤')
    assert mdd['neutral'] == '淺於大盤' and '個百分點' in mdd['explanation']


def test_activity_shows_a_flat_cash_track():
    dates = pd.bdate_range('2018-01-01', periods=300)
    nav = pd.DataFrame({'date': dates, 'nav_base': np.r_[np.linspace(100, 110, 20), np.full(280, 110.0)], 'nav_stress': 100.0})
    trades = pd.DataFrame({'stock_id': ['2330'], 'buy_date': [dates[2].strftime('%Y-%m-%d')], 'sell_date': [dates[19].strftime('%Y-%m-%d')], 'profit_pct': [5.0]})
    out = perf.summarize(nav, trades, None)
    assert out['invested_session_share']['value'] == 17 / 299 and out['zero_return_session_share']['value'] > 0.9
    assert out['last_exit_in_track'] == dates[19].strftime('%Y-%m-%d')


def test_scenarios_need_enough_trades_in_the_complete_years(monkeypatch):
    cal = pd.bdate_range('2017-01-02', '2020-12-31')
    nav = pd.DataFrame({'date': cal.strftime('%Y-%m-%d'), 'nav_base': np.linspace(100, 120, len(cal)), 'nav_stress': 100.0})
    trades = pd.DataFrame({'stock_id': ['1'] * 4, 'buy_date': ['2017-02-01'] * 4, 'sell_date': ['2017-03-01'] * 4, 'profit_pct': [1.0] * 4})
    ctx = {'sessions': pd.Series(cal), 'bench': pd.DataFrame({'date': cal, 'taiex_total_return': 100.0, 'taiex_price': 100.0}), 'profile': PROFILE}
    status = {y: 'VERIFIED' for y in range(2017, 2021)}
    out = si._scenarios(ctx, {'nav': nav, 'trades': trades}, status)
    assert out['status'] == 'INSUFFICIENT_SAMPLE' and out['check']['closed_trades_in_span'] == 4 and 'simulation' not in out


def test_a_thin_track_keeps_the_target_fact_with_a_note_and_no_capacity():
    from core.research.intelligence import feasibility as fz
    d = evaluate.dimensions({'verified': _summary(4), 'provisional': _summary(6), 'primary_type': 'trend_momentum', 'scenarios_sample': {}}, PROFILE)
    assert d['return_target']['status'] == 'RETURN_TARGET_NOT_MET' and '4' in d['return_target']['sample_note']
    cagr = evaluate.health({'verified': _summary(4), 'provisional': _summary(6), 'primary_type': 'trend_momentum'}, PROFILE)['earn'][0]
    assert cagr['status'] == 'RISK' and '不代表策略優勢已驗證' in cagr['explanation']
    liq = {'trades': 6, 'trades_without_bar': 0, 'entry_participation_prev': {'p90': 0.001}}
    cap = fz.capacity(liq, 1_000_000.0, reconciled=True, min_trades=30)
    assert cap['label'] == 'CAPACITY_UNVERIFIED' and cap['value'] is None and cap['reason'].startswith('INSUFFICIENT_SAMPLE')
