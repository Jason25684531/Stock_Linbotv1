"""Ingestion authority (design D1): fixed denials, table scope, decision binding, research plans never covered. Fake grants only."""
import hashlib
import json

import pytest

from core.runtime import ingestion_authority as ia
from core.runtime import public_data as pd


@pytest.fixture
def grants(tmp_path, monkeypatch):
    decision = tmp_path / 'decision.md'
    decision.write_text('Owner decision D1\n', encoding='utf-8')
    monkeypatch.setattr(ia, 'DECISION_REF', str(decision))

    def write(**change):
        grant = {'operation': 'market_data_ingestion', 'sources': ['TWSE:MI_INDEX', 'TWSE:T86', 'MOPS:t21sc03'],
                 'datasets': ['prices_volume', 'monthly_revenue'], 'tables': ['daily_market_data', 'monthly_revenue'],
                 'market_scope': 'TWSE_ONLY', 'operations': ['read', 'commit'], 'rate_limit_seconds': 3.2, 'max_uses': 2,
                 'expires_at': '2099-01-01T00:00:00+08:00', 'decision_ref': str(decision),
                 'decision_sha256': hashlib.sha256(decision.read_bytes()).hexdigest(), **change}
        path = tmp_path / 'grants.json'
        path.write_text(json.dumps({'grants': [grant]}), encoding='utf-8')
        return path
    write.decision, write.uses = decision, tmp_path / 'uses'
    write.uses.mkdir()
    return write


def _mi_index(day='20261105'):
    return pd.context('https://www.twse.com.tw/rwd/zh/afterTrading/MI_INDEX', params={'date': day, 'type': 'ALL', 'response': 'json'})


def evaluate(path, uses, **kw):
    return ia.evaluate(trusted=True, grants_path=path, uses_root=uses, **kw)


def test_read_allowed_in_any_mode_for_a_daily_context(grants):
    grant, reason = evaluate(grants(), grants.uses, operation='read', context=_mi_index(), dataset='prices_volume')
    assert grant is not None and reason == ''


def test_untrusted_or_missing_grant_is_required(grants):
    assert ia.evaluate(trusted=False, grants_path=grants(), uses_root=grants.uses, operation='read', context=_mi_index(),
                       dataset='prices_volume') == (None, 'INGESTION_DISABLED')  # test env: built-in policy switched off


def test_commit_only_for_listed_designated_tables(grants):
    path = grants()
    assert evaluate(path, grants.uses, operation='commit', table='daily_market_data')[1] == ''
    assert evaluate(path, grants.uses, operation='commit', table='financial_statements')[1] == 'INGESTION_TABLE_NOT_GRANTED:financial_statements'
    assert evaluate(path, grants.uses, operation='commit', table='daily_recommendations')[1] == 'INGESTION_TABLE_NOT_GRANTED:daily_recommendations'
    assert evaluate(grants(tables=['daily_recommendations']), grants.uses, operation='commit', table='daily_market_data')[1] \
        == 'INGESTION_GRANT_INVALID:tables'
    assert evaluate(grants(operations=['read']), grants.uses, operation='commit', table='daily_market_data')[1] \
        == 'INGESTION_OPERATION_NOT_GRANTED:commit'


def test_edited_decision_record_revokes(grants):
    path = grants()
    grants.decision.write_text('Owner decision D1, widened by someone\n', encoding='utf-8')
    assert evaluate(path, grants.uses, operation='read', context=_mi_index(), dataset='prices_volume') == (None, 'DECISION_RECORD_CHANGED')


def test_research_plan_contexts_are_never_ingestion(grants):
    historical = _mi_index('20200106')  # inside the h4_hist window: a research job, not daily maintenance
    assert historical.job == 'h4_hist'
    assert evaluate(grants(), grants.uses, operation='read', context=historical, dataset='prices_volume')[1] == 'INGESTION_SOURCE_NOT_GRANTED:MI_INDEX'


