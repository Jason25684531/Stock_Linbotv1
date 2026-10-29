"""The mask loop of the A191_132 corrected rerun on synthetic bars (no real data, no registered experiment is run)."""
import pandas as pd

from scripts import a191_corrected_rerun as rerun

DATES = pd.date_range('2023-01-02', periods=8, freq='B')


def bars_frame(rows):
    """rows: (stock, date index, open, high, low, volume, limit_up, limit_down)."""
    return pd.DataFrame([{'trade_date': DATES[i], 'stock_id': s, 'open': o, 'high': h, 'low': lo, 'volume': v, 'amount': (v or 0) * o if o == o else 0.0,
                          'limit_up': up, 'limit_down': dn} for s, i, o, h, lo, v, up, dn in rows])


def setup(locked_buy=True):
    close = pd.DataFrame({'A': 10.0, 'B': 20.0}, index=DATES)
    target = pd.DataFrame([{'asof_date': DATES[0], 'execution_date': DATES[1], 'asset_id': a, 'target_weight': 0.4, 'config_id': 'T'} for a in 'AB'])
    rows = []
    for i in range(len(DATES)):  # normal bars, A is locked at the limit-up open on the first execution date
        rows.append(('A', i, 10.0, 10.0 if (locked_buy and i == 1) else 10.5, 10.0 if (locked_buy and i == 1) else 9.5, 1_000_000, 10.0 if (locked_buy and i == 1) else 11.0, 9.0))
        rows.append(('B', i, 20.0, 20.5, 19.5, 1_000_000, 22.0, 18.0))
    events = pd.DataFrame(columns=['stock_id', 'ex_date', 'pre_ex_close', 'event_factor', 'range', 'ex_reference_price', 'action_type'])
    events['ex_date'] = pd.to_datetime(events.ex_date)
    reference = bars_frame(rows).assign(reference=lambda d: d.open, amount=lambda d: d.open * d.volume)
    return close, target, reference, events


def test_a_locked_buy_is_dropped_and_the_rerun_converges():
    close, target, reference, events = setup()
    result, orders, flags, rounds, dropped, converged = rerun.run_masked(close, target, 1, reference, events, 'T')
    assert converged and dropped == 1 and rounds == 2  # round 1 flags A, round 2 is clean
    assert set(orders.asset) == {'B'} and not flags.unfillable.any()
    assert result['fill_rule'] == 'DIAGNOSTIC_ONLY_MASK'


def test_without_a_lock_nothing_is_dropped_and_one_round_is_enough():
    close, target, reference, events = setup(locked_buy=False)
    _, orders, _, rounds, dropped, converged = rerun.run_masked(close, target, 1, reference, events, 'T')
    assert converged and rounds == 1 and dropped == 0 and set(orders.asset) == {'A', 'B'}


def test_a_missing_bar_is_unfillable():
    close, target, reference, events = setup(locked_buy=False)
    reference = reference[~((reference.stock_id == 'B') & (reference.trade_date == DATES[1]))]
    _, orders, _, _, dropped, converged = rerun.run_masked(close, target, 2, reference, events, 'T')
    assert converged and dropped == 1 and set(orders.asset) == {'A'}


def test_the_registered_scope_is_pinned():
    assert rerun.WINDOW_END == '2025-12-29' and rerun.HYPOTHESIS == 'A191_132:CORRECTED-FILL-12CFG'
    assert set(rerun.TRACKS.values()) == {'PROJECT_BASE', 'PROJECT_STRESS2X'} and rerun.MAX_ROUNDS >= 5
    manifest = pd.read_csv(rerun.MANIFEST)
    assert len(manifest[manifest.factor_set == 'A191_132']) == 12
