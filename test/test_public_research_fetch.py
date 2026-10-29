from dataclasses import replace
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import subprocess
import sys

import pytest
import requests

from core.runtime import operation_policy as op, public_data as public, research_budget as rb
from scripts import task_controller as tc

URL = 'https://www.twse.com.tw/rwd/zh/reducation/TWTAUU'
PARAMS = {'startDate': '20220101', 'endDate': '20261023', 'response': 'json'}


@pytest.fixture(autouse=True)
def offline_pacing_clock(monkeypatch):
    """This unit file uses fake senders; real elapsed spacing is tested in subprocess/container integration."""
    from core.research.sources.twse import RequestGate
    import time

    def release(gate):
        gate._release()
        gate.last_release_wall = time.time()

    monkeypatch.setattr(RequestGate, 'wait', release)


def reply(payload=None, status=200):
    response = requests.Response()
    response.status_code = status
    response._content = json.dumps(payload or {'stat': 'OK', 'fields': ['恢復買賣日期', '股票代號', '停止買賣前收盤價格', '恢復買賣參考價'],
                                              'data': [['111/01/06', '2330', '100', '90']]}).encode()
    response.headers['Content-Type'] = 'application/json'
    response.url = URL
    return response


@pytest.fixture
def sender(monkeypatch):
    calls = []
    monkeypatch.setattr(public, '_send', lambda *a, **k: calls.append((a, k)) or reply())
    return calls


def test_public_request_without_grants_or_trust(public_budget, monkeypatch, sender, tmp_path):
    monkeypatch.delenv(op.MODE_ENV, raising=False)
    monkeypatch.setattr(tc, 'GRANTS', tmp_path / 'absent-grants.json')
    monkeypatch.setattr(tc, 'grant_trusted', lambda *a: (_ for _ in ()).throw(AssertionError('public research must not consult trust')))
    ctx = public.context(URL, params=PARAMS)
    assert op.authorize(op.Operation.DOWNLOAD_MARKET_DATA, request_context=ctx).allowed
    assert rb.status()['used'] == 7  # precheck is not the HTTP debit
    assert public.request('GET', URL, params=PARAMS).json()['stat'] == 'OK'
    assert len(sender) == 1 and rb.status()['used'] == 8
    assert sender[0][1]['allow_redirects'] is False and sender[0][1]['verify'] is True
    assert rb.audit_rows()[-1]['outcome'] == 'VERIFIED'


@pytest.mark.parametrize('url,params', [
    ('https://example.org/free', {}), ('https://www.twse.com.tw.evil.test/rwd/zh/reducation/TWTAUU', PARAMS),
    ('http://www.twse.com.tw/rwd/zh/reducation/TWTAUU', PARAMS), ('https://u:p@www.twse.com.tw/rwd/zh/reducation/TWTAUU', PARAMS),
    ('https://www.twse.com.tw:8443/rwd/zh/reducation/TWTAUU', PARAMS), (URL, {**PARAMS, 'startDate': '20000101'}),
    (URL, {**PARAMS, 'api_key': 'not-a-credential'}), ('https://www.twse.com.tw/rwd/zh/../reducation/TWTAUU', PARAMS),
])
def test_unapproved_requests_never_reach_sender(public_budget, sender, url, params):
    with pytest.raises(op.AuthorizationDenied, match='SOURCE_NOT_ALLOWLISTED'):
        public.request('GET', url, params=params)
    assert sender == [] and rb.status()['used'] == 7


def test_registered_aliases_share_key_and_wrong_purpose_denies():
    stock, day = public._scope()['od_p6_targets'][0]
    params = {'STK_NO': stock, 'T': day, 'response': 'json'}
    first = public.context('https://www.twse.com.tw' + public.DETAIL_PATHS[0], params=params)
    alias = public.context('https://www.twse.com.tw' + public.DETAIL_PATHS[1], params=params)
    assert first.key == alias.key
    with pytest.raises(op.AuthorizationDenied):
        public.context(first.url, params=params, purpose='t1b')
    with pytest.raises(op.AuthorizationDenied, match='FORGED'):
        public.validate_context(replace(first, job='t1b'))


