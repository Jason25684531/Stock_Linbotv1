"""TWSE_ONLY ingestion (design D3): endpoints without TPEx, closure evidence, official reconciliation, ingestion pre-check. No network."""
import json
from datetime import date
from pathlib import Path

import pandas as pd
import pytest

from services import market_data_update as mdu

ROOT = Path(__file__).resolve().parents[1]
CACHE = ROOT / 'artifacts/official_data_recovery_cache/mi_index'


def _load(day):
    return json.loads((CACHE / f'MI_INDEX_{day}.json').read_text(encoding='utf-8'))


def test_twse_only_endpoints_never_name_tpex():
    for name in ('prices_volume', 'financials', 'monthly_revenue'):
        specs = mdu.endpoints(name, '2026-11-05', 'TWSE_ONLY')
        assert specs and not any('restricted' in s or 'tpex' in s['url'] or '/otc/' in s['url'] or "'otc'" in str(s.get('data')) for s in specs)
    assert any('restricted' in s for s in mdu.endpoints('prices_volume', '2026-11-05', 'ALL'))  # ALL keeps the restriction


def test_market_scope_resolution(monkeypatch):
    monkeypatch.delenv('STOCK_LINBOT_MARKET_SCOPE', raising=False)
    monkeypatch.setattr(mdu, '_market', lambda: 'ALL')
    assert mdu.resolve_market_scope() == 'TWSE_ONLY'  # decision.md D2: 2026 runs TWSE_ONLY unless ALL is set explicitly (web manual update)
    monkeypatch.setenv('STOCK_LINBOT_MARKET_SCOPE', 'ALL')
    assert mdu.resolve_market_scope() == 'ALL'
    assert mdu.resolve_market_scope('ALL') == 'ALL'
    with pytest.raises(ValueError):
        mdu.resolve_market_scope('TPEX_EVERYTHING')


def test_closure_needs_an_explicit_official_no_data_reply_for_a_past_day():
    closed = _load('20261023')
    assert mdu.classify_mi_index(closed, '2026-10-23', today=date(2026, 11, 9)) == 'CLOSED_OBSERVED'
    assert mdu.classify_mi_index(closed, '2026-11-09', today=date(2026, 11, 9)) == 'NOT_YET_PUBLISHED'
    assert mdu.classify_mi_index({'stat': '系統忙碌中，請稍後再試！'}, '2026-10-23', today=date(2026, 11, 9)) == 'ANOMALY'
    assert mdu.classify_mi_index({}, '2026-10-23', today=date(2026, 11, 9)) == 'ANOMALY'
    assert mdu.classify_mi_index(_load('20261022'), '2026-10-22', today=date(2026, 11, 9)) == 'TRADING'


def test_reconciliation_against_the_official_total_row():
    payload = _load('20261022')
    result = mdu.reconcile_mi_index(payload)
    assert result['status'] == 'MATCH'
    assert result['published']['shares'] == result['computed']['shares'] == 11999249506  # 2026-10-22 總計(1~15) 成交股數
    assert result['published']['value'] == 933437473560 and 0 < result['value_residual'] < 1e-5
    tampered = json.loads(json.dumps(payload))
    quotes = next(t for t in tampered['tables'] if '每日收盤行情' in (t.get('title') or ''))
    quotes['data'][0][4] = '1'
    assert mdu.reconcile_mi_index(tampered)['status'] == 'MISMATCH'
    missing = {**payload, 'tables': [t for t in payload['tables'] if '大盤統計' not in (t.get('title') or '')]}
    assert mdu.reconcile_mi_index(missing)['status'] == 'UNAVAILABLE'


def test_twse_only_prices_from_cached_official_replies(tmp_path, monkeypatch):
    from core.crawlers import chip_data_scraper
    from services.mcp import server

    class Reply:
        def __init__(self, payload):
            self.payload = payload

        def json(self):
            return self.payload

        def raise_for_status(self):
            return None

    request = lambda method, url, **kw: Reply(_load(url.split('date=')[1][:8] if 'date=' in url else kw['params']['date']))
    monkeypatch.setattr(server.public_data, 'request', request)
    monkeypatch.setattr(server, 'flow_frame', lambda payload, d: pd.DataFrame())
    monkeypatch.setattr(chip_data_scraper, 'fetch_margin_balance_twse', lambda d, **k: pd.DataFrame())
    payload = mdu.twse_only_prices('2026-10-22', today=date(2026, 11, 9), request=request, closures=tmp_path / 'closures.json')
    assert payload['reconciliation']['status'] == 'MATCH' and payload['universe'] == 'TWSE_LISTED_ONLY' and payload['tpex'] == 'NOT_COVERED'
    frame = payload['frame']
    assert len(frame) > 900 and set(frame['trade_date']) == {'2026-10-22'} and frame['stock_id'].str.fullmatch(r'\d{4}|00\w+').all()
    closed = mdu.twse_only_prices('2026-10-23', today=date(2026, 11, 9), request=request, closures=tmp_path / 'closures.json')
    assert closed['status'] == 'CLOSED_OBSERVED'
    assert json.loads((tmp_path / 'closures.json').read_text(encoding='utf-8'))['2026-10-23']['sha256']


