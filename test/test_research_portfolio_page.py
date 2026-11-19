"""Research portfolio page (change 2026-11-18-core-satellite-portfolio-v7): stored results against the registered targets, read only."""
import hashlib
import inspect
import json

import numpy as np
import pandas as pd
import pytest

from services import research_portfolio as rp

THRESHOLDS = {'base_cagr': 0.15, 'stress_cagr': 0.10, 'max_drawdown': -0.20, 'min_closed_trades': 30, 'min_years_without_trades': 5}
MET = '三項目標皆達成'


def _sha(data):
    return hashlib.sha256(data).hexdigest()


def _write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding='utf-8')
    return _sha(path.read_bytes())


def _dated(start, end):
    dates = [d.strftime('%Y-%m-%d') for d in pd.bdate_range(start, end)]
    return {'date': dates, 'nav_base': [1 + i / 1000 for i in range(len(dates))], 'nav_stress': [1.0] * len(dates)}


def _result(**extra):
    base = {'hypothesis': 'A1', 'variant': 'b', 'window': 'development', 'span': [2017, 2023], 'kind': 'engine', 'proxy': False,
            'cagr': {'value': 0.123, 'reason': None}, 'cagr_stress': {'value': 0.071, 'reason': None}, 'max_drawdown': {'value': -0.264, 'reason': None},
            'max_drawdown_stress': {'value': None, 'reason': 'NO_SESSIONS'}, 'invested_session_share': {'value': 0.82, 'reason': None},
            'closed_trades': 410, 'verdict': 'FAILED', 'failure': 'RETURN', 'run_identity': 'RID7-abc', 'note': 'odd lots',
            'benchmark': {'cagr': {'value': 0.147}, 'max_drawdown': {'value': -0.286}},
            'series': _dated('2016-12-30', '2023-12-29')}
    return base | extra


@pytest.fixture
def root(tmp_path):
    _write(tmp_path / 'charter.json', {'thresholds': THRESHOLDS, 'windows': {'development': [2017, 2023], 'holdout': [2024, 2025]},
                                       'labels': ['RESEARCH_ONLY', 'NOT_CERTIFIED']})
    (tmp_path / 'charter.sha256').write_text(_sha((tmp_path / 'charter.json').read_bytes()) + '\n', encoding='ascii')
    return tmp_path


def _append(root, row):
    path = root / 'queue.jsonl'
    rows = [json.loads(line) for line in path.read_text(encoding='utf-8').splitlines()] if path.exists() else []
    prev = rows[-1]['sha'] if rows else ''
    body = row | {'seq': len(rows), 'at': '2026-11-18T00:00:00+00:00', 'prev': prev}
    body['sha'] = _sha((prev + json.dumps(body, sort_keys=True, ensure_ascii=False)).encode())
    with path.open('a', encoding='utf-8') as handle:
        handle.write(json.dumps(body, ensure_ascii=False, sort_keys=True) + '\n')


def _record(root, result):
    sha = _write(root / 'results' / f"{result['hypothesis']}__{result['variant']}__{result['window']}.json", result)
    _append(root, {'hypothesis': result['hypothesis'], 'event': 'RESULT', 'variant': result['variant'], 'window': result['window'], 'result_sha256': sha})


def _report(root, **over):
    rows = [json.loads(line) for line in (root / 'queue.jsonl').read_text(encoding='utf-8').splitlines()]
    _write(root / 'report.json', {'outcome': 'BUDGET_SPENT_TARGET_NOT_MET', 'final': True, 'queue_head': rows[-1]['sha'],
                                  'charter_sha256': _sha((root / 'charter.json').read_bytes())} | over)


