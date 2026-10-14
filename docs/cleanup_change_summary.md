# Cleanup Change Summary — 2026-09-21

| Commit | Scope | Runtime impact |
|---|---|---|
| `f453fee` | Hygiene baseline, reference map, architecture assessment, deletion ledger | None |
| `e98ae3b` | Active OpenSpec visibility rule and proposal artifacts | None |
| `cfb0011` | Retention decision: no unproven output relocation | None |
| `ced07c2` | Keep active OpenSpec artifacts local/untracked to satisfy repository contract | None |

No application, strategy, backtest, scheduler, database, Web, LINE, dependency, model, data, artifact, or output file was modified or deleted.  The user worktree's modified `strategy_settings.json` and untracked diagnostics/operations files were preserved.

The attempted cache phase made no change because platform policy blocked native deletion.  It has no commit and remains pending in the OpenSpec task list.

## 2026-10-14 Runtime architecture simplification

| Commit | Scope | Verification |
|---|---|---|
| a4b8bcb | LINE composition prerequisite and precleanup contracts | Original suite 126 passed; broader baseline 283 passed |
| fcdf545 | Five native hash replacements/private dead helper | 202 passed; frozen identity unchanged |
| a314cba | Two shared records calculations in existing app module | 102 passed; exact precleanup outputs/cache contracts |
| Final acceptance commit | Isolated smoke, versioned evidence, local OpenSpec tracking convention | 365 related + 5 isolated tests passed |

The final import-only cleanup compresses the remaining LINE TextMessage import. No new library, class, package, config option or framework is introduced. Production source net diff is -30 lines; required tests and evidence are counted separately. Existing user modifications/settings/deletions remain outside these commits. No actual LINE message or broker order is sent, no existing service is restarted and no frozen artifact is rewritten.

Rollback: source changes in fcdf545 and a314cba revert independently in disposable clones, each followed by 19 passing contract tests. Shared tasks.md requires documentation-only reconciliation: retain other Phase states, reset only the reverted Phase. Active OpenSpec files must remain local/untracked; keep them on disk and resolve index conflicts by keeping the final local-only convention. Do not reset the original working tree or include the user's old pending deletions. Baseline/LINE prerequisite rollback intentionally restores the previously known LINE test failure and should not be used to claim a green system.
