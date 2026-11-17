"""V3 publication/linkage/UI contracts, using isolated stored results, never live jobs."""
import copy
import json
import re

import pytest

from test_platform_v2_web import stored, no_database_in_the_shell, approve_run, client, PUB, BAD


def test_linked_evidence_matches_run_and_period(stored):
    from app import platform_v2_routes as r
    from services import web_dashboard as w
    approve_run(stored['out'], 'backtest', PUB)
    run = r._loaded(r.RUNS, PUB)['run']
    envelope = r.analysis(PUB)
    assert w.linked_analysis(run, envelope)
    for field in ('run_id', 'strategy_id', 'snapshot_id', 'engine_version', 'rule_version', 'data_class'):
        wrong = copy.deepcopy(envelope)
        wrong['evidence'][field] = 'DEMO' if field == 'data_class' else 'different'
        assert w.linked_analysis(run, wrong) is None
    for field, value in [('identity_status', 'IDENTITY_STALE'), ('publishable', False), ('analysis_code', 'old')]:
        wrong = copy.deepcopy(envelope)
        wrong['result'][field] = value
        assert w.linked_analysis(run, wrong) is None
    wrong = copy.deepcopy(envelope)
    wrong['result']['performance']['series']['date'][-1] = '2099-01-01'
    assert w.linked_analysis(run, wrong) is None


@pytest.mark.parametrize('value', [-0.2, 0.0, 0.2, None])
def test_report_displays_stored_values_without_target_gate(stored, value):
    from app import platform_v2_routes as r
    approve_run(stored['out'], 'backtest', PUB)
    paths = list(r.ANALYSIS.glob(f'{PUB}__*.json'))
    data = json.loads(paths[0].read_text(encoding='utf-8'))
    data['result']['performance']['provisional']['cagr'] = {'value': value, 'reason': 'NO_DATA' if value is None else None}
    paths[0].write_text(json.dumps(data), encoding='utf-8')
    html = client().get(f'/platform/backtest/{PUB}').get_data(as_text=True)
    assert 'data-testid="kpi"' in html
    assert ('NO_DATA' if value is None else '%.1f%%' % (value * 100)) in html
    assert 'NOT_CERTIFIED' in html
    assert '歷史重抽樣情境' in html and '未來一年可能跌多深' not in html


def test_invalid_publication_contains_no_chart_payload(stored):
    html = client().get(f'/platform/backtest/{BAD}').get_data(as_text=True)
    assert 'data-testid="not-publishable"' in html
    assert 'v3-report-data' not in html and 'kpi-cagr' not in html


def test_current_catalog_all_cards_and_filters(stored, monkeypatch):
    from app import intelligence_routes as i
    from app import platform_v2_routes as r
    from services import web_dashboard as w
    rows = w.strategies(r, i)
    assert len(rows) == 21 and len({x['strategy_id'] for x in rows}) == 21
    c = client()
    for row in rows:
        response = c.get('/platform/strategies/' + row['strategy_id'])
        assert response.status_code == 200
        assert row['strategy_id'] in response.get_data(as_text=True)
    wall = lambda html: html[html.index('data-testid="strategy-wall"'):]
    assert wall(c.get('/strategies').get_data(as_text=True)).count('data-strategy-id=') == 21
    verified = wall(c.get('/strategies?trust=VERIFIED').get_data(as_text=True))
    assert verified.count('data-strategy-id=') == sum(x['trust'] == 'VERIFIED' for x in rows)
    assert c.get('/platform/strategies').headers['Location'].endswith('/strategies')
    assert c.get('/platform/strategies/not_a_strategy').status_code == 404


@pytest.mark.parametrize('profile_type', ['trend_momentum', 'mean_reversion', 'defensive', 'institutional_flow', 'fundamental'])
def test_original_type_checks_render_without_rejudging(stored, profile_type):
    from app import app
    from flask import render_template
    from core.research.intelligence.profile import load
    from pathlib import Path
    checks = load(Path('core/research/intelligence/data/evaluation_profile.json'))['types'][profile_type]['edge_checks']
    saved = [dict(c, measured=None, reference=None, status='INSUFFICIENT_DATA') for c in checks]
    result = copy.deepcopy(stored['analysis'])
    result['dimensions']['type_edge'] = {'label': profile_type, 'track': 'VERIFIED', 'checks': saved, 'sample_note': None}
    with app.test_request_context('/'):
        html = render_template('_web_v3_checks.html', result=result, fills=[], positions=[], report_meta={},
                               iu=app.jinja_env.get_template('_intelligence_ui.html').module)
    for check in saved:
        assert check['metric'] in html and check['source'] in html
    assert 'INSUFFICIENT_DATA' in html
    assert result['dimensions']['type_edge']['checks'] == saved


def test_clean_preserves_zero_and_missing():
    from services.web_dashboard import clean
    assert clean({'zero': 0.0, 'nan': float('nan'), 'inf': float('inf'), 'missing': None}) == {
        'zero': 0.0, 'nan': None, 'inf': None, 'missing': None}


def test_no_corrupt_chinese_or_forecast_titles():
    from pathlib import Path
    for filename in ['platform_run.html', 'platform_system.html', 'web_strategy.html', '_strategy_wall.html', '_stock_cards.html', '_viz.html']:
        text = (Path('templates') / filename).read_text(encoding='utf-8')
        assert not re.search(r'\?{3,}', text), filename
    text = Path('templates/platform_run.html').read_text(encoding='utf-8')
    for title in ['賺多少', '跌多深', '划算嗎', '常贏嗎', '買得到嗎', '歷史重抽樣情境']:
        assert title in text


def test_get_never_evaluates_or_changes_saved_results(stored, monkeypatch):
    import hashlib
    from core.research.intelligence import evaluate, scenarios
    from pathlib import Path
    def forbidden(*args, **kwargs):
        pytest.fail('GET invoked a research calculation')
    monkeypatch.setattr(evaluate, 'health', forbidden)
    monkeypatch.setattr(evaluate, 'dimensions', forbidden)
    files = list(stored['out'].rglob('*'))
    before = {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in files if p.is_file()}
    c = client()
    for path in ['/strategies', '/platform/strategies/mean_reversion', f'/platform/backtest/{PUB}']:
        assert c.get(path).status_code == 200
    assert before == {p: hashlib.sha256(Path(p).read_bytes()).hexdigest() for p in before}


@pytest.mark.parametrize('files,expected', [([], 'DATA_MISSING'), (['fills.csv'], 'NO_EXECUTIONS'), (['positions_daily.csv'], 'NO_POSITION_ROWS')])
def test_empty_evidence_distinguishes_missing_files(stored, files, expected):
    from app import app
    from flask import render_template
    with app.test_request_context('/'):
        html = render_template('_web_v3_checks.html', result=stored['analysis'], fills=[], positions=[], files=files,
                               report_meta={}, saved_metrics={}, iu=app.jinja_env.get_template('_intelligence_ui.html').module)
    assert expected in html
