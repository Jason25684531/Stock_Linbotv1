# Strategy Platform Runtime Contract — C3

Implementation evidence for `strategy-platform-runtime-contract-2026-10-06`.

## Current pipeline

```text
Fundamental research/freeze
  -> FrozenStrategyLoader / FrozenStrategySpec.json
  -> validated PIT + CanonicalSelectionAdapter shadow
  -> research/runtime parity + offline replay + historical drift
  -> Fresh OOS ledger and promotion review
  -> fundamental_production.check_production_eligibility
  -> existing run_daily/status boundary
  -> broker boundary remains DISABLED
```

The existing Fundamental modules remain owners of every algorithm and artifact:

| Concern | Owner | C3 boundary | C3 side effect |
|---|---|---|---|
| Frozen candidate and fingerprint | `core/research/fundamental_strategy_validation.py` | `FrozenStrategyIdentity`, `FingerprintProvider` | None |
| Frozen loading and field checks | `core/runtime/fundamental_shadow.py` | `FundamentalIdentityAdapter` | None |
| PIT, factor health, ranking, Top5, REB60, targets | `CanonicalSelectionAdapter` | `ShadowResult` translation | Existing shadow artifacts only |
| Research/runtime comparison and replay | `build_validation_artifacts()` | `ParityResult` translation | Existing parity/replay artifacts only |
| OOS and promotion | `core/runtime/fresh_oos_promotion.py` | `PromotionDecision` translation | Existing OOS/promotion artifacts only |
| Production gate | `core/runtime/fundamental_production.py` | `GateResult`, `ProductionEligibility` translation | Existing status artifact only |
| Daily/Web/LINE/DB/scheduler | Existing application owners | None in C3 | Unchanged |

## Generic versus Fundamental boundary

Generic implementation is limited to immutable values/protocols in
`core/runtime/contracts.py`: `RuntimeStatus`, `EvidenceRef`, result values,
decision values, and identity protocols.  They do not import application code,
read/write files, calculate domain policy, mutate lifecycle, or submit orders.

`core/runtime/fundamental_identity.py` and
`core/runtime/fundamental_adapter.py` are composition adapters.  They delegate
to existing Fundamental owners and translate returned values; they do not
implement a second selector, validator, fingerprint builder, gate engine,
promotion engine, or runner.

The following remain Fundamental-specific: PIT semantics, factor IDs and
health, universe, G2/G3 formulas, score weighting, Top5, REB60/no-retarget,
selection ordering, OOS minima and metrics, promotion levels/policy, frozen
payload serialization, and production gate precedence.

## Identity, parity, and failure preservation

`FrozenStrategyLoader` remains the only loader and fingerprint authority.  The
adapter exposes its existing `strategy_fingerprint` without reserialization or
hashing.  Existing parity fields remain `stock_id`, `score`, `rank`,
`selected`, `target_weight`, and `reason`, with `rtol=1e-9` and `atol=1e-12`.
No missing field is filled and no mismatch is sorted away.

The production gate remains delegated to its current owner.  Its observed
precedence is evidence/identity consistency, historical drift, runtime gates,
Fresh OOS availability, promotion state, then the explicit production flag.
The adapter preserves opaque existing codes, including
`FINGERPRINT_MISMATCH`, `HISTORICAL_DRIFT`, `INSUFFICIENT_OOS`,
`PRODUCTION_DISABLED`, `PROMOTION_EVIDENCE_INVALID`, `STALE_DATA`,
`LOW_FACTOR_COVERAGE`, and the existing `FROZEN_*`, `PIT_*`, selection, and
runtime-failure codes.  No synonym or rename is introduced.

`ProductionEligibility` is a decision value, not activation.  Every adapter
path reports `BROKER_ORDER_SUBMISSION=DISABLED`; no broker client, queue, or
order method exists in C3.

## Rule of Two and caller isolation

C2 Legacy is a second consumer only for generic result/comparison/failure
boundaries.  It receives no frozen, shadow, OOS, promotion, or Fundamental
fingerprint implementation.  `jobs/run_daily.py`, `jobs/scheduler.py`, Web,
LINE, CLI, backtest, and `StrategyManager` contain no C3 adapter/runner import.
Production caller adoption remains `NONE`.

## Rollback

Rollback removes only C3 contract/adapter modules, C3 tests, this document,
and the OpenSpec change.  It does not require database, frozen artifact,
fingerprint, OOS ledger, historical evidence, scheduler, Web/LINE, or legacy
restoration.  C1 and C2 remain independently usable.

## Verification evidence

- Added runtime contracts: `core/runtime/contracts.py`.
- Added adapters: `core/runtime/fundamental_identity.py` and
  `core/runtime/fundamental_adapter.py`.
- Added tests: `test_strategy_runtime_contracts.py`,
  `test_fundamental_identity_adapter.py`,
  `test_fundamental_runtime_contract_adapters.py`,
  `test_fundamental_runtime_adapter.py`, and
  `test_strategy_runtime_caller_isolation.py`.
- Added this evidence document and the C3 OpenSpec proposal/design/spec/tasks.
- Existing production modules and caller modules were not modified.  The
  unrelated `strategy_settings.json` timestamp/newline touched by test setup
  was restored semantically and is not part of C3 behavior.
- Focused C3: 23 passed. Fundamental/runtime/research: 50 passed. C2 and
  identity: 41 passed. Daily/Backtest/Web/LINE/CLI/Scheduler: 35 passed.
- Full repository: 847 passed, 1 skipped, 1 expected xfail, 0 failed.
- `openspec validate strategy-platform-runtime-contract-2026-10-06 --strict`:
  PASS. `git diff --check`: PASS.
- Direct-versus-adapter fingerprint, shadow status/action/reasons, parity
  fields/tolerance, promotion level, gate precedence, eligibility, evidence
  references, and broker-disabled status are covered by focused tests.
- Production caller adoption: `NONE`; no caller, scheduler, DB schema, or
  broker module was routed through C3.
