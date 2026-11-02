import json
import hashlib

from app.dashboard_payloads import build_research_status_payload
from app import web_server


def test_research_snapshot_read_only_and_unavailable(tmp_path, monkeypatch):
    monkeypatch.setattr(web_server, 'RESEARCH_SNAPSHOT_DIR', tmp_path)
    client = web_server.app.test_client()
    assert client.get('/api/research/strategy-status').get_json()['status'] == 'UNAVAILABLE'
    (tmp_path / 'strategy_qualification_matrix.csv').write_text('strategy\nA\n', encoding='utf-8')
    metric = {'value': None, 'reason': 'not evaluated'}
    status = {'strategies': [{'strategy': 'A', 'version': 'v1', 'status': 'DATA_BLOCKED', 'reason': 'no data',
                              'metrics': {'net_return': metric, 'cost_stress': metric, 'max_drawdown': metric}}]}
    (tmp_path / 'research_status.json').write_text(json.dumps(status), encoding='utf-8')
    (tmp_path / 'evidence_summary.json').write_text(json.dumps({'coverage': None, 'reason': 'unknown denominator'}), encoding='utf-8')
    candidates = {'status': 'AVAILABLE', 'candidates': [{'stock_id': '2330', 'reason': 'test', 'research_only': False}]}
    (tmp_path / 'candidates.json').write_text(json.dumps(candidates), encoding='utf-8')
    def seal():
        names = ('strategy_qualification_matrix.csv', 'research_status.json', 'evidence_summary.json', 'candidates.json')
        manifest = {'label': 'RESEARCH_ONLY', 'files': {name: hashlib.sha256((tmp_path / name).read_bytes()).hexdigest() for name in names}}
        (tmp_path / 'snapshot_manifest.json').write_text(json.dumps(manifest), encoding='utf-8')
    seal()
    before = {p.name: p.read_bytes() for p in tmp_path.iterdir()}
    payload = client.get('/api/research/strategy-status').get_json()
    assert payload['status'] == 'OK' and payload['candidates'][0]['research_only'] is True
    assert payload['strategies'][0]['metrics']['net_return'] == metric
    payload['strategies'].clear()
    assert len(build_research_status_payload(tmp_path)['strategies']) == 1
    assert before == {p.name: p.read_bytes() for p in tmp_path.iterdir()}
    candidates['candidates'][0].pop('reason')
    (tmp_path / 'candidates.json').write_text(json.dumps(candidates), encoding='utf-8')
    assert client.get('/api/research/strategy-status').get_json()['status'] == 'UNAVAILABLE'
    (tmp_path / 'candidates.json').write_text(json.dumps({'status': 'BLOCKED', 'reason': 'missing PIT data', 'candidates': []}), encoding='utf-8')
    seal()
    blocked = client.get('/api/research/strategy-status').get_json()
    assert blocked['candidate_status'] == 'BLOCKED' and blocked['candidate_reason'] == 'missing PIT data'
    assert client.post('/api/research/strategy-status').status_code == 405
