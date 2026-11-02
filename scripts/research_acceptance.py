"""Run research acceptance checks; missing real evidence remains BLOCKED."""
import argparse
import csv
from datetime import date, datetime, timezone
import json
import math
import os
from pathlib import Path
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts import research_verify_synthetic, research_workflow

# Active changes only: archived ones live in openspec/changes/archive/ and their specs are checked by `openspec validate --specs`.
CHANGES = ()  # all research changes archived 2026-11-02; add a name here when a new change is active
OUTPUT_ROOT = ROOT / 'outputs/research_repair'
SYNTHETIC = OUTPUT_ROOT / 'a191_synthetic_20261102_v10'  # v8 and v9 code hashes predate the final Phase 7 ledger-lock fix
SNAPSHOT_ROOT = ROOT / 'outputs/research_workflow'
RESEARCH_REPORT = ROOT / 'outputs/research_mode/report_v6/research_track.json'
OD_P6_FREEZE = '2026-10-30'  # design decision 11: no TWSE Detail probe after this date; one attempt already recorded that day


def latest_snapshot():
    """Prefer the newest two-track snapshot; fall back to the certification-only snapshot."""
    two_track = sorted(p for p in SNAPSHOT_ROOT.glob('research_mode_*') if (p / 'research_track.json').is_file())
    return two_track[-1] if two_track else SNAPSHOT_ROOT / 'research_status_snapshot'


SNAPSHOT = latest_snapshot()


def od_p6_probe_frozen():
    """Count TWSE Detail attempts on or after the freeze date in the budget ledger; exactly one pre-existing attempt is allowed."""
    from core.runtime import research_budget
    try:
        with research_budget.connection() as conn:
            rows = list(conn.execute('SELECT job, reserved_at, context FROM attempts'))
    except (OSError, ValueError, research_budget.BudgetDenied) as exc:
        return {'status': 'BLOCKED', 'reason': f'BUDGET_EVIDENCE_UNAVAILABLE:{type(exc).__name__}'}
    recent = [row for row in rows if ('od_p6' in str(row[0]) or 'TWT49U_DETAIL' in str(row[2])) and str(row[1])[:10] >= OD_P6_FREEZE]
    return {'status': 'PASS' if len(recent) <= 1 else 'FAIL', 'attempts_since_freeze': len(recent), 'freeze_date': OD_P6_FREEZE}


# The six certification states (core/research/strategy_status.derive) plus the data certificate: none may appear on a research row.
CERTIFICATION_STATES = frozenset(('DATA_BLOCKED', 'VALIDATION_REJECTED', 'RESEARCH_CANDIDATE', 'OOS_ELIGIBLE', 'OOS_PASSED',
                                  'PAPER_ELIGIBLE', 'DATA_CERTIFIED'))


def two_track_snapshot(snapshot):
    """The research track must be present and labelled; the certification track must equal the matrix it was built from."""
    from app.dashboard_payloads import build_research_status_payload
    if not (Path(snapshot) / 'snapshot_manifest.json').is_file():
        return {'status': 'BLOCKED', 'reason': 'SNAPSHOT_UNAVAILABLE', 'snapshot': str(snapshot)}
    try:
        payload = build_research_status_payload(snapshot)
        with (Path(snapshot) / 'strategy_qualification_matrix.csv').open(encoding='utf-8-sig', newline='') as stream:
            matrix = {row['strategy']: row['RESEARCH_STATUS'] for row in csv.DictReader(stream)}
    except (OSError, ValueError, KeyError, TypeError) as exc:
        return {'status': 'FAIL', 'reason': f'{type(exc).__name__}: {exc}', 'snapshot': str(snapshot)}
    track = payload.get('research_track') or {}
    certification = {row['strategy']: row['status'] for row in payload.get('certification_track', {}).get('strategies', [])}
    problems = []
    if payload.get('status') != 'OK' or payload.get('research_only') is not True:
        problems.append('payload not OK/research_only')
    if certification != matrix:
        problems.append('certification track differs from matrix')
    if track.get('label') != 'DIAGNOSTIC_ONLY' or track.get('research_only') is not True or not isinstance(track.get('strategies'), list):
        problems.append('research track missing or unlabelled')
    rows = track.get('strategies', []) + track.get('candidates', []) + [c for row in track.get('strategies', []) for c in row.get('candidates') or []]
    if any(isinstance(row, dict) and row.get('status') in CERTIFICATION_STATES for row in rows):
        problems.append('research track carries a certification status')
    return {'status': 'FAIL' if problems else 'PASS', 'problems': problems, 'snapshot': str(snapshot),
            'research_strategies': len(track.get('strategies', [])), 'research_candidates': len(track.get('candidates', []))}


