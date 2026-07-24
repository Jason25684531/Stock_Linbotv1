"""Byte-level dashboard payload baseline under deterministic dependencies."""

import hashlib
import json
import sys
from pathlib import Path


def test_health_check_payload_matches_baseline(monkeypatch):
    sys.path.insert(0, str(Path(__file__).parents[1]))
    import app as app_module
    from app import app as flask_app
    from test_dashboard_health_check_api import _build_war_room_history, _patch_war_room_dependencies

    _patch_war_room_dependencies(monkeypatch, app_module, _build_war_room_history())
    response = flask_app.test_client().get('/api/dashboard/health-check?symbol=2330&date=2026-04-24')
    payload = response.get_json()
    serialized = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(',', ':'))
    baseline_path = Path(__file__).parents[2] / 'openspec' / 'changes' / '2026-07-24-stabilize-architecture-for-future-expansion' / '_baseline' / 'dashboard_health_check.sha256.json'
    baseline = json.loads(baseline_path.read_text(encoding='utf-8'))

    assert response.status_code == 200
    assert hashlib.sha256(serialized.encode()).hexdigest() == baseline['sha256']


def test_macro_payload_matches_baseline(monkeypatch):
    sys.path.insert(0, str(Path(__file__).parents[1]))
    import app as app_module
    import app.dashboard_payloads as dashboard_payloads
    import core.news_agent as news_agent
    from app import app as flask_app
    from test_dashboard_health_check_api import _FakeMCPClient

    monkeypatch.setattr(app_module, '_build_market_snapshot', lambda: {'status': 'error', 'date_str': '2026-04-29', 'message': '盤勢快照暫不可用。'})
    monkeypatch.setattr(app_module, '_build_chip_snapshot', lambda: {'status': 'ok', 'date_str': '2026-04-29', 'summary': '法人偏多。'})
    monkeypatch.setattr(app_module, 'resolve_dashboard_aggregation_cache', lambda *args, **kwargs: kwargs['refresh_fn']())
    monkeypatch.setattr(news_agent, 'get_morning_news_summary', lambda: '固定新聞摘要')
    monkeypatch.setattr(app_module, 'MCPClient', lambda: _FakeMCPClient(hotspot_payload={
        'dataset': 'market_hotspot', 'requested_date': '2026-04-29', 'as_of_date': '2026-04-29',
        'status': 'degraded', 'source': ['stock_basic_snapshot'], 'degraded_fields': ['institutional'],
        'warnings': ['institutional flow unavailable for hotspot ranking'], 'cache_status': 'fresh',
        'payload_version': 'v1', 'breadth': {'advancing': 1, 'declining': 1, 'unchanged': 0},
        'hotspots': {'top_gainers': [{'stock_id': '2330'}], 'top_foreign_inflows': []},
        'records': [{'stock_id': '2330', 'trade_date': '2026-04-29'}],
    }))
    response = flask_app.test_client().get('/api/dashboard/macro?date=2026-04-29')
    serialized = json.dumps(response.get_json(), ensure_ascii=False, sort_keys=True, separators=(',', ':'))
    baseline_path = Path(__file__).parents[2] / 'openspec' / 'changes' / '2026-07-24-stabilize-architecture-for-future-expansion' / '_baseline' / 'dashboard_health_check.sha256.json'

    assert response.status_code == 200
    assert hashlib.sha256(serialized.encode()).hexdigest() == json.loads(baseline_path.read_text(encoding='utf-8'))['macro_sha256']


def test_health_check_local_fallback_contract(monkeypatch):
    import app as app_module
    import app.dashboard_payloads as dashboard_payloads
    from test_dashboard_health_check_api import _FakeMCPClient

    local_payload = {'status': 'ok', 'source': ['daily_market_data'], 'warnings': []}
    monkeypatch.setattr(app_module, 'MCPClient', lambda: _FakeMCPClient())
    monkeypatch.setattr(app_module, 'resolve_dashboard_aggregation_cache', lambda *args, **kwargs: kwargs['refresh_fn']())
    monkeypatch.setattr(dashboard_payloads, '_build_dashboard_health_check_payload_local', lambda *args, **kwargs: dict(local_payload))

    payload = app_module._build_dashboard_health_check_payload('2330', '2026-04-24')

    assert payload == {
        'status': 'degraded',
        'source': ['daily_market_data', 'local_fallback'],
        'warnings': ['MCP aggregation unavailable; using local fallback'],
        'cache_status': 'miss',
    }
