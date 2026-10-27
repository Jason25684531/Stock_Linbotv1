import json

import pytest

from scripts import paper_trading as pt


def bar(open_=100.0, high=None, low=None, close=100.0, volume=1_000_000, amount=1e9, up=110.0, down=90.0, prior_amount=1e9):
    return {'open': open_, 'high': high if high is not None else open_ + 1, 'low': low if low is not None else open_ - 1, 'close': close,
            'volume': volume, 'amount': amount, 'prior_amount': prior_amount, 'limit_up': up, 'limit_down': down}


@pytest.fixture
def acct(tmp_path):
    pt.open_account(tmp_path, 1_000_000.0, 'TEST')
    return tmp_path


def test_unfillable_orders_are_rejected_never_assumed_filled(acct):
    pt.plan(acct, 'D0', {'LOCK': 0.1, 'NOBAR': 0.1, 'NOVOL': 0.1, 'OK': 0.1}, {'LOCK': 100, 'NOBAR': 100, 'NOVOL': 100, 'OK': 100})
    fills = {r['stock_id']: r['status'] for r in pt.execute(acct, 'D1', {
        'LOCK': bar(110.0, high=110.0, low=110.0), 'NOVOL': bar(volume=0), 'OK': bar()})}
    assert fills == {'LOCK': 'REJECTED_LOCKED_LIMIT_UP', 'NOBAR': 'REJECTED_NO_BAR', 'NOVOL': 'REJECTED_NO_TRADE', 'OK': 'FILLED'}
    assert list(pt.load(acct)['positions']) == ['OK']


def test_costs_slippage_and_cash_are_exact(acct):
    pt.plan(acct, 'D0', {'A': 0.1}, {'A': 100.0})
    o = pt.execute(acct, 'D1', {'A': bar(100.0)})[0]
    price = 100.0 * (1 + pt.SLIPPAGE)
    assert o['filled_shares'] == 990 and o['price'] == pytest.approx(price)
    assert pt.load(acct)['cash'] == pytest.approx(1_000_000 - 990 * price - pt.COSTS.buy_cost(990 * price))
    pt.plan(acct, 'D1', {}, {'A': 100.0})
    sell = pt.execute(acct, 'D2', {'A': bar(100.0)})[0]
    assert sell['cost'] == pt.COSTS.sell_cost(990 * 100.0 * (1 - pt.SLIPPAGE))  # fee + 0.3 % tax
    assert pt.load(acct)['positions'] == {} and pt.reconcile(acct)['status'] == 'PASS'


def test_participation_cap_gives_partial_fill(acct):
    pt.plan(acct, 'D0', {'THIN': 0.1}, {'THIN': 100.0})
    o = pt.execute(acct, 'D1', {'THIN': bar(prior_amount=100_000.0)})[0]  # 5 % of the previous session's 100k = 5k -> 49 shares at 100.1
    assert o['status'] == 'PARTIAL' and o['filled_shares'] == int(0.05 * 100_000 / (100 * (1 + pt.SLIPPAGE)))


def test_weight_cap_and_cash_limit(acct):
    orders = pt.plan(acct, 'D0', {'BIG': 0.9}, {'BIG': 100.0})
    assert orders[0]["shares"] == int(1_000_000 * (1 - pt.CASH_BUFFER) * pt.MAX_WEIGHT / 100.0)


def test_each_session_is_processed_once(acct):
    pt.plan(acct, 'D0', {'A': 0.1}, {'A': 100.0})
    assert pt.execute(acct, 'D1', {'A': bar()}) and pt.execute(acct, 'D1', {'A': bar()}) == []
    pt.mark(acct, 'D1', {'A': 100.0})
    pt.mark(acct, 'D1', {'A': 50.0})  # a duplicate trigger does not re-mark
    assert len((acct / 'nav.jsonl').read_text().splitlines()) == 1


def test_kill_switch_on_drawdown_and_missing_close(acct):
    pt.plan(acct, 'D0', {'A': 0.1}, {'A': 100.0})
    pt.execute(acct, 'D1', {'A': bar()})
    pt.mark(acct, 'D1', {})  # held name without a close
    assert pt.load(acct)['status'] == 'HALTED' and pt.plan(acct, 'D1', {'B': 0.1}, {'B': 10.0}) == []


def test_drawdown_halts(tmp_path, monkeypatch):
    monkeypatch.setattr(pt, 'MAX_WEIGHT', 1.0)
    pt.open_account(tmp_path, 100_000.0, 'T')
    pt.plan(tmp_path, 'D0', {'A': 1.0}, {'A': 100.0})
    pt.execute(tmp_path, 'D1', {'A': bar()})
    pt.mark(tmp_path, 'D1', {'A': 70.0})
    assert pt.load(tmp_path)['status'] == 'HALTED' and 'drawdown' in pt.load(tmp_path)['halt_reason']


def test_tampered_state_fails_reconciliation_and_halts(acct):
    pt.plan(acct, 'D0', {'A': 0.1}, {'A': 100.0})
    pt.execute(acct, 'D1', {'A': bar()})
    state = pt.load(acct)
    state['cash'] += 1000
    (acct / 'state.json').write_text(json.dumps(state))
    assert pt.reconcile(acct)['status'] == 'FAIL' and pt.load(acct)['status'] == 'HALTED'


def test_locked_limit_down_sell_is_rejected(acct):
    pt.plan(acct, 'D0', {'A': 0.1}, {'A': 100.0})
    pt.execute(acct, 'D1', {'A': bar()})
    pt.plan(acct, 'D1', {}, {'A': 100.0})
    o = pt.execute(acct, 'D2', {'A': bar(90.0, high=90.0, low=90.0)})[0]
    assert o['status'] == 'REJECTED_LOCKED_LIMIT_DOWN' and pt.load(acct)['positions']['A'] == 990


def test_low_cash_never_goes_negative_or_short(tmp_path):
    pt.open_account(tmp_path, 15.0, 'T')  # below the 20 minimum fee
    state = pt.load(tmp_path)
    state['pending'] = [{'stock_id': 'A', 'side': 'buy', 'shares': 100, 'plan_date': 'D0'}]
    (tmp_path / 'state.json').write_text(json.dumps(state))
    o = pt.execute(tmp_path, 'D1', {'A': bar()})[0]
    assert o['filled_shares'] == 0 and o['status'] == 'REJECTED_CAP_OR_CASH'
    assert pt.load(tmp_path)['cash'] == 15.0 and pt.load(tmp_path)['positions'] == {}


@pytest.mark.parametrize('field', ['volume', 'amount', 'high', 'low', 'open'])
def test_non_finite_bar_fields_are_not_tradable(field):
    assert pt.fill_check('buy', {**bar(), field: float('nan')}) == 'REJECTED_NO_TRADE'


def test_stale_plan_is_rejected_not_filled_late(acct):
    pt.plan(acct, 'D0', {'A': 0.1}, {'A': 100.0})
    o = pt.execute(acct, 'D2', {'A': bar()}, previous_session='D1')[0]  # D1 was skipped
    assert o['status'] == 'REJECTED_STALE_PLAN' and pt.load(acct)['positions'] == {}
