"""public_data.ingestion_scope (design D2): grant debit per HTTP attempt, ingestion audit, research budget untouched. Fake sender only."""
import hashlib
import json
from pathlib import Path

import pytest
import requests

from core.runtime import operation_policy as op, public_data as public, research_budget as rb
from scripts import task_controller as tc

ROOT = Path(__file__).resolve().parents[1]
MI_INDEX = 'https://www.twse.com.tw/rwd/zh/afterTrading/MI_INDEX'
PARAMS = {'date': '20261022', 'type': 'ALL', 'response': 'json'}
BODY = (ROOT / 'artifacts/official_data_recovery_cache/mi_index/MI_INDEX_20261022.json').read_bytes()


@pytest.fixture(autouse=True)
def offline(monkeypatch, tmp_path, public_budget):
    from core.research.sources.twse import RequestGate
    waits = []
    monkeypatch.setattr(RequestGate, 'wait', lambda gate: waits.append(gate.minimum_interval) or gate._release())
    monkeypatch.setenv('STOCK_LINBOT_UPDATE_ROOT', str(tmp_path / 'updates'))
    (tmp_path / 'task_state').mkdir(exist_ok=True)
    return waits


@pytest.fixture
def grant(tmp_path, monkeypatch):
    decision = tmp_path / 'decision.md'
    decision.write_text('D1\n', encoding='utf-8')
    path = tmp_path / 'grants.json'
    path.write_text(json.dumps({'grants': [{
        'operation': 'market_data_ingestion', 'sources': ['TWSE:MI_INDEX'], 'datasets': ['prices_volume'], 'tables': ['daily_market_data'],
        'market_scope': 'TWSE_ONLY', 'operations': ['read', 'commit'], 'rate_limit_seconds': 4.0, 'max_uses': 1,
        'expires_at': '2099-01-01T00:00:00+08:00', 'decision_ref': str(decision),
        'decision_sha256': hashlib.sha256(decision.read_bytes()).hexdigest()}]}), encoding='utf-8')
    monkeypatch.setattr(tc, 'GRANTS', path)
    monkeypatch.setattr(tc, 'grant_trusted', lambda *a: True)
    from core.runtime import ingestion_authority
    monkeypatch.setattr(ingestion_authority, 'DECISION_REF', str(decision))
    return path


def _reply(*a, **k):
    response = requests.Response()
    response.status_code, response._content, response.url = 200, BODY, MI_INDEX
    response.headers['Content-Type'] = 'application/json'
    return response


def _audit(tmp_path):
    path = tmp_path / 'updates/ingestion_audit.jsonl'
    return [json.loads(line) for line in path.read_text(encoding='utf-8').splitlines()] if path.is_file() else []


def test_scope_debits_the_grant_audits_and_leaves_the_research_budget(grant, monkeypatch, tmp_path, offline):
    sent = []
    monkeypatch.setattr(public, '_send', lambda *a, **k: sent.append(a) or _reply())
    before = len(rb.audit_rows())
    with public.ingestion_scope('prices_volume', 'TWSE_ONLY'):
        assert public.request('GET', MI_INDEX, params=PARAMS).json()['stat'] == 'OK'
    assert len(sent) == 1 and len(rb.audit_rows()) == before
    assert len((tmp_path / 'task_state' / tc.USAGE).read_text(encoding='utf-8').splitlines()) == 1
    rows = _audit(tmp_path)
    assert [r['result'] for r in rows] == ['RESERVED', 'VERIFIED'] and rows[-1]['sha256'] == hashlib.sha256(BODY).hexdigest()
    assert rows[0]['source_id'] == 'TWSE:MI_INDEX' and rows[0]['market_scope'] == 'TWSE_ONLY'
    assert offline == [4.0]  # the grant's larger interval wins over 3.2 s


def test_cache_hit_needs_no_use_and_quota_exhaustion_sends_nothing(grant, monkeypatch, tmp_path):
    sent = []
    monkeypatch.setattr(public, '_send', lambda *a, **k: sent.append(a) or _reply())
    with public.ingestion_scope('prices_volume', 'TWSE_ONLY'):
        public.request('GET', MI_INDEX, params=PARAMS)
        assert public.request('GET', MI_INDEX, params=PARAMS).research_cache_hit is True  # verified cache: no second use
        with pytest.raises(op.AuthorizationDenied, match='INGESTION_QUOTA_EXHAUSTED'):
            public.request('GET', MI_INDEX, params={**PARAMS, 'date': '20261021'})  # max_uses 1 already used
    assert len(sent) == 1


def test_no_grant_denies_before_any_send(monkeypatch, tmp_path):
    monkeypatch.setattr(public, '_send', lambda *a, **k: pytest.fail('nothing may be sent'))
    with public.ingestion_scope('prices_volume', 'TWSE_ONLY'), pytest.raises(op.AuthorizationDenied, match='INGESTION_DISABLED'):
        public.request('GET', MI_INDEX, params=PARAMS)


def test_scope_never_widens_and_takes_no_injected_grant(grant, monkeypatch):
    monkeypatch.setattr(public, '_send', lambda *a, **k: pytest.fail('nothing may be sent'))
    with public.ingestion_scope('prices_volume', 'TWSE_ONLY'), pytest.raises(op.AuthorizationDenied):
        public.request('GET', 'https://example.com/data', params={})
    with pytest.raises(TypeError):
        public.request('GET', MI_INDEX, params=PARAMS, grants_path=str(grant))


def test_outside_the_scope_nothing_changes(monkeypatch):
    monkeypatch.setattr(public, '_send', lambda *a, **k: pytest.fail('daily MI_INDEX stays budget-blocked in research'))
    with pytest.raises(op.AuthorizationDenied, match='BUDGET_UNASSIGNED'):
        public.request('GET', MI_INDEX, params=PARAMS)


def test_builtin_policy_sends_at_the_minimum_interval_and_audits_without_a_grant(monkeypatch, tmp_path, offline):
    monkeypatch.delenv('STOCK_LINBOT_INGESTION_DISABLED')
    sent = []
    monkeypatch.setattr(public, '_send', lambda *a, **k: sent.append(a) or _reply())
    with public.ingestion_scope('prices_volume', 'TWSE_ONLY'):
        public.request('GET', MI_INDEX, params=PARAMS)
    rows = [json.loads(line) for line in (tmp_path / 'updates/ingestion_audit.jsonl').read_text(encoding='utf-8').splitlines()]
    assert len(sent) == 1 and offline == [3.2] and rows[0]['result'] == 'RESERVED' and rows[0]['grant_id'] == 'BUILTIN'
    assert not (tmp_path / tc.USAGE).exists()  # no grant usage log is written
    from core.runtime import ingestion_authority as ia
    machine = [json.loads(line) for line in (ia.MACHINE_ROOT / 'ingestion_audit.jsonl').read_text(encoding='utf-8').splitlines()]
    assert [r['attempt'] for r in machine] == [r['attempt'] for r in rows]  # any root also writes the machine audit the cap counts


def test_a_list_reply_is_never_mistaken_for_an_unpublished_session():
    """The holiday schedule answers with a JSON list (real reply 2026-11-09); only an MI_INDEX dict can be an unpublished session."""
    reply = requests.Response()
    reply.status_code, reply._content = 200, b'[{"Date": "1150101"}]'
    ctx = public.context('https://openapi.twse.com.tw/v1/holidaySchedule/holidaySchedule', params={'year': '2026'})
    assert public._unpublished_session(ctx, reply) is False
