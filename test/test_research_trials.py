import hashlib
import json

import pytest

from scripts import research_workflow as rw
from scripts import task_controller as tc

ARCHIVED = [f'cfg{i}' for i in range(12)]


def archived_config(cid, **override):
    return {'config_id': cid, 'factor_set': 'A191_132', 'factor_weighting': 'equal', 'top_n': 10, 'rebalance_days': 20, 'portfolio_weighting': 'equal',
            'config_hash': f'h_{cid}', **override}


@pytest.fixture
def lab(tmp_path, monkeypatch):
    registry = tmp_path / 'registry.csv'
    for hid, budget in (('A191_132:CORRECTED-12', '12'), ('H:ONE', '1'), ('H:HUGE', '13')):
        rw.register({'hypothesis_id': hid, 'strategy': 'A191_132', 'hypothesis': 'h', 'falsification_test': 't', 'data_window': 'dev',
                     'trial_budget': budget, 'source': 's'}, path=registry, today='2026-10-28')
    code, bars = tmp_path / 'engine.py', tmp_path / 'bars.csv'
    code.write_text('v1')
    bars.write_text('a,b\n1,2\n')
    manifest = tmp_path / 'manifest.csv'  # stands in for the pinned archived Day3 manifest
    manifest.write_text('strategy_id,factor_set,factor_weighting,top_n,rebalance_days,portfolio_weighting,config_hash\n'
                        + ''.join(f'{c},A191_132,equal,10,20,equal,h_{c}\n' for c in ARCHIVED))
    monkeypatch.setattr(rw, 'ARCHIVED_MANIFESTS', {manifest: hashlib.sha256(manifest.read_bytes()).hexdigest()})
    return {'registry': registry, 'ledger': tmp_path / 'trials.jsonl', 'code': [code], 'inputs': {'bars': bars}, 'tmp': tmp_path, 'manifest': manifest}


def admit(lab, hid='A191_132:CORRECTED-12', config=None, data_end='2025-12-29', **kw):
    return rw.admit_experiment(hid, config or {'config_id': 'c1', 'top_n': 20}, lab['code'], lab['inputs'], data_end,
                               registry=lab['registry'], ledger=lab['ledger'], **kw)


def test_a_registered_experiment_is_admitted_without_any_owner_grant(lab, monkeypatch):
    monkeypatch.setattr(tc, 'require_grant', lambda *a, **k: pytest.fail('Research admission must not ask for an Owner grant'))
    row = admit(lab)
    assert row['kind'] == 'TRIAL' and len(row['experiment_id']) == 16 and rw.verify_trials(lab['ledger']) == 'PASS'
    assert row['code_sha256'] and row['inputs_sha256']['bars'] == hashlib.sha256(lab['inputs']['bars'].read_bytes()).hexdigest() and row['config_sha256']


def test_replays_are_unlimited_and_never_a_new_trial(lab):
    first = admit(lab)
    for _ in range(50):
        again = admit(lab)
        assert again['kind'] == 'REPLAY' and again['experiment_id'] == first['experiment_id']
    assert rw.effective_trials(lab['ledger']) == rw.TRIALS + 1 and rw.verify_trials(lab['ledger']) == 'PASS'


def test_a_different_config_is_a_new_trial_until_the_registered_budget_is_used(lab):
    admit(lab, 'H:ONE', {'config_id': 'a'})
    with pytest.raises(ValueError, match='already used its 1 registered trial'):  # no searching for a better result under the same registration
        admit(lab, 'H:ONE', {'config_id': 'b'})
    assert admit(lab, 'H:ONE', {'config_id': 'a'})['kind'] == 'REPLAY'  # the first experiment stays repeatable


