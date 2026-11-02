"""Execute archived research grids offline, with independent trial and result checkpoints.

Fail closed locally, continue globally: a configuration that cannot be computed records its
reason (manifest.json or failure.json) and the batch moves to the next one.
"""
import argparse
import json
from pathlib import Path
import sys
import traceback

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from core.research.d5_research_variant import policy_sha
from core.research.repair_runner import run
from core.research.research_mode import D5_RESEARCH_ASSUMPTIONS_V5, D5_RESEARCH_STRATEGY, RESEARCH_ASSUMPTIONS_V6, assumptions_sha
from scripts import research_workflow as workflow
from scripts.prepare_research_inputs import write_once

RESEARCH = ROOT / 'outputs/research_mode'
FAMILIES = {
    'day3': {'prepared': RESEARCH / 'prepared_v6', 'signals': RESEARCH / 'day3_signals_v6', 'output': RESEARCH / 'day3_runs_v6',
             'grid': ROOT / 'outputs/strategy_research/strategy_search_20260914_v2/strategy_search_manifest.csv',
             'count': 300, 'assumptions': RESEARCH_ASSUMPTIONS_V6, 'evidence_kind': 'DAY3_RAW_REPRODUCIBLE_V1'},
    'd5': {'prepared': RESEARCH / 'prepared_v6', 'signals': RESEARCH / 'd5_signals_v5', 'output': RESEARCH / 'd5_runs_v5',
           'grid': ROOT / 'outputs/portfolio_research/d5_portfolio_research_20260814_v1/parameter_grid.csv',
           'count': 48, 'assumptions': D5_RESEARCH_ASSUMPTIONS_V5, 'evidence_kind': 'D5_RESEARCH_PIT_REPRODUCIBLE_V1'},
}
COSTS = {'initial_cash': 1_000_000, 'fee_rate': .001425, 'tax_rate': .003, 'minimum_fee': 20, 'slippage': .001}


def configs(family, prepared, signals, evidence):
    spec = FAMILIES[family]
    manifest = json.loads((signals / 'signals_manifest.json').read_text(encoding='utf-8'))
    grid = pd.read_csv(spec['grid'])
    key = 'strategy_id' if family == 'day3' else 'config_id'
    if len(grid) != spec['count'] or grid[key].duplicated().any():
        raise ValueError('ARCHIVED_GRID_COUNT_MISMATCH')
    sha = assumptions_sha(spec['assumptions'])
    for row in grid.to_dict('records'):
        cadence = int(row['rebalance_days'])
        if family == 'day3':
            index = manifest['composites'].index(row['composite_id'])
            identity = {'strategy': row['factor_set'], 'config_id': row['strategy_id'], 'composite_id': row['composite_id'],
                        'stock_weighting': row['portfolio_weighting']}
        else:
            index = manifest['composites'].index(row['combination_method'])
            identity = {'strategy': D5_RESEARCH_STRATEGY, 'config_id': row['config_id'],
                        'combination_method': row['combination_method'], 'stock_weighting': row['stock_weighting']}
        yield {**identity, 'top_n': int(row['top_n']), 'rebalance_days': cadence, **COSTS,
               'data_label': 'RESEARCH_UNCERTIFIED', 'data_end': '2025-12-31', 'assumptions_sha256': sha,
               'inputs': {'bars': str(prepared / 'bars.json'), 'actions': str(prepared / 'actions.json'),
                          'scores': str(signals / f'scores_{index:02d}_{cadence}.json'),
                          'universe': str(signals / f'universe_{cadence}.json'), 'evidence': str(evidence)}}


def run_family(family, output=None, prepared=None, signals=None, start=0, end=None):
    spec = FAMILIES[family]
    output = Path(output or spec['output']).resolve()
    prepared = Path(prepared or spec['prepared'])
    signals = Path(signals or spec['signals'])
    if not output.is_relative_to(RESEARCH.resolve()) or output == RESEARCH.resolve():
        raise ValueError('OUTPUT_OUTSIDE_RESEARCH_DIRECTORY')
    output.mkdir(parents=True, exist_ok=True)
    evidence = output / 'runner_evidence.json'
    record = {'kind': spec['evidence_kind'], 'label': 'DIAGNOSTIC_ONLY', 'data_label': 'RESEARCH_UNCERTIFIED',
              'research_only': True, 'terms_status': 'RESEARCH_ASSUMPTIONS',
              'assumptions_sha256': assumptions_sha(spec['assumptions']),
              'signals_manifest_sha256': workflow.sha256(signals / 'signals_manifest.json')}
    if family == 'd5':
        record['policy_sha256'] = policy_sha()
    write_once(evidence, record)
    items = list(configs(family, prepared, signals, evidence))
    total = len(items)
    summary = {'COMPUTABLE': 0, 'NOT_COMPUTABLE': 0, 'FAILED': 0}
    for index, config in enumerate(items):
        if index < start or (end is not None and index >= end):
            continue
        target = output / f'{index:03d}'
        config = {**config, 'output': str(target)}
        try:
            result = run(config)
            status = result['manifest']['status']
            reason = result['manifest'].get('reason', '')
            summary[status] += 1
            note = ' (checkpoint)' if result.get('reused') else ''
        except Exception as exc:  # ponytail: one bad configuration must not end the batch; the reason is kept on disk
            status, reason, note = 'FAILED', f'{type(exc).__name__}: {exc}', ''
            summary['FAILED'] += 1
            target.mkdir(parents=True, exist_ok=True)
            failure = target / 'failure.json'
            if not failure.exists():
                failure.write_text(json.dumps({'status': 'FAILED', 'label': 'DIAGNOSTIC_ONLY', 'research_only': True,
                                               'config_id': config['config_id'], 'reason': reason,
                                               'traceback': traceback.format_exc()}, ensure_ascii=False, indent=2), encoding='utf-8')
        print(f"{index + 1}/{total} {config['config_id']}: {status}{(' ' + reason) if reason else ''}{note}", flush=True)
    return summary


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--family', choices=sorted(FAMILIES), default='day3')
    parser.add_argument('--output', type=Path)
    parser.add_argument('--prepared', type=Path)
    parser.add_argument('--signals', type=Path)
    parser.add_argument('--start', type=int, default=0, help='first grid index (inclusive) for a parallel chunk')
    parser.add_argument('--end', type=int, help='last grid index (exclusive) for a parallel chunk')
    args = parser.parse_args()
    print(json.dumps(run_family(args.family, args.output, args.prepared, args.signals, args.start, args.end)))
