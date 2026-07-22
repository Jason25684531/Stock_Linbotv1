"""Compatibility CLI for the canonical backtest runner."""

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core.backtest.runner import *  # noqa: F401,F403 - public CLI compatibility
from core.backtest.runner import _seed_backtest_rng


def main(argv=None):
    """Delegate while keeping this historical module monkeypatch-compatible."""
    import core.backtest.runner as runner

    names = ('BacktestEngine', 'PortfolioBacktestEngine', 'resolve_backtest_plan', 'save_backtest_results')
    original = {name: getattr(runner, name) for name in names}
    try:
        for name in names:
            setattr(runner, name, globals()[name])
        return runner.main(argv)
    finally:
        for name, value in original.items():
            setattr(runner, name, value)


if __name__ == "__main__":
    raise SystemExit(main())
