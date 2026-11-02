"""List the fixed A191 configurations and existing blockers without running a trial."""
import argparse
import csv
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from collections import Counter

from core.research.evidence_ledger import read
from core.research.repair_runner import held_event_terms, verified_sources
from scripts import research_workflow as workflow

MANIFEST = ROOT / 'outputs/strategy_research/strategy_search_20260914_v2/strategy_search_manifest.csv'
COVERAGE = ROOT / 'outputs/research_repair/phase2_20261030/coverage_summary.json'
PINNED = '0db6a8a98906dc9312ce1ba8fc2378c9c6e6e51475a03fbe6148e9eb1ae31b63'
OUTPUT_ROOT = ROOT / 'outputs/research_repair'
EVIDENCE = ROOT / 'outputs/research_repair/autonomous_evidence_inventory_20261030/evidence.jsonl'
STOCKS = ROOT / 'outputs/research_repair/phase2_20261030/a191_stock_coverage.csv'


def report(output):
    output = Path(output).resolve()
    if not output.is_relative_to(OUTPUT_ROOT.resolve()) or output == OUTPUT_ROOT.resolve():
        raise ValueError('OUTPUT_OUTSIDE_RESEARCH_DIRECTORY')
    raw = MANIFEST.read_bytes()
    if hashlib.sha256(raw).hexdigest() != PINNED:
        raise ValueError('ARCHIVED_MANIFEST_HASH_MISMATCH')
    with MANIFEST.open(encoding='utf-8-sig', newline='') as stream:
        configs = [row for row in csv.DictReader(stream) if row['factor_set'] == 'A191_132']
    if len(configs) != 12 or len({row['strategy_id'] for row in configs}) != 12:
        raise ValueError('EXPECTED_12_ARCHIVED_CONFIGURATIONS')
    if workflow.verify_registry() != 'PASS' or workflow.verify_trials() != 'PASS':
        raise ValueError('REGISTRY_OR_TRIAL_LEDGER_UNVERIFIED')
    coverage = json.loads(COVERAGE.read_text(encoding='utf-8'))
    # Dry run: data admission only (no targets, prices, simulation or OOS); every observed stock bounds the holdings.
    with STOCKS.open(encoding='utf-8-sig', newline='') as stream:
        stocks = {row['stock_id'] for row in csv.DictReader(stream)}
    rows = read(EVIDENCE)
    dry = held_event_terms(rows, stocks, {(r['stock_id'], r['event_date']) for r in rows
                                          if r['field'] == 'ex_date' and r['stock_id'] in stocks and r['event_date'] <= '2025-12-31'},
                           verified_sources())
    field_counts = Counter(f'{field}:{reason}' for e in dry['events'] for field, reason in e['blocked_fields'].items())
    trials = [row for row in workflow._trial_rows() if row['hypothesis_id'] == workflow.PIT_RAW_HYPOTHESIS and row['kind'] == 'TRIAL']
    result = {'identity': workflow.PIT_RAW_HYPOTHESIS, 'label': 'DIAGNOSTIC_ONLY', 'verdict': 'BLOCKED_NOT_A_BACKTEST',
              'archived_manifest_sha256': PINNED, 'coverage_source_sha256': hashlib.sha256(COVERAGE.read_bytes()).hexdigest(),
              'observed_events': coverage['observed_events'], 'event_denominator': None,
              'verified_coverage': coverage['verified_coverage_ratio'], 'observed_events_are_not_held_through_proof': True,
              'registered_new_trials': len(trials), 'effective_trials': workflow.effective_trials(),
              'evidence_sha256': hashlib.sha256(EVIDENCE.read_bytes()).hexdigest(), 'dry_run_status': dry['status'],
              'dry_run': {k: v for k, v in dry.items() if k != 'events'}, 'blocked_field_counts': dict(sorted(field_counts.items())),
              # The observed-stock list is not a proven superset of PIT-raw holdings: it can prove BLOCKED, never admission.
              'holding_bound': 'observed A191 stocks (archived targets/orders), not a proven superset',
              'admission': 'NOT_ADMITTED' if dry['status'] != 'VERIFIED' else 'DECIDED_ONLY_BY_RUNNER_DRY_RUN',
              'reason': 'Dry run blocked: no stock has a certified complete event list and no observed event has all required official terms; '
                        'no trial was registered and no performance is asserted.' if dry['status'] != 'VERIFIED' else 'Dry run verified.',
              'configurations': [{'config': row, 'status': 'BLOCKED_UNVERIFIED_OFFICIAL_TERMS',
                                  'formal_total_return': {'value': None, 'reason': 'no certified run'}} for row in configs]}
    output.mkdir(parents=True, exist_ok=False)
    with (output / 'blockers.json').open('x', encoding='utf-8') as stream:
        json.dump(result, stream, ensure_ascii=False, indent=2, allow_nan=False)
    with (output / 'held_event_terms.json').open('x', encoding='utf-8') as stream:  # per stock, event and field
        json.dump(dry, stream, ensure_ascii=False, indent=1, allow_nan=False)
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    result = report(args.output)
    print(json.dumps({'status': result['label'], 'dry_run': result['dry_run_status'], 'admission': result['admission'],
                      'configurations': len(result['configurations']), 'effective_trials': result['effective_trials'],
                      'blocked_field_counts': result['blocked_field_counts']}))
