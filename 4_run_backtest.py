"""Compatibility-only legacy launcher for jobs.run_backtest.

Official daily scheduler path for daily operations: jobs/scheduler.py.
Do not remove this wrapper until cleanup evidence passes.
"""

from __future__ import annotations

from importlib import import_module
import sys

_CANONICAL_MODULE = 'jobs.run_backtest'

if __name__ == '__main__':
    raise SystemExit(import_module(_CANONICAL_MODULE).main())

sys.modules[__name__] = import_module(_CANONICAL_MODULE)
