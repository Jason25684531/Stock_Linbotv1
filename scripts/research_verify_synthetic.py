"""Independently recheck hashes and daily accounting of a synthetic A191 batch."""
import argparse
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from core.backtest.costs import CostModel
from core.research.repair_accounting import reconcile
from scripts import research_workflow as workflow


def verify(batch):
    batch = Path(batch).resolve()
    summary = json.loads((batch / 'summary.json').read_text(encoding='utf-8'))
    if summary['label'] != 'SYNTHETIC_ONLY_NOT_A_FORMAL_BACKTEST' or len(summary['configurations']) != 12:
        raise ValueError('SYNTHETIC_BATCH_INVALID')
    ledger = batch / 'synthetic_trial_ledger.jsonl'
    if workflow.verify_trials(ledger) != 'PASS' or workflow.effective_trials(ledger) != 755:
        raise ValueError('SYNTHETIC_LEDGER_INVALID')
    checked = []
    for item in summary['configurations']:
        folder = batch / item['config_id']
        manifest = json.loads((folder / 'manifest.json').read_text(encoding='utf-8'))
        if manifest['data_label'] != 'SYNTHETIC' or manifest['data_end'] > '2025-12-31':
            raise ValueError('NON_SYNTHETIC_OR_OOS_INPUT')
        if manifest.get('terms_status') != 'NOT_APPLICABLE_SYNTHETIC':
            raise ValueError('SYNTHETIC_TERMS_STATUS_INVALID')
        if hashlib.sha256(workflow._canonical(manifest['config']).encode('utf-8')).hexdigest() != manifest['config_sha256']:
            raise ValueError('CONFIG_HASH_MISMATCH')
        if any(workflow.sha256(path) != digest for path, digest in manifest['code_sha256'].items()):
            raise ValueError('CODE_HASH_MISMATCH')
        paths = {name: Path(path).resolve() for name, path in manifest['input_paths'].items()}
        if any(not path.is_relative_to(batch) for path in paths.values()):
            raise ValueError('INPUT_OUTSIDE_SYNTHETIC_BATCH')
        if {name: workflow.sha256(path) for name, path in paths.items()} != manifest['inputs_sha256']:
            raise ValueError('INPUT_HASH_MISMATCH')
        market = json.loads(paths['bars'].read_text(encoding='utf-8'))
        actions = json.loads(paths['actions'].read_text(encoding='utf-8'))
        if market['price_basis'] != 'RAW' or max(market['bars']) > '2025-12-31':
            raise ValueError('NON_DEVELOPMENT_OR_ADJUSTED_BARS')
        for name in ('base', 'stress'):
            data = json.loads((folder / f'{name}.json').read_text(encoding='utf-8'))
            result = data['result']
            cost = CostModel(**result['cost'])
            check = reconcile(manifest['config']['initial_cash'], result, bars=market['bars'], orders=result['generated_orders'],
                              actions=actions['actions'], action_coverage=actions['coverage'], cost=cost,
                              slippage=result['slippage'], cancellations=result['cancellations'])
            if check['status'] != 'PASS':
                raise ValueError(f'RECONCILIATION_FAILED:{item["config_id"]}:{name}:{check}')
            checked.append({'config_id': item['config_id'], 'track': name, 'days': check['days']})
    return {'status': 'PASS', 'scope': 'SYNTHETIC_ONLY', 'configurations': 12, 'tracks': checked,
            'ledger': 'PASS', 'effective_trials_isolated': 755}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('batch')
    args = parser.parse_args()
    audit = verify(args.batch)
    path = Path(args.batch) / 'independent_audit.json'
    with path.open('x', encoding='utf-8') as stream:
        json.dump(audit, stream, indent=2, allow_nan=False)
    print(json.dumps({'status': audit['status'], 'scope': audit['scope'], 'tracks': len(audit['tracks'])}))
