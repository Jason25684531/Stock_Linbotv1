"""Canonical grouped adapter for the Fundamental shadow entrypoint."""

from jobs.run_fundamental_shadow import main

__all__ = ["main"]


if __name__ == "__main__":
    raise SystemExit(main())
