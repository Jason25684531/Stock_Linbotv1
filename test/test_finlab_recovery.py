from scripts.finlab_recovery import reconcile_event


def ann(cash=0.0, stock_ntd=0.0, rights_ratio_pct=0.0, rights_price=0.0, par='新台幣 10.0000元'):
    return {'cash': cash, 'stock_ntd': stock_ntd, 'rights_ratio_pct': rights_ratio_pct, 'rights_price': rights_price, 'par': par}


def test_a_mixed_event_is_verified_only_when_both_official_reference_prices_are_reproduced():
    # pre 100, cash 2, stock 1 NTD per share at par 10 -> ratio 0.1; dividend reference (100-2)/1.1 = 89.09
    assert reconcile_event(100.0, 89.09, 89.09, [ann(cash=2.0, stock_ntd=1.0)])['status'] == 'VERIFIED'
    assert reconcile_event(100.0, 89.09, 89.09, [ann(cash=3.0, stock_ntd=1.0)])['status'] == 'CONFLICT'


def test_rights_offering_terms_must_reproduce_the_ex_reference():
    # (100 - 0 + 50*0.2) / (1 + 0 + 0.2) = 91.67; dividend reference stays 100
    assert reconcile_event(100.0, 91.67, 100.0, [ann(rights_ratio_pct=20.0, rights_price=50.0)])['status'] == 'VERIFIED'
    assert reconcile_event(100.0, 91.67, 100.0, [ann(rights_ratio_pct=10.0, rights_price=50.0)])['status'] == 'CONFLICT'


def test_missing_unknown_par_and_disagreeing_announcements_are_never_resolved_by_choice():
    assert reconcile_event(100.0, 89.09, 89.09, [])['status'] == 'MISSING'
    assert reconcile_event(100.0, 89.09, 89.09, [ann(cash=2.0, stock_ntd=1.0, par='無面額')])['status'] == 'UNKNOWN'
    two = [ann(cash=2.0, stock_ntd=1.0), ann(cash=2.5, stock_ntd=1.0)]
    assert reconcile_event(100.0, 89.09, 89.09, two)['status'] == 'UNKNOWN'  # never picks the version that fits


def test_the_last_announcement_before_the_ex_date_is_used_never_the_one_that_fits():
    from scripts.finlab_recovery import known_at
    early = {**ann(rights_ratio_pct=20.0, rights_price=0.0), 'announced': '2023-01-04 15:21:25'}
    final = {**ann(rights_ratio_pct=20.0, rights_price=50.0), 'announced': '2023-01-12 17:03:42'}
    after = {**ann(rights_ratio_pct=10.0, rights_price=50.0), 'announced': '2023-02-03 16:32:06'}  # published after the ex-date: unknown on that day
    assert known_at([early, final, after], '2023-01-30') == [final]
    assert known_at([after], '2023-01-30') == []
    same_time = [{**final}, {**after, 'announced': final['announced']}]
    assert len(known_at(same_time, '2023-01-30')) == 2  # two versions at one time stay ambiguous (reconcile_event -> UNKNOWN)
