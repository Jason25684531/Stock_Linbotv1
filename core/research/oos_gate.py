"""Frozen, one-attempt OOS gate; no OOS is read at import."""
import csv
from datetime import datetime, timezone
import hashlib
from importlib import import_module
import json
import os
from pathlib import Path
import re

from core.runtime import operation_policy
from scripts import research_workflow as workflow

REQUIRED_CODE = workflow.PIT_RAW_CODE
REQUIRED_INPUTS = workflow.PIT_RAW_INPUTS


def _digest(value):
    return hashlib.sha256(workflow._canonical(value).encode('utf-8')).hexdigest()


def _rows(ledger):
    if workflow.verify_trials(ledger) != 'PASS':
        raise ValueError('TRIAL_LEDGER_UNVERIFIED')
    return workflow._trial_rows(ledger)


def _append(ledger, rows, kind, frozen, outcome, reason=None):
    body = {'kind': kind, 'experiment_id': frozen['experiment_id'], 'hypothesis_id': frozen['hypothesis_id'],
            'freeze_hash': frozen['freeze_hash'], 'outcome': outcome, 'counts_toward_dsr': False,
            'at': datetime.now(timezone.utc).isoformat(), 'prev': rows[-1]['sha256'] if rows else ''}
    if reason is not None:
        body['reason'] = reason
    return workflow._append_trial_locked(ledger, body, rows)


def _require_oos_eligibility():
    # ponytail: fail closed until a verifier binds per-strategy certification and g1-g5 evidence to the admitted identity.
    raise PermissionError('OOS_QUALIFICATION_EVIDENCE_UNVERIFIED')


def freeze(strategy, experiment_id, input_paths, oos_sha256, *, directory, ledger=workflow.TRIAL_LEDGER, registry=workflow.REGISTRY):
    """Freeze an admitted trial and a pre-existing OOS digest without opening OOS data.

    The ledger reservation precedes file creation. A crash consumes this trial's freeze;
    recovery must inspect evidence instead of silently creating a replacement identity.
    """
    if operation_policy.current_mode() is not operation_policy.ExecutionMode.RESEARCH:
        raise PermissionError('RESEARCH_MODE_REQUIRED')
    _require_oos_eligibility()
    if not isinstance(oos_sha256, str) or not re.fullmatch(r'[0-9a-f]{64}', oos_sha256):
        raise ValueError('OOS_HASH_REQUIRED')
    if workflow.verify_registry(registry) != 'PASS':
        raise ValueError('REGISTRY_UNVERIFIED')
    with workflow._ledger_lock(ledger):
        rows = _rows(ledger)
        matches = [r for r in rows if r['kind'] == 'TRIAL' and r['experiment_id'] == experiment_id]
        if len(matches) != 1:
            raise ValueError('ADMITTED_TRIAL_REQUIRED')
        trial = matches[0]
        with Path(registry).open(encoding='utf-8-sig', newline='') as stream:
            registered = [r for r in csv.DictReader(stream) if r['hypothesis_id'] == trial['hypothesis_id']]
        if len(registered) != 1 or registered[0]['strategy'] != strategy:
            raise ValueError('REGISTERED_STRATEGY_MISMATCH')
        if any(r['kind'] == 'OOS_FREEZE' and r['hypothesis_id'] == trial['hypothesis_id'] for r in rows):
            raise ValueError('HYPOTHESIS_ALREADY_FROZEN: a changed identity needs a new registration')
        code = trial['code_sha256']
        if not code or not trial['inputs_sha256'] or set(input_paths) != set(trial['inputs_sha256']):
            raise ValueError('INCOMPLETE_FREEZE_IDENTITY')
        if not REQUIRED_INPUTS <= set(input_paths):
            raise ValueError('INCOMPLETE_FREEZE_INPUTS')
        if not {p.resolve() for p in REQUIRED_CODE} <= {Path(p).resolve() for p in code}:
            raise ValueError('INCOMPLETE_FREEZE_CODE')
        paths = {name: str(Path(path).resolve()) for name, path in input_paths.items()}
        if ({name: workflow._tree_sha(path) for name, path in paths.items()} != trial['inputs_sha256']
                or {path: workflow.sha256(path) for path in code} != code):
            raise ValueError('TRIAL_HASH_DRIFT')
        identity = {key: trial[key] for key in ('experiment_id', 'hypothesis_id', 'data_end', 'config_sha256', 'code_sha256', 'inputs_sha256')}
        identity.update(strategy=strategy, oos_sha256=oos_sha256, oos_start='2026-01-01')
        frozen = {**identity, 'freeze_hash': _digest(identity), 'input_paths': paths,
                  'frozen_at': datetime.now(timezone.utc).isoformat()}
        _append(ledger, rows, 'OOS_FREEZE', frozen, 'RESERVED')
        path = Path(directory) / (frozen['freeze_hash'] + '.json')
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open('x', encoding='utf-8') as stream:
            json.dump(frozen, stream, ensure_ascii=False, sort_keys=True)
            stream.flush()
            os.fsync(stream.fileno())
    return path


