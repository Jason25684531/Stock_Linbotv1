"""Review r1 B1 and M5/M8/M11: run-level trust downgrades, card coverage, identity inputs, forward-return hygiene. Synthetic data only."""
import json

import numpy as np
import pytest
import pandas as pd

from core.research.intelligence import evaluate, performance as perf
from scripts import historical_report as hr
from scripts import strategy_intelligence as si


def test_assumed_stock_booked_in_a_verified_year_makes_it_partial():
    cells = [{'year': y, 'status': 'VERIFIED'} for y in (2017, 2018, 2019)]
    nav = pd.DataFrame({'date': ['2017-01-03', '2019-12-31'], 'nav_base': [1.0, 1.1], 'nav_stress': [1.0, 1.1]})
    trades = pd.DataFrame({'stock_id': ['1101'], 'buy_date': ['2018-07-01'], 'sell_date': ['2018-07-20'], 'profit_pct': [1.0]})
    actions = pd.DataFrame({'date': ['2018-07-10'], 'stock_id': ['1101'], 'status': ['BOOKED'], 'terms_status': ['ASSUMED_STOCK'], 'cash_credited': [0.0]})
    trust, exposure, impact = hr.run_trust(cells, nav, trades, actions, '2019-12-31', {}, {})
    assert trust == {2017: 'VERIFIED', 2018: 'PARTIAL', 2019: 'VERIFIED'} and impact['years_with_assumed_terms'] == [2018]
    assert perf.verified_span(trust, [2017, 2018, 2019]) == (2017, 2017)


def test_trust_for_uses_the_shared_downgrade_and_blanks_a_stale_run():
    ctx = {'evaluation': {'trust': {'s': [{'year': 2018, 'status': 'VERIFIED'}]}}, 'plan': {'s': {'window': ['2018-01-02', '2018-12-28']}}}
    run = {'nav': pd.DataFrame({'date': ['2018-01-02'], 'nav_base': [1.0], 'nav_stress': [1.0]}),
           'trades': pd.DataFrame(columns=['stock_id', 'buy_date', 'sell_date', 'profit_pct']),
           'actions': pd.DataFrame({'date': ['2018-05-02'], 'stock_id': ['1'], 'status': ['BOOKED'], 'terms_status': ['ASSUMED_STOCK'], 'cash_credited': [0.0]}),
           'record': {'period': ['2018-01-02', '2018-12-28']}}
    assert si.trust_for(ctx, 's', run, 'CURRENT')[0] == {2018: 'PARTIAL'}
    assert si.trust_for(ctx, 's', run, 'IDENTITY_STALE')[0] == {2018: 'IDENTITY_STALE'}


def test_every_catalog_strategy_has_exactly_one_traceable_card():
    built = si.cards(stamp_missing=False)
    assert built['errors'] == [] and sorted(built['built']) == sorted(si.catalog_ids()) and len(built['built']) == 21
    for d in built['built'].values():
        for field in d['fields'].values():
            assert field['status'] in ('TRACED', 'UNVERIFIED_DEFINITION') and (field['text'] is None) == (field['status'] == 'UNVERIFIED_DEFINITION')


def test_identity_covers_every_file_that_computes_a_number():
    files = si.code_hashes()
    for name in ('core/backtest/metrics.py', 'core/validation/bootstrap.py', 'core/validation/correlation.py', 'core/research/history/regime.py',
                 'scripts/historical_report.py', 'core/research/intelligence/performance.py', 'scripts/strategy_intelligence.py'):
        assert name in files


def test_forward_returns_use_official_sessions_skip_invalid_closes_and_event_windows():
    cal = pd.bdate_range('2020-01-01', periods=30)
    bars = pd.DataFrame({'trade_date': cal, 'stock_id': '1101', 'high': 11.0, 'low': 9.0, 'close': 10.0 + np.arange(30) * 0.1})
    bars.loc[6, 'close'] = np.nan  # the 5th session after the first buy has no valid close
    trades = pd.DataFrame({'stock_id': ['1101', '1101'], 'buy_date': [cal[1].strftime('%Y-%m-%d'), cal[10].strftime('%Y-%m-%d')],
                           'sell_date': [cal[4].strftime('%Y-%m-%d'), cal[13].strftime('%Y-%m-%d')], 'buy_price': [10.0, 10.0], 'profit_pct': [1.0, 1.0]})
    out = perf.trade_level(trades, bars, None, cal, {'1101': pd.DatetimeIndex([cal[12]])}, {'fee_rate': 0.001425, 'tax_rate': 0.003})
    assert out['forward_skipped']['no_valid_close'] == 1 and out['forward_skipped']['event_in_window'] == 3
    assert out['forward_return_10']['value'] == (10.0 + 11 * 0.1) / 10 - 1
    assert out['forward_return_10_net']['value'] == out['forward_return_10']['value'] - (2 * 0.001425 + 0.003)
    assert out['mfe_median']['value'] == pytest.approx(0.1)  # buy..day before the exit session only


def test_provisional_numbers_are_shown_but_never_judged():
    prof = json.loads(si.PROFILE.read_text(encoding='utf-8'))
    prov = {'cagr': {'value': 0.2}, 'cagr_stress': {'value': 0.15}, 'excess_cagr': {'value': 0.05}, 'profit_factor': {'value': 2.0}, 'closed_trades': 50}
    d = evaluate.dimensions({'verified': None, 'provisional': prov, 'primary_type': 'trend_momentum', 'scenarios_sample': {}}, prof)
    assert d['cost_tolerance']['status'] == 'INSUFFICIENT_DATA' and d['cost_tolerance']['cagr_stress'] == 0.15
    assert d['benchmark']['status'] == 'INSUFFICIENT_DATA'
    assert {c['status'] for c in d['type_edge']['checks']} == {'INSUFFICIENT_DATA'}
    assert all(c['source'] != 'ENGINE_CONSTANT' for c in d['type_edge']['checks'])