def test_paid_and_unknown_operation_cannot_use_environment_budget(monkeypatch, public_budget):
    monkeypatch.setenv(rb.MAX_REQUESTS_ENV, '1000000')
    assert not op.authorize(op.Operation.CALL_RESEARCH_API).allowed
    assert not op.authorize('public_research_fetch').allowed
    for name in ('gemini', 'finlab', 'yahoo'):
        with pytest.raises(op.AuthorizationDenied):
            public.restricted(name)


def test_mops_is_public_but_unassigned_budget_denies_before_http(public_budget, monkeypatch):
    called = []
    monkeypatch.setattr(public, '_send', lambda *a, **k: called.append(a))
    with pytest.raises(op.AuthorizationDenied, match='BUDGET_UNASSIGNED'):
        public.request('POST', 'https://doc.twse.com.tw/server-java/t57sb01', data={'id': '', 'key': '', 'step': '1', 'co_id': '2330', 'year': '114', 'seamon': '1', 'mtype': 'A', 'dtype': 'AI1'})
    assert called == []


def test_verified_cache_survives_exhaustion_and_missing_ledger(public_budget, sender, monkeypatch):
    public.request('GET', URL, params=PARAMS)
    monkeypatch.setenv(rb.MAX_REQUESTS_ENV, '4')  # fewer than already consumed
    assert public.request('GET', URL, params=PARAMS).research_cache_hit
    db, _ = rb._paths()
    db.rename(db.with_suffix('.lost'))
    assert public.request('GET', URL, params=PARAMS).research_cache_hit
    assert len(sender) == 1


def test_cache_tampering_does_not_trigger_silent_redownload(public_budget, sender):
    public.request('GET', URL, params=PARAMS)
    raw, _ = public._cache_paths(public.context(URL, params=PARAMS))
    raw.write_bytes(b'changed')
    with pytest.raises(op.AuthorizationDenied, match='CACHE_INVALID'):
        public.request('GET', URL, params=PARAMS)
    assert len(sender) == 1


@pytest.mark.parametrize('fields,rows', [([], [{'unexpected': 'shape'}]), (['股票代號'], [['2330']]),
                                       (['恢復買賣日期', '股票代號', '停止買賣前收盤價格', '恢復買賣參考價'], [['2330']])])
def test_schema_drift_is_audited_but_never_verified(public_budget, monkeypatch, fields, rows):
    monkeypatch.setattr(public, '_send', lambda *a, **kw: reply({'stat': 'OK', 'fields': fields, 'data': rows}))
    with pytest.raises(ValueError):
        public.request('GET', URL, params=PARAMS)
    assert rb.audit_rows()[-1]['outcome'] == 'FAILED' and rb.status()['used'] == 8
    assert public.cached(public.context(URL, params=PARAMS)) is None


def test_each_failed_attempt_consumes_quota_and_shared_gate(public_budget, monkeypatch):
    from core.research.sources.twse import RequestGate
    waits, calls = [], []
    monkeypatch.setattr(RequestGate, 'wait', lambda gate: waits.append((gate.minimum_interval, gate.state_path)))
    monkeypatch.setattr(public, '_send', lambda *a, **k: calls.append(a) or reply(status=503))
    for _ in range(2):
        with pytest.raises(op.AuthorizationDenied, match='HTTP_STOP'):
            public.request('GET', URL, params=PARAMS)
    assert rb.status()['used'] == 9 and len(calls) == 2
    assert all(interval >= 3.2 for interval, _ in waits) and waits[0][1] == waits[1][1]
    assert all(row['status'] == 503 and row['outcome'] == 'FAILED' for row in rb.audit_rows()[-2:])


