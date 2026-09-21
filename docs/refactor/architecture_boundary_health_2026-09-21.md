# Architecture Boundary Health — 2026-09-21

## Evidence inspected

- Runtime ownership: `docker-compose.yaml`, `app.py`, `jobs/scheduler.py`, `execution/daily_run.bat`, and README runtime commands.
- Dependency evidence: current Python imports and package-namespace lookups in `app/`.
- Compatibility evidence: root launchers, legacy strategy paths, README, and characterization/pipeline tests.
- Verification evidence: test collection reports 779 tests; the legacy CLI, daily-pipeline, LINE-interaction, and backtest-persistence regression subset passed 18 tests on 2026-09-21.

## Confirmed strengths

- The official daily owner is explicit: `jobs/scheduler.py`, with `execution/daily_run.bat` retained as an operator wrapper.
- Compose isolates MySQL, MCP, and Web/LINE runtime processes; runtime manifests are split between app and MCP service dependencies.
- The recommendation persistence contract is reused by scheduler, Web, and LINE paths; root launchers and historical research paths are thin compatibility surfaces rather than a second runtime.
- Research namespace migrations preserve historical imports through small re-export shims and active tests.

## Concentration and boundary risks

| Evidence | Risk | Disposition |
|---|---|---|
| `core/db_helper.py` (2,344 lines, 64 top-level definitions), `core/mcp_client.py` (1,819), `core/backtest/runner.py` (1,371), `app/web_server.py` (1,000), `app/dashboard_payloads.py` (1,465) | Concentrated responsibilities increase change coupling | Do not mechanically split; isolate only a proven cohesive seam in a future change |
| `app/web_server.py`, `app/dashboard_payloads.py`, `app/line_bot.py`, `app/line_flows.py`, and `app/news_overlay.py` access the app package namespace (`sys.modules[__package__]` or `import app as app_pkg`) | Conflicts with the existing no-service-locator / one-way dependency requirement; import order and hidden dependencies are harder to test | Not dead code and not cleanup; create a dedicated behavior-preserving app-boundary change only after CLI/Web/LINE characterization baselines |
| `outputs/` received files on 2026-09-21 | Active or recently active research evidence cannot be archived safely | Retain pending owner-approved manifest |

## Assessment

The architecture is feasible and operationally reliable for its current scope: runtime ownership, persistence, compatibility paths, and regression coverage are explicit.  It is expandable if new work uses existing canonical packages and avoids adding new facades.  The practical limiting risk is hidden app-package dependency access, not proven dead code.  The smallest justified future seam is explicit dependency passing for one app subdomain at a time, protected first by characterization tests; this hygiene change makes no runtime refactor.

Recovery: this report is documentation only and can be reverted independently.
