"""Run the frozen Fundamental candidate through the scheduler-compatible shadow path."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.runtime.fundamental_shadow import run_shadow


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--asof-date", help="PIT snapshot date; defaults to the current date.")
    parser.add_argument("--output-root", type=Path, help="Optional shadow artifact root.")
    args = parser.parse_args(argv)
    kwargs = {"asof_date": args.asof_date}
    if args.output_root:
        kwargs["output_root"] = args.output_root
    result = run_shadow(**kwargs)
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    return 0 if result["status"] in {"SUCCESS", "DISABLED"} else 2


if __name__ == "__main__":
    raise SystemExit(main())
