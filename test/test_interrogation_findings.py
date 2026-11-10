"""Regression tests for the verified interrogation findings (Claude adversary #1-#5, #7, #8; Codex findings are in their own files)."""
import json
import threading
from datetime import date, datetime

import pandas as pd
import pytest
import requests
from sqlalchemy import create_engine, text

from core.runtime import operation_policy as op, public_data as public
from jobs import push_to_line
from services import market_data_update as mdu
from scripts import task_controller as tc


@pytest.fixture
def db(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'm.db'}")
    with engine.begin() as conn:
        conn.execute(text('CREATE TABLE daily_market_data (stock_id TEXT, trade_date TEXT, close_price REAL, volume REAL, label TEXT)'))
        conn.execute(text("INSERT INTO daily_market_data VALUES ('2317', '2026-11-05', 10.0, 1, 'a'), ('2330', '2026-11-05', 20.0, 2, 'b')"))
    return engine


def _committer(db):
    return mdu.TableCommitter('daily_market_data', ('stock_id', 'trade_date'), engine=lambda: db)


def test_2_values_are_compared_per_key_not_by_sums(db):
    swapped = pd.DataFrame({'stock_id': ['2317', '2330'], 'trade_date': '2026-11-05', 'close_price': [20.0, 10.0], 'volume': [2, 1],
                            'label': ['b', 'a']})
    current = _committer(db).read(swapped)
    assert not mdu.same_rows(current, swapped, ('stock_id', 'trade_date'))
    assert mdu.same_rows(current, current.iloc[::-1], ('stock_id', 'trade_date'))


