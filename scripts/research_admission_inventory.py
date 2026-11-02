"""Archive-readiness check of the previous change and an authorization-gap inventory; reads only, writes two JSON files."""
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from core.research.evidence_ledger import read
from core.runtime import public_data, research_budget

PREVIOUS = '2026-10-30-autonomous-quant-research-system'
EVIDENCE = ROOT / 'outputs/research_repair/autonomous_evidence_inventory_20261030/evidence.jsonl'
OUT = ROOT / 'outputs/research_repair'
DEV_END = '20251231'
ACCEPTANCE = ROOT / 'outputs/research_repair/acceptance_final_20261030_r4/acceptance_report.json'
STATUS = ROOT / 'outputs/research_workflow/research_status_snapshot/research_status.json'


def readiness():
    tasks = (ROOT / f'openspec/changes/{PREVIOUS}/tasks.md').read_text(encoding='utf-8')
    unchecked = re.findall(r'^- \[ \] (.+)$', tasks, re.M)
    # Only completion evidence counts; phase headers list planned names and <ts> templates.
    evidence = '\n'.join(line for line in tasks.splitlines() if re.match(r'\s+- (Evidence|Verification):', line))
    cited = sorted(set(re.findall(r'`((?:outputs|docs|core|scripts|app|test)/[^`\s]+)`', evidence)))
    missing = [path for path in cited if not (ROOT / path.rstrip('/')).exists()]
    executable = shutil.which('openspec.cmd') or shutil.which('openspec') or 'openspec'
    check = subprocess.run([executable, 'validate', PREVIOUS, '--strict', '--type', 'change'],
                           cwd=ROOT, capture_output=True, text=True)
    ready = check.returncode == 0 and not unchecked and not missing
    return {'change': PREVIOUS, 'strict_validation': 'PASS' if check.returncode == 0 else 'FAIL',
            'unchecked_tasks': unchecked, 'cited_paths': len(cited), 'missing_paths': missing,
            'engineering_archive_ready': ready, 'archived': False,
            'research_acceptance': {goal: row['status'] for goal, row in json.loads(ACCEPTANCE.read_text(encoding='utf-8'))['goals'].items()},
            'research_acceptance_source': str(ACCEPTANCE.relative_to(ROOT)),
            'note': 'Engineering archive readiness is not research acceptance; archiving is left to the Owner.'}


def gap():
    rows = [r for r in read(EVIDENCE) if r['field'] not in ('close_price', 'volume')]
    fields = Counter((r['field'], r['tier'], r['value_or_UNKNOWN'] == 'UNKNOWN') for r in rows)
    events = sorted({(r['stock_id'], r['event_date'].replace('-', '')) for r in rows if r['field'] == 'ex_date'})
    scope = {tuple(t) for t in public_data._scope()['od_p6_targets']}
    dev_scope = {t for t in scope if t[1] <= DEV_END}
    in_scope = [e for e in events if e in dev_scope]
    return {'budget': research_budget.status(),
            'od_p6_scope': {'registered': len(scope), 'development_window': len(dev_scope),
                            'after_2025': len(scope) - len(dev_scope),
                            'development_window_stocks': len({s for s, _ in dev_scope})},
            'a191_observed_events': len(events),
            'a191_events_in_od_p6_development_scope': len(in_scope),
            'a191_events_outside_od_p6_scope': len(events) - len(in_scope),
            'outside_scope_reason': 'TWT49U already splits these events; Detail is registered only for mixed and rights events. '
                                    'Their payment and credit dates still have no approved source.',
            'field_tiers': [{'field': f, 'tier': t, 'unknown': u, 'count': n} for (f, t, u), n in sorted(fields.items())],
            'certifying_sources': 'outputs/research_repair/owner_approval_requests.json (releases per source)',
            'evidence_sha256': hashlib.sha256(EVIDENCE.read_bytes()).hexdigest()}


def oos_admission():
    """Read-only OOS admission check: candidates from the status snapshot, OOS rows from the verified trial ledger."""
    from scripts import research_workflow as workflow
    if workflow.verify_trials() != 'PASS':
        raise ValueError('TRIAL_LEDGER_UNVERIFIED')
    strategies = json.loads(STATUS.read_text(encoding='utf-8'))['strategies']
    oos_rows = [r for r in workflow._trial_rows() if r['kind'].startswith('OOS_')]
    candidates = [s['strategy'] for s in strategies if s['status'] in ('RESEARCH_CANDIDATE', 'OOS_ELIGIBLE')]
    return {'strategies': len(strategies), 'status_counts': dict(Counter(s['status'] for s in strategies)),
            'oos_admission_candidates': candidates, 'oos_ledger_rows': len(oos_rows), 'effective_trials': workflow.effective_trials(),
            'freeze_performed': any(r['kind'] == 'OOS_FREEZE' for r in oos_rows), 'oos_evaluation_performed': any(r['kind'] == 'OOS_EVALUATION' for r in oos_rows),
            'status_snapshot_sha256': hashlib.sha256(STATUS.read_bytes()).hexdigest(),
            'note': 'This check reads no OOS data; an empty candidate list means nothing may be sealed or evaluated.'}


def main(stamp):
    results = {f'archive_readiness_{stamp}.json': readiness(), f'authorization_gap_{stamp}.json': gap(),
               f'oos_admission_{stamp}.json': oos_admission()}
    for name, data in results.items():
        with (OUT / name).open('x', encoding='utf-8') as stream:
            json.dump(data, stream, ensure_ascii=False, indent=2, default=str)
    return results


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--stamp', default='20261030')
    for name, data in main(parser.parse_args().stamp).items():
        print(name, json.dumps({k: v for k, v in data.items() if k not in ('field_tiers', 'budget')}, ensure_ascii=False))
