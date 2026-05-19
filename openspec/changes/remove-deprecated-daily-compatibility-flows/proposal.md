## Why

`consolidate-daily-data-backtest-pipeline` already finished the runtime consolidation work: the official scheduler path is clear, price provenance and `pipeline_runs` observability are in place, lightweight daily validation is optional and bounded, and legacy launchers now carry non-breaking deprecation markers. That makes cleanup deletion safe to consider for the first time, but only as a separate evidence-based change after deprecation and inventory work already exist.

This change is intentionally separate from runtime consolidation because deletion has a different risk profile. The goal here is not to redesign the daily pipeline. It is to prove which deprecated compatibility launchers can be removed without breaking code references, operator workflows, docs, tests, Compose topology, or scheduler ownership.

## What Changes

- Add a proposal-only OpenSpec package for deleting deprecated daily compatibility flows only after evidence review passes.
- Use `docs/cleanup_inventory.md` as the primary evidence source for candidate selection and verification.
- Define the verification gates each deletion candidate must pass: no imports, no CLI references, no README or docs references, no docker-compose references, no scheduler references, no test dependencies, no active OpenSpec references beyond historical notes, and no user-facing documented workflow.
- Define a staged deletion plan that preserves `jobs/scheduler.py` as the only official daily entrypoint and preserves the official flow `update_database -> run_daily -> daily_backtest_validation -> push_to_line`.
- Define fallback and recovery guidance in case a removed compatibility launcher is still needed after cleanup.
- Keep this change out of runtime consolidation scope: no scheduler logic changes, no DB or model-path contract changes, no price provenance changes, no `pipeline_runs` changes, and no daily backtest validation behavior changes.

## Capabilities

### New Capabilities
- `cleanup-removal`: evidence-based removal requirements for deprecated compatibility launchers and wrappers after inventory and deprecation are complete
- `scheduler-contract`: guardrails that preserve `jobs/scheduler.py` as the only official daily entrypoint and preserve the current daily flow during cleanup
- `docs-and-tests`: documentation and contract-test requirements that must be aligned before and after any compatibility-flow deletion

### Modified Capabilities
- None.

## Impact

- Affected systems: OpenSpec change artifacts, `docs/cleanup_inventory.md`, legacy launcher and wrapper files, README/docs references, and contract tests that guard scheduler ownership and cleanup safety.
- Unaffected systems: runtime scheduler logic, database configuration, model-path configuration, price provenance, `pipeline_runs`, recommendation persistence, and lightweight daily backtest validation behavior.
- Operational impact: future cleanup work becomes reviewable and reversible because deletions must be backed by evidence, tests, and documented fallback guidance before implementation.
