"""Compatibility-only legacy launcher for jobs.run_backtest.

Do not remove this wrapper until the documented major-version migration.

Official daily scheduler path for daily operations: jobs/scheduler.py.
Deprecated: use ``python jobs/run_backtest.py``. Planned removal: v4.0, after
the characterization contract has remained compatible for a full release.
"""

from __future__ import annotations

from importlib import import_module
import sys

_CANONICAL_MODULE = 'jobs.run_backtest'

if __name__ == '__main__':
    raise SystemExit(import_module(_CANONICAL_MODULE).main())

sys.modules[__name__] = import_module(_CANONICAL_MODULE)
