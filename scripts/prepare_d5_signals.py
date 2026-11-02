"""Build per-decision, point-in-time D5 research-variant score checkpoints from raw development bars."""
import argparse
import json
from pathlib import Path
import sys

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from core.research.d5_research_variant import D5_POLICY_V1, fit_at, historical_ic, policy_sha, raw_factor_panels, weights_at
from core.research.research_mode import D5_RESEARCH_ASSUMPTIONS_V5, INPUT_VERSION_OF, REGISTERED_ASSUMPTIONS, assumptions_sha, registered_version
from scripts.prepare_research_inputs import write_once
from scripts.prepare_research_mode import digest

METHODS = ('equal', 'ic', 'icir', 'redundancy_adjusted')


def build_frames(market):
    bars = market['bars']
    if market.get('price_basis') != 'RAW' or not bars or max(bars) > '2025-12-31':
        raise ValueError('OOS_OR_INPUT_WINDOW_BLOCKED')
    quotes = pd.DataFrame([{'trade_date': day, 'stock_id': stock,
                            **{field: value[field] for field in ('open', 'high', 'low', 'close', 'volume', 'amount')}}
                           for day, daily in bars.items() for stock, value in daily.items()])
    quotes['trade_date'] = pd.to_datetime(quotes['trade_date'])
    return {field: quotes.pivot(index='trade_date', columns='stock_id', values=field).sort_index()
            for field in ('open', 'high', 'low', 'close', 'volume', 'amount')}


def composite(fit, method, ranks, signal_date):
    """Weighted mean of direction-adjusted percentile ranks over the factors fitted at this decision."""
    weights = weights_at(fit, method)
    directions = dict(zip(fit['candidates']['factor_id'], fit['candidates']['direction']))
    parts, total = None, None
    for factor_id, weight in zip(weights['factor_id'], weights['final_factor_weight']):
        rank = ranks[factor_id].loc[signal_date]
        adjusted = rank if directions[factor_id] > 0 else 1 - rank
        weighted = adjusted * weight
        available = rank.notna() * weight
        parts = weighted.fillna(0) if parts is None else parts + weighted.fillna(0)
        total = available if total is None else total + available
    score = (parts / total.where(total > 0)).dropna()
    return score, weights


def prepare(prepared, output, assumptions=D5_RESEARCH_ASSUMPTIONS_V5):
    prepared, output = Path(prepared), Path(output)
    output.mkdir(parents=True, exist_ok=True)
    manifest = json.loads((prepared / 'input_manifest.json').read_text(encoding='utf-8'))
    version = registered_version(assumptions_sha(assumptions))
    if version is None or manifest['assumptions_sha256'] != assumptions_sha(REGISTERED_ASSUMPTIONS[INPUT_VERSION_OF[version]]):
        raise ValueError('UNREGISTERED_RESEARCH_ASSUMPTIONS')
    for name, expected in manifest['files'].items():
        if digest(prepared / name) != expected:
            raise ValueError(f'INPUT_CHECKPOINT_UNVERIFIED:{name}')
    market = json.loads((prepared / 'bars.json').read_text(encoding='utf-8'))
    universe = pd.DataFrame(json.loads((prepared / 'universe.json').read_text(encoding='utf-8')))
    actions = json.loads((prepared / 'actions.json').read_text(encoding='utf-8'))
    days = sorted(universe['asof_date'].unique())
    if not days or min(days) < '2023-01-03' or max(days) > '2025-12-31':
        raise ValueError('INVALID_DEVELOPMENT_WINDOW')
    schedules = {n: days[::n] for n in (20, 60)}
    frames = build_frames(market)
    panels = raw_factor_panels(frames)
    membership = universe.pivot(index='asof_date', columns='asset_id', values='member').fillna(False).astype(bool)
    membership.index = pd.to_datetime(membership.index)
    events = [{'ex_date': e['ex_date'], 'stock_id': e['stock_id']} for e in actions['actions'] + actions['unresolved_events']]
    ic, ranks = historical_ic(frames, panels, membership, events)
    bars_sha = digest(prepared / 'bars.json')
    eligible = universe.loc[universe['member'], ['asof_date', 'asset_id']]
    eligible_sets = {day: set(group['asset_id']) for day, group in eligible.groupby('asof_date')}
    outputs, fits, skipped = {}, {}, []
    for cadence, selected in schedules.items():
        rows = universe.loc[universe['asof_date'].isin(selected)]
        outputs[f'universe_{cadence}.json'] = write_once(output / f'universe_{cadence}.json', rows.to_dict('records'))
    scores = {method: [] for method in METHODS}
    for day in schedules[20]:
        fit = fit_at(day, ic, ranks)
        if fit is None:
            skipped.append({'asof_date': day, 'reason': 'INSUFFICIENT_COMPLETED_HISTORY'})
            continue
        fits[day] = {'last_label_asof': fit['last_label_asof'],
                     'candidates': fit['candidates'].to_dict('records'), 'weights': {}}
        for method in METHODS:
            score, weights = composite(fit, method, ranks, pd.Timestamp(day))
            fits[day]['weights'][method] = dict(zip(weights['factor_id'], map(float, weights['final_factor_weight'])))
            members = eligible_sets.get(day, set())
            for asset, value in score.items():
                if asset in members:
                    scores[method].append({'asof_date': day, 'asset_id': asset, 'composite_score': float(value),
                                           'signal_at': day + 'T15:00:00+08:00', 'score_available_at': day + 'T13:30:00+08:00',
                                           'score_source_sha256': bars_sha})
    for index, method in enumerate(METHODS):
        frame = pd.DataFrame(scores[method], columns=['asof_date', 'asset_id', 'composite_score', 'signal_at',
                                                      'score_available_at', 'score_source_sha256'])
        for cadence, selected in schedules.items():
            rows = frame.loc[frame['asof_date'].isin(selected)].sort_values(['asof_date', 'asset_id'], kind='stable')
            name = f'scores_{index:02d}_{cadence}.json'
            outputs[name] = write_once(output / name, rows.to_dict('records'))
        print(f'{index + 1}/{len(METHODS)} {method}: VERIFIED ({len(frame)} rows)', flush=True)
    outputs['fits.json'] = write_once(output / 'fits.json', {'policy_sha256': policy_sha(), 'policy': D5_POLICY_V1,
                                                              'fits': fits, 'skipped_signal_dates': skipped})
    result = {'kind': 'D5_RESEARCH_PIT_V1', 'parameter_fit_scope': 'PER_DECISION_POINT_IN_TIME',
              'label': 'DIAGNOSTIC_ONLY', 'data_label': 'RESEARCH_UNCERTIFIED', 'research_only': True,
              'terms_status': 'RESEARCH_ASSUMPTIONS', 'assumptions_sha256': assumptions_sha(assumptions),
              'policy_sha256': policy_sha(), 'producer_sha256': digest(Path(__file__)),
              'variant_sha256': digest(ROOT / 'core/research/d5_research_variant.py'),
              'input_manifest_sha256': digest(prepared / 'input_manifest.json'),
              'composites': list(METHODS), 'signal_days': schedules, 'fitted_signal_dates': len(fits),
              'skipped_signal_dates': len(skipped), 'files': outputs}
    write_once(output / 'signals_manifest.json', result)
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--prepared', type=Path, default=ROOT / 'outputs/research_mode/prepared_v6')
    parser.add_argument('--output', type=Path, default=ROOT / 'outputs/research_mode/d5_signals_v5')
    args = parser.parse_args()
    prepare(args.prepared, args.output)
