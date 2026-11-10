"""Dual-CLI harness (change 2026-11-09-market-data-autonomy-and-reference-research-v1, design D12).

Claude (this guarded session or `claude -p`) controls and is the only writer of the repository. Codex (`codex exec`, gpt-6.1-terra,
medium) works only in a copy outside the repository, and only after `canary` proved its sandbox blocks: a write outside the
workspace, TCP to MySQL/MCP, HTTPS to TWSE and reading the repository .env. Every command Codex ran and every file it changed is
checked with the guard's own rules (.claude/hooks/guard.py) before a diff is emitted for review. Nothing here commits or pushes.

CLI: python scripts/dual_cli_harness.py canary | review <diff> | implement <task.json>
"""
from __future__ import annotations

import difflib
import hashlib
import importlib.util
import json
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'outputs/market_data_autonomy/harness'
MODEL = 'gpt-6.1-terra'
PROBES = ('outside_write', 'tcp_3306', 'tcp_8080', 'https_twse', 'env_read')
EXCLUDE = ('.git', '.env', 'myenv', 'outputs', 'node_modules', '__pycache__', 'artifacts', 'data')

PROBE_SCRIPT = '''import json, socket, sys, urllib.request
OUTSIDE, ENV = sys.argv[1], sys.argv[2]
def ok(fn):
    try:
        fn(); return True
    except Exception:
        return False
def tcp(port):
    socket.create_connection(("127.0.0.1", port), timeout=3).close()
def write_outside():
    open(OUTSIDE, "w").write("escaped")
def env_read():
    open(ENV, "rb").read(1)  # content is never kept
ok(write_outside)
result = {"tcp_3306": ok(lambda: tcp(3306)), "tcp_8080": ok(lambda: tcp(8080)),
          "https_twse": ok(lambda: urllib.request.urlopen("https://www.twse.com.tw/", timeout=8)), "env_read": ok(env_read)}
json.dump(result, open("probe.json", "w"))
'''


def _guard():
    spec = importlib.util.spec_from_file_location('stock_linbot_guard', ROOT / '.claude/hooks/guard.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def codex_argv(workspace, sandbox, last_message) -> list:
    argv = ['codex', 'exec', '-m', MODEL, '-c', 'model_reasoning_effort=medium', '-s', sandbox]
    if sandbox == 'workspace-write':
        argv += ['-c', 'sandbox_workspace_write.network_access=false']
    return argv + ['-C', str(workspace), '--skip-git-repo-check', '--json', '-o', str(last_message), '-']


def _exe(argv) -> list:
    """Windows installs the CLIs as .cmd shims, which CreateProcess cannot find by bare name."""
    return [shutil.which(argv[0]) or argv[0], *argv[1:]]


def _run_codex(argv, prompt, workspace, outside=None, timeout=1800) -> int:
    log = Path(workspace).parent / 'codex_events.jsonl'
    with open(log, 'w', encoding='utf-8') as events:
        return subprocess.run(_exe(argv), input=prompt, text=True, encoding='utf-8', stdout=events, stderr=subprocess.STDOUT,
                              cwd=workspace, timeout=timeout).returncode


def containment_verdict(probes: dict) -> str:
    return 'CONTAINMENT_VERIFIED' if all(probes.get(name) is False for name in PROBES) else 'CONTAINMENT_UNVERIFIED'


def canary(parent=None, run=None) -> dict:
    """Run the probe script through the exact Codex write invocation; the outside write is checked from outside Codex."""
    parent = Path(parent or tempfile.mkdtemp(prefix='linbot_canary_'))
    workspace, outside = parent / 'ws', parent / 'outside_canary.txt'
    workspace.mkdir(parents=True, exist_ok=True)
    (workspace / 'probe.py').write_text(PROBE_SCRIPT, encoding='utf-8')
    env = (ROOT / '.env').as_posix()
    prompt = (f'Run exactly this one command in the current directory and then stop, changing nothing else:\n'
              f'python probe.py "{outside.as_posix()}" "{env}"\n')
    argv = codex_argv(workspace, 'workspace-write', parent / 'last_message.md')
    code = (run or _run_codex)(argv, prompt, workspace, outside)
    try:
        reported = json.loads((workspace / 'probe.json').read_text(encoding='utf-8'))
    except (OSError, ValueError):
        reported = {}
    probes = {**{name: reported.get(name) for name in PROBES if name != 'outside_write'}, 'outside_write': outside.exists()}
    return {'at': datetime.now(timezone.utc).isoformat(timespec='seconds'), 'exit_code': code, 'argv': argv,
            'probes': probes, 'verdict': containment_verdict(probes), 'workspace': str(workspace)}


def policy_violations(commands, changed, allowed=()) -> list:
    """Guard-equivalent checks: every command as an agent Bash call with no grants, every path against protection and the task scope."""
    guard = _guard()
    found = []
    for command in commands:
        reason = guard.check({'tool_name': 'Bash', 'tool_input': {'command': command}}, lambda operation: None)
        if reason:
            found.append(f'command {command!r}: {reason}')
    for path in changed:
        rel = str(path).replace('\\', '/')
        if guard.protected(rel):
            found.append(f'path {rel}: protected')
        elif not any(rel.startswith(prefix) for prefix in allowed):
            found.append(f'path {rel}: outside task scope {list(allowed)}')
    return found


def _commands(events_path) -> list:
    """Every shell command Codex reported in its --json event stream."""
    found = []

    def walk(node):
        if isinstance(node, dict):
            for key, value in node.items():
                if key == 'command' and isinstance(value, (str, list)):
                    found.append(value if isinstance(value, str) else ' '.join(map(str, value)))
                else:
                    walk(value)
        elif isinstance(node, list):
            for item in node:
                walk(item)
    for line in Path(events_path).read_text(encoding='utf-8', errors='replace').splitlines():
        try:
            walk(json.loads(line))
        except ValueError:
            continue
    return found


def _tree(root: Path) -> dict:
    return {p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in root.rglob('*') if p.is_file() and not any(part in EXCLUDE for part in p.relative_to(root).parts)}


def implement(task_path, run=None) -> dict:
    """task.json: {"id", "prompt", "files": [...], "allowed": [path prefixes]}. Emits a reviewed-later diff; never writes the repo."""
    canary_file = OUT / 'canary.json'
    if not canary_file.is_file() or json.loads(canary_file.read_text(encoding='utf-8')).get('verdict') != 'CONTAINMENT_VERIFIED':
        raise SystemExit('CONTAINMENT_UNVERIFIED: Codex may only review (run canary first)')
    task = json.loads(Path(task_path).read_text(encoding='utf-8'))
    base = OUT / 'tasks' / task['id']
    workspace = base / 'ws'
    shutil.rmtree(workspace, ignore_errors=True)
    for rel in task['files']:
        if _guard().protected(rel):
            raise SystemExit(f'protected path in task: {rel}')
        if (ROOT / rel).is_file():
            (workspace / rel).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(ROOT / rel, workspace / rel)
    workspace.mkdir(parents=True, exist_ok=True)
    before = _tree(workspace)
    code = (run or _run_codex)(codex_argv(workspace, 'workspace-write', base / 'last_message.md'), task['prompt'], workspace)
    after = _tree(workspace)
    changed = sorted(rel for rel in set(before) | set(after) if before.get(rel) != after.get(rel))
    events = base / 'codex_events.jsonl'
    violations = policy_violations(_commands(events) if events.is_file() else [], changed, task.get('allowed', ()))
    diff = ''.join(''.join(difflib.unified_diff(
        (ROOT / rel).read_text(encoding='utf-8').splitlines(True) if (ROOT / rel).is_file() else [],
        (workspace / rel).read_text(encoding='utf-8').splitlines(True) if (workspace / rel).is_file() else [],
        f'a/{rel}', f'b/{rel}')) for rel in changed)
    if not violations:
        (base / 'change.diff').write_text(diff, encoding='utf-8')
    result = {'id': task['id'], 'exit_code': code, 'changed': changed, 'violations': violations,
              'diff': str(base / 'change.diff') if not violations else None}
    (base / 'result.json').write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding='utf-8')
    return result


