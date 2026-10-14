# Cleanup Audit Report — 2026-09-21

## Completed evidence work

- Captured the tracked/ignored inventory, output activity, cache scope, process state, and user-worktree protection in `docs/refactor/repository_hygiene_baseline_2026-09-21.md`.
- Refreshed the candidate reference map and deletion ledger.  No source or non-cache path has complete no-reference evidence, so no such path is deleted.
- Published the architecture-boundary assessment.  It confirms canonical runtime ownership and identifies app-package namespace coupling as a separately scoped refactor risk.
- Restored the repository's local-OpenSpec convention: active artifacts are visible in `git status` but absent from `git ls-files`; archived material remains ignored.
- Evaluated `outputs/`, `artifacts/`, `ML_Data/`, `data/`, and `tmp_probe/`.  No archive batch has owner, reproducibility, inactivity, and manifest evidence, so all remain in place.

## Deferred / blocked

- Cache disposal was safety-checked for 37 untracked project cache directories (496 files, 8,137,929 bytes), excluding `myenv/**`.  The execution environment rejected the exact native `Remove-Item` command.  No alternate deletion mechanism was attempted.
- No source, compatibility facade, model, data, output, artifact, or unknown path is approved for deletion.
- App package namespace coupling is not cleanup work and requires a future characterization-protected refactor change.

## Recovery

All retained material remains at its original location.  Documentation and governance commits are independently revertible.  Cache cleanup can be retried only through an approved environment that permits the already-recorded exact target list and repeats the active-process/tracked-file safeguards.

## 2026-10-14 Runtime architecture simplification

Change: `2026-10-14-simplify-runtime-architecture-safely` (local OpenSpec artifacts). Full original audit remains in that change's audit.md; reproducible source/fixture/settings checksums and the current decisions are versioned under [architecture_cleanup_2026-10-14](refactor/architecture_cleanup_2026-10-14/).

- Completed: fix the existing shared LINE reply composition contract; replace five duplicated hash loops with hashlib.file_digest; remove the evidence-proven private _freeze_mapping section; share two existing Dashboard/LINE records computations without moving I/O, dates, caching, formatting or errors.
- DEFER: canonical PIT hash helper records self code_sha256; retain that source and existing evidence identity. Large DB/Daily/Dashboard splits and the duplicate F2/V3 record serializer remain deferred.
- KEEP: StrategyManager/Registry/Runner/adapter, public wrappers, v3x aliases, research import shims, lazy V30 proxy, immutable contracts, the sole scheduler and existing production/broker gates.
- UNKNOWN: heavyweight dependency removal; no package is removed or upgraded. No source/data/model/non-cache file is deleted.
- Current evidence: production source diff is 28 inserted/58 deleted lines, net -30. This is the actual diff against f2babcb, excluding tests/docs and unrelated user changes; not a benchmark-derived savings estimate.
- Repository governance: only this change's Git tracking is removed (files retained locally). The 11 previously tracked/deleted 2026-10-13 OpenSpec files belong to the initial user worktree and are neither staged nor committed here. The old global tracking test therefore remains a documented pre-existing failure.
