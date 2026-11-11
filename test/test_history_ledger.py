"""core.research.history.ledger: official event terms from TWSE reference prices, identities, booking order (synthetic rows plus real TWT49U lines)."""
import pandas as pd
import pytest

from core.research.history import ledger

VERSIONS = {'TWT49U_a.json': 'sha-a', 'TWTAUU_a.json': 'sha-b', 'TWTB8U_a.json': 'sha-c'}
CASH, STOCK, BOTH = '息', '權', '權息'
RETURN, LOSS = '退還股款', '彌補虧損'


def events(*rows):
    return pd.DataFrame([{'ex_date': pd.Timestamp(d), 'stock_id': s, 'action_type': t, 'pre_ex_close': p0, 'ex_reference_price': ref, 'event_factor': ref / p0 if p0 else None,
                          'value': p0 - ref if p0 and ref else None, 'source_file': 'TWT49U_a.json', 'prior_session_close': p0} for d, s, t, p0, ref in rows])


def capital(*rows):
    return pd.DataFrame([{'date': pd.Timestamp(d), 'stock_id': s, 'kind': k, 'pre_close': p0, 'reference': ref, 'reason': r,
                          'source_file': 'TWTAUU_a.json' if k == 'CAPITAL_REDUCTION' else 'TWTB8U_a.json'} for d, s, k, p0, ref, r in rows])


EMPTY_CAPITAL = capital().reindex(columns=['date', 'stock_id', 'kind', 'pre_close', 'reference', 'reason', 'source_file'])
EMPTY_EVENTS = events().reindex(columns=['ex_date', 'stock_id', 'action_type', 'pre_ex_close', 'ex_reference_price', 'event_factor', 'value', 'source_file', 'prior_session_close'])


def test_cash_and_stock_only_terms_are_exact_from_the_official_reference_price():
    frame = ledger.build(events(('2015-01-05', '2353', STOCK, 21.35, 21.02), ('2024-01-03', '1101', CASH, 10.0, 9.5)), EMPTY_CAPITAL, VERSIONS)
    stock, cash = frame.set_index('stock_id').loc['2353'], frame.set_index('stock_id').loc['1101']
    assert stock.kind == 'STOCK_DIVIDEND' and stock.share_multiplier == pytest.approx(21.35 / 21.02, abs=1e-5) and stock.cash_per_share == 0
    assert cash.kind == 'CASH_DIVIDEND' and cash.cash_per_share == pytest.approx(0.5) and cash.share_multiplier == 1.0 and cash.price_factor == pytest.approx(0.95)
    assert set(frame.terms_status) == {'REFERENCE_DERIVED'}


def test_real_1808_row_falls_back_to_assumed_stock_without_an_announcement():
    frame = ledger.build(events(('2024-09-26', '1808', BOTH, 119.50, 52.95)), EMPTY_CAPITAL, VERSIONS)
    row = frame.iloc[0]
    assert row.kind == 'CASH_AND_STOCK_DIVIDEND' and row.terms_status == 'ASSUMED_STOCK'
    assert row.share_multiplier == pytest.approx(119.5 / 52.95, abs=1e-5) and row.cash_per_share == 0 and row.price_factor == pytest.approx(52.95 / 119.5)


def test_mixed_terms_use_the_announcement_only_when_it_reproduces_the_reference_price():
    c = ledger.ANNOUNCEMENT_COLUMNS
    good = pd.DataFrame([{'stock_id': '1808', c['announced']: '2024-08-01', c['ex_rights']: '2024-09-26', c['ex_dividend']: '2024-09-26',
                          c['stock_earnings']: 12.0, c['stock_reserve']: 0.0, c['cash_earnings']: 3.0, c['cash_reserve']: 0.0}])
    frame = ledger.build(events(('2024-09-26', '1808', BOTH, 119.50, 52.95)), EMPTY_CAPITAL, VERSIONS, good)
    row = frame.iloc[0]
    assert row.terms_status == 'RECONCILED_FINLAB' and row.cash_per_share == 3.0 and row.share_multiplier == pytest.approx(2.2)  # (119.5 - 3) / 2.2 = 52.95
    bad = good.assign(**{c['cash_earnings']: 20.0})
    assert ledger.build(events(('2024-09-26', '1808', BOTH, 119.50, 52.95)), EMPTY_CAPITAL, VERSIONS, bad).iloc[0].terms_status == 'ASSUMED_STOCK'


