import json
import pytest
import pandas as pd
from scripts.research_mode_inputs import build_raw_bars, build_research_universe, compute_day3_scores, convert_events, convert_reference_changes, load_development_events


def test_raw_bars_use_previous_session_and_ignore_oos_file(tmp_path):
    fields = ['證券代號', '證券名稱', '成交股數', '成交筆數', '成交金額', '開盤價', '最高價', '最低價', '收盤價']
    paths = []
    for day, price, amount in [('20250102', '10', '10000'), ('20250103', '11', '20000')]:
        path = tmp_path / f'MI_INDEX_{day}.json'
        path.write_text(json.dumps({'stat': 'OK', 'date': day, 'tables': [{'title': '每日收盤行情', 'fields': fields, 'data': [['2330', 'X', '1000', '10', amount, price, price, price, price]]}]}), encoding='utf-8')
        paths.append(path)
    future = tmp_path / 'MI_INDEX_20260102.json'
    future.write_text('must not be opened', encoding='utf-8')
    bars = build_raw_bars([*paths, future])['bars']
    assert set(bars) == {'2025-01-02', '2025-01-03'}
    assert bars['2025-01-03']['2330']['prior_amount'] == 10000
    assert bars['2025-01-03']['2330']['limit_up'] == 11
    assert bars['2025-01-03']['2330']['limit_source'] == 'COMPUTED'
    with pytest.raises(ValueError, match='OOS'):
        build_raw_bars(paths, end='2026-01-01')


def test_events_preserve_unknown_terms_and_never_accept_oos():
    rows = [{'ex_date': '2025-06-01', 'stock_id': '2330', 'kind': '息', 'pre_close': 100,
             'ex_reference': 98, 'dividend_reference': 98},
            {'ex_date': '2025-06-02', 'stock_id': '2317', 'kind': '權息', 'pre_close': 100,
             'ex_reference': 98, 'dividend_reference': 98}]
    result = convert_events(rows, 'a' * 64, '2025-12-31')
    assert result['actions'][0]['cash_per_share'] == 2
    assert result['actions'][0]['cash_pay_date'] == '2025-12-31'
    assert result['unresolved_events'][0]['missing_terms'] == ['cash_dividend', 'stock_dividend']
    missing = convert_events([{**rows[0], 'pre_close': None}], 'a' * 64, '2025-12-31')
    assert missing['unresolved_events'][0]['missing_terms'] == ['valid_reference_prices']
    with pytest.raises(ValueError, match='OOS'):
        convert_events([{**rows[0], 'ex_date': '2026-01-01'}], 'a' * 64, '2025-12-31')


def test_reference_change_requires_share_and_settlement_terms():
    row = {'ex_date': '2025-06-02', 'stock_id': '2330', 'kind': 'CAPITAL_REDUCTION'}
    result = convert_reference_changes([row], 'b' * 64, '2025-12-31')
    assert result[0]['missing_terms'] == ['share_count_change', 'settlement_terms']
    with pytest.raises(ValueError, match='OOS'):
        convert_reference_changes([{**row, 'ex_date': '2026-01-01'}], 'b' * 64, '2025-12-31')


def test_local_payload_filters_future_event_before_reading_its_fields(tmp_path):
    dividend = tmp_path / 'events.json'
    dividend.write_text(json.dumps({'stat': 'OK', 'data': [
        ['114年01月03日', '2330', 'X', '100', '98', '', '息', '', '', '', '98'],
        ['115年01月03日']]}), encoding='utf-8')
    result = load_development_events(dividend, {}, '2025-12-31')
    assert len(result['actions']) == 1 and result['actions'][0]['ex_date'] == '2025-01-03'


def test_universe_uses_only_available_calendar_and_previous_prices():
    days = [date.strftime('%Y-%m-%d') for date in pd.bdate_range('2024-01-01', periods=260)]
    bars = {day: {'2330': {'close': 20, 'amount': 25_000_000, 'volume': 1000}} for day in days}
    hashes = {day: 'a' * 64 for day in days}
    calendar = {day: {'execution_date': days[i + 1], 'available_at': day + 'T14:00:00+08:00', 'source_sha256': 'b' * 64}
                for i, day in enumerate(days[:-1])}
    first = build_research_universe({'bars': bars, 'source_sha256_by_date': hashes}, calendar)
    bars[days[255]]['2330']['amount'] = 0
    second = build_research_universe({'bars': bars, 'source_sha256_by_date': hashes}, calendar)
    assert first.loc[first.asof_date == days[254], 'member'].iloc[0] == second.loc[second.asof_date == days[254], 'member'].iloc[0]
    calendar[days[254]]['available_at'] = days[254] + 'T16:00:00+08:00'
    with pytest.raises(ValueError, match='CALENDAR_NOT_AVAILABLE'):
        build_research_universe({'bars': bars, 'source_sha256_by_date': hashes}, calendar)


def test_day3_raw_score_does_not_change_when_future_prices_change():
    from pathlib import Path
    days = [value.strftime('%Y-%m-%d') for value in pd.bdate_range('2023-01-02', periods=145)]
    bars = {day: {str(2300 + stock): {'open': 20 + stock + i / 100, 'high': 21 + stock + i / 100,
                                      'low': 19 + stock + i / 100, 'close': 20 + stock + i / 100,
                                      'volume': 1000 + stock * i, 'amount': 25_000_000 + stock * i}
                  for stock in range(4)} for i, day in enumerate(days)}
    market = {'price_basis': 'RAW', 'bars': bars}
    pool = Path('outputs/factor_validation/factor_validation_20260910_v1/accepted_factor_pool.csv')
    master = Path('outputs/factor_inventory/candidate_inventory_20260910_v1/candidate_factor_master.csv')
    first = compute_day3_scores(market, pool, master, 'A191_088__EQUAL', [days[130]], 'a' * 64)
    bars[days[140]]['2300']['close'] = 999
    second = compute_day3_scores(market, pool, master, 'A191_088__EQUAL', [days[130]], 'a' * 64)
    pd.testing.assert_frame_equal(first, second)
    assert len(first) == 4