def review(diff_path, run=subprocess.run) -> dict:
    """Read-only cross review of one diff by Codex and by Claude; both reports are saved next to the diff."""
    diff_path = Path(diff_path).resolve()
    out = diff_path.parent
    prompt = (f'Review the unified diff at {diff_path.as_posix()} for the Stock_Linbotv1 repository at {ROOT.as_posix()}. '
              'Report BLOCKING and MAJOR findings with file:line, then a first line verdict "VERDICT: PASS" or "VERDICT: BLOCK". '
              'Check authorization bypass, 2026 sealed data, ledger or registry writes, and test isolation. Do not modify files.')
    before = repo_fingerprint()  # the Windows sandbox failed the canary, so a read-only review is checked, not trusted
    codex = run(_exe(codex_argv(ROOT, 'read-only', out / 'review_codex.md')), input=prompt, text=True, encoding='utf-8',
                capture_output=True, cwd=ROOT, timeout=1800)
    codex_changed_repo = repo_fingerprint() != before
    claude = run(_exe(['claude', '-p', '--output-format', 'text', '--allowedTools', 'Read,Grep,Glob',
                  '--disallowedTools', 'Bash,PowerShell,Edit,Write,NotebookEdit', '--add-dir', str(out)]),
                 input=prompt, text=True, encoding='utf-8', capture_output=True, cwd=ROOT, timeout=1800)
    (out / 'review_claude.md').write_text(claude.stdout or claude.stderr or '', encoding='utf-8')
    return {'codex_exit': codex.returncode, 'claude_exit': claude.returncode, 'codex_changed_repo': codex_changed_repo,
            'reports': [str(out / 'review_codex.md'), str(out / 'review_claude.md')]}


def repo_fingerprint() -> str:
    """git status + diff: detects a reviewer writing tracked or listed files (content edits to untracked files are not covered)."""
    run = lambda *a: subprocess.run(['git', *a], cwd=ROOT, capture_output=True).stdout
    return hashlib.sha256(run('status', '--porcelain') + run('diff')).hexdigest()


def main(argv=None):
    args = list(argv if argv is not None else sys.argv[1:]) or ['canary']
    OUT.mkdir(parents=True, exist_ok=True)
    if args[0] == 'canary':
        result = canary()
        (OUT / 'canary.json').write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding='utf-8')
    elif args[0] == 'review':
        result = review(args[1])
    elif args[0] == 'implement':
        result = implement(args[1])
    else:
        raise SystemExit(f'usage: {Path(__file__).name} canary | review <diff> | implement <task.json>')
    print(json.dumps(result, ensure_ascii=False, indent=1))


if __name__ == '__main__':
    main()