def test_retry_after_is_persisted_and_blocks_other_keys(public_budget, monkeypatch):
    calls = []
    response = reply(status=429)
    response.headers['Retry-After'] = '600'
    monkeypatch.setattr(public, '_send', lambda *a, **k: calls.append(a) or response)
    with pytest.raises(op.AuthorizationDenied, match='HTTP_STOP'):
        public.request('GET', URL, params=PARAMS)
    other = 'https://www.twse.com.tw/rwd/zh/change/TWTB8U'
    with pytest.raises(op.AuthorizationDenied, match='SOURCE_COOLDOWN'):
        public.request('GET', other, params=PARAMS)
    assert len(calls) == 1 and rb.status()['used'] == 8
    script = 'import sys; from pathlib import Path; from core.runtime import research_budget as rb; rb.STATE=Path(sys.argv[1]); rb.check("t1b",rate_group="twse")'
    restarted = subprocess.run([sys.executable, '-c', script, str(rb.STATE)], capture_output=True, text=True, timeout=20)
    assert restarted.returncode != 0 and 'SOURCE_COOLDOWN' in restarted.stderr


def test_sender_ignores_netrc_and_rejects_caller_session(public_budget, monkeypatch, tmp_path):
    netrc = tmp_path / 'netrc'
    netrc.write_text('machine www.twse.com.tw login offline-user password offline-placeholder\n')
    monkeypatch.setenv('NETRC', str(netrc))
    prepared = []

    def fake_send(adapter, request, **kwargs):
        prepared.append(request)
        response = reply()
        response.request = request
        return response

    monkeypatch.setattr(requests.adapters.HTTPAdapter, 'send', fake_send)
    public.request('GET', URL, params=PARAMS)
    assert len(prepared) == 1 and 'Authorization' not in prepared[0].headers and 'Cookie' not in prepared[0].headers
    caller = requests.Session()
    caller.cookies.set('login_session', 'offline-placeholder')
    caller.hooks['response'].append(lambda *a, **kw: (_ for _ in ()).throw(AssertionError('caller hook ran')))
    with pytest.raises(op.AuthorizationDenied, match='PUBLIC_SESSION_RESTRICTED'):
        public.request('GET', 'https://www.twse.com.tw/rwd/zh/change/TWTB8U', params=PARAMS, session=caller)
    assert len(prepared) == 1 and rb.status()['used'] == 8


def test_no_audit_no_transmission(public_budget, sender, tmp_path, monkeypatch):
    blocker = tmp_path / 'blocker'
    blocker.write_text('not a directory')
    monkeypatch.setattr(op, 'LOG', blocker / 'decisions.jsonl')
    with pytest.raises(OSError):
        public.request('GET', URL, params=PARAMS)
    assert sender == [] and rb.status()['used'] == 7


def test_response_record_failure_preserves_debit(public_budget, sender, monkeypatch):
    monkeypatch.setattr(rb, 'finish', lambda *a, **k: (_ for _ in ()).throw(OSError('audit unavailable')))
    with pytest.raises(OSError):
        public.request('GET', URL, params=PARAMS)
    assert len(sender) == 1 and rb.status()['used'] == 8
    assert rb.audit_rows()[-1]['outcome'] == 'RESERVED'
    assert public.cached(public.context(URL, params=PARAMS)) is None


def test_internal_mcp_service_is_not_an_arbitrary_url(monkeypatch):
    monkeypatch.delenv(op.MODE_ENV, raising=False)
    public.internal_service('http://localhost:8080', '/v1/tools/get_market_statistics')
    with pytest.raises(op.AuthorizationDenied, match='SERVICE_NOT_ALLOWLISTED'):
        public.internal_service('https://arbitrary.example', '/tools/test')


def test_get_body_and_url_control_characters_are_not_queries(public_budget, sender):
    with pytest.raises(op.AuthorizationDenied, match='SOURCE_NOT_ALLOWLISTED'):
        public.request('GET', URL, data=PARAMS)
    with pytest.raises(op.AuthorizationDenied, match='SOURCE_NOT_ALLOWLISTED'):
        public.request('GET', URL + '\n', params=PARAMS)
    assert sender == [] and rb.status()['used'] == 7


def test_monthly_market_and_period_are_part_of_cache_identity():
    contexts = [public.context('https://mopsov.twse.com.tw/nas/t21/' + tail) for tail in (
        'sii/t21sc03_114_1_0.html', 'sii/t21sc03_114_2_0.html', 'otc/t21sc03_114_1_0.html')]
    assert len({ctx.key for ctx in contexts}) == 3


