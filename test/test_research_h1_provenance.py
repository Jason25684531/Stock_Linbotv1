import json

import pytest

from core.research import repair_runner
from core.research.repair_runner import DataBlocked
from scripts import research_workflow as workflow

PRIMARY = {'strategy': 'near_high_252d', 'config_id': 'near_high_252d__EQUAL__TOP10__REB60__EQUAL',
           'composite_id': 'near_high_252d__EQUAL', 'stock_weighting': 'equal', 'top_n': 10, 'rebalance_days': 60,
           'assumptions_sha256': 'f' * 64}


def fixture(tmp_path, monkeypatch, *, signal_kind='H1_SINGLE_FACTOR_GRID', grid_sha=None, evidence_kind='H1_RAW_REPRODUCIBLE_V1'):
    monkeypatch.setattr(repair_runner, 'RESEARCH_OUTPUT_ROOT', tmp_path)
    prepared, signals = tmp_path / 'prepared', tmp_path / 'signals'
    prepared.mkdir()
    signals.mkdir()
    for name in ('bars.json', 'actions.json'):
        (prepared / name).write_text('{}', encoding='utf-8')
    (prepared / 'input_manifest.json').write_text(json.dumps({'assumptions_sha256': 'f' * 64, 'files': {}}), encoding='utf-8')
    for name in ('scores_00_60.json', 'universe_60.json'):
        (signals / name).write_text('[]', encoding='utf-8')
    grid = workflow.ROOT / repair_runner.EVIDENCE_GRIDS['H1_RAW_REPRODUCIBLE_V1'][1]
    manifest = {'kind': signal_kind, 'composites': ['near_high_252d__EQUAL'], 'assumptions_sha256': 'f' * 64,
                'grid_sha256': grid_sha or workflow.sha256(grid),
                'input_manifest_sha256': workflow.sha256(prepared / 'input_manifest.json'),
                'files': {n: workflow.sha256(signals / n) for n in ('scores_00_60.json', 'universe_60.json')}}
    (signals / 'signals_manifest.json').write_text(json.dumps(manifest), encoding='utf-8')
    paths = {'bars': prepared / 'bars.json', 'actions': prepared / 'actions.json',
             'scores': signals / 'scores_00_60.json', 'universe': signals / 'universe_60.json'}
    evidence = {'kind': evidence_kind, 'signals_manifest_sha256': workflow.sha256(signals / 'signals_manifest.json')}
    calls = []
    monkeypatch.setattr(repair_runner, '_reproduce_day3_checkpoints', lambda *args: calls.append(args) or True)
    monkeypatch.setattr(workflow, 'require_registered', lambda hypothesis_id: {'hypothesis_id': hypothesis_id})
    return evidence, paths, calls


def test_registered_h1_primary_reproduces_checkpoints(tmp_path, monkeypatch):
    evidence, paths, calls = fixture(tmp_path, monkeypatch)
    repair_runner._verify_research_provenance(evidence, paths, PRIMARY)
    assert len(calls) == 1


def test_unregistered_or_judged_h1_is_blocked(tmp_path, monkeypatch):
    evidence, paths, calls = fixture(tmp_path, monkeypatch)

    def refuse(hypothesis_id):
        raise ValueError('already has a result')
    monkeypatch.setattr(workflow, 'require_registered', refuse)
    with pytest.raises(DataBlocked, match='NOT_REGISTERED_OR_ALREADY_JUDGED'):
        repair_runner._verify_research_provenance(evidence, paths, PRIMARY)
    assert not calls


@pytest.mark.parametrize('kwargs', [{'grid_sha': '0' * 64}, {'signal_kind': 'DAY3_ARCHIVED_GRID'},
                                    {'evidence_kind': 'DAY3_RAW_REPRODUCIBLE_V1'}])
def test_mismatched_grid_or_kind_is_refused(tmp_path, monkeypatch, kwargs):
    evidence, paths, calls = fixture(tmp_path, monkeypatch, **kwargs)
    with pytest.raises(DataBlocked, match='SCORES_NOT_FROM_RAW_CONVERSION'):
        repair_runner._verify_research_provenance(evidence, paths, PRIMARY)
    assert not calls


def test_configuration_outside_the_registered_grid_is_refused(tmp_path, monkeypatch):
    evidence, paths, calls = fixture(tmp_path, monkeypatch)
    with pytest.raises(DataBlocked, match='ARCHIVED_CONFIGURATION_MISMATCH'):
        repair_runner._verify_research_provenance(evidence, paths, {**PRIMARY, 'config_id': 'near_high_252d__EQUAL__TOP50__REB60__EQUAL'})
    assert not calls
