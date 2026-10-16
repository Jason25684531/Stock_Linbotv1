import pandas as pd
import pytest
from sqlalchemy import create_engine, text

from core import db_helper
from jobs.update_database import fetch_market_data_with_mcp, merge_market_data, process_and_save
from services.mcp import server


def test_official_parser_rejects_stale_payload_before_stamping(monkeypatch):
    class Response:
        def raise_for_status(self):
            pass

        def json(self):
            return {'stat': 'OK', 'date': '20261008', 'data': [], 'fields': []}

    monkeypatch.setattr(server.requests, 'get', lambda *a, **kw: Response())
    with pytest.raises(RuntimeError, match='source date'):
        server._fetch_twse_flow('2026-10-13')
    for value in ('20261013', '2026-10-13'):
        server._validate_source_date({'date': value}, '2026-10-13')
    for value in (None, '2026-10-13garbage'):
        with pytest.raises(RuntimeError):
            server._validate_source_date({'date': value}, '2026-10-13')


@pytest.mark.parametrize('fetcher', ['fetch_stock_basic_snapshot', 'fetch_foreign_investor_flow'])
def test_all_market_rejects_missing_exchange(monkeypatch, fetcher):
    row = pd.DataFrame({'stock_id': ['2330'], 'close_price': [100], 'open_price': [100],
                        'high_price': [100], 'low_price': [100], 'volume': [10], 'foreign_buy': [0]})
    monkeypatch.setattr(server, '_fetch_twse_snapshot', lambda *a: pd.DataFrame())
    monkeypatch.setattr(server, '_fetch_tpex_snapshot', lambda *a: row)
    monkeypatch.setattr(server, '_fetch_twse_flow', lambda *a: pd.DataFrame())
    monkeypatch.setattr(server, '_fetch_tpex_flow', lambda *a: row)
    kwargs = {'include_etfs': True} if fetcher == 'fetch_stock_basic_snapshot' else {}
    with pytest.raises(RuntimeError, match='Incomplete ALL'):
        getattr(server, fetcher)('ALL', '2026-10-13', **kwargs)


def test_empty_both_markets_remains_empty():
    frames = [server._prepare_flow_frame(pd.DataFrame(), '2026-10-13')] * 2
    assert server._combine_market_frames(frames, 'ALL').empty


def test_missing_flow_stays_sql_null_and_real_zero_stays_zero(monkeypatch):
    engine = create_engine('sqlite:///:memory:')
    with engine.begin() as conn:
        conn.execute(text('CREATE TABLE daily_market_data (stock_id TEXT, trade_date TEXT, '
                          'close_price REAL, foreign_buy REAL, trust_buy REAL, dealer_buy REAL)'))
    monkeypatch.setattr(db_helper, 'get_db_engine', lambda: engine)
    snapshot = pd.DataFrame({'stock_id': ['2330', '2317'], 'trade_date': ['2026-10-13'] * 2,
                             'close_price': [100, 200]})
    flow = pd.DataFrame({'stock_id': ['2330'], 'trade_date': ['2026-10-13'],
                         'foreign_buy': [0], 'trust_buy': [1], 'dealer_buy': [-1]})
    batch = merge_market_data(snapshot, flow, '2026-10-13')
    assert process_and_save(batch, '2026-10-13', engine=None) == 2
    with engine.connect() as conn:
        rows = conn.execute(text('SELECT stock_id, foreign_buy, dealer_buy FROM daily_market_data ORDER BY stock_id')).all()
    assert rows == [('2317', None, None), ('2330', 0, -1)]


def test_failed_flow_does_not_produce_market_batch():
    class Client:
        def fetch_many_sync(self, *a, **kw):
            return {'snapshot': {'records': [{'stock_id': '2330', 'trade_date': '2026-10-13',
                                             'close_price': 100}]}, 'flow': RuntimeError('not published')}

    with pytest.raises(RuntimeError, match='Institutional data is unavailable'):
        fetch_market_data_with_mcp(Client(), '2026-10-13')


