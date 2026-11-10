"""Trading calendar entry (design D4): observed data wins, schedule second, otherwise CALENDAR_UNVERIFIED. Temporary files only."""
import json
from datetime import date

from core.research import trading_calendar as tc
from core.research.sources.twse import REQUIRED_CLOSING_FIELDS


def _closures(tmp_path, closures=(), years=()):
    path = tmp_path / 'closures.json'
    path.write_text(json.dumps({'closures': [{'date': d, 'type': 'ANNUAL_HOLIDAY'} for d in closures], 'annual_schedule_years': list(years)}))
    return path


def _mi_index(folder, day, trading):
    payload = {'stat': 'OK', 'tables': [{'title': '每日收盤行情', 'fields': sorted(REQUIRED_CLOSING_FIELDS), 'data': [['x'] * len(REQUIRED_CLOSING_FIELDS)]}]} if trading else {'stat': '很抱歉，沒有符合條件的資料!'}
    folder.mkdir(exist_ok=True)
    (folder / f'MI_INDEX_{day}.json').write_text(json.dumps(payload, ensure_ascii=False), encoding='utf-8')


def test_unimported_year_is_unverified_and_closures_win(tmp_path):
    path = _closures(tmp_path, closures=['2026-10-23'])
    empty = tmp_path / 'none'
    assert tc.status('2026-11-10', folders=[empty], closures=path) == 'CALENDAR_UNVERIFIED'
    assert tc.status('2026-10-23', folders=[empty], closures=path) == 'CLOSED_SCHEDULED'
    assert tc.status('2026-11-08', folders=[empty], closures=path) == 'CLOSED_SCHEDULED'  # Sunday
    assert tc.status('2026-11-10', folders=[empty], closures=_closures(tmp_path, years=[2026])) == 'EXPECTED_OPEN'


def test_observed_exchange_data(tmp_path):
    folder = tmp_path / 'mi'
    _mi_index(folder, '20261109', True)
    _mi_index(folder, '20261106', False)
    path = _closures(tmp_path)
    assert tc.status('2026-11-09', folders=[folder], closures=path) == 'OPEN_OBSERVED'
    assert tc.status('2026-11-06', folders=[folder], closures=path) == 'CLOSED_OBSERVED'
    assert tc.status('2026-11-09', folders=[folder], closures=_closures(tmp_path, closures=['2026-11-09'])) == 'CONFLICT'
    assert tc.expected_trade_date(date(2026, 11, 8), folders=[folder], closures=path) == (date(2026, 11, 5), 'CALENDAR_UNVERIFIED')