def test_identity_is_computed_here_from_the_files_and_includes_data_end(lab):
    first = admit(lab)
    lab['code'][0].write_text('v2')
    assert admit(lab)['experiment_id'] != first['experiment_id']  # changed code
    lab['inputs']['bars'].write_text('a,b\n9,9\n')
    assert admit(lab)['kind'] == 'TRIAL'  # changed input file: no hash is supplied by the caller that could be left stale
    assert admit(lab, data_end='2025-06-30')['kind'] == 'TRIAL'  # same everything, shorter window: a different experiment
    lab['inputs'] = {'tree': lab['tmp']}  # a directory is hashed by its contents
    assert admit(lab)['inputs_sha256']['tree']


def test_archived_status_comes_only_from_the_pinned_manifest_never_from_the_caller(lab):
    with pytest.raises(TypeError):
        admit(lab, archived_ids=ARCHIVED)  # the caller-supplied list no longer exists
    for cid in ARCHIVED:
        row = admit(lab, config=archived_config(cid))
        assert row['kind'] == 'TRIAL' and row['counts_toward_dsr'] is False and row['archived_config'] is True
    assert rw.effective_trials(lab['ledger']) == 743 == rw.TRIALS
    with pytest.raises(ValueError, match='already used its 12'):
        admit(lab, config={'config_id': 'cfg_new'})
    admit(lab, 'H:ONE', {'config_id': 'brand_new'})  # an id the manifest does not list is a new search and counts
    assert rw.effective_trials(lab['ledger']) == 744


def test_a_changed_manifest_archives_nothing(lab):
    lab['manifest'].write_text(lab['manifest'].read_text() + 'extra_id\n')  # no longer the pinned bytes
    assert admit(lab, config=archived_config('cfg0'))['counts_toward_dsr'] is True


def test_an_archived_label_with_different_parameters_is_a_new_search_and_counts(lab):
    """Relabelling a new search with an archived strategy_id must not hide it from the DSR trial count."""
    for changed in ({'top_n': 50}, {'rebalance_days': 5}, {'portfolio_weighting': 'score_weighted'}, {'factor_set': 'A101_003'}, {'config_hash': 'forged'}):
        row = admit(lab, 'A191_132:CORRECTED-12', archived_config('cfg1', **changed))
        assert row['counts_toward_dsr'] is True and row['archived_config'] is False, changed
    assert admit(lab, 'A191_132:CORRECTED-12', {'config_id': 'cfg1'})['counts_toward_dsr'] is True  # label only: no parameters at all
    assert rw.effective_trials(lab['ledger']) == 743 + 6


def test_unregistered_tampered_late_or_malformed_runs_are_refused(lab):
    with pytest.raises(ValueError, match='not registered'):
        admit(lab, 'NOT:REGISTERED')
    for bad, match in (('2026-01-05', 'strict OOS'), ('', 'ISO date'), ('2025-13-40', 'ISO date'), ('Dec 2025', 'ISO date')):
        with pytest.raises(ValueError, match=match):
            admit(lab, data_end=bad)
    with pytest.raises(ValueError, match='names the code'):
        rw.admit_experiment('H:ONE', {}, [], lab['inputs'], '2025-12-29', registry=lab['registry'], ledger=lab['ledger'])
    with pytest.raises(ValueError, match='from 1 to 12'):
        admit(lab, 'H:HUGE')  # a registration cannot ask for an unbounded search
    lab['registry'].write_text(lab['registry'].read_text(encoding='utf-8-sig') + 'x\n', encoding='utf-8-sig')
    with pytest.raises(ValueError, match='registry does not verify'):
        admit(lab)


def test_a_hypothesis_with_a_recorded_result_admits_replays_but_no_new_trial(lab):
    admit(lab, 'H:ONE', {'config_id': 'a'})
    rw.record_result('H:ONE', 'REJECTED', 'e', path=lab['registry'])
    assert admit(lab, 'H:ONE', {'config_id': 'a'})['kind'] == 'REPLAY'
    with pytest.raises(ValueError, match='already has a recorded result'):
        admit(lab, 'H:ONE', {'config_id': 'z'})


