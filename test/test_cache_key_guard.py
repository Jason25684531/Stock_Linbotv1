"""Workflow guard: the two files that define the feature-cache key must not change by accident.

Cache FC1-b5ce30f75a5d9664 (2,577 trading days) was checked equal to a fresh computation (outputs/strategy_v6/cache_equivalence.json)
with exactly these file contents. A change makes every new key miss and costs hours of recomputation; edit them only on purpose,
re-run the equivalence check, then update the pins here.
"""
from core.research.platform import feature_cache

PINNED = {'core/research/native_frame.py': 'a80f1a495a539d53d1d7394f666322c82b6c1541759d1279ec0fd1828ee25d1f',
          'core/calc_indicators.py': '7c66a5486497bd99e42f501a6ae8525367c7dfa974c4608fcd696f59317cea77'}


def test_the_files_that_define_the_cache_key_match_the_verified_pins():
    assert feature_cache.code_hashes() == PINNED


def test_the_guard_covers_every_file_the_key_reads():
    assert set(feature_cache.CODE_FILES) == set(PINNED)
