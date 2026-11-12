import inspect

import numpy as np
import pandas as pd
import pytest

from core.research.intelligence import portfolio as pf


def _member(sid, returns, start='2018-01-01', snapshot='HS1-a', current=True, holdings=None):
    dates = pd.bdate_range(start, periods=len(returns))
    return {'strategy_id': sid, 'snapshot_id': snapshot, 'accounting': 'POSITION_LEDGER_CA_V1', 'identity_current': current,
            'verified_returns': pd.Series(returns, index=dates), 'holdings': holdings}


def test_eligibility_and_reasons():
    rng = np.random.default_rng(0)
    a, b = _member('a', rng.normal(0, 0.01, 400)), _member('b', rng.normal(0, 0.01, 400))
    out = pf.analyze([a, b, _member('c', rng.normal(0, 0.01, 400), snapshot='HS1-b'), _member('d', rng.normal(0, 0.01, 400), current=False),
                      {'strategy_id': 'e', 'snapshot_id': 'HS1-a', 'accounting': 'POSITION_LEDGER_CA_V1', 'identity_current': True, 'verified_returns': None}],
                     benchmark=None)
    assert out['eligible'] == ['a', 'b']
    assert out['excluded'] == {'c': 'SNAPSHOT_DIFFERS:HS1-b', 'd': 'IDENTITY_STALE', 'e': 'NO_VERIFIED_TRACK'}
    assert out['correlation']['a']['b'] == pytest.approx(np.corrcoef(a['verified_returns'], b['verified_returns'])[0, 1])
    assert out['common_sessions'] == 400


def test_short_overlap_is_refused():
    out = pf.analyze([_member('a', np.linspace(-0.01, 0.01, 300)), _member('b', np.linspace(0.01, -0.01, 300), start='2018-08-01')], benchmark=None)
    assert out['status'] == 'INSUFFICIENT_COMMON_VERIFIED_SESSIONS' and 'correlation' not in out


def test_joint_loss_and_demo_portfolio():
    r = np.r_[np.full(10, -0.05), np.full(290, 0.001)]
    out = pf.analyze([_member('a', r), _member('b', r)], benchmark=None)
    assert out['joint_tail_loss']['a']['b'] == pytest.approx(10 / 300)
    assert out['joint_drawdown_share']['a']['b'] == pytest.approx(out['joint_drawdown_share']['a']['a'])
    demo = out['equal_weight_demo']
    assert demo['label'] == 'RESEARCH_DEMO_NOT_A_STRATEGY' and demo['members'] == ['a', 'b']
    assert 'weights' not in inspect.signature(pf.analyze).parameters


def test_a_member_without_return_variation_is_excluded():
    rng = np.random.default_rng(1)
    out = pf.analyze([_member('a', rng.normal(0, 0.01, 300)), _member('b', rng.normal(0, 0.01, 300)), _member('cash', np.zeros(300))], benchmark=None)
    assert out['eligible'] == ['a', 'b'] and out['excluded']['cash'] == 'NO_RETURN_VARIATION_IN_COMMON_PERIOD'
    assert out['equal_weight_demo']['members'] == ['a', 'b'] and 'cash' not in out['correlation']


def test_holding_overlap_needs_reconciled_positions():
    days = pd.bdate_range('2018-01-01', periods=260)
    h1 = {d.strftime('%Y-%m-%d'): ['1101', '2330'] for d in days}
    h2 = {d.strftime('%Y-%m-%d'): ['2330'] for d in days}
    out = pf.analyze([_member('a', np.linspace(-0.01, 0.01, 260), holdings=h1), _member('b', np.linspace(0.01, -0.01, 260), holdings=h2), _member('c', np.sin(np.arange(260)) / 100)], benchmark=None)
    assert out['holding_overlap']['a']['b'] == pytest.approx(0.5)
    assert out['holding_overlap']['a']['c'] is None and out['holding_overlap_reason']['c'] == 'POSITIONS_UNVERIFIABLE'
