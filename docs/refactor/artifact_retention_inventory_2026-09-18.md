# Artifact Retention Inventory — 2026-09-18

Inventory only. No output was moved or deleted in this phase.

| Root | Observed material | Classification | Disposition |
|---|---:|---|---|
| `data/processed/` | tracked datasets/manifests | IMMUTABLE_EVIDENCE | retain; hash and version |
| `outputs/` | 13 capability roots; 82,300 files; ~3.34 GB | REPRODUCIBLE_OUTPUT | retain pending owner/run review |
| `artifacts/` | 3 roots; 3,072 files; ~22.33 GB | REVIEW_OR_CACHE | retain; separate reports from cache before archive |
| `ML_Data/` | 19 files; ~462 MB | UNKNOWN_GENERATED | retain until owner/rebuild proof exists |
| `tmp_probe/` | empty at inventory time | TEMPORARY | no removal while active-run use is unverified |
| `__pycache__/`, `.pytest_cache/`, `.coverage` | local interpreter/test cache | CACHE | disposable after active-run check |

Fundamental output roots are retained because the two active Fundamental
changes reference their evidence. Same-purpose output directories may be
archived later only with a dated manifest containing command, inputs, hashes,
status, and original location.

## Phase disposition

- Archive phase: no output was approved for movement. `outputs/`, `artifacts/`,
  and `ML_Data/` remain in place because owner/rebuild evidence is incomplete.
- Cleanup phase: after the Compose smoke run was stopped, the disposable root
  caches `.pytest_cache/` and `__pycache__/` were removed. Python virtualenv
  caches and all nested project/source caches were retained; no source,
  evidence, output, or unknown material was deleted.
