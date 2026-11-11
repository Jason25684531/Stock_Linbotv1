import pytest

from core.research.history import taskstate as ts


def test_states_are_persisted_with_repair_limit(tmp_path):
    path = tmp_path / 'state.json'
    ts.update('F01', 'RUNNING', title='engine', path=path)
    assert ts.load(path)['tasks']['F01']['started_at']
    for _ in range(3):
        assert ts.update('F01', 'RETRYING', error_class='CODE_DEFECT', path=path)['status'] == 'RETRYING'
    fourth = ts.update('F01', 'RETRYING', error_class='CODE_DEFECT', path=path)
    assert fourth['status'] == 'FAILED' and fourth['repairs'] == 3 and 'FAILED_WITH_EVIDENCE' in fourth['note']
    ts.update('F02', 'PASS', evidence={'tests': 1}, path=path)
    ts.update('F03', 'BLOCKED', error_class='AUTHORIZATION', retryable=False, path=path)
    summary = ts.summary(ts.load(path))
    assert summary['counts']['FAILED'] == 1 and summary['counts']['PASS'] == 1 and summary['all_terminal']
    with pytest.raises(ValueError):
        ts.update('F04', 'DONE', path=path)
