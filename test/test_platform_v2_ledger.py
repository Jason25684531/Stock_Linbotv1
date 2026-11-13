import pytest
from copy import deepcopy

from core.research.platform.ledger_v2 import daily_ledger, reconcile

FILLS = [{'fill_id': 'b', 'date': 'd2', 'side': 'BUY', 'stock_id': '2330', 'shares': 1000, 'price': 100, 'notional': 100000, 'fee': 142, 'tax': 0, 'cash_delta': -100_142.0, 'cash_after': 899858},
         {'fill_id': 's', 'date': 'd4', 'side': 'SELL', 'stock_id': '2330', 'shares': 1100, 'price': 110, 'notional': 121000, 'fee': 137, 'tax': 363, 'cash_delta': 120_500.0, 'cash_after': 1022358}]
ACTIONS = [{'date': 'd3', 'stock_id': '2330', 'status': 'BOOKED', 'event_id': 'a', 'shares_before': 1000, 'shares_after': 1100, 'cash_credited': 2_000.0, 'share_multiplier': 1.1, 'cash_per_share': 2},
           {'date': 'd3', 'stock_id': '2330', 'status': 'UNRESOLVED_NOT_BOOKED', 'event_id': 'unresolved', 'shares_before': 1100, 'shares_after': 1100, 'cash_credited': 0.0}]
CASH = [1_000_000, 899_858, 901_858, 1_022_358]
POSITIONS = [{'date': 'd1', 'stock_id': 'CASH', 'shares': None, 'value': CASH[0]},
             {'date': 'd2', 'stock_id': '2330', 'shares': 1000, 'value': 101_000.0}, {'date': 'd2', 'stock_id': 'CASH', 'shares': None, 'value': CASH[1]},
             {'date': 'd3', 'stock_id': '2330', 'shares': 1100, 'value': 99_000.0}, {'date': 'd3', 'stock_id': 'CASH', 'shares': None, 'value': CASH[2]},
             {'date': 'd4', 'stock_id': 'CASH', 'shares': None, 'value': CASH[3]}]
NAV = [['d1', 1_000_000.0], ['d2', CASH[1] + 101_000.0], ['d3', CASH[2] + 99_000.0], ['d4', float(CASH[3])]]


def test_complete_ledger_reconciles():
    result = reconcile(1_000_000, NAV, FILLS, ACTIONS, POSITIONS, [])
    assert result == {'status': 'PUBLISHABLE', 'first_mismatch': None, 'reason': None, 'days_checked': 4}
    rows = daily_ledger(NAV, POSITIONS)
    assert rows[1] == {'date': 'd2', 'cash': CASH[1], 'positions_value': 101_000.0, 'nav': NAV[1][1], 'holdings': 1}


def test_missing_fill_is_not_publishable_with_first_date():
    result = reconcile(1_000_000, NAV, FILLS[1:], ACTIONS, POSITIONS, [])
    assert result['status'] == 'NOT_PUBLISHABLE' and result['first_mismatch'] == 'd2'
    assert 'CASH' in result['reason']


def test_nav_and_end_positions_are_checked():
    nav = [row[:] for row in NAV]
    nav[2][1] += 5
    assert reconcile(1_000_000, nav, FILLS, ACTIONS, POSITIONS, [])['first_mismatch'] == 'd3'
    end = reconcile(1_000_000, NAV, FILLS, ACTIONS, POSITIONS, [{'stock_id': '2330', 'shares': 1}])
    assert end['status'] == 'NOT_PUBLISHABLE' and end['reason'].startswith('END_POSITIONS')


def test_tolerance_scales_with_nav():
    nav = [row[:] for row in NAV]
    nav[1][1] += 0.009
    assert reconcile(1_000_000, nav, FILLS, ACTIONS, POSITIONS, [])['status'] == 'PUBLISHABLE'


def test_empty_nav_is_not_publishable():
    assert reconcile(1_000_000, [], [], [], [], [])['reason'] == 'NO_SESSIONS'


def test_action_terms_and_status_must_match_bookings():
    actions = deepcopy(ACTIONS)
    actions[0]['share_multiplier'] = 1
    assert reconcile(1_000_000, NAV, FILLS, actions, POSITIONS, [])['reason'] == 'ACTION_TERMS_MISMATCH'
    actions = deepcopy(ACTIONS)
    actions[0]['status'] = 'BOOKEDD'
    assert reconcile(1_000_000, NAV, FILLS, actions, POSITIONS, [])['reason'] == 'INVALID_ACTION_STATUS'


