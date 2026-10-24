"""The production status file must never be written by a test (see the guard in test/conftest.py)."""
import hashlib

from core.runtime import fundamental_production as production


def _state():
    path = production.OPERATION_ROOT / production.STATUS_FILENAME
    stat = path.stat()
    return hashlib.sha256(path.read_bytes()).hexdigest(), stat.st_mtime_ns, stat.st_size


def test_a_write_aimed_at_the_real_operation_folder_lands_in_tmp_path(tmp_path):
    before = _state()
    production._write_status(production.OPERATION_ROOT, {'asof_date': '1999-01-01', 'marker': 'written-by-test'})
    assert _state() == before
    assert (tmp_path / 'redirected_production_status' / production.STATUS_FILENAME).read_text(encoding='utf-8').count('written-by-test') == 1


def test_a_write_to_another_folder_is_untouched(tmp_path):
    production._write_status(tmp_path, {'marker': 'elsewhere'})
    assert 'elsewhere' in (tmp_path / production.STATUS_FILENAME).read_text(encoding='utf-8')
