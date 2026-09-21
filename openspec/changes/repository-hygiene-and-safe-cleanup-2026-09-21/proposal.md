## Why

The repository has a well-defined canonical runtime path, but its cleanup evidence is stale relative to the current source tree and large ignored output roots.  Deleting or moving material without refreshed provenance would risk breaking legacy CLI, scheduler, Web, LINE Bot, research, and Fundamental OOS evidence.  In addition, newly created OpenSpec changes are ignored by Git, defeating the project's reviewability requirement.

## What Changes

- Establish a current, reproducible hygiene ledger that classifies tracked files, ignored roots, cache, generated artifacts, compatibility surfaces, and unproven candidates before any removal.
- Archive only reproducible, same-purpose material that has an owner-approved retention decision and a dated manifest containing command, inputs, hashes, status, original location, and recovery method.  Keep unknown, model, data, and active-run material in place.
- Permit removal only for confirmed disposable caches after an active-run check; record every source or non-cache deletion candidate with complete no-reference evidence.  A candidate that retains any reference remains preserved.
- Make active OpenSpec change artifacts visible to normal Git status without force-add, and document the CLI/date-name incompatibility rather than silently bypassing it.
- Produce an architecture-boundary health report.  It will distinguish confirmed strengths from coupling risks, including the app package namespace/service-locator dependency pattern, and will create a separately scoped follow-up only if a behavior-preserving refactor is justified.
- Preserve all public and compatibility surfaces, including `4_run_backtest.py`, `5_push_to_line.py`, strategy aliases, `app.py`, `config.py`, Web, LINE Bot, and `jobs/scheduler.py`.

## Capabilities

### New Capabilities

- `repository-hygiene-operations`: Evidence-led classification, manifest-first archival, safe cache disposal, and Git-reviewable OpenSpec change handling.
- `architecture-boundary-health`: Repeatable assessment and reporting of runtime dependency boundaries, module concentration, compatibility exposure, and required follow-up scope.

### Modified Capabilities

- None.

## Impact

- Expected implementation touches: `.gitignore`, `docs/refactor/` inventories and reports, `artifacts/README.md` only if its retention guidance needs alignment, and OpenSpec change artifacts.
- Possible deletions are limited to confirmed cache paths.  No source, compatibility entrypoint, data, model, output, dependency, scheduler, database, strategy, or API behavior is changed by this proposal.
- The audit will inspect `app/`, `core/`, `jobs/`, `services/`, `scripts/`, `execution/`, Compose, tests, documentation, and existing OpenSpec specs.  Any refactor of the app boundary is explicitly deferred to a separate approved change.