def test_precheck_uses_the_ingestion_authority_and_sends_nothing_without_a_grant():
    with pytest.raises(mdu.EndpointBlocked) as blocked:
        mdu.precheck('prices_volume', '2026-11-05', market='TWSE_ONLY')
    assert blocked.value.reasons and all('INGESTION_DISABLED' in r for r in blocked.value.reasons)


def test_guarded_fetches_inside_the_ingestion_scope_and_checks_the_research_budget(monkeypatch):
    from core.runtime import public_data, research_budget
    seen = []
    run = mdu.guarded('prices_volume', lambda day: seen.append(public_data._INGESTION.get()) or {'rows': 0},
                      check=lambda name, day: [], scope='TWSE_ONLY')
    assert run('2026-11-05') == {'rows': 0} and seen == [('prices_volume', 'TWSE_ONLY')]
    assert public_data._INGESTION.get() is None
    rows = iter([[], [{'id': 1, 'request_key': 'k'}]])
    monkeypatch.setattr(research_budget, 'audit_rows', lambda: next(rows))
    assert mdu.guarded('prices_volume', lambda day: {'rows': 0}, check=lambda n, d: [], scope='TWSE_ONLY')('2026-11-05')['status'] \
        == 'SOURCE_SCOPE_MISMATCH'


def test_service_without_grant_blocks_every_network_dataset_with_zero_http(tmp_path, monkeypatch):
    from core.runtime import public_data
    monkeypatch.setattr(public_data, '_send', lambda *a, **k: pytest.fail('no HTTP without a trusted ingestion grant'))
    service = mdu.MarketDataUpdateService(root=tmp_path, today='2026-11-06', market_scope='TWSE_ONLY')
    record = service.run('manual', dates=['2026-11-05'], datasets=['prices_volume', 'financials', 'monthly_revenue', 'corporate_actions'])
    assert record['market_scope'] == 'TWSE_ONLY' and record['universe'] == 'TWSE_LISTED_ONLY'
    for result in record['results']:
        assert result['status'] == 'IMPLEMENTED_BUT_AUTH_BLOCKED', result
        assert all('INGESTION_DISABLED' in r for r in result['blocked_reason'])


def test_twse_only_financials_fetch_listed_companies_in_process(monkeypatch):
    from services.mcp import server
    asked = []
    raw = pd.DataFrame({'stock_id': ['1101', '2330'], 'revenue': [10, 20], 'rd_expense': [0, 1], 'operating_expense': [1, 2],
                        'operating_profit': [3, 4], 'eps': [0.5, 9.9]})
    monkeypatch.setattr(server, 'fetch_historical_financial_statements', lambda year, quarter, market: asked.append((year, quarter, market)) or raw)
    payload = mdu.twse_only_financials('2026-11-20')
    assert asked == [(2026, 3, 'TWSE')]  # ROC 115 Q3 -> 2026 Q3, listed (sii) only
    frame = payload['frame']
    assert set(frame['year']) == {2026} and set(frame['quarter']) == {3} and frame['stock_id'].tolist() == ['1101', '2330']


def test_a_cache_served_reply_is_labelled_verified_cache(tmp_path, monkeypatch):
    from core.crawlers import chip_data_scraper
    from services.mcp import server

    class Reply:
        research_cache_hit = True

        def json(self):
            return _load('20261022')
    monkeypatch.setattr(server, 'flow_frame', lambda payload, d: pd.DataFrame())
    monkeypatch.setattr(chip_data_scraper, 'fetch_margin_balance_twse', lambda d, **k: pd.DataFrame())
    payload = mdu.twse_only_prices('2026-10-22', today=date(2026, 11, 9), request=lambda *a, **k: Reply(), closures=tmp_path / 'c.json')
    assert payload['source'] == 'VERIFIED_CACHE' and payload['latest_trade_date'] == '2026-10-22'  # freshness is still the cached session


