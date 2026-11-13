"""Disk cache for NativeResearchData cross-sections (change 2026-11-12, design D4).

A cross-section for date d reads only snapshot rows in [d - 150 days, d] plus the filings published before d, so it is keyed by the
snapshot, the feature code, the filing digests (they depend on the window end) and the chip manifest, never by the run window.
Frames are pickled: the round trip keeps every dtype and NaN exactly. A sampled recompute that differs writes DISABLED and every
later attach is refused. PLATFORM_V2_FEATURE_CACHE=0 switches the cache off.
"""
from __future__ import annotations

import hashlib
import json
import os
import pickle
import tempfile
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[3] / 'outputs/platform_v2/feature_cache'
CODE_FILES = ('core/research/native_frame.py', 'core/calc_indicators.py')


def code_hashes() -> dict:
    base = Path(__file__).resolve().parents[3]
    return {name: hashlib.sha256((base / name).read_bytes()).hexdigest() for name in CODE_FILES}


def cache_key(parts: dict) -> str:
    """parts: snapshot_id, digests of financials/revenue/publications, chip manifest hash, fill mode. Code hashes are always added."""
    payload = json.dumps({**parts, 'code': code_hashes()}, sort_keys=True, default=str)
    return 'FC1-' + hashlib.sha256(payload.encode('utf-8')).hexdigest()[:16]


def enabled(key, root=ROOT) -> bool:
    return os.environ.get('PLATFORM_V2_FEATURE_CACHE', '1') != '0' and not (Path(root) / key / 'DISABLED').exists()


def _path(root, key, date):
    return Path(root) / key / f'{pd.Timestamp(date):%Y-%m-%d}.pkl'


def attach(data, key, root=ROOT) -> bool:
    """Route data.cross_section through the disk cache; False (nothing attached) when disabled."""
    if not enabled(key, root):
        return False
    original = data.cross_section

    def cached(date):
        if date in data._cache:
            return data._cache[date]
        path = _path(root, key, date)
        if path.exists():
            frame = pd.read_pickle(path)
            data._cache[date] = frame
            return frame
        frame = original(date)
        path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(dir=path.parent, delete=False, suffix='.tmp') as handle:
            pickle.dump(frame, handle, protocol=pickle.HIGHEST_PROTOCOL)
        os.replace(handle.name, path)  # concurrent workers write identical frames; last replace wins
        return frame

    data.cross_section, data.feature_cache_original = cached, original
    return True


def verify(data, key, dates, root=ROOT) -> list[str]:
    """Recompute sampled cached dates without the cache; any difference disables the key and is returned."""
    mismatches = []
    for date in dates:
        path = _path(root, key, date)
        if not path.exists():
            continue
        stored = pd.read_pickle(path)
        held = data._cache.pop(date, None)
        try:
            fresh = data.feature_cache_original(date)
            pd.testing.assert_frame_equal(stored, fresh, check_exact=True)
        except AssertionError as error:
            mismatches.append(f'{pd.Timestamp(date):%Y-%m-%d}: {str(error)[:200]}')
        finally:
            data._cache.pop(date, None)
            if held is not None:
                data._cache[date] = held
    if mismatches:
        (Path(root) / key / 'DISABLED').write_text('\n'.join(mismatches), encoding='utf-8')
    return mismatches
