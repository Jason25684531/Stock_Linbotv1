"""CI gate: fail if a test imports core.db_helper without any DB isolation signal.

# ponytail: a test is flagged only when it has *zero* mitigating signal (no
# local engine override, no monkeypatch/mock usage anywhere in the file, and
# no known DB opt-in marker). This intentionally under-flags rather than
# over-flags in a suite where mocking idioms vary per file; widen the
# heuristic if a genuinely-unmarked, unmocked MySQL-dependent test slips
# through.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

TEST_ROOT = Path(__file__).resolve().parents[1] / "test"
DB_HELPER_IMPORT = re.compile(r"^\s*(import core\.db_helper|from core import[^\n]*\bdb_helper\b|from core\.db_helper import)", re.MULTILINE)
MITIGATING_SIGNALS = ("create_engine", "monkeypatch", "Mock(", "MagicMock", "patch(", "patch.object(")
MARKER_SIGNALS = ("pytest.mark.integration", "pytestmark", "allow_real_backtest_persistence")


def find_unmarked_db_dependent_tests() -> list[Path]:
    flagged = []
    for path in sorted(TEST_ROOT.rglob("test_*.py")):
        source = path.read_text(encoding="utf-8")
        if not DB_HELPER_IMPORT.search(source):
            continue
        has_mitigation = any(signal in source for signal in MITIGATING_SIGNALS)
        has_marker = any(signal in source for signal in MARKER_SIGNALS)
        if not has_mitigation and not has_marker:
            flagged.append(path)
    return flagged


def main() -> int:
    flagged = find_unmarked_db_dependent_tests()
    if flagged:
        print("Tests import core.db_helper with no isolation and no integration marker:")
        for path in flagged:
            print(f"  {path.relative_to(TEST_ROOT.parent)}")
        return 1
    print("Integration-marker coverage check passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
