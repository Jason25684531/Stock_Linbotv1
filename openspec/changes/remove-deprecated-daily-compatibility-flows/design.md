## Context

The repository has already completed daily-pipeline consolidation. `jobs/scheduler.py` is the official scheduled owner, the official daily flow is `jobs/update_database.py` -> `jobs/run_daily.py` -> `jobs/run_daily_backtest_validation.py` -> `jobs/push_to_line.py`, and compatibility launchers now carry deprecation markers instead of acting as peer scheduler paths.

`docs/cleanup_inventory.md` is now the main evidence surface for cleanup review. It already classifies entrypoints as active path, legacy compatibility, removable candidate, or unknown / needs verification, and records evidence fields such as imports, CLI references, docs references, Compose references, scheduler references, test dependencies, OpenSpec references, and user-facing workflow references.

This change is proposal-only. It defines how a future implementation can safely remove deprecated compatibility flows after the completed consolidation work, without reopening runtime architecture decisions.

## Goals / Non-Goals

**Goals:**
- Define how to select removal candidates from `docs/cleanup_inventory.md`
- Define the reference scans and test evidence required before deleting any compatibility launcher or wrapper
- Preserve `jobs/scheduler.py` as the only official daily entrypoint during cleanup
- Preserve the current daily flow order while deletion work proceeds
- Define fallback and recovery guidance if a removed launcher still turns out to be needed

**Non-Goals:**
- Changing runtime behavior
- Introducing a second scheduler path
- Changing scheduler sequencing or job ownership
- Changing `DB_URL`, `MODEL_PATH`, price provenance, `pipeline_runs`, or daily backtest validation behavior
- Introducing broker-backed execution, real-money trading, or production-only topology
- Performing file deletion in this proposal-only step

## Decisions

### Decision: runtime behavior remains unchanged

This cleanup change does not change scheduler logic, API payload behavior, persistence behavior, or validation behavior. It only defines the deletion policy and implementation plan for deprecated compatibility flows.

Alternative considered:
- Fold cleanup deletion into the previous consolidation change. Rejected because runtime consolidation and evidence-based deletion have different review surfaces and rollback concerns.

### Decision: `jobs/scheduler.py` remains the only official daily entrypoint

Cleanup work must preserve the current owner of scheduled daily execution. No compatibility launcher or batch wrapper may be promoted into a second official scheduler path while deletion candidates are being reviewed or removed.

Alternative considered:
- Treat batch wrappers or numeric launchers as equivalent entrypoints. Rejected because the repo already finished consolidating scheduler ownership and docs now point to `jobs/scheduler.py`.

### Decision: deletion requires evidence from inventory plus fresh scans

`docs/cleanup_inventory.md` is the primary source of truth for candidate selection, but future implementation must re-scan the codebase before deletion. A file is removable only if the latest scan confirms:

- no imports
- no CLI references
- no README/docs references
- no docker-compose references
- no scheduler references
- no test dependencies
- no OpenSpec references except historical or deprecation notes
- no user-facing documented workflow

Alternative considered:
- Remove everything already marked deprecated. Rejected because deprecation is only a warning layer, not proof that all dependencies are gone.

### Decision: removal must be backed by tests and reference scans

Future implementation must add or update guardrail tests before deleting any compatibility path. Tests and reference scans must prove that the official scheduler path still covers the supported daily workflow and that docs no longer depend on the candidate path.

Alternative considered:
- Rely on manual grep and reviewer judgment alone. Rejected because cleanup regressions often hide in undocumented wrappers and local operator workflows.

### Decision: fallback guidance is mandatory

Every deletion plan must say how to recover if a removed compatibility launcher is still needed. Recovery guidance may include restoring the removed wrapper from version control, recreating a thin compatibility shim, or redirecting the operator to the supported scheduler command with documented examples.

Alternative considered:
- Assume deleted launchers are never needed again. Rejected because legacy operator muscle memory and local scripts can still surface after removal.

## Risks / Trade-offs

- Evidence can drift between inventory review and implementation review -> Future deletion work must refresh scans before removing files.
- Some compatibility launchers may still be used outside tracked docs or tests -> Recovery guidance must exist before deletion, and removal should happen in small batches.
- A path may appear removable while still referenced in archived OpenSpec notes -> Historical references should not block deletion by themselves, but active guidance must be cleaned up first.
- Cleanup reviews can take longer than direct deletion -> The slower pace is intentional because preserving the scheduler contract matters more than removing wrappers quickly.

## Migration Plan

1. Refresh `docs/cleanup_inventory.md` and classify candidates that appear removable or likely removable.
2. Re-scan each candidate across code, docs, tests, Compose, scheduler wiring, and OpenSpec references.
3. Delete only candidates whose evidence gates pass and whose recovery guidance is documented.
4. Re-run targeted scheduler, docs, and cleanup guardrail tests after each deletion batch.
5. If a removed path is still needed, restore it from version control or replace it with a minimal compatibility shim while the evidence is corrected.

## Open Questions

- Which currently deprecated launchers will pass the full no-reference scan first?
- Should future deletion work remove wrappers one at a time or in small evidence-backed batches?
- Are there any local operator workflows not yet captured in `docs/cleanup_inventory.md` that need to be documented before deletion begins?