def research_batch_evidence():
    """Report the executed research batches from the sealed report; absence is BLOCKED, never a silent PASS."""
    try:
        track = json.loads(RESEARCH_REPORT.read_text(encoding='utf-8'))
        batches = track['batches']
        complete = batches['day3']['total'] == 300 and batches['d5_variant']['total'] == 48 and all(
            b['failed_or_interrupted'] == 0 for b in batches.values())
        return {'status': 'PASS' if complete else 'BLOCKED', 'batches': batches, 'research_trials': track.get('research_trials'),
                'candidates': track.get('candidate_status'), 'evidence': str(RESEARCH_REPORT)}
    except (OSError, ValueError, KeyError, TypeError) as exc:
        return {'status': 'BLOCKED', 'reason': f'{type(exc).__name__}: {exc}', 'evidence': str(RESEARCH_REPORT)}
MONITOR = ROOT / 'outputs/research_workflow/research_monitoring.json'
CATEGORIES = {
    'data_pit': ['test/test_research_data_provider.py', 'test/test_research_evidence_ledger.py',
                 'test/test_research_inventory_evidence.py', 'test/test_research_repair_targets.py',
                 'test/test_research_held_event_terms.py', 'test/test_research_od_p6_detail.py',
                 'test/test_research_fetch_holidays.py', 'test/test_research_a191_blockers.py'],
    'accounting': ['test/test_research_repair_accounting.py', 'test/test_research_stale_accounting.py',
                   'test/test_research_rebalance_accounting.py', 'test/test_research_repair_runner.py'],
    'stats_repro_oos': ['test/test_research_trials.py', 'test/test_research_pit_raw_admission.py',
                        'test/test_research_oos_gate.py', 'test/test_research_a191_synthetic.py'],
    'integration': ['test/test_web_research_status.py', 'test/test_strategy_qualification_matrix.py',
                    'test/test_research_snapshot_assembly.py', 'test/test_research_isolation.py',
                    'test/test_line_bot_interactions.py', 'test/test_push_to_line_flex.py', 'test/test_scheduler_e2e.py'],
    'degradation': ['test/test_research_acceptance.py', 'test/test_research_monitoring_snapshot.py'],
}


def monitoring(snapshot, *, threshold=3):
    """Use recorded trading sessions and registered bounds; never infer holidays or unavailable metrics."""
    if not isinstance(threshold, int) or isinstance(threshold, bool) or threshold < 0:
        raise ValueError('INVALID_STALE_THRESHOLD')
    flags, unknown, states = [], [], []
    if snapshot.get('degradation_status') == 'NOT_EVALUABLE' and snapshot.get('degradation_reason') == 'NO_VERIFIED_STRATEGY':
        states.append({'scope': 'degradation', 'status': 'NOT_EVALUABLE', 'reason': 'NO_VERIFIED_STRATEGY'})  # a state, not missing evidence
    elif snapshot.get('degradation_status') == 'NOT_EVALUABLE':  # a verified strategy without thresholds, or no status snapshot
        unknown.append({'scope': 'degradation', 'reason': snapshot.get('degradation_reason') or 'UNSPECIFIED'})
    elif 'rolling_metrics' not in snapshot:
        unknown.append({'scope': 'rolling_metrics', 'reason': 'REGISTERED_METRICS_UNAVAILABLE'})
    for row in snapshot.get('rolling_metrics', []):
        value = row.get('value')
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
            unknown.append({'strategy': row.get('strategy'), 'metric': row.get('metric'), 'reason': 'METRIC_UNAVAILABLE'})
            continue
        lower, upper = row.get('minimum'), row.get('maximum')
        if lower is None and upper is None:
            unknown.append({'strategy': row.get('strategy'), 'metric': row.get('metric'), 'reason': 'THRESHOLD_UNAVAILABLE'})
            continue
        if any(isinstance(x, bool) or not isinstance(x, (int, float)) or not math.isfinite(x) for x in (lower, upper) if x is not None):
            raise ValueError('INVALID_REGISTERED_THRESHOLD')
        if lower is not None and upper is not None and lower > upper:
            raise ValueError('INVALID_REGISTERED_THRESHOLD')
        if (lower is not None and value < lower) or (upper is not None and value > upper):
            flags.append({'flag': 'DEGRADED', 'strategy': row['strategy'], 'metric': row['metric'], 'value': value})
    market = snapshot.get('daily_market', {})
    try:
        latest, as_of = date.fromisoformat(market['latest_official_session']), date.fromisoformat(market['as_of'])
        through = date.fromisoformat(market['calendar_complete_through'])
        sessions = [date.fromisoformat(day) for day in market['official_sessions']]
        if sessions != sorted(set(sessions)) or latest not in sessions or through < as_of or latest > as_of:
            raise ValueError('incomplete trading calendar')
        elapsed = sum(latest < day <= as_of for day in sessions)
        if elapsed > threshold:
            flags.append({'flag': 'STALE', 'scope': 'daily_market', 'sessions': elapsed, 'threshold': threshold})
    except (KeyError, TypeError, ValueError):
        unknown.append({'scope': 'daily_market', 'reason': 'OFFICIAL_CALENDAR_OR_CACHE_DATE_UNAVAILABLE'})
    for row in snapshot.get('source_stops', []):
        flags.append({'flag': 'SOURCE_ANOMALY', 'source': row['source'], 'reason': row.get('reason', 'SOURCE_STOP')})
    return {'flags': flags, 'unknown': unknown, 'states': states}


