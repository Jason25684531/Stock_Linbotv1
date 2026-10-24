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


# core/research/native_frame.py deliberately builds its cross-section with Daily's own feature code so the research snapshot and the recovered Daily
# cannot drift (f35b7e1, shared core in 4bb3911). The exception is pinned to this file and these names: any other file, module or name still fails.
PINNED_EXCEPTIONS = {("core/research/native_frame.py", "core.calc_indicators"): {"build_feature_history", "build_multi_factor_matrix"}}


def _violations():
    found = {}
    for source_file in RESEARCH_ROOT.rglob("*.py"):
        relative = source_file.relative_to(RESEARCH_ROOT.parents[1]).as_posix()
        for node in ast.walk(ast.parse(source_file.read_text(encoding="utf-8"))):
            modules = [alias.name for alias in node.names] if isinstance(node, ast.Import) else [node.module] if isinstance(node, ast.ImportFrom) and node.module else []
            for module in modules:
                if any(module == forbidden or module.startswith(f"{forbidden}.") for forbidden in FORBIDDEN_IMPORTS):
                    names = {alias.name for alias in node.names} if isinstance(node, ast.ImportFrom) else set()
                    found.setdefault((relative, module), set()).update(names)
    return found


def test_research_package_has_no_prohibited_imports():
    assert _violations() == PINNED_EXCEPTIONS
