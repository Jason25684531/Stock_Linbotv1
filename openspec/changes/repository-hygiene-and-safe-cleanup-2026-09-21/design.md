## Context

The canonical runtime is `app/`, `jobs/`, `core/`, `services/`, `config/`, and the Compose-defined MySQL/MCP/Web services.  The official operational entrypoint is `jobs/scheduler.py`; `4_run_backtest.py`, `5_push_to_line.py`, `app.py`, `config.py`, and several research paths are compatibility surfaces rather than removable duplicates.  The current source also has intentional research re-export shims such as `core.research.market_data` and `core.research.selection.*`.

Existing inventories correctly require evidence before deletion, but their file counts and findings predate the current research and Fundamental-runtime layout.  Ignored roots contain cache plus large output/evidence/data/model material whose provenance is not uniformly complete.  A new OpenSpec change is also ignored by the current `.gitignore` rule, preventing normal review.  Finally, `app/web_server.py` and other `app` modules resolve dependencies through the package namespace, contrary to the existing one-way dependency requirement; changing that behavior is not a cleanup operation.

## Goals / Non-Goals

**Goals:**

- Refresh and persist reproducible cleanup and archive evidence before any non-cache mutation.
- Keep compatibility, runtime behavior, research semantics, backtest signals, and external contracts unchanged.
- Define a manifest-first archive flow and a narrow, active-run-aware cache disposal flow.
- Restore reviewability for newly active OpenSpec artifacts and explicitly record the CLI/date-prefix naming conflict.
- Provide a fact-based architecture health assessment that identifies the smallest justified follow-up work.

**Non-Goals:**

- No deletion of source, dependencies, models, datasets, unknown outputs, compatibility wrappers, or historical OpenSpec material without a later approved evidence record.
- No move of Fundamental OOS, validation, or production-gate evidence.
- No rewrite of `app/`, strategy logic, backtest logic, scheduler behavior, database schema, Web API, LINE behavior, or dependency versions.
- No broad module split merely to reduce line count; any app-boundary refactor is a separate change with characterization tests.

## Decisions

### 1. Evidence determines disposition, not file age or apparent duplication

Each candidate will have one current classification and a reference matrix covering imports, dynamic strings, CLI/batch entrypoints, scheduler, Compose, CI, docs, tests, Web/LINE, agent configuration, and OpenSpec.  A reference or incomplete search is a preservation result, not a failed cleanup.

Alternatives considered:

- Delete unreferenced-looking modules based only on Python imports. Rejected because operator CLI paths, documentation, dynamic imports, and compatibility contracts are explicit project concerns.
- Move all old material to an archive directory. Rejected because moving unknown input/evidence can break reproducibility as surely as deletion.

### 2. Archive manifests precede any output relocation

Only duplicate, reproducible same-purpose runs with a confirmed owner and inactive status can move.  The manifest will record the dated command, input and output hashes, status, original and canonical locations, retention period, owner, and recovery command.  Unknown, model, data, active-run, and unique evidence paths remain in place.

Alternatives considered:

- Archive by modification time or directory size. Rejected: neither establishes authority, provenance, or rebuildability.
- Delete reproducible output immediately. Rejected: research/OOS evidence may be needed to audit a production decision.

### 3. Cache cleanup is a separate reversible batch

The only pre-approved removable category is confirmed disposable cache (`__pycache__/`, `.pytest_cache/`, `.coverage`, `htmlcov/`, `.mypy_cache/`, `.ruff_cache/`).  The implementation must first show that no repository run is using the target and must retain a path/count record.  It will not recurse into an unresolved broad root.

Alternatives considered:

- Combine cache disposal with source cleanup. Rejected: it obscures the safety review and rollback boundary.

### 4. Reviewable active OpenSpec artifacts are a repository-governance requirement

The ignore policy will expose future active change directories as local untracked files while continuing to ignore generated/archived OpenSpec state.  The implementation will validate ordinary `git status` visibility and absence from `git ls-files`, without force-add.  Because this OpenSpec CLI rejects names beginning with a date while project policy requires that prefix, the discrepancy is recorded with the created-date suffix used for this change; it must be resolved by a documented tooling/policy decision, not an undocumented naming exception.

Alternatives considered:

- Track or force-add each active change. Rejected by the active repository contract test and not scalable.
- Ignore all OpenSpec files. Rejected because proposal, design, specs, and tasks need code review.

### 5. Architecture assessment informs, but does not bundle, refactoring

The health report will measure module concentration, dependency direction, public compatibility edges, test coverage evidence, and runtime ownership.  It will call out the package-namespace access pattern as a reliability and extensibility risk, but it will not change it.  A later change can extract explicit services or parameters only after characterization baselines establish API/LINE/CLI equivalence.

Alternatives considered:

- Fix the app boundary as incidental cleanup. Rejected because it spans many routes and interaction flows and would mix cleanup with behavior-sensitive refactoring.

## Risks / Trade-offs

- [A process is using a cache or output during cleanup] → Require an active-run check, record target paths exactly, and defer on uncertainty.
- [A missing dynamic or external reference makes a candidate appear dead] → Include string, shell, Compose, docs, CI, agent, OpenSpec, and user-facing workflow checks; classify incomplete evidence as `UNKNOWN`.
- [Archiving loses provenance or makes reproduction impossible] → Require a hash-backed manifest and a tested recovery command before relocation.
- [Making OpenSpec changes visible exposes local generated history] → Unignore only active change content and preserve ignore rules for generated/archive state.
- [The app package dependency pattern becomes a large redesign] → Report it now, create a follow-up only with an agreed seam and characterization baseline.

## Migration Plan

1. Capture baseline Git state, process/run state, tracked/ignored inventory, reference matrix, and architecture evidence without mutation.
2. Update the hygiene ledger and candidate classifications; stop any source/non-cache removal whose evidence is incomplete.
3. Correct active-change visibility in ignore rules and verify normal Git review behavior; document the naming-policy/CLI conflict.
4. For each owner-approved archive batch, write and validate its manifest before a reversible relocation.  Do not combine batches.
5. Dispose only confirmed idle caches, then run focused regression tests, `git diff --check`, and strict OpenSpec validation.

Rollback is a separate commit per phase: restore an archive from its manifest/original path, revert a cache batch only by regenerating cache, and revert policy/docs independently.  No runtime or strategy state is migrated.

## Open Questions

- Which owner can approve retention and recovery commitments for `outputs/`, `artifacts/`, `ML_Data/`, and `data/`?
- Does the installed OpenSpec CLI provide a configurable date-prefix naming rule, or must project policy adopt a compatible ID format while preserving ISO dates in metadata and tasks?
- Which explicit service seams best replace app-package namespace access after a dedicated characterization baseline is agreed?
