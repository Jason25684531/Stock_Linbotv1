"""Compatibility-only legacy launcher for jobs.optimize_params.

Official daily scheduler path for daily operations: jobs/scheduler.py.
Do not remove this wrapper until cleanup evidence passes.
"""

from __future__ import annotations

from importlib import import_module
import sys

_CANONICAL_MODULE = 'jobs.optimize_params'

if __name__ == '__main__':
    raise SystemExit(import_module(_CANONICAL_MODULE).main())

sys.modules[__name__] = import_module(_CANONICAL_MODULE)
