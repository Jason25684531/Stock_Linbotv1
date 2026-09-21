## 1. [2026-09-21] Evidence baseline and classification

- [x] 1.1 [2026-09-21] Capture the immutable repository hygiene baseline before mutation.
  - Goal: record Git state, tracked/ignored inventory, root sizes, active-run/process evidence, and current cache paths.
  - Scope: add a dated report under `docs/refactor/`; do not alter runtime code, generated roots, or user worktree changes.
  - Expected files: `docs/refactor/repository_hygiene_baseline_2026-09-21.md`.
  - Acceptance: the report differentiates cache, generated artifact, data/model, compatibility, and unknown roots and records commands/results without secrets.
  - Verification: `git status --short`, `git ls-files`, `git check-ignore -v <target>`, and path-specific size/count commands.
  - Risk / rollback: stale baseline → stop before mutation and recapture; documentation-only commit is independently revertible.

- [x] 1.2 [2026-09-21] Refresh the complete file-reference matrix and deletion-candidate ledger.
  - Goal: classify every proposed non-cache candidate using imports, dynamic strings, CLI/batch, scheduler, Compose, CI, docs, tests, Web/LINE, agent configuration, and OpenSpec evidence.
  - Scope: update the dated inventory/reference evidence and `docs/refactor/deletion_candidates.md`; preserve every referenced or unresolved candidate.
  - Expected files: `docs/refactor/repository_hygiene_reference_map_2026-09-21.md`, `docs/refactor/deletion_candidates.md`.
  - Acceptance: each candidate has exactly one disposition plus reason, impact, validation, and recovery; no source deletion is approved from incomplete evidence.
  - Verification: targeted `rg` searches for every recorded candidate and `git diff --check`.
  - Risk / rollback: dynamic/external use omitted → mark `UNKNOWN`; revert the documentation-only phase if the evidence is inaccurate.

- [x] 1.3 [2026-09-21] Publish the architecture-boundary health report.
  - Goal: document canonical runtime ownership, dependency direction, concentration, compatibility surfaces, test evidence, strengths, and risks.
  - Scope: report current code evidence only; do not refactor `app/`, strategy, backtest, scheduler, or database code.
  - Expected files: `docs/refactor/architecture_boundary_health_2026-09-21.md`.
  - Acceptance: the report records app-package namespace coupling separately from proven dead code and names the smallest safe follow-up seam.
  - Verification: import/reference searches, Compose and scheduler inspection, focused test collection, and `git diff --check`.
  - Risk / rollback: historical docs mistaken for proof → cite current source locations and revert the report for correction.

## 2. [2026-09-21] OpenSpec reviewability governance

- [x] 2.1 [2026-09-21] Make future active OpenSpec change artifacts visible to normal Git review and record the naming-policy conflict.
  - Goal: unignore active change contents without exposing generated/archive state; document the installed CLI rejection of date-prefixed identifiers and its approved resolution path.
  - Scope: `.gitignore` and governance documentation only; do not force-add, rename historical changes, or modify application code.
  - Expected files: `.gitignore`, `docs/refactor/openspec_change_governance_2026-09-21.md`.
  - Acceptance: a newly created active change appears in ordinary `git status`, while generated/archive OpenSpec material remains ignored; the ISO creation date remains recorded.
  - Verification: `git check-ignore -v` for active and archived paths, `git status --short --untracked-files=all`, and `openspec status --change <id>`.
  - Risk / rollback: local generated history becomes visible → narrow the unignore pattern and revert this independent governance commit.

## 3. [2026-09-21] Manifest-first artifact archival

- [ ] 3.1 [2026-09-21] Obtain retention-owner decisions and prepare candidate archive manifests without moving files.
  - Goal: prove whether any same-purpose outputs are redundant, reproducible, inactive, and owner-approved.
  - Scope: `outputs/` and `artifacts/` candidates only; `data/`, `ML_Data/`, Fundamental OOS evidence, unknown material, and active runs are non-goals.
  - Expected files: dated manifest drafts beneath the documented archive location and an updated retention inventory.
  - Acceptance: every proposed move has command, input/output hashes, owner, status, original/canonical location, retention, and recovery command; lack of any field blocks movement.
  - Verification: hash comparison, manifest schema review, and no changed source/runtime files.
  - Risk / rollback: misclassified evidence → retain in place; manifest-only commit is independently revertible.

- [ ] 3.2 [2026-09-21] Archive one approved artifact batch per reversible commit.
  - Goal: relocate only material approved by task 3.1 while preserving a recoverable provenance chain.
  - Scope: one manifest-defined batch at a time; no delete operation and no mutation of canonical runtime inputs.
  - Expected files: the validated manifest, its archive location, and retention inventory update.
  - Acceptance: hash verification succeeds before and after relocation, the recovery command is tested, and the original location is recorded.
  - Verification: manifest hash check, recovery dry run, targeted consumer command, `git diff --check`.
  - Risk / rollback: consumer expects original location → restore from the manifest in the same phase; do not proceed to another batch.

## 4. [2026-09-21] Narrow cache cleanup

- [ ] 4.1 [2026-09-21] Remove only confirmed idle repository caches in a dedicated cleanup commit.
  - Goal: dispose of project source/test caches without touching environments, data, model, output, evidence, or unknown roots.
  - Scope: exact confirmed paths among `.pytest_cache/`, `.coverage`, `htmlcov/`, `.mypy_cache/`, `.ruff_cache/`, and `__pycache__/` below source/test paths; exclude `myenv/**` and all unresolved broad roots.
  - Expected files: dated cache-cleanup evidence report and `.gitignore` update only if a newly discovered cache is not already ignored.
  - Acceptance: active-run check completes, every target path/count is recorded, and no tracked or non-cache path is removed.
  - Verification: `git ls-files --error-unmatch <target>` safeguards, `git status --ignored --short`, focused test run, and `git diff --check`.
  - Risk / rollback: active interpreter uses a target → defer; caches regenerate naturally, while an accidental non-cache deletion requires immediate restore from Git/recovery evidence.

## 5. [2026-09-21] Validation and handoff

- [ ] 5.1 [2026-09-21] Validate each completed phase and publish the cleanup handoff.
  - Goal: prove behavior, compatibility, artifact provenance, and governance remain intact; distinguish completed work from deferred risks.
  - Scope: validation reports and task checkboxes; no feature or refactor work.
  - Expected files: `docs/cleanup_audit_report.md`, `docs/cleanup_change_summary.md`, `docs/cleanup_validation_report.md`, and updated task evidence.
  - Acceptance: reports contain per-phase commits, commands/results, deferred candidates, recovery paths, and explicit preservation of CLI/Web/LINE/scheduler/backtest contracts.
  - Verification: relevant `pytest -q -p no:cacheprovider`, characterization tests before any boundary refactor, `git diff --check`, `openspec validate repository-hygiene-and-safe-cleanup-2026-09-21 --strict`, and normal Git-status visibility checks.
  - Risk / rollback: an incomplete gate is reported as deferred, never converted into a passing result; revert only the affected independently verifiable phase.
