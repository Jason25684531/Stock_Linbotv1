from __future__ import annotations

import ast
from pathlib import Path


FORBIDDEN_ROOTS = {
    "app",
    "jobs",
    "flask",
    "line",
    "scheduler",
    "sqlalchemy",
    "templates",
}


def _import_roots(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    roots: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            roots.update(alias.name.split(".", 1)[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            roots.add(node.module.split(".", 1)[0])
    return roots


def test_new_contract_modules_have_no_transport_or_persistence_imports() -> None:
    roots = set()
    for package in (Path("core/strategy"), Path("core/recommendation")):
        for path in package.rglob("*.py"):
            roots.update(_import_roots(path))
    assert not roots.intersection(FORBIDDEN_ROOTS)
