# Repository Hygiene Baseline — 2026-09-21

This is a pre-mutation snapshot for `repository-hygiene-and-safe-cleanup-2026-09-21`.  It contains no secrets and records current evidence rather than a deletion decision.

## Working-tree protection

`git status --short --untracked-files=all` recorded a modified `strategy_settings.json` and untracked diagnostics, operations, and documentation files.  They are pre-existing user work for this change and are excluded from every cleanup action.

## Tracked inventory

`git ls-files` reported 402 tracked files totalling 24,863,301 bytes.  The canonical runtime roots are `app/`, `core/`, `jobs/`, `services/`, `config/`, `execution/`, and `docker-compose.yaml`; `jobs/scheduler.py` remains the documented daily owner.

## Large and generated roots

| Root | Files | Size | Current disposition |
|---|---:|---:|---|
| `artifacts/` | 3,074 | 21,294.05 MiB | `REVIEW_OR_CACHE`; retain pending manifest and owner/rebuild evidence |
| `outputs/` | 82,421 | 3,194.44 MiB | `REPRODUCIBLE_OUTPUT`; retain; newest content was 2026-09-21 13:00 so it is not an inactive archive candidate |
| `myenv/` | 62,164 | 2,650.88 MiB | environment; out of scope |
| `ML_Data/` | 19 | 441.02 MiB | `UNKNOWN_GENERATED`; retain |
| `data/` | 15 | 272.49 MiB | data/evidence; retain |
| `tmp_probe/` | 0 | 0 MiB | `UNKNOWN`; retain until its active-run provenance is resolved |

The contents of generated roots are ignored by repository policy where applicable.  A root directory itself need not match `git check-ignore`; `git status --ignored` confirms ignored descendants.  No output, artifact, model, data, or unknown root is approved for movement or deletion by this baseline.

## Cache candidates

The disposable candidates are `.pytest_cache/` and `__pycache__/` directories below `app/`, `config/`, `core/`, `jobs/`, `scripts/`, `services/`, and `test/`.  `myenv/**` is explicitly excluded.  No project Python, cmd, or PowerShell process was running when the process check completed.

## Commands and recovery

Evidence commands: `git status --short --untracked-files=all`, `git ls-files`, `git status --ignored --short`, `git check-ignore -v <path>`, path-specific file/size counts, and project-process inspection.  This report is documentation only; reverting its dedicated commit restores the prior state.
