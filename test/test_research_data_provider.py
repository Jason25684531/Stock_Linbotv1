import json
from types import SimpleNamespace

import pytest

from core.research.data_provider import resolve
from core.runtime.operation_policy import AuthorizationDenied


def test_unapproved_source_never_sends_and_diagnostic_does_not_certify(tmp_path, monkeypatch):
    class FakeSession:
        calls = []

        def request(self, *args, **kwargs):
            self.calls.append((args, kwargs))
            raise AssertionError('unapproved source reached HTTP')

    session = FakeSession()
    monkeypatch.setattr('core.runtime.public_data.request', session.request)
    path = tmp_path / 'requests.json'
    need = dict(source='TPEx', purpose='corporate actions', max_requests=None, cost=None,
                licence_limits='unknown', alternatives='official cache')
    result = resolve([{'url': 'https://unapproved.example/data'}], path, need, b'diagnostic')
    assert result['status'] == 'DATA_BLOCKED'
    assert result['provenance']['tier'] == 'THIRD_PARTY_DIAGNOSTIC'
    resolve([], path, need)
    assert json.loads(path.read_text(encoding='utf-8')) == [{**need, 'max_requests': 'UNKNOWN', 'cost': 'UNKNOWN', 'flag': 'NOT_APPROVABLE'}]
    assert session.calls == []
    incomplete = resolve([], path, {'source': 'x'})
    assert incomplete['status'] == 'DATA_BLOCKED' and incomplete['approval_request'].startswith('NOT_WRITTEN')


def test_cache_priority_and_corruption_fail_closed(tmp_path, monkeypatch):
    contexts = {name: SimpleNamespace(source='twse', purpose='test', job='od_p6', name=name, dataset='MI_INDEX',
                                      parameters=(('date', '20250102'),)) for name in ('first', 'second')}
    monkeypatch.setattr('core.runtime.public_data.context', lambda url, **kwargs: contexts[url])
    monkeypatch.setattr('core.runtime.public_data.request', lambda *a, **k: pytest.fail('network called'))
    fields = ['證券代號', '證券名稱', '成交股數', '成交筆數', '成交金額', '開盤價', '最高價', '最低價', '收盤價']
    payload = {'tables': [{'title': '每日收盤行情', 'fields': fields,
                           'data': [['2330', 'fixture', '2,000', '1', '20000', '10', '11', '9', '10']]}]}
    response = SimpleNamespace(content=b'verified', json=lambda: payload)
    monkeypatch.setattr('core.runtime.public_data.cached', lambda ctx: response if ctx.name == 'second' else None)
    need = dict(source='official', purpose='test', max_requests=1, cost=0, licence_limits='public', alternatives='none',
                field='close_price', stock_id='2330', date='2025-01-02')
    result = resolve([{'url': 'first'}, {'url': 'second'}], tmp_path / 'requests.json', need)
    assert result['status'] == 'AVAILABLE' and result['provenance']['tier'] == 'LOCAL_OFFICIAL_CACHE'
    assert result['value'] == 10 and result['certification'] == 'SOURCE_VALUE_ONLY'
    assert result['provenance']['fetched_at'] is None
    monkeypatch.setattr('core.runtime.public_data.cached', lambda ctx: (_ for _ in ()).throw(AuthorizationDenied('CACHE_INVALID')))
    with pytest.raises(AuthorizationDenied, match='CACHE_INVALID'):
        resolve([{'url': 'first'}], tmp_path / 'requests.json', need)


def test_unrelated_official_dataset_never_certifies_action_terms(tmp_path, monkeypatch):
    ctx = SimpleNamespace(source='twse', purpose='test', job='od_p6', dataset='MI_INDEX', parameters=(('date', '20250102'),))
    monkeypatch.setattr('core.runtime.public_data.context', lambda *a, **k: ctx)
    monkeypatch.setattr('core.runtime.public_data.cached', lambda *a, **k: pytest.fail('unsupported field touched cache'))
    monkeypatch.setattr('core.runtime.public_data.request', lambda *a, **k: pytest.fail('unsupported field sent request'))
    need = dict(source='official', purpose='action terms', max_requests=None, cost=None, licence_limits='unknown',
                alternatives='none', field='cash_dividend', stock_id='2330', date='2025-01-02')
    result = resolve([{'url': 'first'}], tmp_path / 'requests.json', need)
    written = json.loads((tmp_path / 'requests.json').read_text(encoding='utf-8'))
    assert result['status'] == 'DATA_BLOCKED' and written == [{**need, 'max_requests': 'UNKNOWN', 'cost': 'UNKNOWN', 'flag': 'NOT_APPROVABLE'}]


def test_detail_fields_are_blocked_until_the_detail_schema_is_confirmed(tmp_path, monkeypatch):
    ctx = SimpleNamespace(source='twse', purpose='od_p6', job='od_p6', dataset='TWT49U_DETAIL', parameters=())
    monkeypatch.setattr('core.runtime.public_data.context', lambda *a, **k: ctx)
    monkeypatch.setattr('core.runtime.public_data.cached', lambda *a, **k: pytest.fail('detail cache read'))
    monkeypatch.setattr('core.runtime.public_data.request', lambda *a, **k: pytest.fail('detail request sent'))
    need = dict(source='TWSE', purpose='action terms', max_requests=1, cost=0, licence_limits='public',
                alternatives='none', field='cash_dividend', stock_id='3017', date='2023-02-17')
    result = resolve([{'url': 'detail'}], tmp_path / 'requests.json', need)
    assert result == {'status': 'DATA_BLOCKED', 'reason': 'OD_P6_SCHEMA_UNCONFIRMED', 'provenance': None}
    assert not (tmp_path / 'requests.json').exists()  # the source is approved; the blocker is the schema, not authorization
