import json

import pytest

from core.research.intelligence import jobstate as js


@pytest.mark.parametrize('exc, cls', [
    (MemoryError(), 'RESOURCE_EXHAUSTED'), (FileNotFoundError('nav.csv'), 'DATA_MISSING'), (ValueError('bad'), 'DATA_INVALID'),
    (ValueError('DATA_TRUST_PARTIAL'), 'DATA_TRUST_BLOCKED'), (ConnectionError(), 'TRANSIENT_SOURCE_FAILURE'), (PermissionError(), 'PERMISSION_BLOCKED'),
    (ValueError('INSUFFICIENT_SAMPLE'), 'MODEL_OR_STATISTICAL_LIMITATION'), (RuntimeError('SECURITY: token'), 'SECURITY_RISK'),
    (ValueError('PROFILE_NOT_FROZEN: x'), 'GOVERNANCE_BLOCKED'), (ValueError('SNAPSHOT_SOURCES_CHANGED'), 'DATA_INVALID'),
    (ZeroDivisionError(), 'CODE_ERROR')])
def test_classify(exc, cls):
    assert js.classify(exc) == cls


def test_third_failure_is_final_and_survives_a_new_job(tmp_path):
    for n in range(3):
        job = js.Job(tmp_path, f'j{n}')
        assert not job.exhausted('k')
        job.start('a', 'performance', 'k', {})
        status = job.fail('a', 'performance', 'k', ZeroDivisionError('x'))
    assert status == 'FAILED_WITH_EVIDENCE'
    assert js.Job(tmp_path, 'j9').exhausted('k')


def test_overall_status_and_exit_codes(tmp_path):
    job = js.Job(tmp_path, 'j')
    job.start('a', 's', 'ka', {})
    job.done('a', 's', 'out', reused=False)
    job.start('b', 's', 'kb', {})
    job.fail('b', 's', 'kb', ValueError('x'))
    assert job.finish() == 'PARTIAL' and job.state['exit_code'] == 2
    assert job.per_strategy() == {'a': 'DONE', 'b': 'FAILED'}
    only_fail = js.Job(tmp_path, 'k')
    only_fail.start('b', 's', 'kb2', {})
    only_fail.fail('b', 's', 'kb2', ValueError('x'))
    assert only_fail.finish() == 'FAILED'
    clean = js.Job(tmp_path, 'm')
    clean.start('a', 's', 'ka', {})
    clean.done('a', 's', 'out', reused=True)
    assert clean.finish() == 'COMPLETE'
    assert json.loads((tmp_path / 'jobs' / 'm.json').read_text())['exit_code'] == 0
