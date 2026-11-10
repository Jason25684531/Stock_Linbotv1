"""Checkpoint, backup and rollback for change 2026-11-09-autonomous-data-readiness-v1 (design D1).

outputs/autonomous_data_readiness/
  baseline.json        workspace, ledgers, effective_trials(), sealed H4 hashes, Run Store pointers, update state
  backup/<rel path>    pre-round content of every existing file the round edits (first copy wins, never overwritten)
  backup.json          {rel: sha256 of the pre-round content, or None when the round created the file}
  task_state.json      {'tasks': {id: {...}}, 'files': {rel: sha256 recorded when its task completed}}
  decision_log.md, blockers.json, recovery_log.jsonl

Rollback restores the backed-up pre-round content (never git HEAD). A file whose current hash is neither the recorded nor the backed-up
hash was changed by someone else: it is reported CONFLICT with a diff and left alone.

CLI: python scripts/data_readiness_checkpoint.py baseline | verify | conflicts | rollback
"""
from __future__ import annotations

import difflib
import hashlib
import json
import os
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'outputs/autonomous_data_readiness'
H4_TREES = ('prepared_v6', 'h4_signals_v1', 'h4_runs_v1', 'report_h4_v2', 'h4_attribution_v1', 'h4_web_v1')
STATUSES = ('PENDING', 'RUNNING', 'COMPLETED', 'PARTIAL', 'BLOCKED', 'FAILED')


def _out(out=None) -> Path:
    """Explicit root, else STOCK_LINBOT_CHECKPOINT_ROOT, else the default round folder."""
    return Path(out or os.environ.get('STOCK_LINBOT_CHECKPOINT_ROOT') or OUT)


def _now():
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


def sha256(path) -> str | None:
    path = Path(path)
    if not path.is_file():
        return None
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def _rel(path) -> str:
    path = Path(path)
    return (path.resolve().relative_to(ROOT) if path.is_absolute() else path).as_posix()


def _read(name, default, out=None):
    path = _out(out) / name
    return json.loads(path.read_text(encoding='utf-8')) if path.is_file() else default


def _write(name, value, out=None):
    path = _out(out) / name
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + '.tmp')
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=1, default=str), encoding='utf-8')
    os.replace(tmp, path)


def log(kind, text, out=None, **fields):
    """kind 'decision' -> decision_log.md, 'recovery' -> recovery_log.jsonl, 'blocker' -> blockers.json."""
    out = _out(out)
    out.mkdir(parents=True, exist_ok=True)
    if kind == 'decision':
        with (out / 'decision_log.md').open('a', encoding='utf-8') as stream:
            stream.write(f'\n## {_now()} {text}\n\n' + ''.join(f'- **{k}**: {v}\n' for k, v in fields.items()))
    elif kind == 'recovery':
        with (out / 'recovery_log.jsonl').open('a', encoding='utf-8') as stream:
            stream.write(json.dumps({'at': _now(), 'event': text, **fields}, ensure_ascii=False, default=str) + '\n')
    elif kind == 'blocker':
        rows = [r for r in _read('blockers.json', [], out) if r['id'] != text]
        _write('blockers.json', rows + [{'id': text, 'at': _now(), **fields}], out)
    else:
        raise ValueError(f'UNKNOWN_LOG_KIND: {kind}')


def backup(paths, root=ROOT, out=None):
    """Copy the current content of each file once; a file that does not exist yet is recorded as created by the round."""
    out = _out(out)
    manifest = _read('backup.json', {}, out)
    for path in paths:
        rel = Path(path).resolve().relative_to(Path(root).resolve()).as_posix() if Path(path).is_absolute() else Path(path).as_posix()
        if rel in manifest:
            continue  # first copy wins: it is the pre-round content
        source = Path(root) / rel
        if source.is_file():
            target = out / 'backup' / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
            if sha256(target) != sha256(source):
                raise OSError(f'BACKUP_VERIFY_FAILED: {rel}')
        manifest[rel] = sha256(source)
        _write('backup.json', manifest, out)
    return manifest


def set_task(task_id, status, *, files=(), root=ROOT, out=None, **fields):
    """Record a task atomically; files are hashed now and become the expected content for conflicts() and rollback()."""
    if status not in STATUSES:
        raise ValueError(f'UNKNOWN_STATUS: {status}')
    state = _read('task_state.json', {'tasks': {}, 'files': {}}, out)
    task = state['tasks'].setdefault(task_id, {'task_id': task_id, 'started_at': _now()})
    hashes = {Path(f).as_posix(): sha256(Path(root) / f) for f in files}
    task.update(fields, status=status, updated_at=_now(), files={**task.get('files', {}), **hashes})
    if status != 'RUNNING':
        task['ended_at'] = _now()
    state['files'].update(hashes)
    _write('task_state.json', state, out)
    return task


def verify(root=ROOT, out=None):
    """COMPLETED tasks whose recorded files changed return to PENDING (logged). Returns the reset task IDs."""
    state = _read('task_state.json', {'tasks': {}, 'files': {}}, out)
    reset = []
    for task in state['tasks'].values():
        changed = [f for f, digest in task.get('files', {}).items() if sha256(Path(root) / f) != digest]
        if task['status'] == 'COMPLETED' and changed:
            task['status'] = 'PENDING'
            reset.append(task['task_id'])
            log('recovery', 'TASK_RESET_HASH_MISMATCH', out, task_id=task['task_id'], files=changed)
    if reset:
        _write('task_state.json', state, out)
    return reset