def test_tpex_quotes_use_requested_date_and_include_etfs_in_one_request(monkeypatch):
    calls = []

    class Response:
        def raise_for_status(self):
            pass

        def json(self):
            return {'date': '20261012', 'tables': [{'data': [
                ['00679B', 'ETF', 24, 0, 23, 25, 22, 24, 100] + [0] * 10]}]}

    def get(url, **kwargs):
        calls.append((url, kwargs['params']))
        return Response()

    monkeypatch.setattr(server.requests, 'get', get)
    frame = server._fetch_tpex_snapshot('2026-10-12')
    assert calls == [('https://www.tpex.org.tw/www/zh-tw/afterTrading/dailyQuotes',
                      {'date': '2026/10/12', 'response': 'json'})]
    assert frame.iloc[0][['stock_id', 'close_price', 'volume']].tolist() == ['00679B', 24, 100]


def test_twse_dealer_is_total_not_foreign_column(monkeypatch):
    fields = ['證券代號', '證券名稱', '外陸資買進股數(不含外資自營商)', '外陸資賣出股數(不含外資自營商)',
              '外陸資買賣超股數(不含外資自營商)', '外資自營商買進股數', '外資自營商賣出股數',
              '外資自營商買賣超股數', '投信買進股數', '投信賣出股數', '投信買賣超股數', '自營商買賣超股數',
              '自營商買進股數(自行買賣)', '自營商賣出股數(自行買賣)', '自營商買賣超股數(自行買賣)',
              '自營商買進股數(避險)', '自營商賣出股數(避險)', '自營商買賣超股數(避險)', '三大法人買賣超股數']
    row = ['2330', 'x'] + [0] * 17
    row[4], row[10], row[11] = 4, 5, 3

    class Response:
        def raise_for_status(self):
            pass

        def json(self):
            return {'stat': 'OK', 'date': '20261013', 'fields': fields, 'data': [row]}

    monkeypatch.setattr(server.requests, 'get', lambda *a, **kw: Response())
    frame = server._fetch_twse_flow('2026-10-13')
    assert frame.iloc[0][['foreign_buy', 'trust_buy', 'dealer_buy']].tolist() == [4, 5, 3]


def test_tpex_dealer_uses_total_instead_of_proprietary_only(monkeypatch):
    class Response:
        def raise_for_status(self):
            pass

        def json(self):
            row = ['5483', 'stock'] + [0] * 22
            row[10], row[13], row[16], row[19], row[22] = 4, 5, 1, 2, 3
            return {'date': '20261013', 'tables': [{'data': [row]}]}

    monkeypatch.setattr(server.requests, 'get', lambda *a, **kw: Response())
    frame = server._fetch_tpex_flow('2026-10-13')
    assert frame.iloc[0][['foreign_buy', 'trust_buy', 'dealer_buy']].tolist() == [4, 5, 3]


def test_tpex_margin_tables_use_balance_and_reject_stale_date(monkeypatch):
    from core.crawlers import chip_data_scraper as chip

    class Response:
        status_code = 200
        date = '20261012'

        def json(self):
            row = ['5483', 'stock'] + [0] * 18
            row[6], row[13], row[14] = '1,000', 777, 50
            return {'date': self.date, 'tables': [{'data': [row]}]}

    response = Response()
    monkeypatch.setattr(chip.requests, 'get', lambda *a, **kw: response)
    monkeypatch.setattr(chip.time, 'sleep', lambda *a: None)
    frame = chip.fetch_margin_balance_tpex('2026-10-12', max_retries=1)
    assert frame.iloc[0][['margin_balance', 'short_balance']].tolist() == [1000, 50]
    response.date = '20261013'
    assert chip.fetch_margin_balance_tpex('2026-10-12', max_retries=1).empty
