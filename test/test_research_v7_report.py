import json

import pytest

from scripts.research_v7_hypcost_report import load_result, verify_proxy_source
from scripts.research_workflow import sha256


def test_buffer_track_targets_are_verified(tmp_path):
    for name in ('targets.csv', 'base.json', 'stress.json', 'targets_base.csv', 'targets_stress.csv'):
        (tmp_path / name).write_text('{}', encoding='utf-8')
    manifest = {'status': 'COMPUTABLE', 'config': {'rank_buffer_multiple': 2},
                'output_sha256': {name: sha256(tmp_path / name) for name in ('targets.csv', 'base.json', 'stress.json')},
                'track_targets_sha256': {name: sha256(tmp_path / f'targets_{name}.csv') for name in ('base', 'stress')}}
    (tmp_path / 'manifest.json').write_text(json.dumps(manifest), encoding='utf-8')
    (tmp_path / 'targets_stress.csv').write_text('tampered', encoding='utf-8')
    with pytest.raises(ValueError, match='TRACK_TARGET_HASH_MISMATCH'):
        load_result(tmp_path)


def test_event_stopped_result_preserves_reason(tmp_path):
    manifest = {'status': 'NOT_COMPUTABLE', 'reason': 'UNRESOLVED_CORPORATE_ACTION_HELD', 'stock_id': '1234'}
    (tmp_path / 'manifest.json').write_text(json.dumps(manifest), encoding='utf-8')
    result, tracks = load_result(tmp_path)
    assert tracks is None and result == manifest


def test_proxy_source_change_is_rejected(tmp_path):
    source = tmp_path / 'bars.json'
    source.write_text('original', encoding='utf-8')
    manifest = {'inputs_sha256': {'bars': sha256(source)}}
    source.write_text('changed', encoding='utf-8')
    with pytest.raises(ValueError, match='PROXY_SOURCE_HASH_MISMATCH'):
        verify_proxy_source(source, [manifest])


@pytest.mark.parametrize('reason', ['ZERO_VARIANCE', 'DATE_MISMATCH'])
def test_deliver_handles_not_computable_with_and_without_tracks(tmp_path, monkeypatch, reason):
    from scripts import research_v7_hypcost_report as subject
    out = tmp_path / 'out'
    (out / 'review').mkdir(parents=True)
    (out / 'review' / 'review_1.md').write_text('review', encoding='utf-8')
    for index in (289, 129):
        directory = out / 'replay' / 'revision_4' / str(index)
        directory.mkdir(parents=True)
        (directory / 'manifest.json').write_text(json.dumps({'experiment_id': str(index)}), encoding='utf-8')
    frozen = '| RS_004_NEAR_HIGH_120D | HYP-COST 一次 | PENDING_HYP_COST |\n| A191_088 | HYP-COST 一次 | PENDING_HYP_COST |\n'
    (out / 'strategy_decisions.md').write_text(frozen, encoding='utf-8', newline='\n')
    ledger = tmp_path / 'ledger'
    ledger.write_text('ledger', encoding='utf-8')
    monkeypatch.setattr(subject, 'OUT', out)
    monkeypatch.setattr(subject, 'ROOT', tmp_path)
    track = tmp_path / 'outputs/research_mode/report_v6/research_track.json'
    track.parent.mkdir(parents=True)
    track.write_text('{}', encoding='utf-8')
    monkeypatch.setattr(subject.workflow, 'RESEARCH_MODE_LEDGER', ledger)
    monkeypatch.setattr(subject.workflow, 'TRIAL_LEDGER', ledger)
    monkeypatch.setattr(subject.workflow, 'verify_trials', lambda *args: 'PASS')
    monkeypatch.setattr(subject.workflow, 'effective_trials', lambda: 743)
    monkeypatch.setattr(subject.workflow, '_trial_rows', lambda *args: [
        {'experiment_id': str(index), 'sha256': str(index)} for index in (289, 129)])
    metric = {'total_return': {'value': 0.1, 'reason': None}, 'annual_total_return': {}}
    available = {'base': metric, 'stress': metric, 'top5_asset_profit_share': {'value': 0.1, 'reason': None}}
    experiment = lambda family, new, reason: {'family': family, 'status': 'COMPUTABLE' if new else 'NOT_COMPUTABLE',
        'old': available, 'new': new, 'proxy': metric, 'l1': 'NOT_COMPUTABLE', 'l2': 'NOT_COMPUTABLE',
        'l3': {'status': 'NOT_COMPUTABLE'}, 'reason': reason, 'ledger_sha256': 'sha', 'new_manifest': {'experiment_id': family}}
    evidence = {'pre_experiment_decisions_sha256': sha256(out / 'strategy_decisions.md'), 'research_trials': 510,
                'formal_trials': 743, 'dsr_trials': 743, 'trial_sharpe_variance': .1,
                'experiments': [experiment('RS_004_NEAR_HIGH_120D', available, reason),
                                experiment('A191_088', None, 'UNRESOLVED_CORPORATE_ACTION_HELD')]}
    evidence['experiments'][0]['l3'] = {'status': 'RESEARCH_RUN_FAIL_SCREEN'}
    subject.deliver(evidence)
    text = (out / 'README.md').read_text(encoding='utf-8')
    assert reason in text and 'UNRESOLVED_CORPORATE_ACTION_HELD' in text and 'RESEARCH_RUN_FAIL_SCREEN' in text
    import csv
    with (out / 'hypcost_comparison.csv').open(encoding='utf-8-sig', newline='') as stream:
        rows = list(csv.DictReader(stream))
    stopped = [row for row in rows if row['family'] == 'A191_088' and row['metric'] == 'total_return']
    assert stopped and stopped[0]['v6'] and stopped[0]['v7_reason'] == 'UNRESOLVED_CORPORATE_ACTION_HELD'