def budget_anomalies():
    """Read the existing validated budget state; never make a request or repair state."""
    from core.runtime import research_budget
    try:
        with research_budget.connection() as conn:
            stops = [dict(row) for row in conn.execute('SELECT rate_group,until_at,reason FROM cooldowns')]
            attempts = list(conn.execute('SELECT context,status,error FROM attempts WHERE error IS NOT NULL OR status=429'))
        flags = [{'flag': 'SOURCE_ANOMALY', 'source': row['rate_group'], 'reason': row['reason'], 'until': row['until_at']} for row in stops]
        for row in attempts:
            error = str(row['error'] or '')
            if row['status'] == 429 or 'schema' in error.lower():
                context = json.loads(row['context'])
                flags.append({'flag': 'SOURCE_ANOMALY', 'source': context.get('source', 'UNKNOWN'),
                              'reason': 'HTTP_429' if row['status'] == 429 else 'SCHEMA_DRIFT'})
        return {'status': 'PASS', 'flags': flags}
    except (OSError, ValueError, KeyError, TypeError, research_budget.BudgetDenied) as exc:
        return {'status': 'UNKNOWN', 'reason': f'BUDGET_EVIDENCE_UNAVAILABLE:{type(exc).__name__}', 'flags': []}


def _command(name, args, output):
    path = output / f'{name}.log'
    with path.open('x', encoding='utf-8') as stream:
        try:
            process = subprocess.run(args, cwd=ROOT, stdout=stream, stderr=subprocess.STDOUT, check=False)
            code = process.returncode
        except OSError as exc:
            stream.write(f'{type(exc).__name__}: {exc}\n')
            code = -1
    return {'status': 'PASS' if code == 0 else 'FAIL', 'exit_code': code, 'evidence': str(path)}


def assess_goals(checks, monitored, budget, snapshot):
    """Only mark positively evidenced goals PASS; never promote a synthetic run to real evidence."""
    goals = {name: {'status': 'BLOCKED', 'reason': reason} for name, reason in {
        'GOAL_1': 'official event completeness and source certification are unverified',
        'GOAL_2': 'no certified real-data PIT-raw run and reconciliation',
        'GOAL_3': 'strategy validation and OOS eligibility evidence are incomplete',
        'GOAL_4': 'research snapshot unavailable or integration tests incomplete',
        'GOAL_5': 'operational monitoring or acceptance evidence is incomplete',
    }.items()}
    if checks.get('integration', {}).get('status') == 'PASS':
        from app.dashboard_payloads import build_research_status_payload
        payload = build_research_status_payload(snapshot)
        if payload.get('status') == 'OK' and payload.get('research_only') is True and len(payload.get('strategies', [])) == 22:
            goals['GOAL_4'] = {'status': 'PASS', 'reason': 'read-only snapshot and integration checks passed', 'evidence': str(snapshot)}
    failures = [name for name, result in checks.items() if result['status'] == 'FAIL']
    if failures:
        goals['GOAL_5'] = {'status': 'FAIL', 'reason': 'failed checks: ' + ', '.join(failures)}
    elif (not monitored['unknown'] and budget.get('status') == 'PASS'
          and all(checks.get(name, {}).get('status') == 'PASS' for name in
                  (*CATEGORIES, 'full_pytest', 'openspec_strict', 'git_diff_check',
                   'synthetic_manifest_and_reconciliation', 'trial_ledger'))):
        goals['GOAL_5'] = {'status': 'PASS', 'reason': 'acceptance checks and monitoring evidence are available'}
    return goals


