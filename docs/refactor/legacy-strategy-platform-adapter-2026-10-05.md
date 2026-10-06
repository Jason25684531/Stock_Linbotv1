# C2 Legacy Strategy Platform Adapter Evidence

## Scope

This change adds one composition-only `LegacyStrategyAdapter`, immutable
legacy `StrategySpec` mappings, and an opt-in in-memory registry seam. Existing
strategy callers still use `StrategyManager`; no production caller imports the
adapter or registry.

## Authority and identity

- C1 archive: `openspec/changes/archive/2026-10-05-strategy-platform-contract-2026-10-05`
- Canonical IDs: the seven entries in `StrategyManager.CANONICAL_REGISTRY`.
- Legacy aliases: the fourteen entries in `StrategyManager.STRATEGY_METADATA`.
- Factory paths: copied at registry construction from `CANONICAL_REGISTRY` and
  resolved only when a registry factory is called.
- Legacy settings and historical strategy values are not rewritten.

## Parity evidence

- `test/unit/strategy/test_legacy_strategy_adapter.py`: direct legacy versus
  adapter candidate IDs/order for all seven canonical IDs and fourteen aliases;
  missing semantic fields remain `None`; legacy exceptions propagate.
- `test/unit/strategy/test_strategy_registry_legacy_parity.py`: canonical
  listing, alias identity, lazy factories, duplicate/unknown failures, and
  manager listing compatibility.
- `test/characterization/test_daily_selection_deterministic.py` and
  `test/characterization/test_backtest_seed.py`: deterministic daily/backtest
  baselines remain green.
- Web/LINE/CLI/Fundamental suites remain green; scheduler ownership is covered
  by `test/characterization/test_scheduler_baseline.py`.
- Full suite result: 824 passed, 1 skipped (environment-dependent), 1 xfailed;
  no failure.

## Rollback boundary

Rollback is independent: remove `core/strategy/adapters/`, the opt-in
`StrategyManager.get_platform_registry()` seam, C2 tests, and this evidence
file. No database migration, settings rewrite, historical value rewrite,
Fundamental artifact change, or legacy strategy deletion is required.
