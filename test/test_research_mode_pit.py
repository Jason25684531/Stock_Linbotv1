import pytest
from pandas.testing import assert_frame_equal

from core.research.research_mode import apply_event_exclusions, event_excluded
from test_research_repair_targets import build, inputs
from test_research_repair_accounting import DAYS, example
from core.research.repair_accounting import UnresolvedCorporateAction, simulate


def test_future_event_does_not_change_selection():
    scores, universe = inputs()
    signals = {(r.asof_date, r.asset_id): r.signal_at for r in scores.itertuples()}
    event = {'stock_id': 'AAA', 'ex_date': '2025-09-08', 'source_sha256': 'd' * 64}
    assert_frame_equal(build(scores, universe), build(scores, apply_event_exclusions(universe, signals, [event])))
    assert not event_excluded('2025-09-08T13:30:00+08:00', event['ex_date'])
    assert event_excluded('2025-09-08T15:00:00+08:00', event['ex_date'])
    available_event = {**event, 'ex_date': '2025-09-05'}
    assert build(scores, apply_event_exclusions(universe, signals, [available_event])).asset_id.tolist() == ['BBB']


def test_unresolved_event_while_held_stops_without_result():
    data = example()
    data['actions'] = []
    event = {'ex_date': DAYS[1], 'stock_id': '2330', 'missing_terms': ['cash_dividend', 'rights_terms']}
    with pytest.raises(UnresolvedCorporateAction) as caught:
        simulate(**data, unresolved_events=[event])
    assert caught.value.details == {'status': 'NOT_COMPUTABLE', 'reason': 'UNRESOLVED_CORPORATE_ACTION_HELD',
                                    'date': DAYS[1], 'stock_id': '2330', 'holdings': 100,
                                    'missing_terms': event['missing_terms'],
                                    'stop_reason': 'Required corporate-action terms are unavailable.'}


def test_unresolved_event_without_holdings_does_not_stop():
    data = example()
    data['actions'] = []
    event = {'ex_date': DAYS[1], 'stock_id': '9999', 'missing_terms': ['rights_terms']}
    assert len(simulate(**data, unresolved_events=[event])['daily']) == len(DAYS)


def test_forfeit_policy_keeps_shares_logs_official_value_and_reconciles_independently():
    from core.research.repair_accounting import reconcile
    from core.research.research_mode import RESEARCH_ASSUMPTIONS_V3, RESEARCH_ASSUMPTIONS_V4, assumptions_sha, held_event_policy
    assert held_event_policy(RESEARCH_ASSUMPTIONS_V3) == 'STOP' and held_event_policy(RESEARCH_ASSUMPTIONS_V4) == 'FORFEIT_UNKNOWN_ENTITLEMENTS'
    assert assumptions_sha(RESEARCH_ASSUMPTIONS_V3) != assumptions_sha(RESEARCH_ASSUMPTIONS_V4)
    data = example()
    data['actions'] = []
    event = {'ex_date': DAYS[1], 'stock_id': '2330', 'missing_terms': ['cash_dividend', 'stock_dividend'], 'value_per_share': 2}
    result = simulate(**data, unresolved_events=[event], unresolved_policy='FORFEIT_UNKNOWN_ENTITLEMENTS')
    forfeits = [row for row in result['ledger'] if row['kind'] == 'ENTITLEMENT_FORFEITED']
    assert forfeits == [{'date': DAYS[1], 'kind': 'ENTITLEMENT_FORFEITED', 'stock_id': '2330', 'shares': 100,
                         'missing_terms': ['cash_dividend', 'stock_dividend'], 'source_event_date': None,
                         'value_per_share': 2, 'forfeited_value': 200.0}]
    assert [r['positions'].get('2330', 0) for r in result['daily']] == [100, 100, 60, 60, 60]  # shares unchanged, no cash credited
    assert all(r['cash_receivable'] == 0 for r in result['daily'])
    kwargs = dict(bars=data['bars'], orders=data['orders'], actions=[], action_coverage=data['action_coverage'],
                  cost=data['cost'], slippage=0)
    assert reconcile(10_000, result, unresolved_events=[event], unresolved_policy='FORFEIT_UNKNOWN_ENTITLEMENTS', **kwargs)['status'] == 'PASS'
    assert reconcile(10_000, result, unresolved_events=[event], **kwargs)['reason'] == 'UNRESOLVED_HELD_EVENT_NOT_STOPPED'
    assert reconcile(10_000, result, unresolved_events=[], unresolved_policy='FORFEIT_UNKNOWN_ENTITLEMENTS', **kwargs)['reason'] == 'FORFEIT_MISMATCH'
    reduction = {**event, 'missing_terms': ['share_count_change', 'settlement_terms']}
    with pytest.raises(UnresolvedCorporateAction) as caught:
        simulate(**data, unresolved_events=[reduction], unresolved_policy='FORFEIT_UNKNOWN_ENTITLEMENTS')
    assert caught.value.details['missing_terms'] == reduction['missing_terms']
    with pytest.raises(UnresolvedCorporateAction):
        simulate(**data, unresolved_events=[event])  # STOP remains the default