def _nav(root, run):
    """A stored run whose value doubles and then halves over 2017-2023: a -50 percent drawdown."""
    dates = pd.bdate_range('2017-01-02', '2023-12-29')
    nav = np.concatenate([np.linspace(1, 2, len(dates) // 2), np.linspace(2, 1, len(dates) - len(dates) // 2)])
    (root / 'runs' / run).mkdir(parents=True)
    pd.DataFrame({'date': dates, 'nav_base': nav, 'nav_stress': nav}).to_csv(root / 'runs' / run / 'nav.csv', index=False)


def test_without_a_charter_the_view_says_nothing_was_registered(tmp_path):
    assert rp.view(tmp_path)['state'] == 'NO_CHARTER'


def test_with_a_charter_and_no_inputs_the_view_names_the_closed_data_gate(root):
    view = rp.view(root)
    assert view['state'] == 'NO_DATA' and view['cells'] == []
    (root / 'data').mkdir()
    _write(root / 'data' / 'manifest.json', {'revenue_rows': 3})
    assert rp.view(root)['state'] == 'NO_RESULTS'


def test_a_cell_shows_the_stored_values_the_gaps_and_a_dash_with_its_reason_for_an_undefined_metric(root):
    _write(root / 'data' / 'manifest.json', {})
    _record(root, _result())
    cell = rp.view(root)['cells'][0]
    assert cell['cagr']['value'] == 0.123 and cell['verdict'] == 'FAILED' and cell['failure'] == 'RETURN'
    assert cell['max_drawdown_stress'] == {'value': None, 'reason': 'NO_SESSIONS'}
    assert [(g['name'], round(g['gap'] * 100, 1)) for g in cell['gaps']] == [('cagr', 2.7), ('stress', 2.9), ('drawdown', 6.4)]
    assert cell['spark'] and cell['drawdown_spark']


def test_a_result_file_that_changed_after_it_was_recorded_is_shown_as_invalid_not_as_a_result(root):
    _write(root / 'data' / 'manifest.json', {})
    _record(root, _result())
    path = root / 'results' / 'A1__b__development.json'
    path.write_text(path.read_text(encoding='utf-8').replace('0.123', '0.223'), encoding='utf-8')
    view = rp.view(root)
    assert view['cells'] == [] and view['invalid'] == ['A1__b__development']


def test_a_cell_that_meets_every_target_has_no_gaps_and_may_say_so_only_with_a_promising_verdict(root):
    _write(root / 'data' / 'manifest.json', {})
    _record(root, _result(cagr={'value': 0.2, 'reason': None}, cagr_stress={'value': 0.12, 'reason': None}, max_drawdown={'value': -0.15, 'reason': None},
                          verdict='PROMISING', failure=None))
    cell = rp.view(root)['cells'][0]
    assert cell['gaps'] == [] and cell['targets_met'] is True


def test_a_failed_cell_whose_numbers_sit_inside_the_thresholds_is_not_called_a_pass(root):
    _write(root / 'data' / 'manifest.json', {})
    _record(root, _result(verdict='FAILED', failure='SAMPLE', cagr={'value': 0.36, 'reason': None}, cagr_stress={'value': 0.30, 'reason': None},
                          max_drawdown={'value': -0.16, 'reason': None}))
    cell = rp.view(root)['cells'][0]
    assert cell['gaps'] == [] and cell['targets_met'] is False


def test_a_tampered_queue_or_charter_shows_no_results_at_all(root):
    _write(root / 'data' / 'manifest.json', {})
    _record(root, _result())
    queue = root / 'queue.jsonl'
    original = queue.read_text(encoding='utf-8')
    queue.write_text(original.replace('RESULT', 'RESULT ', 1), encoding='utf-8')
    assert rp.view(root)['state'] == 'CHAIN_BROKEN' and rp.view(root)['cells'] == []
    queue.write_text(original, encoding='utf-8')
    assert rp.view(root)['state'] == 'OK'
    (root / 'charter.sha256').write_text('0' * 64 + '\n', encoding='ascii')
    assert rp.view(root)['state'] == 'CHAIN_BROKEN'


def test_a_cell_computed_on_fewer_years_than_its_registered_span_says_so(root):
    _write(root / 'data' / 'manifest.json', {})
    _record(root, _result(hypothesis='C1', variant='a', span=[2017, 2023], series=_dated('2018-12-28', '2021-12-30')))
    cell = rp.view(root)['cells'][0]
    assert cell['covered']['start'] == '2018-12-28' and cell['covered']['end'] == '2021-12-30' and cell['span_mismatch'] is True


def test_a_cell_that_covers_its_registered_span_has_no_mismatch(root):
    _write(root / 'data' / 'manifest.json', {})
    _record(root, _result(span=[2017, 2023], series=_dated('2016-12-30', '2023-12-29')))
    cell = rp.view(root)['cells'][0]
    assert cell['span_mismatch'] is False and cell['excluded_years'] == []


def test_a_cell_cut_to_its_verified_years_names_the_years_it_leaves_out(root):
    _write(root / 'data' / 'manifest.json', {})
    _record(root, _result(span=[2019, 2021], series=_dated('2018-12-28', '2021-12-30')))
    cell = rp.view(root)['cells'][0]
    assert cell['excluded_years'] == [2017, 2018, 2022, 2023] and cell['span_mismatch'] is False


def test_the_view_carries_the_charter_labels_and_the_stored_outcome(root):
    _write(root / 'data' / 'manifest.json', {})
    _record(root, _result())
    assert rp.view(root)['outcome'] is None
    _report(root)
    view = rp.view(root)
    assert view['outcome'] == 'BUDGET_SPENT_TARGET_NOT_MET' and view['labels'] == ['RESEARCH_ONLY', 'NOT_CERTIFIED']


def test_a_report_that_is_not_final_or_not_current_for_this_queue_and_charter_shows_no_outcome(root):
    _write(root / 'data' / 'manifest.json', {})
    _record(root, _result())
    _report(root, final=False)
    assert rp.view(root)['outcome'] is None
    _report(root, queue_head='0' * 64)
    assert rp.view(root)['outcome'] is None
    _report(root, charter_sha256='1' * 64)
    assert rp.view(root)['outcome'] is None
    report = json.loads((root / 'report.json').read_text(encoding='utf-8'))
    del report['queue_head']
    (root / 'report.json').write_text(json.dumps(report), encoding='utf-8')
    assert rp.view(root)['outcome'] is None
    _report(root)
    assert rp.view(root)['outcome'] == 'BUDGET_SPENT_TARGET_NOT_MET'


def test_a_cell_cut_to_its_verified_years_carries_the_same_runs_drawdown_over_the_whole_window(root):
    _write(root / 'data' / 'manifest.json', {})
    _nav(root, 'RUN1')
    _record(root, _result(span=[2019, 2021], run_id='RUN1', series=_dated('2018-12-28', '2021-12-30')))
    cell = rp.view(root)['cells'][0]
    assert cell['diagnostic_drawdown'] == pytest.approx(-0.5, abs=0.01) and cell['window_span'] == [2017, 2023]


def test_a_cell_on_its_full_window_or_without_a_stored_run_has_no_diagnostic_drawdown(root):
    _write(root / 'data' / 'manifest.json', {})
    _record(root, _result(span=[2017, 2023], run_id='RUN1'))
    _record(root, _result(hypothesis='A2', span=[2019, 2021], run_id='MISSING', series=_dated('2018-12-28', '2021-12-30')))
    assert [c['diagnostic_drawdown'] for c in rp.view(root)['cells']] == [None, None]


@pytest.fixture
def client(monkeypatch, root):
    from app import app, web_server
    monkeypatch.setattr(web_server, '_build_system_status_payload', lambda *a, **k: ({'status': 'ok'}, 200))
    monkeypatch.setattr(web_server, '_latest_ui_market_date', lambda *a, **k: None)
    monkeypatch.setattr(rp, 'ROOT', root)
    monkeypatch.setattr(rp, 'EXTRA_ROOTS', ())
    return app.test_client()


def _page(client):
    return client.get('/backtest/research').get_data(as_text=True)


def test_the_page_is_a_backtest_tab_and_renders_labels_with_their_codes(client, root):
    _write(root / 'data' / 'manifest.json', {})
    _record(root, _result())
    html = _page(client)
    assert 'data-testid="research-portfolio"' in html
    assert 'href="/backtest/research" aria-current="page"' in html
    assert 'data-code="FAILED"' in html and 'data-code="RETURN"' in html and 'UNKNOWN' not in html
    assert '+12.3%' in html and '+7.1%' in html and '-26.4%' in html


def test_the_page_shows_the_research_labels_and_the_outcome_above_the_cells(client, root):
    _write(root / 'data' / 'manifest.json', {})
    _record(root, _result())
    _report(root)
    html = _page(client)
    assert 'data-code="RESEARCH_ONLY"' in html and 'data-code="NOT_CERTIFIED"' in html and 'data-code="BUDGET_SPENT_TARGET_NOT_MET"' in html
    assert html.index('data-code="BUDGET_SPENT_TARGET_NOT_MET"') < html.index('data-testid="research-cell"')


def test_the_empty_page_states_which_gate_is_closed(client, root):
    assert 'research-gate' in _page(client)
    (root / 'charter.json').unlink()
    assert 'research-gate' in _page(client)


def test_a_failed_cell_inside_the_thresholds_never_prints_the_all_targets_met_sentence(client, root):
    _write(root / 'data' / 'manifest.json', {})
    _record(root, _result(verdict='FAILED', failure='SAMPLE', cagr={'value': 0.36, 'reason': None}, cagr_stress={'value': 0.30, 'reason': None},
                          max_drawdown={'value': -0.16, 'reason': None}))
    html = _page(client)
    assert MET not in html and 'data-testid="not-evidence"' in html


def test_a_cell_with_every_metric_undefined_is_undecided_not_met(client, root):
    _write(root / 'data' / 'manifest.json', {})
    none = {'value': None, 'reason': 'NO_SESSIONS_OR_NOT_PUBLISHABLE'}
    _record(root, _result(verdict='FAILED', failure='DATA', cagr=none, cagr_stress=none, max_drawdown=none, series=None))
    html = _page(client)
    assert MET not in html and 'data-testid="not-evidence"' in html


def test_a_promising_cell_may_say_all_targets_are_met(client, root):
    _write(root / 'data' / 'manifest.json', {})
    _record(root, _result(cagr={'value': 0.2, 'reason': None}, cagr_stress={'value': 0.12, 'reason': None}, max_drawdown={'value': -0.15, 'reason': None},
                          verdict='PROMISING', failure=None))
    assert MET in _page(client)


def test_the_page_shows_the_covered_dates_instead_of_a_benchmark_when_a_mix_covers_fewer_years(client, root):
    _write(root / 'data' / 'manifest.json', {})
    _record(root, _result(hypothesis='C1', variant='a', span=[2017, 2023], series=_dated('2018-12-28', '2021-12-30')))
    html = _page(client)
    assert 'data-testid="span-mismatch"' in html and '2018-12-28' in html and '2021-12-30' in html and 'data-testid="benchmark"' not in html


def test_the_page_names_the_years_a_verified_span_leaves_out_and_says_the_verdict_is_not_evidence_of_being_near(client, root):
    _write(root / 'data' / 'manifest.json', {})
    _nav(root, 'RUN1')
    _record(root, _result(span=[2019, 2021], run_id='RUN1', verdict='NEAR', failure='DRAWDOWN', series=_dated('2018-12-28', '2021-12-30')))
    html = _page(client)
    notice = html.split('data-testid="span-cut"')[1].split('</p>')[0]
    assert '2022' in notice and '-50.0%' in notice and 'data-code="NEAR"' in notice and '不能當作接近目標的證據' in notice


def test_a_cell_without_trades_does_not_print_zero_trades(client, root):
    _write(root / 'data' / 'manifest.json', {})
    _record(root, _result(proxy=True, closed_trades=0, kind='index_switch'))
    html = _page(client)
    assert '0 筆' not in html and '代理：無驗證區間' in html


def test_the_service_never_imports_the_research_runner_and_a_get_starts_no_process(client, monkeypatch):
    import subprocess
    assert 'scripts' not in inspect.getsource(rp)
    monkeypatch.setattr(subprocess, 'Popen', lambda *a, **k: pytest.fail('the page must not start a process'))
    assert client.get('/backtest/research').status_code == 200


def test_a_second_program_is_listed_after_the_first_with_its_own_heading_and_its_own_verification(client, root, tmp_path_factory, monkeypatch):
    _write(root / 'data' / 'manifest.json', {})
    _record(root, _result())
    other = tmp_path_factory.mktemp('programs') / 'strategy_v9'
    _write(other / 'charter.json', {'thresholds': THRESHOLDS, 'windows': {'development': [2017, 2023]}, 'labels': []})
    (other / 'charter.sha256').write_text(_sha((other / 'charter.json').read_bytes()) + '\n', encoding='ascii')
    _write(other / 'data' / 'manifest.json', {})
    _record(other, _result(hypothesis='LV1', variant='a'))
    monkeypatch.setattr(rp, 'EXTRA_ROOTS', (other,))
    html = _page(client)
    assert html.count('data-testid="research-cell"') == 2 and 'data-testid="program-V7"' in html and 'data-testid="program-V9"' in html
    assert html.index('data-testid="program-V7"') < html.index('data-testid="program-V9"')
    (other / 'queue.jsonl').write_text((other / 'queue.jsonl').read_text(encoding='utf-8').replace('RESULT', 'RESULT ', 1), encoding='utf-8')
    html = _page(client)
    assert html.count('data-testid="research-cell"') == 1 and 'data-testid="research-gate"' in html


def test_a_program_folder_that_does_not_exist_is_not_listed():
    names = [name for name, _ in rp.programs()]
    assert names[0] == 'V7' and 'V10' not in names


def test_a_later_program_that_shares_the_first_programs_data_is_not_stopped_at_the_data_gate(root, tmp_path_factory, monkeypatch):
    _write(root / 'data' / 'manifest.json', {})
    other = tmp_path_factory.mktemp('programs') / 'strategy_v9'
    _write(other / 'charter.json', {'thresholds': THRESHOLDS, 'windows': {'development': [2017, 2023]}, 'labels': []})
    (other / 'charter.sha256').write_text(_sha((other / 'charter.json').read_bytes()) + '\n', encoding='ascii')
    _record(other, _result(hypothesis='LV1', variant='a'))
    monkeypatch.setattr(rp, 'ROOT', root)
    monkeypatch.setattr(rp, 'EXTRA_ROOTS', (other,))
    states = {name: view['state'] for name, view in rp.programs()}
    assert states == {'V7': 'NO_RESULTS', 'V9': 'OK'}
    assert rp.view(other)['state'] == 'NO_DATA'
