"""Every third-party package a scheduler job imports at top level is pinned in some manifest."""
import ast
import re
import sys
from importlib.metadata import packages_distributions
from pathlib import Path

from jobs.scheduler import JOBS

ROOT = Path(__file__).resolve().parents[1]
MANIFESTS = ('requirements.txt', 'requirements.runtime.txt', 'requirements.mcp.txt')
LOCAL = {'core', 'app', 'jobs', 'scripts', 'services', 'config', '__future__'}


def _norm(name):
    return re.sub(r'[-_.]+', '-', name).lower()


def _pinned():
    names = set()
    for manifest in MANIFESTS:
        raw = (ROOT / manifest).read_bytes()
        text = raw.decode('utf-16') if raw[:2] in (b'\xff\xfe', b'\xfe\xff') else raw.decode('utf-8')
        for line in text.splitlines():
            line = line.strip()
            if line and not line.startswith(('#', '-')):
                names.add(_norm(re.split(r'[<>=!~\[ ;]', line)[0]))
    return names


def _top_level_imports(path):
    for node in ast.parse(path.read_text(encoding='utf-8')).body:
        if isinstance(node, ast.Import):
            yield from (alias.name.split('.')[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            yield node.module.split('.')[0]


def test_scheduler_job_imports_are_pinned():
    pinned, dists = _pinned(), packages_distributions()
    missing = []
    for spec in JOBS.values():
        for module in set(_top_level_imports(ROOT / spec.script_path)):
            if module in LOCAL or module in sys.stdlib_module_names:
                continue
            if not any(_norm(dist) in pinned for dist in dists.get(module, [module])):
                missing.append(f'{spec.script_path}: {module}')
    assert not missing, f'unpinned in {MANIFESTS}: {sorted(missing)}'
