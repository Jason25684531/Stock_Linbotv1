from pathlib import Path

import pandas as pd
import pytest
from linebot.v3.messaging import FlexBox, FlexBubble


def test_production_consumers_never_read_research_snapshot(tmp_path, monkeypatch):
    from app import dashboard_payloads, web_server
    from jobs import push_to_line, scheduler

    snapshot = tmp_path / 'snapshot'
    snapshot.mkdir()
    (snapshot / 'candidates.json').write_text('{"candidates":[{"stock_id":"9999"}]}', encoding='utf-8')
    monkeypatch.setattr(web_server, 'RESEARCH_SNAPSHOT_DIR', snapshot)
    monkeypatch.setattr(dashboard_payloads, 'build_research_status_payload', lambda *a, **k: pytest.fail('research payload read'))
    import builtins
    original_open, original_builtin_open = Path.open, builtins.open
    def guarded_open(path, *args, **kwargs):
        if path.resolve().is_relative_to(snapshot.resolve()):
            pytest.fail('production path read research snapshot')
        return original_open(path, *args, **kwargs)
    def guarded_builtin_open(file, *args, **kwargs):
        if isinstance(file, (str, Path)) and Path(file).resolve().is_relative_to(snapshot.resolve()):
            pytest.fail('production path read research snapshot')
        return original_builtin_open(file, *args, **kwargs)
    monkeypatch.setattr(Path, 'open', guarded_open)
    monkeypatch.setattr(builtins, 'open', guarded_builtin_open)
    monkeypatch.setattr(web_server, '_latest_ui_market_date', lambda: '2025-01-02')
    rows = pd.DataFrame([{'stock_id': '2330', 'stock_name': 'example', 'ai_score': 1}])
    monkeypatch.setattr(web_server.services, 'get_recommendations_with_market_fallback',
                        lambda **kw: (rows, {'recommendation_date': '2025-01-02', 'resolution_source': 'persisted'}))
    response = web_server.app.test_client().get('/api/market/recommendations').get_json()
    assert [row['stock_id'] for row in response['data']['recommendations']] == ['2330']
    monkeypatch.setattr(push_to_line, 'build_news_summary_bubble', lambda *a, **k: FlexBubble(body=FlexBox(layout='vertical', contents=[])))
    result = push_to_line._build_morning_flex(news_summary='', picks=[('strategy', '2330', 10.0, .5)],
                                             market_status='normal', date_str='2025-01-02', picks_title='test')
    assert result is not None
    scheduled = []
    monkeypatch.setattr(scheduler, 'run_job', lambda target, *a, **k: scheduled.append(target) or 0)
    assert scheduler.run_pipeline('daily', stop_on_error=True, dry_run=True) == 0
    assert scheduled == ['update_database', 'run_daily', 'daily_backtest_validation', 'push_to_line']


def test_fabricated_passing_research_candidates_cannot_promote_certification(tmp_path, monkeypatch):
    """Phase 7: a research track where every strategy passes the screen leaves the certification track equal to the matrix."""
    import csv
    import hashlib
    import json
    from app.dashboard_payloads import build_research_status_payload
    from core.research.evidence_ledger import append
    from scripts import assemble_research_snapshot as assembly
    from scripts import research_acceptance

    matrix = tmp_path / 'matrix.csv'
    with matrix.open('w', encoding='utf-8', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=['strategy', 'DATA_STATUS', 'reasons', 'RESEARCH_STATUS', 'RESEARCH_REASON'])
        writer.writeheader()
        writer.writerows({'strategy': f'S{i}', 'DATA_STATUS': 'DATA_BLOCKED', 'reasons': 'missing MOPS terms',
                          'RESEARCH_STATUS': 'DATA_BLOCKED', 'RESEARCH_REASON': 'DATA_STATUS=DATA_BLOCKED: missing MOPS terms'} for i in range(22))
    evidence = tmp_path / 'evidence'
    evidence.mkdir()
    ledger = evidence / 'evidence.jsonl'
    append(ledger, [dict(stock_id='S1', event_date='2025-01-02', field='ex_date', value_or_UNKNOWN='UNKNOWN', source='fixture',
                         sha256='a' * 64, fetched_at=None, grant_scope='test', tier='UNKNOWN')])
    (evidence / 'evidence_summary.json').write_text(json.dumps({'coverage': None, 'reason': 'unknown denominator'}), encoding='utf-8')
    (evidence / 'evidence_audit.json').write_text(json.dumps({'chain': 'PASS', 'sha256': hashlib.sha256(ledger.read_bytes()).hexdigest()}), encoding='utf-8')
    monkeypatch.setattr(assembly, 'MATRIX', matrix)
    monkeypatch.setattr(assembly, 'EVIDENCE', evidence)
    monkeypatch.setattr(assembly, 'OUTPUT_ROOT', tmp_path)

    def snapshot(name, status):
        track = tmp_path / f'{name}.json'
        rows = [{'strategy': f'S{i}', 'status': status, 'research_only': True, 'candidates': [{'config_id': f'S{i}-best', 'candidate': True}]}
                for i in range(22)]
        track.write_text(json.dumps({'label': 'DIAGNOSTIC_ONLY', 'data_label': 'RESEARCH_UNCERTIFIED', 'terms_status': 'RESEARCH_ASSUMPTIONS',
                                     'research_only': True, 'strategies': rows,
                                     'candidates': [{'strategy': f'S{i}', 'candidate': True} for i in range(22)]}), encoding='utf-8')
        output = tmp_path / name
        assembly.assemble(output, research_track=track)
        return output

    passing = snapshot('all_pass', 'RESEARCH_RUN_PASS_SCREEN')
    payload = build_research_status_payload(passing)
    with matrix.open(encoding='utf-8-sig', newline='') as stream:
        expected = {row['strategy']: row['RESEARCH_STATUS'] for row in csv.DictReader(stream)}
    assert {row['strategy']: row['status'] for row in payload['certification_track']['strategies']} == expected
    assert {row['status'] for row in payload['certification_track']['strategies']} == {'DATA_BLOCKED'}
    assert all(row['research_only'] is True for row in payload['research_track']['strategies'])
    assert research_acceptance.two_track_snapshot(passing)['status'] == 'PASS'
    for state in sorted(research_acceptance.CERTIFICATION_STATES):  # a research row may never carry a certification state
        check = research_acceptance.two_track_snapshot(snapshot('carries_' + state, state))
        assert check['status'] == 'FAIL' and 'research track carries a certification status' in check['problems']
