"""Checkpoint backup, conflict and rollback rules (design D1); temporary folders only."""
from scripts import data_readiness_checkpoint as cp


def test_rollback_restores_pre_round_content_and_skips_conflicts(tmp_path):
    root, out = tmp_path / 'repo', tmp_path / 'out'
    root.mkdir()
    (root / 'a.py').write_text('user uncommitted edit\n', encoding='utf-8')
    (root / 'b.py').write_text('b original\n', encoding='utf-8')
    cp.backup(['a.py', 'b.py', 'new.py'], root=root, out=out)
    (root / 'a.py').write_text('round edit\n', encoding='utf-8')
    (root / 'b.py').write_text('round edit b\n', encoding='utf-8')
    (root / 'new.py').write_text('created\n', encoding='utf-8')
    cp.backup(['a.py'], root=root, out=out)  # a second backup never replaces the pre-round copy
    cp.set_task('T1', 'COMPLETED', files=['a.py', 'b.py', 'new.py'], root=root, out=out)
    (root / 'b.py').write_text('someone else\n', encoding='utf-8')  # changed by another process after the round recorded it

    assert sorted(cp.conflicts(root, out)) == ['b.py'] and 'someone else' in cp.conflicts(root, out)['b.py']
    result = cp.rollback(root, out)
    assert result == {'restored': ['a.py'], 'deleted': ['new.py'], 'conflicts': ['b.py']}
    assert (root / 'a.py').read_text(encoding='utf-8') == 'user uncommitted edit\n'
    assert (root / 'b.py').read_text(encoding='utf-8') == 'someone else\n' and not (root / 'new.py').exists()


def test_verify_resets_completed_task_whose_output_changed(tmp_path):
    root, out = tmp_path / 'repo', tmp_path / 'out'
    root.mkdir()
    (root / 'x.json').write_text('{}', encoding='utf-8')
    cp.set_task('P1', 'COMPLETED', files=['x.json'], root=root, out=out)
    assert cp.verify(root, out) == []
    (root / 'x.json').write_text('{"changed": 1}', encoding='utf-8')
    assert cp.verify(root, out) == ['P1']
    assert cp._read('task_state.json', {}, out)['tasks']['P1']['status'] == 'PENDING'
    assert 'TASK_RESET_HASH_MISMATCH' in (out / 'recovery_log.jsonl').read_text(encoding='utf-8')


def test_cli_out_and_backup_keep_roots_apart(tmp_path, monkeypatch):
    root, first, second = tmp_path / 'repo', tmp_path / 'first', tmp_path / 'second'
    root.mkdir()
    (root / 'a.py').write_text('original\n', encoding='utf-8')
    monkeypatch.setattr(cp, 'ROOT', root)
    cp.main(['backup', 'a.py', '--out', str(first)])
    assert (first / 'backup/a.py').read_text(encoding='utf-8') == 'original\n'
    assert not second.exists()
    monkeypatch.setenv('STOCK_LINBOT_CHECKPOINT_ROOT', str(second))
    (root / 'a.py').write_text('edited\n', encoding='utf-8')
    cp.main(['backup', 'a.py'])
    assert (second / 'backup/a.py').read_text(encoding='utf-8') == 'edited\n'
    assert (first / 'backup/a.py').read_text(encoding='utf-8') == 'original\n'


def test_compare_baseline_names_a_changed_sealed_tree(tmp_path, monkeypatch):
    from scripts import research_workflow as rw
    root, out = tmp_path / 'repo', tmp_path / 'out'
    tree = root / 'outputs/research_mode/prepared_v6'
    tree.mkdir(parents=True)
    (tree / 'bars.json').write_text('{}', encoding='utf-8')
    monkeypatch.setattr(cp, 'ROOT', root)
    monkeypatch.setattr(rw, 'effective_trials', lambda: 770)
    cp._write('baseline.json', {'ledger_files': {}, 'effective_trials': 770, 'h4_sha256': {'prepared_v6': cp._tree_hashes(tree)}}, out)
    assert cp.compare_baseline(out) == []
    (tree / 'bars.json').write_text('{"changed": 1}', encoding='utf-8')
    assert cp.compare_baseline(out) == ['H4 tree changed: prepared_v6']
    monkeypatch.setattr(rw, 'effective_trials', lambda: 771)
    assert 'effective_trials: 770 -> 771' in cp.compare_baseline(out)