def test_scope_source_dataset_and_quota(grants):
    path = grants()
    assert evaluate(path, grants.uses, operation='read', context=_mi_index(), dataset='prices_volume', market_scope='ALL')[1] \
        == 'INGESTION_SCOPE_EXCEEDED:ALL'
    assert evaluate(path, grants.uses, operation='read', context=_mi_index(), dataset='financials')[1] == 'INGESTION_DATASET_NOT_GRANTED:financials'
    margin = pd.context('https://www.twse.com.tw/rwd/zh/marginTrading/MI_MARGN', params={'date': '20261105', 'selectType': 'ALL', 'response': 'json'})
    assert evaluate(path, grants.uses, operation='read', context=margin, dataset='prices_volume')[1] == 'INGESTION_SOURCE_NOT_GRANTED:TWSE:MI_MARGN'
    gid = ia.tc.grant_id(json.loads(path.read_text(encoding='utf-8'))['grants'][0])
    (grants.uses / 'grant_usage.jsonl').write_text(json.dumps({'grant_id': gid, 'uses': 2}) + '\n', encoding='utf-8')
    assert evaluate(path, grants.uses, operation='read', context=_mi_index(), dataset='prices_volume')[1] == 'INGESTION_QUOTA_EXHAUSTED:2/2'


def test_effective_scope_is_the_narrowest():
    assert ia.effective_scope('ALL', {'market_scope': 'TWSE_ONLY'}) == 'TWSE_ONLY'
    assert ia.effective_scope('TWSE_ONLY', {'market_scope': 'ALL'}) == 'TWSE_ONLY'
    assert ia.effective_scope('ALL', None) == 'ALL'


def test_the_grant_must_reference_this_changes_decision_record(grants, monkeypatch):
    """Interrogation finding (Codex): a hash of any other file never binds the grant."""
    monkeypatch.setattr(ia, 'DECISION_REF', str(grants.decision))
    assert evaluate(grants(), grants.uses, operation='commit', table='daily_market_data')[1] == ''
    other = grants.decision.with_name('other.md')
    other.write_text('anything\n', encoding='utf-8')
    path = grants(decision_ref=str(other), decision_sha256=hashlib.sha256(other.read_bytes()).hexdigest())
    assert evaluate(path, grants.uses, operation='commit', table='daily_market_data') == (None, 'INGESTION_GRANT_INVALID:decision_ref')


# decision.md D7 (2026-11-09): no grant file required; the built-in routine policy has the same limits and a kill switch.

def test_builtin_policy_allows_routine_ingestion_with_the_same_limits(tmp_path, monkeypatch):
    monkeypatch.delenv('STOCK_LINBOT_INGESTION_DISABLED', raising=False)
    monkeypatch.setenv('STOCK_LINBOT_UPDATE_ROOT', str(tmp_path / 'u'))
    grant, reason = ia.evaluate(operation='read', context=_mi_index(), dataset='prices_volume', trusted=False)
    assert reason == '' and grant['builtin'] is True
    assert ia.evaluate(operation='commit', table='daily_market_data', trusted=False)[1] == ''
    assert ia.evaluate(operation='commit', table='daily_recommendations', trusted=False)[1] == 'INGESTION_TABLE_NOT_GRANTED:daily_recommendations'
    assert ia.evaluate(operation='read', context=_mi_index(), dataset='prices_volume', market_scope='ALL', trusted=False)[1] \
        == 'INGESTION_SCOPE_EXCEEDED:ALL'
    assert ia.evaluate(operation='read', context=_mi_index('20200106'), dataset='prices_volume', trusted=False)[1] \
        == 'INGESTION_SOURCE_NOT_GRANTED:MI_INDEX'  # research plans stay with the research budget


def test_kill_switch_and_daily_cap(tmp_path, monkeypatch):
    monkeypatch.delenv('STOCK_LINBOT_INGESTION_DISABLED', raising=False)
    root = tmp_path / 'u'
    monkeypatch.setenv('STOCK_LINBOT_UPDATE_ROOT', str(root))
    root.mkdir()
    (root / 'INGESTION_DISABLED').write_text('', encoding='utf-8')
    assert ia.evaluate(operation='read', context=_mi_index(), dataset='prices_volume', trusted=False) == (None, 'INGESTION_DISABLED')
    (root / 'INGESTION_DISABLED').unlink()
    monkeypatch.setenv('STOCK_LINBOT_INGESTION_DISABLED', '1')
    assert ia.evaluate(operation='commit', table='daily_market_data', trusted=False) == (None, 'INGESTION_DISABLED')
    monkeypatch.delenv('STOCK_LINBOT_INGESTION_DISABLED')
    from datetime import datetime
    today = datetime.now().astimezone().isoformat(timespec='seconds')
    rows = ''.join(json.dumps({'at': today, 'result': 'RESERVED'}) + '\n' for _ in range(ia.DAILY_CAP))
    from pathlib import Path
    Path(ia.MACHINE_ROOT).mkdir(parents=True, exist_ok=True)
    (Path(ia.MACHINE_ROOT) / 'ingestion_audit.jsonl').write_text(rows, encoding='utf-8')  # the cap is counted per machine
    assert ia.evaluate(operation='read', context=_mi_index(), dataset='prices_volume', trusted=False)[1] == f'INGESTION_QUOTA_EXHAUSTED:{ia.DAILY_CAP}/{ia.DAILY_CAP}'


