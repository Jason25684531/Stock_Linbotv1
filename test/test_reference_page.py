"""Reference results in each strategy report (formerly GET /reference): values equal the report, no ranking, banner and disclosure."""
import json

import pytest

from core.research.reference import runner


@pytest.fixture
def report(tmp_path, monkeypatch):
    monkeypatch.setattr(runner, 'OUT', tmp_path / 'reference_mode')
    metric = lambda v: {'value': v, 'reason': None}
    rows = [{'strategy_id': 'mean_reversion', 'family': 'native', 'state': 'REUSE', 'reason': 'IDENTICAL_IDENTITY:R1', 'run_id': 'R1',
             'comparison_group': 'REFERENCE_ENGINE:abc', 'warnings': [], 'error': None,
             'metrics': {'base': {'total_return': metric(0.1234), 'sharpe': {'value': None, 'reason': 'ZERO_VARIANCE'}, 'max_drawdown': metric(-0.2)},
                         'x2': {'total_return': metric(0.1001), 'sharpe': metric(0.5), 'max_drawdown': metric(-0.21)}},
             'levels': {'ENGINEERING_PASS': True, 'REPRODUCIBLE': True, 'DATA_TRUST_VERIFIED': False, 'CERTIFIED': False},
             'level_reasons': {'DATA_TRUST_VERIFIED': runner.DATA_TRUST_MISSING, 'CERTIFIED': 'NO_POSITIVE_CERTIFICATION_RECORD'}},
            {'strategy_id': 'quality_growth', 'family': 'native', 'state': 'DATA_BLOCKED', 'reason': 'MONTHLY_REVENUE_NO_ANNOUNCEMENT_TIME',
             'run_id': None, 'comparison_group': None, 'metrics': None, 'warnings': [], 'error': None,
             'levels': {'ENGINEERING_PASS': False, 'REPRODUCIBLE': False, 'DATA_TRUST_VERIFIED': False, 'CERTIFIED': False}, 'level_reasons': {}}]
    batch = {'batch_id': '20261109T000000Z-aaaaaaaa', 'labels': runner.LABELS, 'disclosure': runner.DISCLOSURE, 'reference_group': 'REFERENCE_ENGINE:abc',
             'counts': {s: 0 for s in runner.STATES} | {'REUSE': 1, 'DATA_BLOCKED': 1}, 'rows': rows, 'created_at': 'now', 'plan_only': False}
    folder = runner.write_report(batch)
    (folder / 'gap_report.json').write_text(json.dumps({'strategies': {'quality_growth': {'gaps': [
        {'field': 'monthly_revenue.announcement_time', 'recovery': {'status': 'PIT_UNVERIFIABLE', 'reason': 'BUDGET_UNASSIGNED'}}]}}}), encoding='utf-8')
    return batch


def _reference_section(path):
    from app import app
    html = app.test_client().get(path).get_data(as_text=True)
    return html[html.index('data-testid="reference-row"'):html.index('data-testid="cross-regime-section"')]


def test_report_evidence_shows_the_stored_reference_row_banner_and_no_ranking(report):
    """/reference is retired (2026-11-16); each strategy report's evidence section shows that strategy's stored row."""
    from app import app
    assert app.test_client().get('/reference').headers['Location'].endswith('/strategies')
    html = _reference_section('/platform/strategies/mean_reversion')
    assert 'REFERENCE_ONLY · NOT_CERTIFIED' in html and runner.DISCLOSURE in html
    assert '0.1234' in html and 'ZERO_VARIANCE' in html and 'ENGINEERING_PASS' in html
    blocked = _reference_section('/platform/strategies/quality_growth')
    assert 'MONTHLY_REVENUE_NO_ANNOUNCEMENT_TIME' in blocked and 'PIT_UNVERIFIABLE' in blocked
    lowered = html.lower()
    assert 'sortable' not in lowered and 'data-sort' not in lowered and '<form' not in lowered


def test_no_json_ranking_endpoint():
    from app import app
    client = app.test_client()
    assert client.get('/api/reference/report').status_code == 404
    assert client.post('/reference').status_code == 405


def test_without_a_batch_the_page_says_so(tmp_path, monkeypatch):
    from app import app
    monkeypatch.setattr(runner, 'OUT', tmp_path / 'empty')
    assert 'NO_REFERENCE_BATCH' in app.test_client().get('/platform/strategies/mean_reversion').get_data(as_text=True)


def test_admin_data_shows_scope_and_ingestion_authority(monkeypatch, tmp_path):
    from app import app, platform_routes
    from services.market_data_update import MarketDataUpdateService
    svc = MarketDataUpdateService(root=tmp_path, adapters={}, today='2026-11-09', market_scope='TWSE_ONLY', calendar=lambda d: 'CALENDAR_UNVERIFIED')
    monkeypatch.setattr(platform_routes, '_service', lambda: svc)
    monkeypatch.setattr(platform_routes, 'is_admin', lambda: True)
    html = app.test_client().get('/admin/data').get_data(as_text=True)
    assert 'data-testid="market-scope">TWSE_ONLY<' in html and 'TPEx NOT_COVERED' in html
    assert 'data-testid="ingestion-reason">INGESTION_DISABLED<' in html
