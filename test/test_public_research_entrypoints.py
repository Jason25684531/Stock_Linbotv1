"""Real policy with fake senders: denial must stop the actual entry points, not just a CLI banner."""
from datetime import date
import json
from pathlib import Path
import subprocess
import sys

import pytest

from core.runtime import operation_policy as op, public_data as public, research_budget as rb


@pytest.fixture
def no_network(monkeypatch):
    calls = []
    monkeypatch.setattr(public, '_send', lambda *a, **kw: calls.append(a) or (_ for _ in ()).throw(AssertionError('unexpected HTTP')))
    return calls


def test_unassigned_real_twse_mcp_crawler_and_pit_paths_deny(public_budget, no_network, tmp_path):
    from core.research.sources import twse
    from core.research.fundamentals import pit
    from services.mcp import server
    from core.crawlers.quarterly_scraper import QuarterlyScraper
    for call in (lambda: twse.fetch_daily_quotes(date(2026, 10, 27), tmp_path),
                 lambda: server._fetch_twse_flow('2026-10-27'),
                 lambda: pit.fetch_publication_request(pit.PublicationRequest('2330', 114, 1, pit.make_request_key('2330', 114, 1)))):
        with pytest.raises(op.AuthorizationDenied, match='BUDGET_UNASSIGNED'):
            call()
    assert QuarterlyScraper()._fetch_data(114, 1, 'sii') is None
    assert no_network == [] and rb.status()['used'] == 7


def test_news_and_sdk_have_no_public_permission(public_budget, no_network, tmp_path):
    from core import news_agent
    from core.research.sources import finlab_source, yahoo
    for call in (lambda: news_agent._rss_bytes('https://news.google.com/rss/search?q=stock', 'news_google'),
                 lambda: finlab_source.fetch('price:收盤價', tmp_path, tmp_path / 'finlab.jsonl'),
                 lambda: yahoo.fetch_history('2330.TW', date(2023, 1, 1), date(2023, 2, 1))):
        with pytest.raises(op.AuthorizationDenied):
            call()
    assert no_network == [] and rb.status()['used'] == 7


def test_backfill_yahoo_proxy_denies_before_loading_sdk(public_budget, monkeypatch):
    from jobs import backfill_pipeline as backfill
    from types import SimpleNamespace
    calls = []
    monkeypatch.setattr(backfill.yf, '_load', lambda: SimpleNamespace(download=lambda *a, **kw: calls.append(a)))
    with pytest.raises(op.AuthorizationDenied, match='UNMETERED_RESTRICTED_SDK'):
        backfill._fetch_twse_calendar_dates('2026-10-01', '2026-10-02')
    assert calls == []


@pytest.mark.parametrize('capability', [{}, {'research_public_policy': public.POLICY_VERSION, 'execution_mode': 'PRODUCTION'}])
def test_research_mcp_refuses_legacy_or_production_proxy_before_fetch(public_budget, monkeypatch, capability):
    import asyncio
    import httpx
    from unittest.mock import AsyncMock
    from types import SimpleNamespace
    from core.mcp_client import MCPClient, MCPConfigurationError
    response = httpx.Response(200, request=httpx.Request('GET', 'http://localhost:8080/health'), json=capability)
    post = AsyncMock(side_effect=AssertionError('legacy service must not receive an upstream fetch RPC'))
    client = MCPClient(base_url='http://localhost:8080')
    monkeypatch.setattr(client, '_ensure_client', AsyncMock(return_value=SimpleNamespace(get=AsyncMock(return_value=response), post=post)))
    with pytest.raises(MCPConfigurationError, match='RESEARCH_SERVICE_POLICY_REQUIRED'):
        asyncio.run(client._post_json(endpoint='/v1/stock-basic-snapshot', payload={}, correlation_id='offline-health-check'))
    post.assert_not_called()
    assert rb.status()['used'] == 7