@pytest.mark.parametrize('invalid', [float('nan'), float('inf'), float('-inf')])
@pytest.mark.parametrize(('source', 'index', 'field', 'day'), [
    ('capital', None, None, None),
    ('nav', 1, 1, 'd2'),
    ('fills', 0, 'shares', 'd2'),
    ('fills', 0, 'cash_delta', 'd2'),
    ('fills', 0, 'raw_price', 'd2'),
    ('fills', 0, 'price', 'd2'),
    ('fills', 0, 'notional', 'd2'),
    ('fills', 0, 'fee', 'd2'),
    ('fills', 0, 'tax', 'd2'),
    ('fills', 0, 'slippage', 'd2'),
    ('fills', 0, 'cash_after', 'd2'),
    ('actions', 0, 'shares_before', 'd3'),
    ('actions', 0, 'shares_after', 'd3'),
    ('actions', 0, 'cash_credited', 'd3'),
    ('positions', 1, 'shares', 'd2'),
    ('positions', 1, 'value', 'd2'),
    ('positions', 2, 'value', 'd2'),
    ('end', 0, 'shares', 'd4'),
])
def test_nonfinite_accounting_values_are_not_publishable(source, index, field, day, invalid):
    data = {'capital': 1_000_000, 'nav': deepcopy(NAV), 'fills': deepcopy(FILLS),
            'actions': deepcopy(ACTIONS), 'positions': deepcopy(POSITIONS),
            'end': [{'stock_id': '2330', 'shares': 1}]}
    if source == 'capital':
        data[source] = invalid
    else:
        data[source][index][field] = invalid
    result = reconcile(data['capital'], data['nav'], data['fills'], data['actions'], data['positions'], data['end'])
    assert result['status'] == 'NOT_PUBLISHABLE'
    assert result['first_mismatch'] == day
    assert 'NONFINITE' in result['reason']


def test_invalid_cash_value_fails_closed_instead_of_raising():
    positions = deepcopy(POSITIONS)
    positions[0]['value'] = None
    result = reconcile(1_000_000, NAV, FILLS, ACTIONS, positions, [])
    assert result['status'] == 'NOT_PUBLISHABLE'
    assert result['first_mismatch'] == 'd1'
    assert 'NONFINITE' in result['reason']


@pytest.mark.parametrize('source', ['fills', 'actions', 'positions'])
def test_events_outside_nav_sessions_are_not_ignored(source):
    data = {'fills': deepcopy(FILLS), 'actions': deepcopy(ACTIONS), 'positions': deepcopy(POSITIONS)}
    row = deepcopy(data[source][0])
    row['date'] = 'd5'
    data[source].append(row)
    result = reconcile(1_000_000, NAV, data['fills'], data['actions'], data['positions'], [])
    assert result['status'] == 'NOT_PUBLISHABLE'
    assert result['first_mismatch'] == 'd5'
    assert 'OUTSIDE_NAV' in result['reason']


@pytest.mark.parametrize('nav', [NAV + [NAV[-1]], [NAV[0], NAV[2], NAV[1], NAV[3]]])
def test_nav_sessions_must_be_unique_and_chronological(nav):
    result = reconcile(1_000_000, nav, FILLS, ACTIONS, POSITIONS, [])
    assert result['status'] == 'NOT_PUBLISHABLE'
    assert 'NAV_SESSION_ORDER' in result['reason']


@pytest.mark.parametrize(('change', 'reason'), [
    ({'fill_id': 'b'}, 'DUPLICATE_FILL_ID'),
    ({'fill_id': ''}, 'INVALID_FILL_ID'),
    ({'side': 'INVALID'}, 'INVALID_FILL_SIDE'),
    ({'shares': 0}, 'INVALID_FILL_SHARES'),
    ({'shares': -1}, 'INVALID_FILL_SHARES'),
])
def test_invalid_fill_identity_side_and_quantity_fail_closed(change, reason):
    fills = deepcopy(FILLS)
    fills[1].update(change)
    result = reconcile(1_000_000, NAV, fills, ACTIONS, POSITIONS, [])
    assert result['status'] == 'NOT_PUBLISHABLE'
    assert result['first_mismatch'] == 'd4'
    assert reason in result['reason']