@pytest.mark.parametrize('after', ['600', 'Wed, 28 Oct 2099 00:00:00 GMT'])
def test_503_retry_after_also_stops_other_keys(public_budget, monkeypatch, after):
    response = reply(status=503)
    response.headers['Retry-After'] = after
    calls = []
    monkeypatch.setattr(public, '_send', lambda *a, **kw: calls.append(a) or response)
    with pytest.raises(op.AuthorizationDenied, match='HTTP_STOP'):
        public.request('GET', URL, params=PARAMS)
    with pytest.raises(op.AuthorizationDenied, match='SOURCE_COOLDOWN'):
        public.request('GET', 'https://www.twse.com.tw/rwd/zh/change/TWTB8U', params=PARAMS)
    assert len(calls) == 1 and rb.status()['used'] == 8


def test_detail_generic_table_is_not_a_verified_action_schema(public_budget, monkeypatch):
    stock, day = public._scope()['od_p6_targets'][0]
    monkeypatch.setattr(public, '_send', lambda *a, **kw: reply({'stat': 'OK', 'fields': ['unrelated'], 'data': [['not action terms']]}))
    ctx = public.context('https://www.twse.com.tw' + public.DETAIL_PATHS[0], params={'STK_NO': stock, 'T': day, 'response': 'json'})
    with pytest.raises(ValueError, match='OD_P6_SCHEMA_UNCONFIRMED'):
        public.request('GET', ctx.url, params=dict(ctx.parameters))
    assert rb.audit_rows()[-1]['outcome'] == 'FAILED'
    assert public.cached(ctx) is None


@pytest.mark.parametrize('with_sources', [False, True])
def test_production_preserves_legacy_dataset_and_date_scope(public_budget, monkeypatch, with_sources):
    from core.research.sources import twse
    monkeypatch.setenv(op.MODE_ENV, 'production')
    monkeypatch.setattr(tc, 'grant_trusted', lambda *a: True)
    tc.STATE.mkdir(parents=True, exist_ok=True)
    expires = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()
    official = {'operation': 'official_fetch', 'expires_at': expires, 'max_uses': 3,
                'scope': {'datasets': ['MI_INDEX'], 'dates': ['2026-10-27']}}
    if with_sources:
        official['sources'] = ['MI_INDEX']
    tc.GRANTS.write_text(json.dumps({'grants': [{'operation': 'production_mode', 'expires_at': expires, 'max_uses': 1}, official]}))
    calls = []
    fields = sorted(twse.REQUIRED_CLOSING_FIELDS)
    response = reply({'stat': 'OK', 'date': '20261027', 'tables': [{'fields': fields, 'data': [['2330' if f == '證券代號' else '100' for f in fields]]}]})
    monkeypatch.setattr(public, '_send', lambda *a, **kw: calls.append(a) or response)
    with pytest.raises(op.AuthorizationDenied, match='legacy date scope'):
        public.request('GET', twse.MI_INDEX_URL, params={'date': '20261028', 'type': 'ALL', 'response': 'json'})
    assert calls == [] and not (tc.STATE / tc.USAGE).exists()
    assert public.request('GET', twse.MI_INDEX_URL, params={'date': '20261027', 'type': 'ALL', 'response': 'json'}).status_code == 200
    assert len(calls) == 1 and tc.grant_uses(official, tc.STATE) == 1
    assert rb.status()['used'] == 7  # Production's separate grant quota does not spend the Research allowance


@pytest.mark.parametrize('field,value', [('scope', None), ('scope', []), ('scope', 'unstructured scope'), ('sources', None), ('sources', {})])
def test_malformed_explicit_production_scope_never_becomes_unrestricted(field, value):
    ctx = public.context(URL, params=PARAMS)
    assert op.official_scope_reason(ctx, {field: value})


def test_production_cannot_directly_enter_research_request_scope(public_budget, monkeypatch, sender):
    monkeypatch.setattr(op, 'current_mode', lambda *a, **kw: op.ExecutionMode.PRODUCTION)
    with pytest.raises(op.AuthorizationDenied, match='RESEARCH_SCOPE_REQUIRES_RESEARCH_ADMISSION'):
        with op.research_request_scope():
            public.request('GET', URL, params=PARAMS)
    assert sender == [] and rb.status()['used'] == 7
