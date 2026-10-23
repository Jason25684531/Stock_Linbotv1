"""Track B (G2/G3, D5, Day3): timing probe, reruns that capture each configuration's daily returns and orders, execution audit, statistics.

The existing jobs are reused unchanged; only their engine call is wrapped to keep its result. Inputs are the official-source D3 dataset
and PIT fundamentals the jobs already read. Each job truncates its input to its own development boundary.
"""
import argparse
import json
import sys
import tempfile
import time
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.audit_strategy_history import read, write

OUT = ROOT / 'outputs/dual_track_validation/2026-10-23'
FAMILY_SIZE = {'G2G3': 36, 'D5': 48, 'DAY3': 300}
PROBE_BUDGET_HOURS = 4.0  # owner threshold (OD-25): all 384 configurations only if the estimate is within this


class ProbeDone(Exception):
    """Raised right after the first engine call so a probe never writes a full run."""


def shape(value, depth=0):
    """Compact description of an engine result, so the capture code is written against facts."""
    if isinstance(value, dict):
        return {k: shape(v, depth + 1) for k, v in list(value.items())[:30]} if depth < 3 else 'dict'
    if isinstance(value, pd.DataFrame):
        return f'DataFrame{tuple(value.shape)} cols={list(value.columns)[:10]}'
    if isinstance(value, pd.Series):
        return f'Series[{len(value)}] {value.index.min()}..{value.index.max()}'
    return type(value).__name__


def hook(module, name, record, stop=True):
    """Wrap module.name: time the call, keep (args summary, result); with stop=True raise ProbeDone after the first call."""
    original = getattr(module, name)
    state = {'first_call_at': None}

    def wrapper(*args, **kwargs):
        state['first_call_at'] = state['first_call_at'] or time.perf_counter()
        start = time.perf_counter()
        result = original(*args, **kwargs)
        record.setdefault('durations', []).append(time.perf_counter() - start)
        record.setdefault('results', []).append(result)
        record.setdefault('first_call_offset', state['first_call_at'] - record['started'])
        if stop:
            raise ProbeDone
        return result

    setattr(module, name, wrapper)
    return lambda: setattr(module, name, original)


def run_probe(family):
    record = {'family': family, 'started': time.perf_counter()}
    with tempfile.TemporaryDirectory() as tmp:
        target = Path(tmp) / 'probe'
        if family == 'DAY3':
            import jobs.run_strategy_search as job
            restore = hook(job.ss, 'run_backtest_tracks', record)
            call = lambda: job.run(day2_dir=job.DEFAULT_DAY2_DIR, d3_root=job.DEFAULT_D3_ROOT, master_path=job.DEFAULT_MASTER_PATH, output_dir=target)
        elif family == 'G2G3':
            import jobs.run_fundamental_strategy_validation as job
            restore = hook(job.f, 'run_cost_tracks', record)
            call = lambda: job.run_pipeline(output_dir=target)
        else:
            import jobs.run_portfolio_research as job
            restore = hook(job, 'run_vectorbt', record)
            call = lambda: job.run(handoff_path=Path('artifacts/d5_handoff/d4_acceptance_20260811_v8_with_scoreboard'),
                                   dataset_path=Path('artifacts/factors/d3_full_20230103_20260728/research_dataset'), output_dir=target)
        try:
            call()
        except ProbeDone:
            pass
        finally:
            restore()
    setup, per_config = record['first_call_offset'], record['durations'][0]
    n = FAMILY_SIZE[family]
    return {'family': family, 'configurations': n, 'setup_seconds': round(setup, 1), 'first_config_seconds': round(per_config, 2),
            'estimated_total_hours': round((setup + n * per_config) / 3600, 2), 'result_shape': shape(record['results'][0])}


def probe():
    """First configuration of each family in the job's own (id-sorted) order; performance is never looked at."""
    results = {}
    for family in FAMILY_SIZE:
        results[family] = run_probe(family)
        print(family, {k: v for k, v in results[family].items() if k != 'result_shape'})
    total = sum(r['estimated_total_hours'] for r in results.values())
    write(OUT / 'timing_probe.json', {
        'rule': 'OD-25: run all 384 configurations only if the estimate is within 4 hours; otherwise registered and Frozen configurations first, the rest PENDING',
        'selection': "each job's first configuration in its own grid order; no result metric was read", 'families': results,
        'estimated_total_hours': round(total, 2), 'decision': 'RUN_ALL_384' if total <= PROBE_BUDGET_HOURS else 'REGISTERED_AND_FROZEN_FIRST'})
    print('estimated total hours', round(total, 2))


STAGES = ('probe',)

if __name__ == '__main__':
    sys.stdout.reconfigure(encoding='utf-8')
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('stage', choices=STAGES)
    globals()[parser.parse_args().stage]()
