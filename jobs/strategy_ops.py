"""Read-only research and strategy operations diagnostics CLI."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from core.diagnostics import DiagnosticsService


def _table(value, prefix=""):
    if isinstance(value, dict):
        return "\n".join(_table(item, f"{prefix}{key.upper()}.") for key, item in value.items())
    if isinstance(value, list):
        return "\n".join(_table(item, prefix) for item in value)
    return f"{prefix[:-1]:<28} {value}"


def main(argv=None):
    parser = argparse.ArgumentParser(description="Read-only research/strategy diagnostics")
    subs = parser.add_subparsers(dest="command", required=True)
    for name in ("status", "data", "data-quality", "pipelines", "factors", "strategies", "validate", "oos", "health", "all", "report"):
        command = subs.add_parser(name)
        command.add_argument("--json", action="store_true")
        command.add_argument("--verbose", action="store_true")
    for name in ("factor", "strategy"):
        command = subs.add_parser(name)
        command.add_argument("--id", required=True)
        command.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    service = DiagnosticsService()
    method = {"data": "data_sources", "data-quality": "data_quality", "validate": "validation"}.get(args.command, args.command)
    result = getattr(service, method)(getattr(args, "id")) if args.command in {"factor", "strategy"} else getattr(service, method)(verbose=args.verbose) if args.command == "all" else getattr(service, method)()
    if args.json:
        print(json.dumps(result, indent=2, sort_keys=True, default=str))
    else:
        print(_table(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
