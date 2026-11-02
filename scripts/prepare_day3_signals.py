"""Build resumable Day3 score checkpoints from raw development OHLCV."""
import argparse
import json
from pathlib import Path
import sys

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from core.research import strategy_search as search
from core.research.day2_candidates import panel_to_long
from core.research.research_mode import RESEARCH_ASSUMPTIONS_V6, assumptions_sha
from scripts.prepare_research_inputs import write_once
from scripts.prepare_research_mode import digest

POOL = ROOT / 'outputs/factor_validation/factor_validation_20260910_v1/accepted_factor_pool.csv'
MASTER = ROOT / 'outputs/factor_inventory/candidate_inventory_20260910_v1/candidate_factor_master.csv'
GRID = ROOT / 'outputs/strategy_research/strategy_search_20260914_v2/strategy_search_manifest.csv'
ARCHIVE = ROOT / 'outputs/strategy_research/strategy_search_20260914_v2/run_manifest.json'


def build_panels(market):
    bars = market['bars']
    if market.get('price_basis') != 'RAW' or not bars or max(bars) > '2025-12-31':
        raise ValueError('OOS_OR_INPUT_WINDOW_BLOCKED')
    quotes = pd.DataFrame([{'trade_date': day, 'stock_id': stock,
                            **{field: value[field] for field in ('open', 'high', 'low', 'close', 'volume', 'amount')}}
                           for day, daily in bars.items() for stock, value in daily.items()])
    quotes['trade_date'] = pd.to_datetime(quotes['trade_date'])
    frames = {field: quotes.pivot(index='trade_date', columns='stock_id', values=field).sort_index()
              for field in ('open', 'high', 'low', 'close', 'volume', 'amount')}
    return search.compute_factor_panels(frames, MASTER)


def prepare(prepared, output, assumptions=RESEARCH_ASSUMPTIONS_V6):
    prepared, output = Path(prepared), Path(output)
    output.mkdir(parents=True, exist_ok=True)
    manifest = json.loads((prepared / 'input_manifest.json').read_text(encoding='utf-8'))
    if manifest['assumptions_sha256'] != assumptions_sha(assumptions):
        raise ValueError('UNREGISTERED_RESEARCH_ASSUMPTIONS')
    for name, expected in manifest['files'].items():
        if digest(prepared / name) != expected:
            raise ValueError(f'INPUT_CHECKPOINT_UNVERIFIED:{name}')
    archive = json.loads(ARCHIVE.read_text(encoding='utf-8'))
    if digest(POOL) != archive['day2_input_hashes']['accepted_factor_pool.csv']:
        raise ValueError('ARCHIVED_FACTOR_POOL_CHANGED')
    pool = pd.read_csv(POOL, usecols=['candidate_id', 'research_direction', 'mean_ic', 'icir', 'factor_score'])
    grid, weights = search.search_grid(pool)
    frozen = pd.read_csv(GRID)
    if not grid.set_index('strategy_id').config_hash.equals(frozen.set_index('strategy_id').config_hash):
        raise ValueError('ARCHIVED_GRID_MISMATCH')
    market = json.loads((prepared / 'bars.json').read_text(encoding='utf-8'))
    universe = pd.DataFrame(json.loads((prepared / 'universe.json').read_text(encoding='utf-8')))
    days = sorted(universe['asof_date'].unique())
    if not days or min(days) < '2023-01-03' or max(days) > '2025-12-31':
        raise ValueError('INVALID_DEVELOPMENT_WINDOW')
    schedules = {n: days[::n] for n in (20, 60)}
    panels = build_panels(market)
    directions = search.resolve_effective_directions(pool).set_index('candidate_id')['effective_strategy_direction']
    signal_dates = pd.to_datetime(schedules[20])
    ranks = pd.concat([panel_to_long(panel.loc[panel.index.isin(signal_dates)], factor_id=factor,
                                    direction=int(directions[factor])) for factor, panel in panels.items()], ignore_index=True)
    outputs = {}
    for cadence, selected in schedules.items():
        rows = universe.loc[universe['asof_date'].isin(selected)]
        outputs[f'universe_{cadence}.json'] = write_once(output / f'universe_{cadence}.json', rows.to_dict('records'))
    bars_sha = digest(prepared / 'bars.json')
    eligible = universe.loc[universe['member'], ['asof_date', 'asset_id']].copy()
    eligible['asof_date'] = pd.to_datetime(eligible['asof_date'])
    for index, composite in enumerate(sorted(weights)):
        score = search.build_all_composite_scores(ranks, {composite: weights[composite]})[composite]
        score = score.merge(eligible, on=['asof_date', 'asset_id'], how='inner', validate='one_to_one')
        score = score[['asof_date', 'asset_id', 'composite_score']].copy()
        score['asof_date'] = pd.to_datetime(score['asof_date']).dt.strftime('%Y-%m-%d')
        score['signal_at'] = score['asof_date'] + 'T15:00:00+08:00'
        score['score_available_at'] = score['asof_date'] + 'T13:30:00+08:00'
        score['score_source_sha256'] = bars_sha
        for cadence, selected in schedules.items():
            rows = score.loc[score['asof_date'].isin(selected)].sort_values(['asof_date', 'asset_id'], kind='stable')
            name = f'scores_{index:02d}_{cadence}.json'
            outputs[name] = write_once(output / name, rows.to_dict('records'))
        print(f'{index + 1}/{len(weights)} {composite}: VERIFIED', flush=True)
    result = {'kind': 'DAY3_ARCHIVED_GRID', 'parameter_fit_scope': 'IN_SAMPLE_DEVELOPMENT_WINDOW',
              'label': 'DIAGNOSTIC_ONLY', 'data_label': 'RESEARCH_UNCERTIFIED', 'research_only': True,
              'terms_status': 'RESEARCH_ASSUMPTIONS', 'assumptions_sha256': assumptions_sha(assumptions),
              'producer_sha256': digest(Path(__file__)), 'input_manifest_sha256': digest(prepared / 'input_manifest.json'),
              'pool_sha256': digest(POOL), 'master_sha256': digest(MASTER), 'grid_sha256': digest(GRID),
              'composites': sorted(weights), 'signal_days': schedules, 'files': outputs}
    write_once(output / 'signals_manifest.json', result)
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--prepared', type=Path, default=ROOT / 'outputs/research_mode/prepared_v6')
    parser.add_argument('--output', type=Path, default=ROOT / 'outputs/research_mode/day3_signals_v6')
    args = parser.parse_args()
    prepare(args.prepared, args.output)
