import csv
import hashlib
import json

import pytest

from app.dashboard_payloads import build_research_status_payload
from core.research.evidence_ledger import append
from scripts import assemble_research_snapshot as assembly


def test_snapshot_keeps_missing_candidates_and_coverage_blocked(tmp_path, monkeypatch):
    matrix = tmp_path / 'matrix.csv'
    with matrix.open('w', encoding='utf-8', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=['strategy', 'DATA_STATUS', 'reasons', 'RESEARCH_STATUS', 'RESEARCH_REASON'])
        writer.writeheader()
        writer.writerows({'strategy': f'S{i}', 'DATA_STATUS': 'DATA_BLOCKED', 'reasons': 'missing OD-P6',
                          'RESEARCH_STATUS': 'DATA_BLOCKED', 'RESEARCH_REASON': 'DATA_STATUS=DATA_BLOCKED: missing OD-P6'}
                         for i in range(22))
    evidence = tmp_path / 'evidence'
    evidence.mkdir()
    ledger = evidence / 'evidence.jsonl'
    append(ledger, [dict(stock_id='S1', event_date='2025-01-02', field='ex_date', value_or_UNKNOWN='UNKNOWN',
                         source='fixture', sha256='a' * 64, fetched_at=None, grant_scope='test', tier='UNKNOWN')])
    (evidence / 'evidence_summary.json').write_text(json.dumps({'coverage': None, 'reason': 'unknown denominator'}), encoding='utf-8')
    (evidence / 'evidence_audit.json').write_text(json.dumps({'chain': 'PASS', 'sha256': hashlib.sha256(ledger.read_bytes()).hexdigest()}), encoding='utf-8')
    monkeypatch.setattr(assembly, 'MATRIX', matrix)
    monkeypatch.setattr(assembly, 'EVIDENCE', evidence)
    monkeypatch.setattr(assembly, 'OUTPUT_ROOT', tmp_path)
    output = tmp_path / 'snapshot'
    assert len(assembly.assemble(output)['files']) == 5
    payload = build_research_status_payload(output)
    assert payload['status'] == 'OK' and len(payload['strategies']) == 22
    assert payload['candidate_status'] == 'BLOCKED' and payload['data_completeness']['coverage'] is None
    with pytest.raises(FileExistsError):
        assembly.assemble(output)
    track = tmp_path / 'research_input.json'
    track.write_text(json.dumps({'label': 'DIAGNOSTIC_ONLY', 'data_label': 'RESEARCH_UNCERTIFIED',
                                 'terms_status': 'RESEARCH_ASSUMPTIONS', 'research_only': True,
                                 'strategies': [{'strategy': 'S0', 'status': 'RESEARCH_RUN_PASS_SCREEN', 'research_only': True}]}), encoding='utf-8')
    second = tmp_path / 'dual_snapshot'
    assert len(assembly.assemble(second, research_track=track)['files']) == 6
    dual = build_research_status_payload(second)
    assert dual['research_track']['strategies'][0]['status'] == 'RESEARCH_RUN_PASS_SCREEN'
    assert dual['certification_track']['strategies'][0]['status'] == 'DATA_BLOCKED'
    (second / 'research_track.json').write_text('{}', encoding='utf-8')
    assert build_research_status_payload(second)['status'] == 'UNAVAILABLE'