def evaluate(freeze_path, config, oos_path, *, ledger=workflow.TRIAL_LEDGER):
    """Reserve the unique attempt before any OOS read; failed or interrupted attempts stay spent."""
    if operation_policy.current_mode() is not operation_policy.ExecutionMode.RESEARCH:
        raise PermissionError('RESEARCH_MODE_REQUIRED')
    _require_oos_eligibility()
    frozen = json.loads(Path(freeze_path).read_text(encoding='utf-8'))
    keys = ('experiment_id', 'hypothesis_id', 'data_end', 'config_sha256', 'code_sha256', 'inputs_sha256',
            'strategy', 'oos_sha256', 'oos_start')
    identity = {key: frozen[key] for key in keys}
    if _digest(identity) != frozen['freeze_hash'] or frozen['oos_start'] != '2026-01-01':
        raise ValueError('FREEZE_INVALID')
    with workflow._ledger_lock(ledger):
        rows = _rows(ledger)
        reservations = [r for r in rows if r['kind'] == 'OOS_FREEZE' and r.get('freeze_hash') == frozen['freeze_hash']]
        if len(reservations) != 1 or reservations[0]['experiment_id'] != frozen['experiment_id']:
            raise ValueError('FREEZE_NOT_REGISTERED')
        if any(r['kind'] == 'OOS_EVALUATION' and r.get('freeze_hash') == frozen['freeze_hash'] for r in rows):
            raise ValueError('OOS_ALREADY_ATTEMPTED')
        _append(ledger, rows, 'OOS_EVALUATION', frozen, 'ATTEMPT_STARTED')
    try:
        if _digest(config) != frozen['config_sha256']:
            raise ValueError('CONFIG_HASH_DRIFT')
        if {path: workflow.sha256(path) for path in frozen['code_sha256']} != frozen['code_sha256']:
            raise ValueError('CODE_HASH_DRIFT')
        if ({name: workflow._tree_sha(path) for name, path in frozen['input_paths'].items()}
                != frozen['inputs_sha256']):
            raise ValueError('INPUT_HASH_DRIFT')
        evaluator = getattr(import_module('core.research.repair_runner'), 'evaluate_oos', None)
        if not callable(evaluator):
            raise ValueError('OOS_EVALUATOR_UNAVAILABLE')
        raw = Path(oos_path).read_bytes()  # first OOS read, strictly after durable reservation
        if hashlib.sha256(raw).hexdigest() != frozen['oos_sha256']:
            raise ValueError('OOS_HASH_DRIFT')
        # The fixed evaluator is part of REQUIRED_CODE; caller-supplied code cannot bypass the freeze.
        outcome = evaluator(raw, config)
        if outcome not in ('PASS', 'FAIL'):
            raise ValueError('INVALID_OOS_OUTCOME')
    except BaseException as exc:
        # Drift is checked after the reservation on purpose: a failed validation also spends the single attempt.
        with workflow._ledger_lock(ledger):
            _append(ledger, _rows(ledger), 'OOS_RESULT', frozen, 'FAILED_OR_INTERRUPTED', f'{type(exc).__name__}: {exc}'[:200])
        raise
    with workflow._ledger_lock(ledger):
        _append(ledger, _rows(ledger), 'OOS_RESULT', frozen, outcome)
    return outcome
