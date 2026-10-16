import pandas as pd
import pytest
from sqlalchemy import create_engine


@pytest.fixture
def market(monkeypatch):
    import app.web_server as web
    from app import app

    engine = create_engine('sqlite://')
    monkeypatch.setattr(web.db_helper, 'get_db_engine', lambda: engine)
    monkeypatch.setattr(web, '_latest_ui_market_date', lambda: '2026-10-16')
    return app.test_client(), engine


def test_margin_changes_load_previous_observation_from_single_day_query(market):
    client, engine = market
    rows = [
        {'stock_id': '2454', 'trade_date': day, 'margin_balance': margin, 'short_balance': short}
        for day, margin, short in [('2026-10-14', 100, 10), ('2026-10-15', 120, 15), ('2026-10-16', 125, 13), ('2026-10-19', 200, 25)]
    ]
    pd.DataFrame(rows).to_sql('daily_market_data', engine, index=False)
    response = client.get('/api/market/margin')
    assert response.status_code == 200
    payload = response.get_json()
    assert payload['status'] == 'ok'
    assert payload['data']['margin_increase'][0]['margin_change'] == 5
    assert payload['data']['short_decrease'][0]['short_change'] == -2
    assert payload['data']['high_short_margin_risk'][0]['short_margin_ratio'] == pytest.approx(13 / 125, rel=1e-9, abs=1e-12)


@pytest.mark.parametrize('rows', [
    [{'stock_id': '2454', 'trade_date': '2026-10-16', 'margin_balance': 125, 'short_balance': 13}],
    [{'stock_id': '2454', 'trade_date': '2026-10-15', 'margin_balance': None, 'short_balance': None},
     {'stock_id': '2454', 'trade_date': '2026-10-16', 'margin_balance': 125, 'short_balance': 13}],
])
def test_missing_previous_balances_are_not_ranked_as_zero(market, rows):
    client, engine = market
    pd.DataFrame(rows).to_sql('daily_market_data', engine, index=False)
    payload = client.get('/api/market/margin').get_json()
    assert payload['status'] == 'degraded' and payload['warnings']
    assert payload['data']['margin_increase'] == payload['data']['short_increase'] == []


def test_zero_or_missing_balances_have_no_ratio(market):
    client, engine = market
    pd.DataFrame([
        {'stock_id': '2454', 'trade_date': '2026-10-16', 'margin_balance': 0, 'short_balance': 5},
        {'stock_id': '2317', 'trade_date': '2026-10-16', 'margin_balance': 100, 'short_balance': None},
    ]).to_sql('daily_market_data', engine, index=False)
    response = client.get('/api/market/margin')
    payload = response.get_json()
    assert payload['data']['high_short_margin_risk'] == []
    assert any('short_margin_ratio unavailable' in warning for warning in payload['warnings'])
    assert 'Infinity' not in response.get_data(as_text=True)


def test_previous_observation_is_per_stock_not_global_calendar(market):
    client, engine = market
    pd.DataFrame([
        {'stock_id': '2454', 'trade_date': '2026-10-14', 'margin_balance': 100, 'short_balance': 10},
        {'stock_id': '2317', 'trade_date': '2026-10-15', 'margin_balance': 300, 'short_balance': 20},
        {'stock_id': '2454', 'trade_date': '2026-10-16', 'margin_balance': 125, 'short_balance': 13},
        {'stock_id': '2317', 'trade_date': '2026-10-16', 'margin_balance': 310, 'short_balance': 25},
    ]).to_sql('daily_market_data', engine, index=False)
    result = client.get('/api/market/margin').get_json()['data']['margin_increase']
    assert {row['stock_id']: row['margin_change'] for row in result} == {'2454': 25, '2317': 10}