def test_capital_reductions_and_par_value_changes_from_real_twse_rows():
    frame = ledger.build(EMPTY_EVENTS, capital(('2015-01-23', '3040', 'CAPITAL_REDUCTION', 31.90, 41.28, RETURN), ('2015-03-20', '3536', 'CAPITAL_REDUCTION', 6.58, 13.33, LOSS),
                                               ('2020-08-17', '8070', 'PAR_VALUE_CHANGE', 190.0, 19.0, ''), ('2016-01-04', '9999', 'CAPITAL_REDUCTION', 20.0, 25.0, 'other')), VERSIONS)
    by = frame.set_index('stock_id')
    assert by.loc['3040'].kind == 'CAPITAL_REDUCTION_CASH' and by.loc['3040'].share_multiplier == pytest.approx(0.7, abs=2e-4)
    assert by.loc['3040'].cash_per_share == pytest.approx(3.0, abs=2e-3) and by.loc['3040'].terms_status == 'REFERENCE_DERIVED'
    assert by.loc['3536'].kind == 'CAPITAL_REDUCTION_LOSS' and by.loc['3536'].share_multiplier == pytest.approx(6.58 / 13.33, abs=1e-5) and by.loc['3536'].cash_per_share == 0
    assert by.loc['8070'].kind == 'PAR_VALUE_CHANGE' and by.loc['8070'].share_multiplier == 10.0 and by.loc['8070'].price_factor == pytest.approx(0.1)
    assert by.loc['9999'].terms_status == 'UNRESOLVED' and by.loc['9999'].share_multiplier == 1.0 and by.loc['9999'].cash_per_share == 0


def test_non_positive_prices_and_par_reference_are_unresolved():
    frame = ledger.build(events(('2024-01-03', '1101', CASH, 0.0, 9.5)), capital(('2016-01-04', '2222', 'CAPITAL_REDUCTION', 12.0, 10.0, RETURN)), VERSIONS)
    assert set(frame.terms_status) == {'UNRESOLVED'}


def test_the_same_official_event_from_two_files_is_one_row_with_both_sources():
    one = events(('2024-01-03', '1101', CASH, 10.0, 9.5))
    other = one.assign(source_file='TWT49U_b.json', pre_ex_close=10.00000)
    frame = ledger.build(pd.concat([one, other], ignore_index=True), EMPTY_CAPITAL, VERSIONS | {'TWT49U_b.json': 'sha-b2'})
    assert len(frame) == 1 and frame.iloc[0].sources == 'TWT49U_a.json:sha-a;TWT49U_b.json:sha-b2' and len(frame.iloc[0].event_id) == 64
    reductions = capital(('2023-02-17', '2321', 'CAPITAL_REDUCTION', 6.38, 19.93, LOSS))
    overlap = pd.concat([reductions, reductions.assign(source_file='TWTAUU_overlap.json')], ignore_index=True)
    assert len(ledger.build(EMPTY_EVENTS, overlap, VERSIONS)) == 1  # overlapping TWTAUU windows never book a reduction twice


def test_conflicting_terms_for_one_stock_day_and_kind_stop_the_build():
    republished = pd.concat([events(('2024-01-03', '1101', CASH, 10.0, 9.5)), events(('2024-01-03', '1101', CASH, 10.0, 9.4))], ignore_index=True)
    with pytest.raises(ValueError, match='CONFLICTING_EVENT_TERMS: 1101:2024-01-03:CASH_DIVIDEND'):
        ledger.build(republished, EMPTY_CAPITAL, VERSIONS)


def test_full_cash_return_is_a_zero_multiplier_cash_out():
    row = ledger.build(EMPTY_EVENTS, capital(('2016-01-04', '2222', 'CAPITAL_REDUCTION', 10.0, 20.0, RETURN)), VERSIONS).iloc[0]
    assert row.terms_status == 'REFERENCE_DERIVED' and row.share_multiplier == 0 and row.cash_per_share == pytest.approx(10.0)


def test_an_announcement_published_after_the_ex_date_is_never_used():
    c = ledger.ANNOUNCEMENT_COLUMNS
    late = pd.DataFrame([{'stock_id': '1808', c['announced']: '2024-10-10', c['ex_rights']: '2024-09-26', c['ex_dividend']: '2024-09-26',
                          c['stock_earnings']: 12.0, c['stock_reserve']: 0.0, c['cash_earnings']: 3.0, c['cash_reserve']: 0.0}])
    assert ledger.build(events(('2024-09-26', '1808', BOTH, 119.50, 52.95)), EMPTY_CAPITAL, VERSIONS, late).iloc[0].terms_status == 'ASSUMED_STOCK'


