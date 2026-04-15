"""Compatibility launcher for jobs.update_database."""

from __future__ import annotations

from importlib import import_module
import sys

_CANONICAL_MODULE = 'jobs.update_database'

if __name__ == '__main__':
    raise SystemExit(import_module(_CANONICAL_MODULE).main())

sys.modules[__name__] = import_module(_CANONICAL_MODULE)