def test_builtin_policy_reads_the_official_holiday_schedule_for_the_calendar(tmp_path, monkeypatch):
    """Owner 2026-11-09 one-shot brief (R1 交易日曆): the annual TWSE holiday schedule is a routine source of the built-in policy."""
    monkeypatch.delenv('STOCK_LINBOT_INGESTION_DISABLED', raising=False)
    monkeypatch.setenv('STOCK_LINBOT_UPDATE_ROOT', str(tmp_path / 'u'))
    for year in ('2026', '2027'):
        ctx = pd.context('https://openapi.twse.com.tw/v1/holidaySchedule/holidaySchedule', params={'year': year})
        assert ia.evaluate(operation='read', context=ctx, dataset='calendar', trusted=False)[1] == ''
    with pytest.raises(Exception, match='SOURCE_NOT_ALLOWLISTED'):
        pd.context('https://openapi.twse.com.tw/v1/holidaySchedule/holidaySchedule', params={'year': '2030'})


def test_routine_reads_never_reach_the_research_window(tmp_path, monkeypatch):
    """Claude review 2026-11-09 (MAJOR): an ingestion read of a 2025-or-earlier period would land in the research cache outside the
    research budget; only periods after the commit floor are read."""
    monkeypatch.delenv('STOCK_LINBOT_INGESTION_DISABLED', raising=False)
    monkeypatch.setenv('STOCK_LINBOT_UPDATE_ROOT', str(tmp_path / 'u'))
    t86 = lambda day: pd.context('https://www.twse.com.tw/rwd/zh/fund/T86', params={'date': day, 'selectType': 'ALL', 'response': 'json'})
    assert ia.evaluate(operation='read', context=t86('20261105'), dataset='prices_volume', trusted=False)[1] == ''
    assert ia.evaluate(operation='read', context=t86('20251231'), dataset='prices_volume', trusted=False)[1] == 'INGESTION_PERIOD_BEFORE_FLOOR:T86'
    revenue = lambda roc: pd.context(f'https://mopsov.twse.com.tw/nas/t21/sii/t21sc03_{roc}_10_0.html')
    assert ia.evaluate(operation='read', context=revenue(115), dataset='monthly_revenue', trusted=False)[1] == ''
    assert ia.evaluate(operation='read', context=revenue(114), dataset='monthly_revenue', trusted=False)[1].startswith('INGESTION_PERIOD_BEFORE_FLOOR')


def test_switch_and_cap_hold_for_every_update_root_and_survive_a_broken_audit_line(tmp_path, monkeypatch):
    """Adversarial review 2026-11-09: another STOCK_LINBOT_UPDATE_ROOT must not escape the machine kill switch or the daily cap,
    and a truncated audit line counts as a use instead of raising."""
    from datetime import datetime
    monkeypatch.delenv('STOCK_LINBOT_INGESTION_DISABLED', raising=False)
    machine, other = tmp_path / 'machine', tmp_path / 'other'
    machine.mkdir(), other.mkdir()
    monkeypatch.setattr(ia, 'MACHINE_ROOT', machine)
    monkeypatch.setenv('STOCK_LINBOT_UPDATE_ROOT', str(other))
    (machine / 'INGESTION_DISABLED').write_text('', encoding='utf-8')
    assert ia.evaluate(operation='commit', table='daily_market_data', trusted=False) == (None, 'INGESTION_DISABLED')
    (machine / 'INGESTION_DISABLED').unlink()
    today = datetime.now().astimezone().isoformat(timespec='seconds')
    (machine / 'ingestion_audit.jsonl').write_text(''.join(json.dumps({'at': today, 'result': 'RESERVED'}) + '\n' for _ in range(ia.DAILY_CAP - 1))
                                                   + '{"at": "' + today[:10], encoding='utf-8')
    assert ia.evaluate(operation='read', context=_mi_index(), dataset='prices_volume', trusted=False)[1] == f'INGESTION_QUOTA_EXHAUSTED:{ia.DAILY_CAP}/{ia.DAILY_CAP}'
    assert ia.describe()['uses'] == ia.DAILY_CAP
