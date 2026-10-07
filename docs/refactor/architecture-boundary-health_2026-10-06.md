# Architecture Boundary Health — 2026-10-06

## Scope and working-tree safety

This report records the pre-implementation evidence for
`change-2026-10-06-architecture-simplification-readme-alignment`. The working
tree already contained Strategy Platform stabilization edits in `app/`,
`core/`, `jobs/`, tests, and dated refactor reports. Those paths are retained;
this change does not reset, clean, or overwrite them.

The OpenSpec proposal/design/tasks and four delta specs were read before this
report was written. No application source, dependency manifest, strategy
settings, or research evidence was changed during baseline collection.

## Current ownership evidence

| Boundary | Current owner | Evidence |
|---|---|---|
| Web and LINE composition | `app/__init__.py` plus `app/web_server.py`, `app/line_bot.py`, `app/line_flows.py` | `app` import and each submodule fresh-import successfully under Python 3.11.9. |
| Strategy identity | `core/strategy/registry.py` through the compatibility surface in `core/strategy_manager.py` | `StrategyRegistry`, `StrategyRunner`, and `StrategyManager` import and instantiate by contract. |
| Strategy execution | `core/strategy/runner.py` and `core/strategy/adapters/legacy.py` | Existing Strategy Platform tests and stabilization evidence route Daily/Backtest callers through the runner while retaining rollback seams. |
| Fundamental runtime | `core/runtime/fundamental_*` and `core/runtime/fundamental_adapter.py` | Existing integration tests and stabilization evidence preserve frozen identity, fail-closed eligibility, and disabled broker submission. |
| Scheduler | `jobs/scheduler.py` | Existing scheduler characterization contract; no second scheduler owner found. |
| Runtime services | `docker-compose.yaml`, `requirements.runtime.txt`, `requirements.mcp.txt` | Compose references both manifests from a clean checkout and keeps credentials external. |
| Compatibility | `app.py`, `config.py`, `4_run_backtest.py`, `5_push_to_line.py`, flat research re-exports, legacy strategy aliases | Existing reference audit classifies these as KEEP/compatibility surfaces. |

## Boundary risks before implementation

1. The application modules previously accessed the package namespace through
   `app_pkg` or `sys.modules[__package__]`. This was an explicit
   dependency-direction violation and made import order part of the runtime
   contract.
2. `app/__init__.py` constructs the global Flask app and StrategyManager at
   import time. The compatibility-preserving `create_app()` seam must keep the
   existing `from app import app` and `python app.py` paths.
3. Responsibility is concentrated in `core/db_helper.py` (2,625 lines),
   `core/mcp_client.py` (2,004), `app/dashboard_payloads.py` (1,628),
   `core/backtest/runner.py` (1,537), `services/mcp/server.py` (1,342),
   `app/web_server.py` (1,143), and `jobs/run_daily.py` (1,069). Size alone is
   not deletion evidence; only a characterized cohesive seam is eligible for
   extraction.
4. `README.md` describes an older StrategyManager/V31–V38 architecture and
   claims Python 3.8+, while the Compose runtime is Python 3.11 and current
   source uses modern type syntax. The active operator contract is therefore
   stale even though historical reports remain useful.
5. `requirements.txt` contains 196 entries and mixes direct and transitive
   packages. Static import evidence is absent for several heavyweight
   candidates, but dynamic, operator, and tooling consumers still require a
   complete audit before removal.

## Ponytail-ranked simplification candidates

| Rank | Candidate | Replacement / decision | Evidence gate |
|---|---|---|---|
| 1 | `README.md` historical/current content overlap | Current architecture and supported commands with links to historical reports | Strict UTF-8, command/link, Compose, and operator workflow checks |
| 2 | `requirements.txt` broad freeze | Direct development/research manifest plus generated constraints/lock | Static, dynamic, script, test, CLI, docs, fresh install and collection checks |
| 3 | `app` package namespace lookup | Direct imports, explicit parameters, or one cohesive context wired at composition | Fresh-import, Web, LINE, payload, and monkeypatch characterization |
| 4 | Dashboard payload concentration | Existing health/macro/war-room/serialization/MCP seams; retain facade | Complete payload and MCP-fallback equivalence |
| 5 | Daily orchestration concentration | DEFER unless one stage has a stable input/output and side-effect boundary | Exact recommendation and persistence parity |

Necessary complexity is not counted as immediately removable: StrategyManager,
Registry/Runner, legacy adapters, alias modules, root launchers, research
re-exports, scheduler ownership, and Fundamental gates all have current
compatibility or safety evidence.

## Baseline commands and results

- `python -c "import app; ..."`: PASS; `app` imports and exposes both the
  legacy global `app` and `create_app()`.
- Fresh imports of `app.web_server`, `app.line_bot`, `app.line_flows`,
  `app.dashboard_payloads`, and `app.news_overlay`: PASS individually under
  Python 3.11.9. Import-time strategy initialization and existing requests
  dependency warning were observed; they are pre-existing behavior.
- `python -c "from core.strategy.registry import StrategyRegistry; ..."`:
  PASS; Registry, Runner, and StrategyManager remain importable.
