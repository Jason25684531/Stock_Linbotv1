"""H4:GROWTH-MOMENTUM-V2 score checkpoints: 0.5 x growth rank + 0.5 x near_high_252d rank among prepared_v6 members per signal day."""
import argparse
import json
from pathlib import Path
import sys

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from core.research.d5_research_variant import raw_factor_panels
from core.research.research_mode import RESEARCH_ASSUMPTIONS_V6, assumptions_sha
from scripts.prepare_h1_signals import frames_of
from scripts.prepare_research_inputs import write_once
from scripts.prepare_research_mode import digest

GRID = ROOT / 'outputs/strategy_research/h4_growth_momentum_v1/strategy_grid.csv'
FUNDAMENTAL = ROOT / 'data/processed/fundamental_pit_v3_2_verified'
FACTOR_RESULT = ROOT / 'outputs/research_mode/g2g3_factor_dev_v2/factor_result.json'
COMPOSITE = 'H4_GROWTH_MOMENTUM'
GROWTH = {'G2_OPERATING_INCOME_YOY': 'operating_income_yoy', 'G3_EPS_YOY': 'eps_yoy'}


def _winsor_rank(values):
    """p1/p99 clip among the stocks that have a value, then percentile rank (average ties); missing stays missing."""
    lo, hi = values.quantile(.01), values.quantile(.99)
    return values.clip(lo, hi).rank(pct=True, method='average')


def score_components(members, growth_day, momentum_day, ready):
    """One signal day: per-metric growth ranks, their mean, and the momentum rank. momentum_day is None when the day has no bar."""
    ranks = {m: _winsor_rank(growth_day[m].reindex(members).dropna()) for m in ready}
    g = pd.concat([ranks[m] for m in ready], axis=1).mean(axis=1)
    mom = momentum_day.reindex(members).dropna().rank(pct=True, method='average') if momentum_day is not None else pd.Series(dtype=float)
    return ranks, g, mom


def scores(eligible, growth, momentum, ready):
    """eligible: (asof_date, asset_id) members on signal days; growth: asof_date x ticker frame per metric; momentum: date x asset panel."""
    rows = []
    for day, members in eligible.groupby('asof_date')['asset_id']:
        _, g, mom = score_components(members, {m: growth[m].loc[day] for m in ready}, momentum.loc[day] if day in momentum.index else None, ready)
        both = g.index.intersection(mom.index)
        rows += [{'asof_date': day, 'asset_id': a, 'composite_score': .5 * g[a] + .5 * mom[a]} for a in both]
    return pd.DataFrame(rows, columns=['asof_date', 'asset_id', 'composite_score'])


def prepare(prepared, output, assumptions=RESEARCH_ASSUMPTIONS_V6):
    prepared, output = Path(prepared), Path(output)
    output.mkdir(parents=True, exist_ok=True)
    manifest = json.loads((prepared / 'input_manifest.json').read_text(encoding='utf-8'))
    if manifest['assumptions_sha256'] != assumptions_sha(assumptions):
        raise ValueError('UNREGISTERED_RESEARCH_ASSUMPTIONS')
    for name, expected in manifest['files'].items():
        if digest(prepared / name) != expected:
            raise ValueError(f'INPUT_CHECKPOINT_UNVERIFIED:{name}')
    result = json.loads(FACTOR_RESULT.read_text(encoding='utf-8'))
    ready = [GROWTH[f] for f, d in result['decision'].items() if d == 'READY_FOR_STRATEGY_TEST']
    if result['admission'] != 'PASS' or not ready:
        raise ValueError('H4_RUN_CONDITION_NOT_MET')
    grid = pd.read_csv(GRID)
    if len(grid) != 12 or set(grid['composite_id']) != {COMPOSITE}:
        raise ValueError('H4_GRID_SHAPE')
    market = json.loads((prepared / 'bars.json').read_text(encoding='utf-8'))
    universe = pd.DataFrame(json.loads((prepared / 'universe.json').read_text(encoding='utf-8')))
    days = sorted(universe['asof_date'].unique())
    if not days or min(days) < '2023-01-03' or max(days) > '2025-12-31':
        raise ValueError('INVALID_DEVELOPMENT_WINDOW')
    schedules = {n: days[::n] for n in (20, 60)}
    matrix = pd.read_parquet(FUNDAMENTAL / 'fundamental_matrix.parquet')
    matrix['asof_date'], matrix['ticker'] = pd.to_datetime(matrix['asof_date']), matrix['ticker'].astype(str)
    if matrix['asof_date'].max() > pd.Timestamp('2025-12-31'):
        raise ValueError('OOS_OR_INPUT_WINDOW_BLOCKED')
    growth = {m: matrix.pivot(index='asof_date', columns='ticker', values=m) for m in ready}
    momentum = raw_factor_panels(frames_of(market))['near_high_252d']
    outputs = {}
    for cadence, selected in schedules.items():
        rows = universe.loc[universe['asof_date'].isin(selected)]
        outputs[f'universe_{cadence}.json'] = write_once(output / f'universe_{cadence}.json', rows.to_dict('records'))
    eligible = universe.loc[universe['member'] & universe['asof_date'].isin(schedules[20]), ['asof_date', 'asset_id']].copy()
    eligible['asof_date'] = pd.to_datetime(eligible['asof_date'])
    eligible['asset_id'] = eligible['asset_id'].astype(str)
    score = scores(eligible, growth, momentum, ready)
    score['asof_date'] = pd.to_datetime(score['asof_date']).dt.strftime('%Y-%m-%d')
    score['signal_at'] = score['asof_date'] + 'T15:00:00+08:00'
    score['score_available_at'] = score['asof_date'] + 'T13:30:00+08:00'
    score['score_source_sha256'] = digest(prepared / 'bars.json')
    coverage = {}
    for cadence, selected in schedules.items():
        rows = score.loc[score['asof_date'].isin(selected)].sort_values(['asof_date', 'asset_id'], kind='stable')
        outputs[f'scores_00_{cadence}.json'] = write_once(output / f'scores_00_{cadence}.json', rows.to_dict('records'))
        coverage[cadence] = {day: int(n) for day, n in rows.groupby('asof_date').size().items()}
    result = {'kind': 'H4_GROWTH_MOMENTUM_GRID', 'parameter_fit_scope': 'NONE_FIXED_WEIGHTS_FIXED_GRID', 'label': 'DIAGNOSTIC_ONLY',
              'data_label': 'RESEARCH_UNCERTIFIED', 'research_only': True, 'terms_status': 'RESEARCH_ASSUMPTIONS',
              'assumptions_sha256': assumptions_sha(assumptions), 'producer_sha256': digest(Path(__file__)),
              'input_manifest_sha256': digest(prepared / 'input_manifest.json'), 'grid_sha256': digest(GRID),
              'growth_metrics': ready, 'weights': {'growth': .5, 'momentum': .5},
              'fundamental_sha256': {n: digest(FUNDAMENTAL / n) for n in ('fundamental_matrix.parquet', 'fundamental_records.parquet')},
              'factor_result_sha256': digest(FACTOR_RESULT), 'composites': [COMPOSITE], 'signal_days': schedules,
              'scored_assets_per_signal_day': coverage, 'files': outputs}
    write_once(output / 'signals_manifest.json', result)
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--prepared', type=Path, default=ROOT / 'outputs/research_mode/prepared_v6')
    parser.add_argument('--output', type=Path, default=ROOT / 'outputs/research_mode/h4_signals_v1')
    args = parser.parse_args()
    prepare(args.prepared, args.output)
