# Repository Hygiene Reference Map — 2026-09-21

Scope: current repository references were searched with `rg` across Python, strings, batch/CLI entrypoints, scheduler, Compose, CI, docs, tests, Web/LINE, agent configuration, and OpenSpec.  External/manual operator use cannot be proven absent and therefore blocks deletion.

| Candidate | Classification | Current evidence | Disposition |
|---|---|---|---|
| `4_run_backtest.py` | `LEGACY_COMPATIBILITY` | `AGENTS.md`, README, characterization and pipeline-contract tests | Retain |
| `5_push_to_line.py` | `LEGACY_COMPATIBILITY` | `AGENTS.md`, README, `core/news_agent.py`, pipeline-contract tests | Retain |
| `app.py` | `LEGACY_COMPATIBILITY` | Compose command, Windows launchers, README, Web/LINE tests | Retain |
| `config.py` | `LEGACY_COMPATIBILITY` | Runtime, research, jobs, services, and tests import its compatibility exports | Retain |
| `init_settings.py` | `DEVELOPMENT_TOOLING` | Documented direct database-bootstrap CLI; no safe external-use proof | Retain |
| `core/viz_helper.py` | `ACTIVE` | Imported by application flows; report helpers are called by Web and LINE paths | Retain |
| `core/report_helper.py` | `ACTIVE` | Application, MCP service, and tests use stock reports | Retain |
| `jobs/run_candidate_redundancy.py` | `UNKNOWN` | Recent D5 direct CLI whose core diagnostic dependency remains active; no complete external/operator absence proof | Retain |
| `jobs/run_engine_diagnostics.py` | `UNKNOWN` | Recent D6 direct CLI reads frozen research evidence and writes diagnostic output; no complete external/operator absence proof | Retain |
| `scripts/diagnose_strategies.py` | `DEVELOPMENT_TOOLING` | README/test reference; direct DB diagnostic CLI | Retain |
| `tmp_probe/` | `UNKNOWN` | Empty but prior retention inventory deferred active-run provenance | Retain |

Research re-exports including `core.research.market_data`, `core.research.fundamental_pit`, `core.research.target_weights`, and `core.research.universe` are active compatibility paths; callers and tests import the historical paths.  They are not duplicate deletion candidates.

No candidate has complete no-reference evidence.  Therefore this map authorizes no source, compatibility, non-cache, output, model, data, or unknown-file deletion.

Recovery: every retained path stays at its original location.  If a later candidate passes all evidence gates, update `docs/refactor/deletion_candidates.md` before a separately reviewed deletion change.
