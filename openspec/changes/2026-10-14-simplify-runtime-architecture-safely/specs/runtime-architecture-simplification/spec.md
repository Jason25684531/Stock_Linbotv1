## ADDED Requirements

### Requirement: Cleanup starts from a reproducible green baseline
The cleanup SHALL record current working-tree state, Python and package versions, settings identity, fixture identity, exact commands and pass/fail/skip counts before source changes. All relevant characterization and caller checks MUST pass without excluding known failing cases to manufacture a green baseline. Existing failures SHALL be resolved in a separately documented correctness step before cleanup proceeds.

#### Scenario: Existing LINE reply test fails
- **WHEN** the baseline reproduces an SDK validation failure in the LINE reply path
- **THEN** source cleanup is deferred and the failure, exact command and separate resolution evidence are recorded
- **AND** implementation does not mark that test xfail or silently exclude it

### Requirement: Native file hashing preserves contracts and frozen evidence
The six audited streaming SHA-256 helpers SHALL use Python 3.11 hashlib.file_digest only after parity and evidence-consumer checks pass. Function names, signatures, digests, resource closure and existing error semantics SHALL be preserved. Existing frozen manifests, fingerprints and historical validation artifacts MUST NOT be rewritten to hide source-hash drift.

#### Scenario: Valid or unreadable input is hashed
- **WHEN** each candidate receives an empty file, binary file, multi-chunk file or a missing/unreadable path
- **THEN** its digest or exception/None result equals the pre-change implementation and its opened file is closed

#### Scenario: Source hash is consumed by a frozen runtime gate
- **WHEN** a proposed helper refactor changes a source file hash used by frozen evidence
- **THEN** that candidate is deferred unless a separate evidence-update change provides the required gate validation
- **AND** the cleanup does not replace the frozen expected hash

### Requirement: Private dead code removal requires complete evidence
The cleanup SHALL remove only core.strategy.spec._freeze_mapping after docs/refactor/deletion_candidates.md records complete current static, dynamic, test, script, document and deployment no-reference evidence, replacement, verification and rollback. Unknown consumers SHALL cause retention. No non-cache file SHALL be deleted in this change.

#### Scenario: Evidence is incomplete
- **WHEN** any consumer cannot be ruled out
- **THEN** the helper is retained with a DEFER decision

### Requirement: Shared records arithmetic preserves caller behavior
Dashboard and LINE market/chip builders SHALL use at most two shared private pure calculation functions for the already-equivalent records arithmetic. The refactor SHALL preserve per-record integer conversion, safe_float handling, rising/falling comparisons, flat count, volume units and net totals. Caller-specific HTTP calls, dates, cache keys, TTL, expiry, cross-day behavior, API schemas, message text and all error/empty branches SHALL remain unchanged.

#### Scenario: Both callers receive the same records
- **WHEN** Dashboard and LINE process records including missing values, NaN, zero, negative and fractional values
- **THEN** their numerical fields and formatted output equal their respective pre-change fixtures
- **AND** each caller retains its original data fetch and cache behavior

#### Scenario: Superficially similar Web arithmetic differs
- **WHEN** Web institutional totals convert to integer after aggregation or the market light uses different missing-value semantics
- **THEN** those functions retain their existing implementation and do not adopt the records helper

### Requirement: Public and trading compatibility remains intact
The cleanup SHALL preserve app.py, config.py, 4_run_backtest.py, 5_push_to_line.py, legacy strategy aliases, StrategyManager/Registry/Runner/adapter ownership, CLI/Web/LINE behavior and jobs.scheduler as sole scheduler owner. Trade dates, stock IDs, direction, order and quantity SHALL match exactly; floating results SHALL use rtol=1e-9 and atol=1e-12. Signals, execution timing, costs, slippage and strategy conditions SHALL not change. Undefined performance metrics SHALL retain value=None with an uncomputable reason. Fundamental eligibility SHALL not activate production or broker submission.

#### Scenario: Caller and trading characterization runs
- **WHEN** each Phase completes
- **THEN** affected characterization, caller parity and broker-isolation checks match their established baseline
- **AND** historical database strategy keys and frozen research artifacts remain unchanged

### Requirement: Cleanup phases have independent verification and rollback
Each implementation Phase SHALL have an independently revertible commit and verification evidence without including unrelated user changes. Final acceptance SHALL include relevant tests, isolated persistence smoke where affected, git diff --check, OpenSpec strict validation and docs/cleanup_audit_report.md, docs/cleanup_change_summary.md and docs/cleanup_validation_report.md. Missing environments or failed checks SHALL be recorded and SHALL prevent a claim of complete operational verification.

#### Scenario: A Phase fails validation
- **WHEN** its relevant parity or integration check fails
- **THEN** the next Phase does not begin and rollback uses only that Phase commit

### Requirement: Proposal generation has no runtime effects
Generating this proposal SHALL write only files inside its OpenSpec change directory. It SHALL not modify application code, tests, settings, dependencies, runtime outputs, database contents or services, and SHALL not send LINE messages.

#### Scenario: Proposal is delivered
- **WHEN** all OpenSpec artifacts are prepared
- **THEN** implementation tasks remain unchecked and the report distinguishes proposal checks from future operational acceptance
