# 2026-10-14 Phase 0 evidence

- Original isolated LINE baseline: 1 failed in 3.86s. Same original 126-case suite after the shared composition prerequisite fix: 126 passed, 1 warning in 20.61s.
- Broader characterization/caller baseline: 283 passed, 2296 warnings in 121.38s. Commands and final suite are recorded in cleanup_validation_report.md. Includes the original failed LINE case (now green), all strategy/runtime/recommendation/backtest unit suites, the new hash and precleanup records contracts, seeded trade sequence, Daily deterministic/parity/persistence, CLI/Web/LINE/scheduler and SQLite pipeline state tests.
- Existing warnings: XGBoost serialized model (1), pandas groupby.apply (3), fillna/downcasting (2291), resample M (1). Retained; no global warning filter or package upgrades added.
- 12 golden snapshot/message/cache outputs were captured before records refactoring. Empty/None/MCP/error/cached/edge-value branches and TTL/day-boundary contracts pass. Six hash helpers pass empty/binary/multi-chunk/error/closure tests before replacement.
- Current settings, target source, fixture and frozen artifact checksums and read-only production gate result are in baseline.json; original source snapshots are ignored local outputs/architecture_cleanup_2026-10-14/baseline_sources, not duplicated in committed documentation.
- source_hash_inventory.json records 153 canonical metadata JSON manifests checked. Canonical PIT helper is DEFER because it records its own code_sha256; the other five hash data/artifacts and have no self-source gate consumer in checked source/manifests. Frozen artifacts are not rewritten.
- Full private helper/no-dynamic-reference evidence is recorded in docs/refactor/deletion_candidates.md before removal. No non-cache file deletion is planned.
