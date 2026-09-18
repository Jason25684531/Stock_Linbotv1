"""Advance the frozen Fundamental Strategy through PIT refresh and shadow OOS."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.runtime.fundamental_advancement import OUTPUT_ROOT, run_advancement


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--current-run-date", default=None)
    parser.add_argument("--output-root", type=Path, default=OUTPUT_ROOT)
    parser.add_argument("--cache-only", action="store_true")
    args = parser.parse_args(argv)
    result = run_advancement(current_run_date=args.current_run_date, output_root=args.output_root, cache_only=args.cache_only)
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    return 0 if result.get("SHADOW_MODE") in {"PASS", None} and result.get("status") != "BLOCKED" else 2


if __name__ == "__main__":
    raise SystemExit(main())
