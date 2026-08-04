"""CLI for the isolated factor-research pipeline."""

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.research import pipeline


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", required=True)
    parser.add_argument("--end", required=True)
    parser.add_argument("--run-id", default=datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ_manual"))
    parser.add_argument("--no-fetch", action="store_true")
    parser.add_argument("--reconciliation-seed", type=int, default=0)
    parser.add_argument("--allow-vendor-fallback", action="store_true")
    args = parser.parse_args()
    result = pipeline.run(pipeline.RunConfig(
        run_id=args.run_id, generated_at=datetime.now(timezone.utc).isoformat(),
        adjustment_as_of=datetime.now(timezone.utc), requested_start=args.start, requested_end=args.end,
        output_dir=Path("artifacts") / "factors" / args.run_id, quotes=None,
        reconciliation_seed=args.reconciliation_seed, allow_vendor_fallback=args.allow_vendor_fallback, no_fetch=args.no_fetch,
    ))
    return 0 if result.status == "success" else 1


if __name__ == "__main__":
    raise SystemExit(main())
