"""Fail-closed publication and exact saved-value rendering; no research on GET."""
import json

import pytest

from services import strategy_v4 as service
from core.backtest import run_store


def client():
    from app import app
    app.config['TESTING'] = True
    return app.test_client()


@pytest.fixture
def saved(tmp_path, monkeypatch):
    monkeypatch.setattr(service, 'ROOT', tmp_path)
    monkeypatch.setattr(service, 'OUT', tmp_path / 'outputs/strategy_v4')
    out = service.OUT; (out / 'results').mkdir(parents=True); (out / 'publication').mkdir(); (out / 'acceptance').mkdir()
    sid = 'mad_distance'
    summary = {k: {'value': v, 'reason': None} for k, v in {'cagr': .1234, 'cagr_stress': -.023, 'max_drawdown': -.22}.items()}
    summary['total_return'] = {'value': None, 'reason': 'NOT_AVAILABLE'}
    result = {'strategy_id': sid, 'status': 'COMPARABLE', 'common_verified_span': [2023, 2025], 'conclusion': 'test',
              'cagr_delta': {'value': .02}, 'gap_5': {'value': -.0734}, 'gap_10': {'value': -.0234}}
    hashes = {}
    for label, root in [('original', tmp_path / 'outputs/platform_v2/runs'), ('fixed', out / 'runs')]:
        rid = '20261115T000000Z-00000001' if label == 'original' else '20261115T000000Z-00000002'
        folder = root / rid; folder.mkdir(parents=True)
        (folder / 'run.json').write_text(json.dumps({'publishable': True}), encoding='utf-8')
        manifest = {'run.json': run_store.file_hash(folder / 'run.json')}
        (folder / 'manifest.json').write_text(json.dumps(manifest), encoding='utf-8')
        digest = run_store.file_hash(folder / 'manifest.json')
        audit = out / 'acceptance' / f'ledger_{rid}.json'
        audit.write_text(json.dumps({'manifest_sha256': digest, 'tracks': {t: {'status': 'PUBLISHABLE'} for t in ['base', 'stress']}}), encoding='utf-8')
        hashes[audit.relative_to(tmp_path).as_posix()] = run_store.file_hash(audit)
        result[label] = {'run_id': rid, 'source': folder.relative_to(tmp_path).as_posix(), 'manifest_sha256': digest, 'publishable': True,
                         'summary': summary, 'full_window': summary, 'series': {'date': ['2023-01-02','2023-01-03'], 'base': [1,1.1], 'stress': [1,.99], 'benchmark': [1,1.05]},
                         'checks': [], 'costs': {}, 'liquidity': {}, 'unfilled_reasons': {}, 'annual_turnover_full_window': {'value': 1},
                         'variant_id': label, 'run_identity': label, 'snapshot_id': 'HS-test', 'rule_version': 'LOT_ENGINE_V1', 'evaluation_id': None, 'data_trust': {}, 'parameters': {}}
    path = out / 'results' / f'{sid}.json'; path.write_text(json.dumps(result), encoding='utf-8')
    hashes[path.relative_to(tmp_path).as_posix()] = run_store.file_hash(path)
    marker = out / 'publication' / f'{sid}.json'
    marker.write_text(json.dumps({'status': 'PASS', 'strategy_id': sid, 'sha256': hashes}), encoding='utf-8')
    return path, marker, result


def test_saved_values_chart_and_unknowns_render_exactly(saved):
    response = client().get('/platform/research/v4/mad_distance')
    html = response.get_data(as_text=True)
    assert response.status_code == 200
    assert '12.34%' in html and '-2.30%' in html and 'NOT_AVAILABLE' in html
    assert 'v4-chart-data' in html and 'NOT_CERTIFIED' in html
    assert client().get('/platform/research/v4').status_code == 200
    assert client().get('/platform/research/v4/unknown').status_code == 404
    assert client().post('/platform/research/v4').status_code == 405


@pytest.mark.parametrize('kind', ['result', 'run', 'audit', 'missing', 'outside', 'malformed_hashes'])
def test_tampering_hides_kpis(saved, kind):
    path, marker, result = saved
    if kind == 'result':
        path.write_text('{}', encoding='utf-8')
    elif kind == 'run':
        source = service.ROOT / result['fixed']['source'] / 'run.json'
        source.write_text('{"publishable": false}', encoding='utf-8')
    elif kind == 'audit':
        (service.OUT / 'acceptance' / f"ledger_{result['fixed']['run_id']}.json").write_text('{}', encoding='utf-8')
    elif kind == 'outside':
        data = json.loads(marker.read_text(encoding='utf-8')); data['sha256']['../../outside'] = '0'*64
        marker.write_text(json.dumps(data), encoding='utf-8')
    elif kind == 'malformed_hashes':
        data = json.loads(marker.read_text(encoding='utf-8')); data['sha256'] = []
        marker.write_text(json.dumps(data), encoding='utf-8')
    else:
        marker.write_text('{}', encoding='utf-8')
    html = client().get('/platform/research/v4/mad_distance').get_data(as_text=True)
    assert 'v4-blocked' in html and 'v4-chart-data' not in html and '12.34%' not in html


def test_missing_evidence_get_never_runs_research(tmp_path, monkeypatch):
    monkeypatch.setattr(service, 'OUT', tmp_path)
    from scripts import platform_v2_engine
    monkeypatch.setattr(platform_v2_engine, 'execute', lambda *a, **kw: pytest.fail('GET started backtest'))
    response = client().get('/platform/research/v4')
    assert response.status_code == 200 and response.get_data(as_text=True).count('data-testid="v4-blocked"') == 2
