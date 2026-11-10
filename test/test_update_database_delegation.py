"""jobs/update_database.main delegates to the market data service (design D4) and keeps its pipeline_runs step and exit-code meaning."""
import pytest

from jobs import update_database as ud


class FakeService:
    records = []

    def __init__(self, root=None):
        pass

    def run(self, mode):
        assert mode == 'daily'
        return FakeService.records.pop(0)

    def expected(self, day):
        return '2026-11-05', 'OPEN_OBSERVED'


@pytest.fixture
def pipeline(monkeypatch):
    calls = []
    monkeypatch.setattr(ud, 'MarketDataUpdateService', FakeService, raising=False)
    monkeypatch.setattr('services.market_data_update.MarketDataUpdateService', FakeService)
    monkeypatch.setattr(ud, 'record_pipeline_step_start', lambda **kw: calls.append(('start', kw)))
    monkeypatch.setattr(ud, 'record_pipeline_step_finish', lambda **kw: calls.append(('finish', kw)))
    monkeypatch.setattr(ud, 'prewarm_dashboard_aggregation_cache', lambda day: calls.append(('prewarm', day)))
    monkeypatch.setattr(ud, 'LOCK_WAIT_SECONDS', 0)
    return calls


def test_current_prices_exit_zero_and_record_success(pipeline, monkeypatch):
    FakeService.records = [{'status': 'COMPLETED', 'update_id': 'u1', 'results': []}]
    monkeypatch.setattr(ud, 'get_latest_trade_date', lambda: '2026-11-05')
    assert ud.main() == 0
    finish = [kw for kind, kw in pipeline if kind == 'finish'][0]
    assert finish['step_name'] == 'update_database' and finish['status'] == 'success' and finish['trade_date'] == '2026-11-05'
    assert ('prewarm', '2026-11-05') in pipeline


def test_no_grant_or_stale_prices_exit_one_so_picks_never_run_on_old_data(pipeline, monkeypatch):
    FakeService.records = [{'status': 'SKIPPED_INGESTION_DISABLED', 'reason': 'INGESTION_DISABLED'}]
    monkeypatch.setattr(ud, 'get_latest_trade_date', lambda: '2026-10-22')
    assert ud.main() == 1
    finish = [kw for kind, kw in pipeline if kind == 'finish'][0]
    assert finish['status'] == 'failed' and 'INGESTION_DISABLED' in finish['error_summary'] and 'expected 2026-11-05' in finish['error_summary']


def test_lock_held_is_waited_for(pipeline, monkeypatch):
    FakeService.records = [{'status': 'UPDATE_IN_PROGRESS'}, {'status': 'COMPLETED', 'results': []}]
    monkeypatch.setattr(ud, 'get_latest_trade_date', lambda: '2026-11-05')
    assert ud.main() == 0 and FakeService.records == []