def conflicts(root=ROOT, out=None):
    """Files changed by someone else since the round recorded them: {rel: unified diff against the recorded expectation's backup}."""
    manifest = _read('backup.json', {}, out)
    expected = _read('task_state.json', {'files': {}}, out)['files']
    found = {}
    for rel, original in manifest.items():
        current = sha256(Path(root) / rel)
        if current in (original, expected.get(rel, original)):
            continue
        before = (_out(out) / 'backup' / rel)
        a = before.read_text(encoding='utf-8', errors='replace').splitlines() if before.is_file() else []
        b = (Path(root) / rel).read_text(encoding='utf-8', errors='replace').splitlines() if (Path(root) / rel).is_file() else []
        found[rel] = '\n'.join(difflib.unified_diff(a, b, 'pre-round', 'current', lineterm=''))[:20000]
    return found


def rollback(root=ROOT, out=None):
    """Restore pre-round content; skip conflicts. Returns {'restored': [...], 'deleted': [...], 'conflicts': [...]}."""
    out = _out(out)
    manifest = _read('backup.json', {}, out)
    clash = conflicts(root, out)
    result = {'restored': [], 'deleted': [], 'conflicts': sorted(clash)}
    for rel, original in manifest.items():
        target = Path(root) / rel
        if rel in clash or sha256(target) == original:
            continue
        if original is None:
            target.unlink(missing_ok=True)
            result['deleted'].append(rel)
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(out / 'backup' / rel, target)
            result['restored'].append(rel)
    log('recovery', 'ROLLBACK', out, **result)
    return result


def _tree_hashes(directory: Path) -> dict:
    return {p.relative_to(directory).as_posix(): sha256(p) for p in sorted(directory.rglob('*')) if p.is_file()}


def baseline(out=None):
    """Write baseline.json once (an existing baseline is the round's anchor and is never rewritten)."""
    out = _out(out)
    if (out / 'baseline.json').is_file():
        return _read('baseline.json', {}, out)
    sys.path.insert(0, str(ROOT))
    from scripts import research_workflow as rw
    research = ROOT / 'outputs/research_mode'
    files = {_rel(p): sha256(p) for p in (rw.REGISTRY, rw.REGISTRY.with_suffix('.head.json'), rw.results_path(rw.REGISTRY), rw.TRIAL_LEDGER,
                                          rw.TRIAL_LEDGER.with_suffix('.head.json'), rw.CLOSURES)}
    runs = ROOT / 'outputs/backtest_runs'
    value = {'created_at': _now(), 'git_status': subprocess.run(['git', 'status', '--short'], cwd=ROOT, capture_output=True, text=True).stdout,
             'git_head': subprocess.run(['git', 'rev-parse', 'HEAD'], cwd=ROOT, capture_output=True, text=True).stdout.strip(),
             'effective_trials': rw.effective_trials(), 'trial_ledger_verify': rw.verify_trials(), 'registry_verify': rw.verify_registry(),
             'ledger_files': files, 'h4_sha256': {tree: _tree_hashes(research / tree) for tree in H4_TREES},
             'run_store': {'runs': sorted(p.name for p in runs.iterdir() if p.is_dir() and p.name[0].isdigit()),
                           'pointers': {_rel(p): sha256(p) for p in sorted((runs / 'latest').rglob('*.json'))}},
             'market_data_updates': sorted(p.name for p in (ROOT / 'outputs/market_data_updates').glob('*')),
             'grant_file_trusted': __import__('scripts.task_controller', fromlist=['grant_trusted']).grant_trusted()}
    _write('baseline.json', value, out)
    return value


def compare_baseline(out=None):
    """Final reconciliation: ledgers, effective_trials() and every sealed H4 hash against baseline.json. Returns a list of differences."""
    sys.path.insert(0, str(ROOT))
    from scripts import research_workflow as rw
    base = _read('baseline.json', None, out)
    if base is None:
        return ['BASELINE_MISSING']
    diffs = [f'{rel}: {digest} -> {sha256(ROOT / rel)}' for rel, digest in base['ledger_files'].items() if sha256(ROOT / rel) != digest]
    if rw.effective_trials() != base['effective_trials']:
        diffs.append(f"effective_trials: {base['effective_trials']} -> {rw.effective_trials()}")
    for tree, files in base['h4_sha256'].items():
        if _tree_hashes(ROOT / 'outputs/research_mode' / tree) != files:
            diffs.append(f'H4 tree changed: {tree}')
    return diffs


def main(argv=None):
    args = list(argv if argv is not None else sys.argv[1:]) or ['verify']
    out = None
    if '--out' in args:
        i = args.index('--out')
        out, args = args[i + 1], args[:i] + args[i + 2:]
    command, rest = args[0], args[1:]
    actions = {'baseline': lambda: {k: v for k, v in baseline(out).items() if k in ('created_at', 'effective_trials', 'trial_ledger_verify')},
               'verify': lambda: verify(ROOT, out), 'conflicts': lambda: sorted(conflicts(ROOT, out)), 'rollback': lambda: rollback(ROOT, out),
               'compare': lambda: compare_baseline(out), 'backup': lambda: backup(rest, root=ROOT, out=out)}
    if command not in actions:
        raise SystemExit(f'usage: {Path(__file__).name} ' + '|'.join(actions) + ' [paths] [--out DIR]')
    print(json.dumps(actions[command](), ensure_ascii=False, indent=1, default=str))


if __name__ == '__main__':
    main()
