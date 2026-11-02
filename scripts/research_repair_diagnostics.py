"""Print read-only strategy diagnostics as JSON; no external request or production write."""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from core.research.repair_diagnostics import diagnostics  # noqa: E402


def main():
    result = diagnostics()
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result['status'] == 'OK' else 1


if __name__ == '__main__':
    raise SystemExit(main())
