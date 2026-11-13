import pytest

from core.research.platform.evidence import validate_evidence, validate_evidence_fields, write_result


def evidence(**changes):
    value = {
        'data_class': 'DEMO', 'as_of_date': '2025-12-31',
        'source_timestamp': '2025-12-31T12:00:00Z', 'data_trust': 'PARTIAL',
        'identity': 'run-1', 'labels': ['RESEARCH_ONLY', 'NOT_CERTIFIED'],
        'strategy_id': 's', 'run_id': 'r', 'snapshot_id': 'snap',
        'engine_version': 'v2', 'rule_version': 'v1',
    }
    value.update(changes)
    return value


@pytest.fixture(autouse=True)
def output_root(tmp_path, monkeypatch):
    monkeypatch.setattr('core.research.platform.evidence.OUTPUT_ROOT', tmp_path)


def test_required_fields_and_existing_trust(tmp_path):
    invalid = evidence(snapshot_id=None)
    with pytest.raises(ValueError, match='snapshot_id'):
        write_result(tmp_path / 'run.json', invalid, {}, 'backtest')
    assert not (tmp_path / 'run.json').exists()
    with pytest.raises(ValueError, match='trusted V1'):
        validate_evidence(evidence(data_class='REAL_RESEARCH', data_trust='PARTIAL'), 'backtest')
    with pytest.raises(ValueError, match='trusted V1'):
        validate_evidence(evidence(data_class='REAL_RESEARCH', data_trust='VERIFIED'), 'backtest')
    validate_evidence(evidence(), 'backtest')


def test_demo_and_real_are_separated(tmp_path):
    with pytest.raises(ValueError, match='trusted V1'):
        write_result(tmp_path / 'demo' / 'x.json', evidence(data_class='REAL_RESEARCH'), {}, 'backtest')
    demo = evidence(data_class='DEMO')
    with pytest.raises(ValueError, match='DEMO'):
        write_result(tmp_path / 'x.json', demo, {}, 'backtest')
    write_result(tmp_path / 'demo' / 'x.json', demo, {}, 'backtest')
    assert (tmp_path / 'demo' / 'x.json').exists()


def test_kind_specific_requirements():
    with pytest.raises(ValueError, match='model_version'):
        validate_evidence(evidence(), 'prediction')
    with pytest.raises(ValueError, match='source_table'):
        validate_evidence(evidence(), 'selection')
    with pytest.raises(ValueError, match='evaluation_id'):
        validate_evidence(evidence(), 'readiness')
    validate_evidence(evidence(evaluation_id='evaluation'), 'readiness')


@pytest.mark.parametrize('changes', [
    {'identity': {'unexpected': 'object'}}, {'as_of_date': 'tomorrow'},
    {'source_timestamp': 'not a timestamp'}, {'labels': 'RESEARCH_ONLY NOT_CERTIFIED'},
    {'members': 'invalid', 'strategy_id': None}, {'data_trust': 'UNRECOGNISED'},
])
def test_invalid_field_types_and_values(changes):
    with pytest.raises(ValueError):
        validate_evidence(evidence(**changes), 'backtest')


def test_failed_atomic_replace_preserves_existing_result(tmp_path, monkeypatch):
    path = tmp_path / 'demo' / 'run.json'
    path.parent.mkdir()
    path.write_text('original', encoding='utf-8')
    before = set(path.parent.iterdir())
    def fail(*args):
        raise OSError('simulated interruption')
    monkeypatch.setattr('core.research.platform.evidence.os.link', fail)
    with pytest.raises(OSError, match='interruption'):
        write_result(path, evidence(), {}, 'backtest')
    assert path.read_text(encoding='utf-8') == 'original'
    assert set(path.parent.iterdir()) == before


def test_existing_result_and_outside_root_are_never_overwritten(tmp_path):
    path = tmp_path / 'demo' / 'run.json'
    write_result(path, evidence(), {'value': 1}, 'backtest')
    original = path.read_bytes()
    with pytest.raises(FileExistsError):
        write_result(path, evidence(), {'value': 2}, 'backtest')
    assert path.read_bytes() == original
    with pytest.raises(ValueError, match='outside output root'):
        write_result(tmp_path.parent / 'outside.json', evidence(), {}, 'backtest')


def test_real_writer_refuses_forged_trust_and_sealed_date(tmp_path):
    record = evidence(data_class='REAL_RESEARCH', data_trust='PARTIAL', evaluation_id='evaluation')
    path = tmp_path / 'result.json'
    with pytest.raises(ValueError, match='trusted V1'):
        write_result(path, record, {'value': 1}, 'readiness')
    assert not path.exists()
    with pytest.raises(ValueError, match='2025-12-31'):
        write_result(tmp_path / 'future.json', record | {'as_of_date': '2026-01-01'}, {}, 'readiness')


def test_real_fields_are_checked_without_authorizing_write(tmp_path):
    record = evidence(data_class='REAL_RESEARCH', data_trust='PARTIAL', evaluation_id='evaluation')
    validate_evidence_fields(record, 'readiness')
    assert record['data_class'] == 'REAL_RESEARCH'
    with pytest.raises(ValueError, match='2025-12-31'):
        validate_evidence_fields(record | {'as_of_date': '2026-01-01'}, 'readiness')
    with pytest.raises(ValueError, match='trusted V1'):
        write_result(tmp_path / 'result.json', record, {}, 'readiness')