def test_v5_reference_change_exits_at_official_pre_close_and_tolerates_announced_suspension():
    from core.research.repair_accounting import reconcile
    from core.research.research_mode import RESEARCH_ASSUMPTIONS_V4, RESEARCH_ASSUMPTIONS_V5, reference_change_policy
    assert reference_change_policy(RESEARCH_ASSUMPTIONS_V4) == 'STOP' and reference_change_policy(RESEARCH_ASSUMPTIONS_V5) == 'FORCED_EXIT'
    data = example()
    data['orders'] = data['orders'][:1]
    days = ['2025-01-02', '2025-01-03', '2025-01-06', '2025-01-07', '2025-01-08', '2025-01-09', '2025-01-10', '2025-01-13']
    data['bars'] = {day: (data['bars'][DAYS[0]] if i == 0 else {'2330': dict(data['bars'][DAYS[0]]['2330'], open=12, close=12)} if i == 7 else {})
                    for i, day in enumerate(days)}
    data['action_coverage'] = {'2330': days}
    exit_action = {'stock_id': '2330', 'ex_date': days[7], 'type': 'reference_change_exit', 'pre_close': 10.0, 'reference_price': 12.0,
                   'reason': 'cash_return', 'source': 'synthetic_fixture', 'source_sha256': 'b' * 64}
    data['actions'] = [exit_action]
    result = simulate(**data, max_stale_sessions=5)  # six stale sessions are tolerated because the resumption is on file
    exits = [row for row in result['ledger'] if row['kind'] == 'FORCED_EXIT']
    assert exits == [{'date': days[7], 'kind': 'FORCED_EXIT', 'stock_id': '2330', 'shares': 100, 'price': 10.0, 'notional': 1000.0,
                      'fee': 10, 'tax': 20, 'reason': 'cash_return'}]
    assert result['daily'][-1]['positions'] == {} and result['daily'][-1]['cash'] == 8990 + 1000 - 10 - 20
    assert result['daily'][-2]['stale_marks'] == {'2330': days[0]}  # marked at the last official close while suspended
    kwargs = dict(bars=data['bars'], orders=data['orders'], actions=data['actions'], action_coverage=data['action_coverage'],
                  cost=data['cost'], slippage=0)
    assert reconcile(10_000, result, **kwargs)['status'] == 'PASS'
    tampered = {**result, 'ledger': [dict(row, fee=0) if row['kind'] == 'FORCED_EXIT' else row for row in result['ledger']]}
    assert reconcile(10_000, tampered, **kwargs)['reason'] == 'FORCED_EXIT_MISMATCH'
    data['actions'] = []
    with pytest.raises(UnresolvedCorporateAction):  # without the resumption on file the five-session rule still stops
        simulate(**data, max_stale_sessions=5)


def test_v6_second_action_with_uncredited_shares_uses_holdings_only_and_reconciles():
    from core.research.repair_accounting import DataBlocked, reconcile
    from core.research.research_mode import RESEARCH_ASSUMPTIONS_V5, RESEARCH_ASSUMPTIONS_V6, overlap_policy
    assert overlap_policy(RESEARCH_ASSUMPTIONS_V5) == 'STOP' and overlap_policy(RESEARCH_ASSUMPTIONS_V6) == 'HOLDINGS_ONLY'
    data = example()
    data['orders'] = data['orders'][:1]
    first = {**data['actions'][0], 'type': 'stock', 'cash_per_share': 0, 'stock_ratio': .1, 'cash_pay_date': None, 'stock_credit_date': DAYS[-1]}
    second = {**first, 'ex_date': DAYS[2]}
    data['actions'] = [first, second]
    with pytest.raises(DataBlocked, match='OVERLAPPING_STOCK_ENTITLEMENT'):
        simulate(**data, fractional_policy='DROP')
    result = simulate(**data, fractional_policy='DROP', overlap_policy='HOLDINGS_ONLY')
    actions = [row for row in result['ledger'] if row['kind'] == 'ACTION']
    assert [row['basis_shares'] for row in actions] == [100, 100] and [row['uncredited_shares'] for row in actions] == [0, 10]
    assert [row['shares'] for row in result['ledger'] if row['kind'] == 'STOCK_ENTITLEMENT'] == [10, 10]  # second entitlement ignores the 10 uncredited
    assert result['daily'][-1]['positions'] == {'2330': 120}
    kwargs = dict(bars=data['bars'], orders=data['orders'], actions=data['actions'], action_coverage=data['action_coverage'],
                  cost=data['cost'], slippage=0, fractional_policy='DROP')
    assert reconcile(10_000, result, overlap_policy='HOLDINGS_ONLY', **kwargs)['status'] == 'PASS'
    assert reconcile(10_000, result, **kwargs)['reason'] == 'OVERLAPPING_STOCK_ENTITLEMENT'


def test_long_suspension_stops_held_configuration_without_partial_nav():
    data = example()
    data['actions'] = []
    data['orders'] = data['orders'][:1]
    days = ['2025-01-02', '2025-01-03', '2025-01-06', '2025-01-07', '2025-01-08', '2025-01-09', '2025-01-10']
    data['bars'] = {day: (data['bars'][DAYS[0]] if i == 0 else {}) for i, day in enumerate(days)}
    data['action_coverage'] = {'2330': days}
    with pytest.raises(UnresolvedCorporateAction) as caught:
        simulate(**data, max_stale_sessions=5)
    assert caught.value.details['date'] == days[6]
    assert caught.value.details['missing_terms'] == ['suspension_or_delisting_status']
