"""Run the twelve pinned A191 parameter rows against an isolated synthetic fixture."""
import argparse
import csv
from datetime import date, timedelta
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from core.research import repair_accounting, repair_runner
from scripts import research_workflow as workflow
from scripts.research_a191_blockers import MANIFEST, PINNED


def run(output):
    output = Path(output).resolve()
    if not output.is_relative_to(repair_runner.OUTPUT_ROOT.resolve()) or output == repair_runner.OUTPUT_ROOT.resolve():
        raise ValueError('OUTPUT_OUTSIDE_RESEARCH_DIRECTORY')
    raw = MANIFEST.read_bytes()
    if hashlib.sha256(raw).hexdigest() != PINNED:
        raise ValueError('ARCHIVED_MANIFEST_HASH_MISMATCH')
    with MANIFEST.open(encoding='utf-8-sig', newline='') as stream:
        configs = [row for row in csv.DictReader(stream) if row['factor_set'] == 'A191_132']
    if len(configs) != 12 or len({row['strategy_id'] for row in configs}) != 12:
        raise ValueError('EXPECTED_12_ARCHIVED_CONFIGURATIONS')
    def ledger_hashes():
        return {str(path): workflow.sha256(path) if path.exists() else None
                for path in (workflow.TRIAL_LEDGER, workflow._trial_head(workflow.TRIAL_LEDGER))}
    real_before = ledger_hashes()
    output.mkdir(parents=True, exist_ok=False)
    days, current = [], date(2025, 1, 2)
    while len(days) < 90:
        if current.weekday() < 5:
            days.append(current.isoformat())
        current += timedelta(days=1)
    stocks = [f'S{i:03d}' for i in range(35)]
    bars = {}
    for index, day in enumerate(days):
        bars[day] = {}
        for i, stock in enumerate(stocks):
            close = round(20 + i / 10 + index / 100, 2)
            volume = 100000 + (i * 1000) + (index % 7) * 100
            amount = round(close * volume, 2)
            prior = bars[days[index - 1]][stock]['amount'] if index else amount
            bars[day][stock] = {'open': close, 'high': close + .5, 'low': close - .5, 'close': close,
                                'volume': volume, 'amount': amount, 'prior_amount': prior,
                                'limit_up': close + 2, 'limit_down': close - 2,
                                'close_available_at': day + 'T13:30:00+08:00'}
    data = {'bars': {'price_basis': 'RAW', 'source': 'synthetic', 'bars': bars},
            'actions': {'actions': [], 'coverage': {stock: days for stock in stocks}},
            'evidence': {'label': 'SYNTHETIC_FIXTURE', 'official_certification': False}}
    paths = {}
    for name, value in data.items():
        path = output / f'{name}.json'
        with path.open('x', encoding='utf-8') as stream:
            json.dump(value, stream, allow_nan=False)
        paths[name] = str(path)
    generator_sha = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    bars_sha = hashlib.sha256(Path(paths['bars']).read_bytes()).hexdigest()
    schedules = {}
    for period in (20, 60):
        scores, universe = [], []
        for index in range(20, len(days) - 1, period):
            day = days[index]
            for stock in stocks:
                score = sum(bars[days[j]][stock]['amount'] for j in range(index - 19, index + 1)) / 20
                scores.append({'asof_date': day, 'asset_id': stock, 'composite_score': score,
                               'signal_at': day + 'T15:00:00+08:00', 'score_available_at': day + 'T14:00:00+08:00',
                               'score_source_sha256': bars_sha})
                universe.append({'asof_date': day, 'asset_id': stock, 'member': True, 'execution_date': days[index + 1],
                                 'membership_available_at': day + 'T14:00:00+08:00', 'membership_source_sha256': generator_sha,
                                 'calendar_available_at': day + 'T14:00:00+08:00', 'calendar_source_sha256': generator_sha})
        schedules[period] = {}
        for name, value in (('scores', scores), ('universe', universe)):
            path = output / f'{name}_{period}.json'
            with path.open('x', encoding='utf-8') as stream:
                json.dump(value, stream, allow_nan=False)
            schedules[period][name] = str(path)
    registry, ledger = output / 'synthetic_registry.csv', output / 'synthetic_trial_ledger.jsonl'
    workflow.register(repair_accounting.registration(), path=registry)
    results = []
    for row in configs:
        period = int(row['rebalance_days'])
        config = {'config_id': row['strategy_id'], 'factor_set': row['factor_set'], 'factor_weighting': row['factor_weighting'],
                  'top_n': int(row['top_n']), 'rebalance_days': period, 'portfolio_weighting': row['portfolio_weighting'],
                  'stock_weighting': row['portfolio_weighting'], 'config_hash': row['config_hash'],
                  'initial_cash': 1_000_000, 'fee_rate': .001425, 'tax_rate': .003, 'minimum_fee': 20,
                  'slippage': .001, 'data_label': 'SYNTHETIC', 'data_end': days[-1],
                  'inputs': {**paths, **schedules[period]}, 'output': str(output / row['strategy_id'])}
        result = repair_runner.run(config, registry=registry, ledger=ledger)
        results.append({'config_id': row['strategy_id'], 'experiment_id': result['manifest']['experiment_id'],
                        'reconciliation': {name: track['reconciliation'] for name, track in result['tracks'].items()}})
        print(f"synthetic {len(results)}/12: {row['strategy_id']}", flush=True)
    real_after = ledger_hashes()
    summary = {'label': 'SYNTHETIC_ONLY_NOT_A_FORMAL_BACKTEST', 'manifest_sha256': PINNED,
               'configurations': results, 'synthetic_ledger_status': workflow.verify_trials(ledger),
               'synthetic_effective_trials': workflow.effective_trials(ledger),
               'actual_ledger_hashes_before': real_before, 'actual_ledger_hashes_after': real_after,
               'actual_ledger_unchanged_observed': real_before == real_after,
               'reason': 'Synthetic fixture and isolated registry/ledger; official data and held-event terms are not certified.'}
    with (output / 'summary.json').open('x', encoding='utf-8') as stream:
        json.dump(summary, stream, ensure_ascii=False, indent=2, allow_nan=False)
    return summary


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    result = run(args.output)
    print(json.dumps({'label': result['label'], 'configurations': len(result['configurations']),
                      'synthetic_effective_trials': result['synthetic_effective_trials']}))