def test_an_edited_ledger_row_is_detected_and_blocks_admission_and_the_count(lab):
    admit(lab)
    admit(lab, config={'config_id': 'c2'})
    rows = [json.loads(line) for line in lab['ledger'].read_text(encoding='utf-8').splitlines()]
    rows[0]['counts_toward_dsr'] = False
    lab['ledger'].write_text('\n'.join(json.dumps(r) for r in rows) + '\n', encoding='utf-8')
    assert rw.verify_trials(lab['ledger']) == 'FAIL'
    with pytest.raises(ValueError, match='does not verify'):
        admit(lab)
    with pytest.raises(ValueError, match='not trustworthy'):  # a broken ledger is an error, not a smaller count
        rw.effective_trials(lab['ledger'])


def test_truncating_or_deleting_the_ledger_cannot_reset_the_trials(lab):
    admit(lab, 'H:ONE', {'config_id': 'a'})
    admit(lab)
    kept = lab['ledger'].read_text(encoding='utf-8').splitlines()[0] + '\n'
    lab['ledger'].write_text(kept, encoding='utf-8')  # drop the last row: the head still says 2 rows
    assert rw.verify_trials(lab['ledger']) == 'FAIL'
    with pytest.raises(ValueError, match='does not verify'):
        admit(lab, 'H:ONE', {'config_id': 'b'})  # the budget does not re-open
    lab['ledger'].unlink()
    assert rw.verify_trials(lab['ledger']) == 'FAIL'  # head without ledger
    lab['ledger'].write_text(kept, encoding='utf-8')
    rw._trial_head(lab['ledger']).unlink()
    assert rw.verify_trials(lab['ledger']) == 'NO_HEAD'


def test_clearing_the_obsolete_registered_experiment_blocker_reopens_the_task(tmp_path):
    tc.create('T', 'p', ['a'], 'backtest-engineer', 'G', root=tmp_path)
    tc.block('T', 'AUTHORIZATION', 'no valid Owner grant for registered_experiment', 'Owner adds a grant for registered_experiment', owner=True, root=tmp_path)
    task = tc.clear_obsolete_blocker('T', root=tmp_path)
    assert task['status'] == 'PENDING' and task['blockers'] == [] and len(task['cleared_blockers']) == 1


def test_clearing_keeps_every_other_blocker_and_state(tmp_path):
    tc.create('T', 'p', ['a'], 'backtest-engineer', 'G', root=tmp_path)
    tc.block('T', 'AUTHORIZATION', 'no valid Owner grant for registered_experiment', 'Owner adds a grant for registered_experiment', owner=True, root=tmp_path)
    tc.block('T', 'AUTHORIZATION', 'no valid Owner grant for official_fetch', 'Owner adds a grant for official_fetch', owner=True, root=tmp_path)
    tc.block('T', 'DATA', 'MI_INDEX missing', 'cache it', owner=False, root=tmp_path)
    task = tc.clear_obsolete_blocker('T', root=tmp_path)
    assert [b['type'] for b in task['blockers']] == ['AUTHORIZATION', 'DATA'] and 'official_fetch' in task['blockers'][0]['detail']
    assert task['status'] == 'BLOCKED'  # not reopened: other blockers remain


def test_a_blocker_that_also_names_a_production_operation_is_never_cleared(tmp_path):
    tc.create('T', 'p', ['a'], 'x', 'G', root=tmp_path)
    tc.block('T', 'AUTHORIZATION', 'no valid Owner grant for registered_experiment', 'Owner adds a grant for registered_experiment and a grant for publish_external', owner=True, root=tmp_path)
    tc.block('T', 'AUTHORIZATION', 'Level 3 operation: live_order registered_experiment', 'Owner provides a trusted live_order grant', owner=True, root=tmp_path)
    assert len(tc.clear_obsolete_blocker('T', root=tmp_path)['blockers']) == 2


def test_clearing_never_touches_a_terminal_task(tmp_path):
    tc.create('T', 'p', ['a'], 'x', 'G', root=tmp_path)
    tc.set_status('T', 'CANCELLED', tmp_path)
    with pytest.raises(ValueError, match='terminal'):
        tc.clear_obsolete_blocker('T', root=tmp_path)