def test_engine_view_books_dividends_before_capital_changes_on_the_same_day():
    frame = ledger.build(events(('2024-01-03', '1101', CASH, 10.0, 9.5)), capital(('2024-01-03', '1101', 'CAPITAL_REDUCTION', 9.5, 19.0, LOSS)), VERSIONS)
    view = ledger.to_engine(frame)
    assert list(view) == ['2024-01-03'] and [r['kind'] for r in view['2024-01-03']] == ['CASH_DIVIDEND', 'CAPITAL_REDUCTION_LOSS']
    assert ledger.version(frame) != ledger.version(frame.iloc[:1]) and ledger.summary(frame)['rows'] == 2


def test_snapshot_joined_sources_keep_every_file_and_version():
    row = events(('2024-01-03', '1101', CASH, 10.0, 9.5)).assign(source_file='TWT49U_a.json;TWT49U_b.json')
    frame = ledger.build(row, EMPTY_CAPITAL, VERSIONS | {'TWT49U_b.json': 'sha-b2'})
    assert frame.iloc[0].sources == 'TWT49U_a.json:sha-a;TWT49U_b.json:sha-b2' and frame.iloc[0].source_version == 'sha-a'


def test_rounded_reference_prices_use_exact_announced_terms_when_available():
    """Codex round 3: a 10% stock dividend at pre-close 100 is quoted at 90.90; 10,000 shares must become 11,000, not 11,001."""
    c = ledger.ANNOUNCEMENT_COLUMNS
    stock_only = events(('2024-07-01', '2330', STOCK, 100.0, 90.90))
    derived = ledger.build(stock_only, EMPTY_CAPITAL, VERSIONS).iloc[0]
    assert derived.terms_status == 'REFERENCE_DERIVED' and int(10_000 * derived.share_multiplier) == 11_001  # the bounded rounding error, labelled
    notice = pd.DataFrame([{'stock_id': '2330', c['announced']: '2024-06-01', c['ex_rights']: '2024-07-01', c['ex_dividend']: None,
                            c['stock_earnings']: 1.0, c['stock_reserve']: 0.0, c['cash_earnings']: 0.0, c['cash_reserve']: 0.0}])
    exact = ledger.build(stock_only, EMPTY_CAPITAL, VERSIONS, notice).iloc[0]
    assert exact.terms_status == 'RECONCILED_FINLAB' and int(10_000 * exact.share_multiplier + 1e-9) == 11_000
    cash_notice = pd.DataFrame([{'stock_id': '1101', c['announced']: '2023-12-01', c['ex_rights']: None, c['ex_dividend']: '2024-01-03',
                                 c['stock_earnings']: 0.0, c['stock_reserve']: 0.0, c['cash_earnings']: 0.503, c['cash_reserve']: 0.0}])
    cash = ledger.build(events(('2024-01-03', '1101', CASH, 10.0, 9.5)), EMPTY_CAPITAL, VERSIONS, cash_notice).iloc[0]
    assert cash.terms_status == 'RECONCILED_FINLAB' and cash.cash_per_share == 0.503
    wrong_type = cash_notice.assign(**{c['stock_earnings']: 1.0})  # a notice of another event type is never used
    assert ledger.build(events(('2024-01-03', '1101', CASH, 10.0, 9.5)), EMPTY_CAPITAL, VERSIONS, wrong_type).iloc[0].terms_status == 'REFERENCE_DERIVED'


def test_a_later_announcement_of_another_type_never_hides_a_matching_one():
    """Final review: an earlier 10% stock notice matches 90.90; a later cash-only notice (also before the ex-date) must not override it."""
    c = ledger.ANNOUNCEMENT_COLUMNS
    row = lambda announced, stock, cash: {'stock_id': '2330', c['announced']: announced, c['ex_rights']: '2024-07-01', c['ex_dividend']: '2024-07-01',
                                          c['stock_earnings']: stock, c['stock_reserve']: 0.0, c['cash_earnings']: cash, c['cash_reserve']: 0.0}
    notices = pd.DataFrame([row('2024-05-01', 1.0, 0.0), row('2024-06-01', 0.0, 2.0)])
    exact = ledger.build(events(('2024-07-01', '2330', STOCK, 100.0, 90.90)), EMPTY_CAPITAL, VERSIONS, notices).iloc[0]
    assert exact.terms_status == 'RECONCILED_FINLAB' and int(10_000 * exact.share_multiplier + 1e-9) == 11_000