def test_mcp_research_request_remains_restricted_if_process_configuration_changes(public_budget, monkeypatch):
    from services.mcp import server
    from scripts import task_controller as tc
    from datetime import datetime, timedelta, timezone
    tc.GRANTS.write_text(json.dumps({'grants': [{'operation': 'production_mode', 'expires_at': (datetime.now(timezone.utc) + timedelta(days=1)).isoformat(), 'max_uses': 1}]}))
    monkeypatch.setattr(tc, 'grant_trusted', lambda *a: True)
    monkeypatch.delenv(op.MODE_ENV, raising=False)

    def switch_during_health():
        # Simulate an Owner configuration change AFTER the server admitted a Research-origin request.
        monkeypatch.setenv(op.MODE_ENV, 'production')
        return {'execution_mode': op.current_mode().value}, 200

    monkeypatch.setitem(server.app.view_functions, 'health', switch_during_health)
    response = server.app.test_client().get('/health', headers={public.RESEARCH_HEADER: public.POLICY_VERSION})
    assert response.status_code == 200 and response.json['execution_mode'] == 'RESEARCH'
    assert op.current_mode() is op.ExecutionMode.PRODUCTION  # request teardown restored the original process resolver
    denied = server.app.test_client().get('/health', headers={public.RESEARCH_HEADER: public.POLICY_VERSION})
    assert denied.status_code == 403  # a process already in Production never admits the Research proxy request


def test_daily_precheck_can_reuse_verified_canonical_cache_without_budget(public_budget, monkeypatch, tmp_path):
    import hashlib
    from scripts import daily_runner as daily
    from core.research.sources import twse
    fields = sorted(twse.REQUIRED_CLOSING_FIELDS)
    payload = {'stat': 'OK', 'date': '20261027', 'tables': [{'title': '每日收盤行情', 'fields': fields, 'data': [['2330' if f == '證券代號' else '100' for f in fields]]}]}
    ctx = public.context(twse.MI_INDEX_URL, params={'date': '20261027', 'type': 'ALL', 'response': 'json'})
    raw_path, meta_path = public._cache_paths(ctx)
    raw = json.dumps(payload).encode()
    public._atomic(raw_path, raw)
    public._atomic(meta_path, json.dumps({'key': ctx.key, 'context': json.loads(ctx.audit_json()), 'sha256': hashlib.sha256(raw).hexdigest(), 'status': 200, 'headers': {}}).encode())
    monkeypatch.setattr(op, 'enforce', lambda *a, **kw: (_ for _ in ()).throw(AssertionError('verified local cache asked for network permission')))
    assert daily.index_permission('2026-10-27').research_cache_hit
    result = twse.fetch_daily_quotes(date(2026, 10, 27), tmp_path)
    assert result.source_revision == '20261027' and rb.status()['used'] == 7


@pytest.mark.parametrize('scope', [None, {'datasets': ['MI_INDEX'], 'dates': ['2026-10-26']},
                                    {'datasets': ['MI_INDEX'], 'dates': ['2026-10-27']}])
def test_production_daily_keeps_its_legacy_explicit_dataset_and_date_requirement(public_budget, monkeypatch, scope):
    from datetime import datetime, timedelta, timezone
    from scripts import daily_runner as daily, task_controller as tc
    expires = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()
    official = {'operation': 'official_fetch', 'expires_at': expires, 'max_uses': 10}
    if scope is not None:
        official['scope'] = scope
    tc.GRANTS.write_text(json.dumps({'grants': [official, {'operation': 'production_mode', 'expires_at': expires, 'max_uses': 1}]}))
    monkeypatch.setattr(tc, 'grant_trusted', lambda *a: True)
    monkeypatch.setenv(op.MODE_ENV, 'production')
    if scope is None or scope['dates'] != ['2026-10-27']:
        with pytest.raises(op.AuthorizationDenied, match='does not cover MI_INDEX 2026-10-27'):
            daily.index_permission('2026-10-27')
    else:
        assert daily.index_permission('2026-10-27').allowed
    assert rb.status()['used'] == 7  # precheck never consumes Research or Production quota
    assert not (tc.STATE / tc.USAGE).exists()