def test_3_concurrent_pushes_broadcast_once(tmp_path, monkeypatch):
    monkeypatch.setattr(push_to_line, 'PUBLISH_DIR', tmp_path / 'p')
    sends, results, start = [], [], threading.Barrier(8)

    def push():
        start.wait()
        try:
            results.append(push_to_line.publish_once('2026-11-09_evening', lambda: sends.append(1)))
        except push_to_line.PublishStateUnresolved:
            results.append('UNRESOLVED')
    threads = [threading.Thread(target=push) for _ in range(8)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert len(sends) == 1 and results.count('SENT') == 1 and push_to_line.publish_state('2026-11-09_evening') == 'SENT'


def test_4_today_is_never_requested_before_the_1500_cutoff(tmp_path):
    seen = []
    service = mdu.MarketDataUpdateService(root=tmp_path, adapters={'prices_volume': lambda d: seen.append(d) or {'rows': 0}},
                                          today='2026-11-09', calendar=lambda d: 'CALENDAR_UNVERIFIED', now=lambda: datetime(2026, 11, 9, 10, 0))
    assert service.run('daily', datasets=['prices_volume'])['status'] in ('SKIPPED_NOT_YET_PUBLISHED', 'UP_TO_DATE')
    assert '2026-11-09' not in service.run('manual', datasets=['prices_volume'])['dates']
    assert service.catch_up_dates() != ['2026-11-09'] and seen.count('2026-11-09') == 0


def test_5_refused_or_unreconciled_units_are_errors(tmp_path):
    adapters = {'prices_volume': lambda d: {'status': 'RECONCILIATION_MISMATCH'}}
    service = mdu.MarketDataUpdateService(root=tmp_path, adapters=adapters, today='2026-11-06', calendar=lambda d: 'OPEN_OBSERVED')
    assert service.run('manual', dates=['2026-11-05'], datasets=['prices_volume'])['status'] == 'COMPLETED_WITH_ERRORS'
    for status in ('COMMIT_AUTH_BLOCKED', 'BACKUP_REQUIRED', 'COMMIT_FLOOR_VIOLATION', 'IMPLEMENTED_BUT_AUTH_BLOCKED', 'RECONCILIATION_UNAVAILABLE'):
        assert status in mdu.FAILING


def test_7_restore_refuses_when_a_newer_commit_replaced_the_rows(tmp_path, db):
    def service(close):
        frame = pd.DataFrame({'stock_id': ['2330'], 'trade_date': ['2026-11-06'], 'close_price': [close], 'volume': [5.0], 'label': ['x']})
        adapter = lambda d: {'frame': frame, 'rows': 1, 'min_rows': 1, 'latest_trade_date': d, 'required_empty': []}
        return mdu.MarketDataUpdateService(root=tmp_path / 'u', adapters={'prices_volume': adapter}, committers={'prices_volume': _committer(db)},
                                           enforce=lambda t: None, today='2026-11-07', calendar=lambda d: 'OPEN_OBSERVED')
    first = service(1000.0).run('manual', dates=['2026-11-06'], datasets=['prices_volume'])
    service(1001.0).run('manual', dates=['2026-11-06'], datasets=['prices_volume'], force=True)
    assert service(1000.0).restore(first['update_id']) == [{'table': 'daily_market_data', 'date': '2026-11-06', 'status': 'RECONCILIATION_REQUIRED'}]
    assert pd.read_sql(text("SELECT close_price FROM daily_market_data WHERE trade_date='2026-11-06'"), db)['close_price'].tolist() == [1001.0]


def test_8_twse_only_refuses_otc_requests_before_sending(tmp_path, monkeypatch):
    import hashlib
    from core.runtime import ingestion_authority as ia
    decision = tmp_path / 'decision.md'
    decision.write_text('D1\n', encoding='utf-8')
    grant = {'operation': 'market_data_ingestion', 'sources': ['MOPS:t21sc03', 'MOPS:t163sb04'], 'datasets': ['monthly_revenue', 'financials'],
             'tables': ['monthly_revenue'], 'market_scope': 'TWSE_ONLY', 'operations': ['read'], 'rate_limit_seconds': 3.2, 'max_uses': 9,
             'expires_at': '2099-01-01T00:00:00+08:00', 'decision_ref': str(decision), 'decision_sha256': hashlib.sha256(decision.read_bytes()).hexdigest()}
    path = tmp_path / 'g.json'
    path.write_text(json.dumps({'grants': [grant]}), encoding='utf-8')
    monkeypatch.setattr(ia, 'DECISION_REF', str(decision))
    otc = public.context('https://mopsov.twse.com.tw/nas/t21/otc/t21sc03_115_10_0.html')
    form = public.context('https://mopsov.twse.com.tw/mops/web/ajax_t163sb04', method='POST',
                          data={'encodeURIComponent': '1', 'step': '1', 'firstin': '1', 'off': '1', 'TYPEK': 'otc', 'year': '115', 'season': '03'})
    sii = public.context('https://mopsov.twse.com.tw/nas/t21/sii/t21sc03_115_10_0.html')
    ev = lambda ctx, ds: ia.evaluate(operation='read', context=ctx, dataset=ds, trusted=True, grants_path=path, uses_root=tmp_path)[1]
    assert ev(otc, 'monthly_revenue') == 'INGESTION_SCOPE_EXCEEDED:TWSE_ONLY' and ev(form, 'financials') == 'INGESTION_SCOPE_EXCEEDED:TWSE_ONLY'
    assert ev(sii, 'monthly_revenue') == ''


def test_1_a_no_data_reply_for_today_is_not_cached_and_never_becomes_a_closure(tmp_path, monkeypatch, public_budget):
    from core.research.sources.twse import NON_TRADING_STAT, RequestGate
    monkeypatch.setattr(RequestGate, 'wait', lambda gate: gate._release())
    monkeypatch.setenv('STOCK_LINBOT_UPDATE_ROOT', str(tmp_path / 'u'))
    monkeypatch.setattr(public, '_today_ymd', lambda: '20261106')
    monkeypatch.setattr(op, 'enforce', lambda *a, **k: None)
    monkeypatch.setattr(tc, 'require_grant', lambda *a, **k: {'rate_limit_seconds': 3.2})
    monkeypatch.setattr(tc, 'grant_id', lambda g: 'g')
    monkeypatch.setattr(tc, 'grant_uses', lambda g: 0)
    sends = []

    def reply(*a, **k):
        sends.append(1)
        r = requests.Response()
        r.status_code, r._content, r.url = 200, json.dumps({'stat': NON_TRADING_STAT}).encode(), a[1]
        r.headers['Content-Type'] = 'application/json'
        return r
    monkeypatch.setattr(public, '_send', reply)
    url = 'https://www.twse.com.tw/rwd/zh/afterTrading/MI_INDEX?date=20261106&type=ALL&response=json'
    with public.ingestion_scope('prices_volume', 'TWSE_ONLY'):
        public.request('GET', url)
        public.request('GET', url)
    assert len(sends) == 2  # not served from a cache the next time: a same-day no-data reply is not a verified observation
