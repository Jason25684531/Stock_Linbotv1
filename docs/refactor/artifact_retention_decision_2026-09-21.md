# Artifact Retention Decision — 2026-09-21

## Decision

No artifact archive batch is approved for this change.

## Evidence

- `outputs/` contained 82,421 files (3,194.44 MiB) and received new content on 2026-09-21 13:00.
- `artifacts/` contained 3,074 files (21,294.05 MiB), but size and age do not establish redundancy, reproducibility, ownership, or recovery.
- `ML_Data/` is `UNKNOWN_GENERATED`; `data/` is data/evidence; Fundamental OOS and validation outputs have documented runtime/research lineage.
- No owner-approved candidate supplied a dated command, input/output hashes, status, original/canonical location, retention period, or recovery command.

## Disposition

`outputs/`, `artifacts/`, `ML_Data/`, `data/`, and `tmp_probe/` remain at their original paths.  No manifest is created because no relocation is proposed.  This is the minimal safe result: an empty archive would imply a decision that evidence does not support.

## Future gate

A future batch may proceed only after a specific owner supplies all manifest fields required by `repository-hygiene-operations`.  It must be one reversible relocation commit, verify hashes before and after moving, and test the manifest recovery command.