def accept(output):
    output = Path(output).resolve()
    if not output.is_relative_to(OUTPUT_ROOT.resolve()) or output == OUTPUT_ROOT.resolve():
        raise ValueError('OUTPUT_OUTSIDE_RESEARCH_DIRECTORY')
    output.mkdir(parents=True, exist_ok=False)
    checks = {}
    progress = output / 'progress.json'
    def record(name, value):
        checks[name] = value
        temporary = output / 'progress.tmp'
        temporary.write_text(json.dumps(checks, indent=2, ensure_ascii=False), encoding='utf-8')
        os.replace(temporary, progress)
        print(f'{name}: {value["status"]}', flush=True)
    for name, paths in CATEGORIES.items():
        record(name, _command(name, [sys.executable, '-m', 'pytest', *paths, '-q'], output))
    record('full_pytest', _command('full_pytest', [sys.executable, '-m', 'pytest', 'test', '-q'], output))
    executable = shutil.which('openspec.cmd') or shutil.which('openspec') or 'openspec'
    for index, change in enumerate(CHANGES):  # the CLI validates one item per call
        record(f'openspec_strict_{index}', _command(f'openspec_strict_{index}', [executable, 'validate', change, '--strict', '--type', 'change'], output))
    record('openspec_strict_specs', _command('openspec_strict_specs', [executable, 'validate', '--specs', '--strict'], output))  # synced main specs
    names = [f'openspec_strict_{i}' for i in range(len(CHANGES))] + ['openspec_strict_specs']
    record('openspec_strict', {'status': 'PASS' if all(checks[n]['status'] == 'PASS' for n in names) else 'FAIL', 'changes': list(CHANGES)})
    record('git_diff_check', _command('git_diff_check', ['git', 'diff', '--check'], output))
    try:
        audit = research_verify_synthetic.verify(SYNTHETIC)
        record('synthetic_manifest_and_reconciliation', {'status': 'PASS', 'tracks': len(audit['tracks']), 'evidence': str(SYNTHETIC)})
    except (OSError, ValueError, KeyError, TypeError) as exc:
        record('synthetic_manifest_and_reconciliation', {'status': 'FAIL', 'reason': str(exc), 'evidence': str(SYNTHETIC)})
    try:
        ledger_status = research_workflow.verify_trials()
        record('trial_ledger', {'status': 'PASS' if ledger_status == 'PASS' else 'FAIL', 'result': ledger_status,
                                'effective_trials': research_workflow.effective_trials() if ledger_status == 'PASS' else None})
    except (OSError, ValueError, KeyError, TypeError) as exc:
        record('trial_ledger', {'status': 'FAIL', 'reason': str(exc)})
    try:
        research_status = research_workflow.verify_trials(research_workflow.RESEARCH_MODE_LEDGER)
        record('research_ledger', {'status': 'PASS' if research_status in ('PASS', 'EMPTY') else 'FAIL', 'result': research_status,
                                   'research_trials': research_workflow.research_trials() if research_status in ('PASS', 'EMPTY') else None,
                                   'formal_trials_unchanged_by_research': research_workflow.effective_trials() if ledger_status == 'PASS' else None})
    except (OSError, ValueError, KeyError, TypeError) as exc:
        record('research_ledger', {'status': 'BLOCKED', 'reason': f'RESEARCH_LEDGER_EVIDENCE_UNAVAILABLE:{type(exc).__name__}: {exc}'})
    record('od_p6_probe_frozen', od_p6_probe_frozen())
    record('two_track_snapshot', two_track_snapshot(SNAPSHOT))
    record('research_batch_evidence', research_batch_evidence())
    try:
        monitored = monitoring(json.loads(MONITOR.read_text(encoding='utf-8')))
    except (OSError, ValueError, TypeError, KeyError):
        monitored = {'flags': [], 'unknown': [{'reason': 'MONITORING_SNAPSHOT_UNAVAILABLE'}], 'states': []}
    source = budget_anomalies()
    failures = [name for name, result in checks.items() if result['status'] == 'FAIL']
    goals = assess_goals(checks, monitored, source, SNAPSHOT)
    overall = 'FAIL' if failures else ('BLOCKED' if any(goal['status'] == 'BLOCKED' for goal in goals.values()) else 'PASS')
    report = {'overall': overall, 'checks': checks, 'goals': goals,
              'monitoring': monitored, 'budget': source, 'snapshot_path': str(SNAPSHOT),
              'note': 'Synthetic passes do not certify real data or a strategy.'}
    with (output / 'acceptance_report.json').open('x', encoding='utf-8') as stream:
        json.dump(report, stream, ensure_ascii=False, indent=2, allow_nan=False)
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', default=str(OUTPUT_ROOT / ('acceptance_' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ'))))
    args = parser.parse_args()
    result = accept(args.output)
    print(json.dumps({'overall': result['overall'], 'goals': result['goals']}, ensure_ascii=False))
    sys.exit({'PASS': 0, 'FAIL': 1, 'BLOCKED': 2}[result['overall']])
