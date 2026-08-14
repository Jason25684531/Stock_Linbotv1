"""CLI for the D5 candidate-redundancy diagnostic."""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.research.candidate_redundancy import CandidateRedundancyError, analyze_handoff, freeze_handoff


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-run-id", required=True)
    parser.add_argument("--handoff-id")
    parser.add_argument("--output-root", default="artifacts/d5_handoff")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    handoff_id = args.handoff_id or args.source_run_id
    try:
        handoff = freeze_handoff(args.source_run_id, handoff_id=handoff_id, output_root=Path(args.output_root))
        artifacts = analyze_handoff(handoff)
    except CandidateRedundancyError as caught:
        print(str(caught), file=sys.stderr)
        return 1
    print(f"Handoff: {handoff}\nArtifacts:\n" + "\n".join(str(path) for path in artifacts.values()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