def test_script_url_and_entry_alias_cannot_escape(public_budget, no_network, tmp_path, monkeypatch):
    from scripts import od_p6_fetch as od, t1b_fetch as t1
    monkeypatch.setattr(od, 'CACHE', tmp_path / 'od-cache')
    monkeypatch.setattr(od, 'LEDGER', tmp_path / 'od-report.jsonl')
    assert od.fetch(od.gate(), 'https://unapproved.example/detail', '2243', '20230130') == 'FAILED'
    with pytest.raises(op.AuthorizationDenied):
        t1.fetch(None, 'TWTAUU', '20000101', '20261023')
    assert no_network == [] and rb.status()['used'] == 7


def test_only_exact_obsolete_public_blockers_are_reclassified(tmp_path):
    from scripts import daily_runner as dr, task_controller as tc
    day = '2026-10-27'
    task = dr.ensure_task(day, tmp_path)
    tid = task['task_id']
    tc.block(tid, 'AUTHORIZATION', f'MI_INDEX {day} not cached: no unexpired official_fetch grant', 'old grant requirement', owner=True, root=tmp_path)
    tc.block(tid, 'PRODUCTION', 'keep this unrelated restriction', 'Owner decision', owner=True, root=tmp_path)
    refreshed = tc.refresh_public_fetch_blocker(tid, tmp_path)
    assert refreshed['status'] == 'WAITING_OWNER'
    assert any(b['type'] == 'PRODUCTION' for b in refreshed['blockers'])
    assert any('BUDGET_UNASSIGNED' in b['detail'] for b in refreshed['blockers'])
    assert len(refreshed['cleared_blockers']) == 1
    tc.block(tid, 'AUTHORIZATION', f'MI_INDEX {day} not cached: no unexpired official_fetch grant AND keep live_order restriction', 'not a pure public-fetch blocker', owner=True, root=tmp_path)
    refreshed = tc.refresh_public_fetch_blocker(tid, tmp_path)
    assert any('live_order' in b['detail'] for b in refreshed['blockers'])


@pytest.mark.parametrize('different_keys', [False, True])
def test_native_processes_share_rate_and_verified_cache(public_budget, different_keys):
    script = '''
import json,sys,time
from pathlib import Path
import requests
from core.runtime import public_data as p,research_budget as rb,operation_policy as op
rb.STATE=Path(sys.argv[1]); op.LOG=rb.STATE/'test-decisions.jsonl'
def offline(*a,**k):
    marker=rb.STATE/'first-send-delay'
    if not marker.exists():
        marker.write_text('simulate a process paused AFTER its gate release')
        time.sleep(0.2)
    with (rb.STATE/'test-sends.jsonl').open('a') as out: out.write(json.dumps({'at':time.time()})+'\\n')
    r=requests.Response(); r.status_code=200
    r._content=json.dumps({'stat':'OK','fields':['恢復買賣日期','股票代號','停止買賣前收盤價格','恢復買賣參考價'],'data':[['111/01/06','2330','100','90']]}).encode()
    return r
p._send=offline
url='https://www.twse.com.tw/rwd/zh/'+sys.argv[2]
r=p.request('GET',url,params={'startDate':'20220101','endDate':'20261023','response':'json'})
print(r.research_cache_hit)
'''
    routes = ['reducation/TWTAUU', 'change/TWTB8U' if different_keys else 'reducation/TWTAUU']
    processes = [subprocess.Popen([sys.executable, '-c', script, str(rb.STATE), route], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True) for route in routes]
    for process in processes:
        _, stderr = process.communicate(timeout=30)
        assert process.returncode == 0, stderr
    sends = [json.loads(line) for line in (Path(rb.STATE) / 'test-sends.jsonl').read_text().splitlines()]
    assert len(sends) == (2 if different_keys else 1)
    if different_keys:
        assert sends[1]['at'] - sends[0]['at'] >= 3.18
    assert rb.status()['used'] == 7 + len(sends)
