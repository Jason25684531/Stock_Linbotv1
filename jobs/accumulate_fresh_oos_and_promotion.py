"""Accumulate the frozen Fundamental shadow ledger and review promotion."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.runtime.fresh_oos_promotion import OUTPUT_ROOT, run_accumulation


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--current-run-date", default=None)
    parser.add_argument("--output-root", type=Path, default=OUTPUT_ROOT)
    parser.add_argument("--allow-network", action="store_true", help="allow the existing refresh job to fetch missing data")
    args = parser.parse_args(argv)
    result = run_accumulation(current_run_date=args.current_run_date, output_root=args.output_root, cache_only=not args.allow_network)
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    return 0 if result.get("status") != "BLOCKED" and result.get("SHADOW_MODE") in {"PASS", None} else 2


if __name__ == "__main__":
    raise SystemExit(main())