def test_mi_margn_grouped_duplicate_fields_are_accepted_and_parsed_by_group(monkeypatch):
    """MI_MARGN repeats 今日餘額 under the 融資 and 融券 groups; the groups disambiguate them (official reply 2022-01-03)."""
    import requests
    from core.crawlers import chip_data_scraper as chips
    from core.runtime import public_data
    body = (ROOT / 'artifacts/official_chip_cache/twse/MI_MARGN_20220103.json').read_bytes()
    reply = requests.Response()
    reply.status_code, reply._content = 200, body
    url = 'https://www.twse.com.tw/rwd/zh/marginTrading/MI_MARGN'
    public_data.verify_response(public_data.context(url, params={'date': '20220103', 'selectType': 'ALL', 'response': 'json'}), reply)
    monkeypatch.setattr(chips.time, 'sleep', lambda s: None)
    monkeypatch.setattr(chips.public_data, 'request', lambda *a, **k: reply)
    frame = chips.fetch_margin_balance_twse('2022-01-03').set_index('stock_id')
    raw = {r[0].strip(): r for r in json.loads(body)['tables'][1]['data']}['2330']
    assert frame.loc['2330', 'margin_balance'] == float(raw[6].replace(',', ''))
    assert frame.loc['2330', 'short_balance'] == float(raw[12].replace(',', ''))
    assert '00631L' in frame.index  # letter-suffixed ETFs are kept by clean_market_frame, so their margin must be kept too


def test_twt49u_no_event_reply_is_a_documented_answer_not_a_failure():
    """A day without ex-rights events returns stat 很抱歉，沒有符合條件的資料! and no fields; any other non-OK stat still fails."""
    import requests
    from core.runtime import public_data
    ctx = public_data.context('https://www.twse.com.tw/rwd/zh/exRight/TWT49U',
                              params={'startDate': '20261030', 'endDate': '20261030', 'response': 'json'})

    def reply(payload):
        r = requests.Response()
        r.status_code, r._content = 200, json.dumps(payload, ensure_ascii=False).encode('utf-8')
        return r
    public_data.verify_response(ctx, reply({'stat': '很抱歉，沒有符合條件的資料!'}))
    with pytest.raises(ValueError):
        public_data.verify_response(ctx, reply({'stat': 'busy'}))


def test_mops_header_split_by_a_line_break_is_still_the_expected_table():
    """t163sb04 writes the header as 公司<br>代號 (real reply 2026-11-09, outputs/one_shot_20261109/diag); a page without the table still fails."""
    import requests
    from core.runtime import public_data
    ctx = public_data.context('https://mopsov.twse.com.tw/mops/web/ajax_t163sb04', method='POST',
                              data={'encodeURIComponent': '1', 'step': '1', 'firstin': '1', 'off': '1', 'TYPEK': 'sii', 'year': '115', 'season': '02'})

    def reply(html):
        r = requests.Response()
        r.status_code, r._content = 200, html.encode('utf-8')
        return r
    public_data.verify_response(ctx, reply("<table><tr><th>公司<br>代號</th><th>營業收入</th></tr><tr><td>1101</td><td>1</td></tr></table>"))
    with pytest.raises(ValueError):
        public_data.verify_response(ctx, reply('<html>查無資料</html>'))


def test_flow_numbers_with_thousands_separators_survive_cleaning_and_missing_stays_null():
    """T86 sends -21,603,811 (2330, 2026-11-04); before the fix only values under 1,000 survived. A missing flow is NULL, never 0."""
    import jobs.update_database as ud
    frame = pd.DataFrame({'stock_id': ['2330', '2317'], 'close_price': ['2,260.00', '240.00'], 'volume': ['53,879,371', '1'],
                          'foreign_buy': ['-21,603,811', None], 'trust_buy': ['3,715,164', '512'], 'dealer_buy': [None, None],
                          'trade_date': ['2026-11-04', '2026-11-04']})
    out = ud.clean_market_frame(frame, '2026-11-04').set_index('stock_id')
    assert out.loc['2330', 'foreign_buy'] == -21603811 and out.loc['2330', 'trust_buy'] == 3715164
    assert out.loc['2317', 'foreign_buy'] is None and out.loc['2317', 'trust_buy'] == 512


def test_a_new_year_imports_the_official_calendar_once(tmp_path):
    """D5 + Owner brief 2026-11-09: when today's year has no imported schedule, the daily run fetches it once; never again after."""
    closures = tmp_path / 'closures.json'
    calls = []

    def fetch(out_dir, year, path):
        calls.append(year)
        path.write_text(json.dumps({'closures': [], 'annual_schedule_years': [int(year)]}), encoding='utf-8')
        return {'status': 'IMPORTED', 'closures_added': 0}
    assert mdu.ensure_calendar_year(2027, tmp_path, closures=closures, fetch=fetch)['status'] == 'IMPORTED'
    assert mdu.ensure_calendar_year(2027, tmp_path, closures=closures, fetch=fetch) is None
    assert calls == ['2027'] and json.loads((tmp_path / 'calendar' / 'import_2027.json').read_text(encoding='utf-8'))['status'] == 'IMPORTED'
