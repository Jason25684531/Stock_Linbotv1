import json
from types import SimpleNamespace

import pandas as pd
import pytest

from core.runtime import research_budget
from core.runtime.operation_policy import AuthorizationDenied
from scripts import research_workflow as rw

BODY = json.dumps([{'Name': '元旦', 'Date': '1150101', 'Weekday': '四', 'Description': ''},
                   {'Name': '開始交易', 'Date': '1150102', 'Weekday': '五', 'Description': '開始交易'}]).encode('utf-8')


@pytest.fixture
def closures(tmp_path, monkeypatch):
    path = tmp_path / 'closures.json'
    path.write_text('{"closures": [], "annual_schedule_years": []}', encoding='utf-8')
    audit = []
    monkeypatch.setattr(research_budget, 'audit_rows', lambda: list(audit))
    monkeypatch.setattr(research_budget, 'STATE', tmp_path / 'budget')  # locks and gates never touch the real budget folder
    monkeypatch.setattr(rw, 'load_sessions', lambda: {pd.Timestamp('2025-12-31'): (True, None, None)})
    return SimpleNamespace(path=path, audit=audit, before=path.read_bytes())


def test_denied_budget_sends_nothing_and_keeps_closures(tmp_path, monkeypatch, closures):
    monkeypatch.setattr('core.runtime.public_data._send', lambda *a, **k: (_ for _ in ()).throw(AssertionError('HTTP sent')))
    monkeypatch.setattr('core.runtime.public_data.cached', lambda ctx: None)
    monkeypatch.setattr('core.runtime.operation_policy.enforce', lambda *a, **k: None)  # reach the budget layer itself
    def denied(*a, **k):
        raise research_budget.BudgetDenied('BUDGET_HISTORY_UNKNOWN: calendar_2026; no assumption of zero prior usage')
    monkeypatch.setattr(research_budget, 'reserve', denied)
    result = rw.fetch_holidays(tmp_path / 'out', path=closures.path)
    assert result['status'] == 'BLOCKED' and result['http_attempted'] is False and 'BUDGET_HISTORY_UNKNOWN' in result['reason']
    assert closures.path.read_bytes() == closures.before
    with pytest.raises(FileExistsError):  # each attempt writes a new evidence folder
        rw.fetch_holidays(tmp_path / 'out', path=closures.path)


def test_failure_after_sending_is_reported_as_an_attempt(tmp_path, monkeypatch, closures):
    def sent_then_stopped(*a, **k):
        closures.audit.append({'job': 'calendar_2026'})
        raise AuthorizationDenied('HTTP_STOP: status 503; no automatic retry or redirect')
    monkeypatch.setattr('core.runtime.public_data.request', sent_then_stopped)
    result = rw.fetch_holidays(tmp_path / 'out', path=closures.path)
    assert result['status'] == 'BLOCKED' and result['http_attempted'] is True
    assert json.loads((tmp_path / 'out' / 'holiday_fetch.json').read_text(encoding='utf-8'))['http_attempted'] is True


def test_verified_response_is_saved_hashed_and_imported(tmp_path, monkeypatch, closures):
    def sent(*a, **k):
        closures.audit.append({'job': 'calendar_2026'})
        return SimpleNamespace(content=BODY, research_cache_hit=False)
    monkeypatch.setattr('core.runtime.public_data.request', sent)
    result = rw.fetch_holidays(tmp_path / 'out', path=closures.path)
    data = json.loads(closures.path.read_text(encoding='utf-8'))
    assert result['status'] == 'IMPORTED' and result['closures_added'] == 1 and len(result['raw_sha256']) == 64
    assert result['http_attempted'] is True  # read from the audit, not from a cache flag
    assert data['annual_schedule_years'] == [2026] and [c['date'] for c in data['closures']] == ['2026-01-01']


def test_a_holiday_with_cached_trading_data_is_undone(tmp_path, monkeypatch, closures):
    monkeypatch.setattr(rw, 'load_sessions', lambda: {pd.Timestamp('2026-01-01'): (True, None, None)})
    monkeypatch.setattr('core.runtime.public_data.request', lambda *a, **k: SimpleNamespace(content=BODY, research_cache_hit=False))
    result = rw.fetch_holidays(tmp_path / 'out', path=closures.path)
    assert result['status'] == 'CONFLICT' and result['conflicts'] == ['2026-01-01'] and result['closures_added'] == 0
    assert closures.path.read_bytes() == closures.before


def test_failed_import_or_conflict_without_a_previous_file_leaves_no_file(tmp_path, monkeypatch, closures):
    closures.path.unlink()
    monkeypatch.setattr(rw, 'load_sessions', lambda: {pd.Timestamp('2026-01-01'): (True, None, None)})
    monkeypatch.setattr('core.runtime.public_data.request', lambda *a, **k: SimpleNamespace(content=BODY, research_cache_hit=True))
    result = rw.fetch_holidays(tmp_path / 'conflict', path=closures.path)
    assert result['status'] == 'CONFLICT' and not closures.path.exists() and result['closures_sha256_after'] is None
    monkeypatch.setattr(rw, 'import_holidays', lambda *a, **k: (_ for _ in ()).throw(ValueError('bad shape')))
    failed = rw.fetch_holidays(tmp_path / 'failed', path=closures.path)
    assert failed['status'] == 'BLOCKED' and 'IMPORT_FAILED' in failed['reason'] and not closures.path.exists()
    assert json.loads((tmp_path / 'failed' / 'holiday_fetch.json').read_text(encoding='utf-8'))['status'] == 'BLOCKED'
