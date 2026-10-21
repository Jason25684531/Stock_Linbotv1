"""Execution success and incomplete artifacts must never turn into validation PASS."""
import json

from scripts import validate_strategy_inventory as validation


def test_parity_requires_shared_inputs_and_preserves_quantity_and_order():
    from copy import deepcopy
    reference = {key: 'same' for key in ('snapshot_sha256', 'calendar_sha256', 'as_of', 'universe_sha256')}
    reference.update(producer_sha256='original', signals=[{'identity': ['2026-10-01', '2330', 1, 100], 'values': [.5]}], holdings=[], execution=[])
    port = deepcopy(reference)
    port['producer_sha256'] = 'port'
    assert validation.compare_parity(reference, port)['signals']['status'] == 'PASS'
    port['signals'][0]['identity'][-1] = 101
    assert validation.compare_parity(reference, port)['signals']['status'] == 'FAIL'
    port['calendar_sha256'] = 'different'
    assert all(r['status'] == 'BLOCKED' for r in validation.compare_parity(reference, port).values())


def test_report_keeps_failures_and_undefined_metrics(tmp_path, monkeypatch):
    monkeypatch.setattr(validation, 'ROOT', tmp_path)
    (tmp_path / 'docs').mkdir()
    record = {'id': 'stock', 'kind': 'REGISTERED', 'sources': [{'path': 'strategy.py'}], 'counts_as_strategy': True}
    validation.write_json(tmp_path / 'inventory.json', {'records': [record], 'counts': {'ported_sources': 0}})
    validation.write_json(tmp_path / 'stocks.json', {'stock': {'execution_status': 'COMPLETED', 'validation': 'FAIL', 'reason': 'SAME_CLOSE_SIGNAL_AND_FILL'}})
    validation.report(tmp_path)
    result = json.loads((tmp_path / 'validation.json').read_text(encoding='utf-8'))
    assert result['records'][0]['verdict'] == 'FAIL'
    assert result['totals']['discovered'] == 1
    assert all(v['value'] is None and v['reason'] for v in result['records'][0]['metrics'].values())


def test_successful_research_exit_without_metrics_is_not_a_pass(tmp_path, monkeypatch):
    monkeypatch.setattr(validation, 'ROOT', tmp_path)
    (tmp_path / 'docs').mkdir()
    record = {'id': 'grid:one', 'strategy_id': 'one', 'kind': 'RESEARCH_SETTING', 'namespace': 'grid',
              'sources': [{'path': 'grid.csv'}], 'counts_as_strategy': True}
    validation.write_json(tmp_path / 'inventory.json', {'records': [record], 'counts': {'ported_sources': 0}})
    validation.write_json(tmp_path / 'grid_attempt.json', {'exit_status': 0})
    validation.report(tmp_path)
    row = json.loads((tmp_path / 'validation.json').read_text(encoding='utf-8'))['records'][0]
    assert row['backtest']['status'] == 'NOT_TESTED'
    assert row['verdict'] != 'PASS'


def test_deployed_failure_is_not_hidden_by_local_capability(tmp_path, monkeypatch):
    monkeypatch.setattr(validation, 'ROOT', tmp_path)
    (tmp_path / 'docs').mkdir()
    record = {'id': 'stock', 'kind': 'REGISTERED', 'sources': [{'path': 'strategy.py'}], 'counts_as_strategy': True}
    validation.write_json(tmp_path / 'inventory.json', {'records': [record], 'counts': {'ported_sources': 0}})
    validation.write_json(tmp_path / 'daily_load_probe.json', {'stock': {'loaded': True, 'listed': True}})
    validation.write_json(tmp_path / 'deployed_catalog_probe.json', {'items': [{'strategy_id': 'stock', 'available_for_daily': False}]})
    validation.report(tmp_path)
    result = json.loads((tmp_path / 'validation.json').read_text(encoding='utf-8'))
    assert result['totals']['local_daily_capable'] == 1
    assert result['totals']['daily_capable'] == 0
    assert result['records'][0]['daily']['status'] == 'BLOCKED'