- Focused pytest command covering Web, LINE, dashboard, daily, CLI,
  scheduler, compatibility, Strategy Platform, and Fundamental tests collected
  150 tests. The process was stopped after exceeding the command observation
  window before it emitted a final result; this is recorded as incomplete, not
  as a pass.
- Existing `docs/refactor/strategy-platform-stabilization-2026-10-06.md`
  records a prior green focused result (`265 passed`) and full result
  (`897 passed, 1 skipped, 1 xfailed`) for the Strategy Platform work already
  present in the working tree. It is historical evidence, not a substitute for
  the post-change run.
- `git diff --check`: PASS with existing LF/CRLF conversion notices only.

## Integrity checks

Pre-change SHA-256 values captured for the implementation session:

- `strategy_settings.json`: `4B7DCB6AC324EDFFFFC92A4120404710B863935183E76F0C8B6C2C3D9D713617`
- `data/processed/fundamental_pit_v3/fundamental_manifest.json`: `E198F5E1259BCCC8A73EEE2D2A1055880A219A227CAAF22135EF0280860E4EE6`
- `data/processed/fundamental_pit_v3/reproducibility.json`: `BCABA9DFDF2FD37168AFA6123482378DA96DAAFCA89ABD2FD88B2ECA22E4AB3A`

Post-change verification must recalculate these hashes and confirm no semantic
mutation. Any affected floating-point comparison uses `rtol=1e-9` and
`atol=1e-12`.

## Decision

Proceed only with small, independently revertible phases. Keep all current
compatibility facades and rollback seams unless a future complete
`SAFE_TO_REMOVE` record proves otherwise. If an explicit app composition seam
cannot be introduced without changing observable contracts, record DEFER and
leave the runtime boundary unchanged rather than adding a framework.

## Post-implementation evidence (2026-10-07)

### Completed simplifications

| Area | Change | Evidence |
|---|---|---|
| Application boundary | Replaced package lookups with direct module imports plus one small `_ApplicationServices` composition proxy. The proxy preserves existing tests that patch symbols on `app`. | `python -m compileall -q app`; fresh imports of all five application submodules; `rg` finds no `app_pkg`, `sys.modules[__package__]`, or `import app as` in `app/`. |
| Stable factory | Added `create_app()` while retaining the global Flask app and root launcher. | `import app; hasattr(app, 'create_app')` passed; CLI and scheduler characterization tests passed. |
| Documentation | Replaced the stale V38/phase-centric README with current ownership, Registry/Runner/Manager, Fundamental isolation, manifests, compatibility, and verification guidance. | Strict UTF-8 decode and Markdown link/heading checks passed. |

### Focused validation

- Strategy/Backtest/Fundamental: `37 passed`.
- CLI/legacy launcher/scheduler: `11 passed`.
- Application compilation and independent imports: passed.
- Dashboard health-check tests pass for deterministic cases; the macro
  fallback case remains environment-dependent when the external news/MCP
  service is unavailable and timed out under the 10-second guard. It is not
  reported as a green full-suite result.
- Full `python -m pytest -q --timeout=10` collection reached `898 items / 1
  skipped` and was stopped by the timeout in the live-DB daily selection smoke
  (`test/characterization/test_daily_selection_regression.py`). Focused tests
  therefore remain the authoritative post-change evidence for this worktree.
- `git diff --check`: passed (only existing LF/CRLF conversion notices).

### Deferred or evidence-gated work

- Dashboard file splitting is DEFERRED. The existing health, macro, war-room,
  serialization, overlay, and local-fallback functions already form stable
  seams, but moving them into new modules would expand the diff without a
  payload-equivalence baseline that is independent of the current external
  service. Keep `app/dashboard_payloads.py` as the compatibility facade.
- `jobs/run_daily.py` extraction is DEFERRED. Its stages share persistence,
  fallback, and exception side effects; no single stage currently has a
  sufficiently narrow contract to extract without a new pipeline abstraction.
- Heavyweight packages without static imports remain `UNKNOWN`/KEEP until
  dynamic, script, notebook, operator, and clean-install evidence is complete.
  No dependency or non-cache source was removed.

### Integrity and safety

The implementation does not rewrite strategy keys or immutable research
artifacts. Recalculate and compare the three baseline SHA-256 values before
archiving this change. Fundamental broker submission remains disabled and all
phase work remains revertible by file-level change review; no destructive
cleanup or reset was performed.

Post-change SHA-256 comparison (case-insensitive) on 2026-10-07:

```text
strategy_settings.json                                      4B7DCB6AC324EDFFFFC92A4120404710B863935183E76F0C8B6C2C3D9D713617
data/processed/fundamental_pit_v3/fundamental_manifest.json E198F5E1259BCCC8A73EEE2D2A1055880A219A227CAAF22135EF0280860E4EE6
data/processed/fundamental_pit_v3/reproducibility.json     BCABA9DFDF2FD37168AFA6123482378DA96DAAFCA89ABD2FD88B2ECA22E4AB3A
```

All three values match the pre-change capture.
