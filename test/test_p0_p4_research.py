from scripts.p0_p4_research import split_event


def test_only_pure_cash_or_pure_stock_events_are_split_and_mixed_events_are_never_guessed():
    cash = split_event('息', 76.30, 71.12, 71.12)  # TWT49U 2636 2023-01-04: dividend reference = pre close - cash
    assert cash['status'] == 'SPLIT_PROVEN' and abs(cash['cash'] - 5.18) < 1e-9 and cash['stock_ratio'] == 0
    stock = split_event('權', 100.0, 90.90909, 90.90909)  # 10 % stock dividend, no rights offering
    assert stock['status'] == 'SPLIT_PROVEN' and stock['cash'] == 0 and abs(stock['stock_ratio'] - 0.1) < 1e-6
    offering = split_event('權', 15.40, 14.97, 15.40)  # 2243 2023-01-30: the gap between the references is a cash capital increase
    assert offering['rights_offering'] and offering['stock_ratio'] == 0 and offering['status'] == 'SPLIT_PROVEN_RIGHTS_OFFERING_TERMS_UNKNOWN'
    mixed = split_event('權息', 940.0, 849.1, 849.1)
    assert mixed['cash'] is None and mixed['stock_ratio'] is None and mixed['status'].startswith('NOT_IDENTIFIABLE')


def test_fill_rule_diff_status_precedence_and_columns():
    import pandas as pd
    from scripts.fill_feasibility_impact import diff_status
    cols = ['no_bar', 'no_trade', 'lock_buy', 'lock_sell', 'over_5pct']
    rows = [dict(zip(cols, [a == i for a in range(5)])) for i in range(5)] + [dict.fromkeys(cols, False), {**dict.fromkeys(cols, False), 'no_bar': True, 'over_5pct': True}]
    flags = pd.DataFrame(rows).assign(config_id='C', date='2024-01-02', asset='A', side='buy', notional=1.0)
    out = diff_status(flags)
    assert list(out.columns) == ['config_id', 'date', 'asset', 'side', 'notional', 'old_status', 'new_status', 'label']
    assert out.new_status.tolist() == ['REJECTED_NO_BAR', 'REJECTED_NO_TRADE', 'REJECTED_LOCKED_LIMIT_UP', 'REJECTED_LOCKED_LIMIT_DOWN', 'PARTIAL_PARTICIPATION_CAP', 'FILLED', 'REJECTED_NO_BAR']
    assert (out.old_status == 'FILLED').all() and (out.label == 'DIAGNOSTIC_ONLY').all()
