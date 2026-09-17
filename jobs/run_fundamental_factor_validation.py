"""Run frozen Fundamental PIT v3 factor validation only."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.research.fundamental_factor_validation import run_validation


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fundamental-root", type=Path, default=Path("outputs/fundamental_data/fundamental_pit_v3_20260915T000000Z"))
    parser.add_argument("--d3-root", type=Path, default=Path("artifacts/factors/d3_full_20230103_20260728"))
    parser.add_argument("--output-root", type=Path, default=Path("outputs/fundamental_factor_validation/add-fundamental-factor-validation-and-composite-v1_20260916"))
    parser.add_argument("--universe", type=Path, default=Path("data/processed/research_universe.parquet"))
    args = parser.parse_args(argv)
    result = run_validation(args.fundamental_root, args.d3_root, args.output_root, args.universe)
    manifest = result["manifest"]
    print(f"Fundamental factor validation complete: candidates={manifest['candidate_count']}; accepted={manifest['accepted_factor_count']}; output={args.output_root}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
