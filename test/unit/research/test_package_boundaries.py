"""Research package public-import and dependency-boundary tests."""

import ast
from pathlib import Path


RESEARCH_ROOT = Path(__file__).resolve().parents[3] / "core" / "research"
FORBIDDEN_IMPORTS = {
    "flask",
    "plotly",
    "linebot",
    "core.db_helper",
    "core.strategies",
    "core.calc_indicators",
}


def test_research_package_imports():
    import core.research  # noqa: F401


def test_research_package_has_no_prohibited_imports():
    imports = set()
    for source_file in RESEARCH_ROOT.rglob("*.py"):
        tree = ast.parse(source_file.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imports.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imports.add(node.module)

    assert not any(
        imported == forbidden or imported.startswith(f"{forbidden}.")
        for imported in imports
        for forbidden in FORBIDDEN_IMPORTS
    )
