import numpy as np
import pandas as pd
import pytest

from core.research.platform import feature_cache as fc


class Data:
    """NativeResearchData's cache contract: cross_section(date) fills and reads self._cache."""

    def __init__(self):
        self._cache, self.calls = {}, 0

    def cross_section(self, date):
        if date in self._cache:
            return self._cache[date]
        self.calls += 1
        frame = pd.DataFrame({'stock_id': ['1101', '2330'], 'x': [np.nan, 1.0 / 3], 'n': pd.array([1, None], dtype='Int64'),
                              'when': [date, date]})
        self._cache[date] = frame
        return frame


DAY = pd.Timestamp('2024-01-02')


def test_hit_equals_recompute_and_skips_work(tmp_path):
    first = Data()
    assert fc.attach(first, 'k', tmp_path)
    computed = first.cross_section(DAY)
    second = Data()
    fc.attach(second, 'k', tmp_path)
    pd.testing.assert_frame_equal(second.cross_section(DAY), computed, check_exact=True)
    assert second.calls == 0
    assert fc.verify(second, 'k', [DAY], tmp_path) == []


def test_different_key_misses(tmp_path, monkeypatch):
    a = fc.cache_key({'snapshot_id': 's', 'financials': 'f1'})
    assert a != fc.cache_key({'snapshot_id': 's', 'financials': 'f2'})
    monkeypatch.setattr(fc, 'code_hashes', lambda: {'core/research/native_frame.py': 'changed'})
    assert fc.cache_key({'snapshot_id': 's', 'financials': 'f1'}) != a
    first = Data()
    fc.attach(first, a, tmp_path)
    first.cross_section(DAY)
    other = Data()
    fc.attach(other, 'other', tmp_path)
    other.cross_section(DAY)
    assert other.calls == 1


def test_mismatch_disables_and_switch_turns_off(tmp_path, monkeypatch):
    data = Data()
    fc.attach(data, 'k', tmp_path)
    data.cross_section(DAY)
    pd.to_pickle(pd.DataFrame({'stock_id': ['9999']}), tmp_path / 'k' / '2024-01-02.pkl')
    assert fc.verify(data, 'k', [DAY], tmp_path)
    assert not fc.attach(Data(), 'k', tmp_path)
    monkeypatch.setenv('PLATFORM_V2_FEATURE_CACHE', '0')
    assert not fc.attach(Data(), 'fresh', tmp_path)
