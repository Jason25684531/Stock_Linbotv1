"""H1 near_high_252d single-factor score checkpoints from raw development OHLCV, converted exactly like Day3."""
import argparse
import json
from pathlib import Path
import sys

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from core.research import strategy_search as search
from core.research.d5_research_variant import raw_factor_panels
from core.research.day2_candidates import panel_to_long
from core.research.research_mode import RESEARCH_ASSUMPTIONS_V6, assumptions_sha
from scripts.prepare_research_inputs import write_once
from scripts.prepare_research_mode import digest

GRID = ROOT / 'outputs/strategy_research/h1_near_high_252d_v1/strategy_grid.csv'
FACTOR, COMPOSITE, DIRECTION = 'near_high_252d', 'near_high_252d__EQUAL', 1


def frames_of(market):
    bars = market['bars']
    if market.get('price_basis') != 'RAW' or not bars or max(bars) > '2025-12-31':
        raise ValueError('OOS_OR_INPUT_WINDOW_BLOCKED')
    quotes = pd.DataFrame([{'trade_date': day, 'stock_id': stock, **{f: value[f] for f in ('open', 'high', 'low', 'close', 'volume', 'amount')}}
                           for day, daily in bars.items() for stock, value in daily.items()])
    quotes['trade_date'] = pd.to_datetime(quotes['trade_date'])
    return {f: quotes.pivot(index='trade_date', columns='stock_id', values=f).sort_index()
            for f in ('open', 'high', 'low', 'close', 'volume', 'amount')}


def prepare(prepared, output, assumptions=RESEARCH_ASSUMPTIONS_V6):
    prepared, output = Path(prepared), Path(output)
    output.mkdir(parents=True, exist_ok=True)
    manifest = json.loads((prepared / 'input_manifest.json').read_text(encoding='utf-8'))
    if manifest['assumptions_sha256'] != assumptions_sha(assumptions):
        raise ValueError('UNREGISTERED_RESEARCH_ASSUMPTIONS')
    for name, expected in manifest['files'].items():
        if digest(prepared / name) != expected:
            raise ValueError(f'INPUT_CHECKPOINT_UNVERIFIED:{name}')
    grid = pd.read_csv(GRID)
    if len(grid) != 12 or set(grid['composite_id']) != {COMPOSITE}:
        raise ValueError('H1_GRID_SHAPE')
    market = json.loads((prepared / 'bars.json').read_text(encoding='utf-8'))
    universe = pd.DataFrame(json.loads((prepared / 'universe.json').read_text(encoding='utf-8')))
    days = sorted(universe['asof_date'].unique())
    if not days or min(days) < '2023-01-03' or max(days) > '2025-12-31':
        raise ValueError('INVALID_DEVELOPMENT_WINDOW')
    schedules = {n: days[::n] for n in (20, 60)}
    panel = raw_factor_panels(frames_of(market))[FACTOR]
    signal_dates = pd.to_datetime(schedules[20])
    ranks = panel_to_long(panel.loc[panel.index.isin(signal_dates)], factor_id=FACTOR, direction=DIRECTION)
    outputs = {}
    for cadence, selected in schedules.items():
        rows = universe.loc[universe['asof_date'].isin(selected)]
        outputs[f'universe_{cadence}.json'] = write_once(output / f'universe_{cadence}.json', rows.to_dict('records'))
    bars_sha = digest(prepared / 'bars.json')
    eligible = universe.loc[universe['member'], ['asof_date', 'asset_id']].copy()
    eligible['asof_date'] = pd.to_datetime(eligible['asof_date'])
    score = search.build_all_composite_scores(ranks, {COMPOSITE: {FACTOR: 1.0}})[COMPOSITE]
    score = score.merge(eligible, on=['asof_date', 'asset_id'], how='inner', validate='one_to_one')
    score = score[['asof_date', 'asset_id', 'composite_score']].copy()
    score['asof_date'] = pd.to_datetime(score['asof_date']).dt.strftime('%Y-%m-%d')
    score['signal_at'] = score['asof_date'] + 'T15:00:00+08:00'
    score['score_available_at'] = score['asof_date'] + 'T13:30:00+08:00'
    score['score_source_sha256'] = bars_sha
    coverage = {}
    for cadence, selected in schedules.items():
        rows = score.loc[score['asof_date'].isin(selected)].sort_values(['asof_date', 'asset_id'], kind='stable')
        outputs[f'scores_00_{cadence}.json'] = write_once(output / f'scores_00_{cadence}.json', rows.to_dict('records'))
        coverage[cadence] = {day: int(n) for day, n in rows.groupby('asof_date').size().items()}
    result = {'kind': 'H1_SINGLE_FACTOR_GRID', 'parameter_fit_scope': 'NONE_FIXED_FACTOR_FIXED_GRID',
              'label': 'DIAGNOSTIC_ONLY', 'data_label': 'RESEARCH_UNCERTIFIED', 'research_only': True,
              'terms_status': 'RESEARCH_ASSUMPTIONS', 'assumptions_sha256': assumptions_sha(assumptions),
              'producer_sha256': digest(Path(__file__)), 'input_manifest_sha256': digest(prepared / 'input_manifest.json'),
              'grid_sha256': digest(GRID), 'factor': FACTOR, 'factor_direction': DIRECTION,
              'factor_source': 'd5_research_variant.raw_factor_panels', 'composites': [COMPOSITE], 'signal_days': schedules,
              'scored_assets_per_signal_day': coverage, 'files': outputs}
    write_once(output / 'signals_manifest.json', result)
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--prepared', type=Path, default=ROOT / 'outputs/research_mode/prepared_v6')
    parser.add_argument('--output', type=Path, default=ROOT / 'outputs/research_mode/h1_signals_v1')
    args = parser.parse_args()
    prepare(args.prepared, args.output)
