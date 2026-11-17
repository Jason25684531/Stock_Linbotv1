"""Readable main surface (change 2026-11-17-readable-surface-and-dead-code-round): codes carry a Chinese label, an unknown page gets a way back."""
import pytest


@pytest.fixture
def client(monkeypatch):
    from app import app, web_server
    monkeypatch.setattr(web_server, '_build_system_status_payload', lambda *a, **k: ({'status': 'ok'}, 200))
    monkeypatch.setattr(web_server, '_latest_ui_market_date', lambda *a, **k: None)
    return app.test_client()


@pytest.mark.parametrize('code,label', [('MONTHLY_REVENUE_NO_ANNOUNCEMENT_TIME', '月營收缺公告時間'), ('TWSE_LISTED_ONLY', '僅上市股票'),
                                        ('NOT_COVERED', '未涵蓋'), ('SENT', '已送出'), ('H4_SEALED_RESEARCH', '封存研究'),
                                        ('FUNDAMENTAL_PIT_SHADOW', '基本面觀察中')])
def test_a_known_code_shows_its_chinese_label_and_keeps_the_code_readable(code, label):
    from app import app
    html = str(app.jinja_env.get_template('_viz.html').module.tag(code))
    assert f'>{label}<' in html and f'title="{code}"' in html


def test_an_unlisted_code_is_shown_as_stored():
    from app import app
    assert '>SOME_NEW_REASON<' in str(app.jinja_env.get_template('_viz.html').module.tag('SOME_NEW_REASON'))


def test_an_unknown_page_offers_the_five_primary_pages_without_reading_system_status(monkeypatch):
    from app import app, web_server
    monkeypatch.setattr(web_server, '_build_system_status_payload', lambda *a, **k: pytest.fail('a missing page must not query status'))
    response = app.test_client().get('/no-such-page')
    html = response.get_data(as_text=True)
    assert response.status_code == 404 and '找不到這個頁面' in html
    assert all(f'href="{path}"' in html for path in ('/dashboard', '/stock-analysis', '/strategies', '/backtest', '/system'))


def test_a_missing_api_resource_keeps_its_json_answer(client):
    response = client.get('/api/selection/latest?strategy=nope')
    assert response.status_code == 404 and response.get_json()['status'] == 'NOT_FOUND'
    assert '找不到這個頁面' not in client.get('/api/no-such-endpoint').get_data(as_text=True)


def test_dashboard_publish_card_reads_in_chinese_with_codes_kept(client, monkeypatch):
    from app import platform_routes
    monkeypatch.setattr(platform_routes, 'publish_overview', lambda: {
        'universe': 'TWSE_LISTED_ONLY', 'tpex': 'NOT_COVERED', 'market_date': '2026-11-09', 'recommendation_date': '2026-11-09',
        'expected_session': '2026-11-09', 'fresh': True, 'reason': None, 'line': [{'key': '2026-11-09_evening', 'state': 'SENT'}]})
    html = client.get('/dashboard').get_data(as_text=True)
    card = html[html.index('data-testid="publish-overview"'):html.index('data-testid="publish-line"') + 400]
    assert '僅上市股票' in card and 'data-code="TWSE_LISTED_ONLY"' in card and '未涵蓋' in card
    assert '2026-11-09 盤後' in card and '已送出' in card and 'data-code="SENT"' in card
