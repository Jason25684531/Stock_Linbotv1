# 2026-10-19 Repository architecture and hygiene audit

## Scope and evidence

Baseline e22d1a4, clean working tree. Inspected all tracked path categories and
parsed all 356 Python files (66,758 lines) under app/core/jobs/services/scripts/test.
The tracked inventory has 536 paths, including compatibility entrypoints,
deployment, templates, tests, retained source examples and historical artifacts.
Hidden/no-ignore reference searches include ignored documentation and OpenSpec.
AST and pyflakes are candidate detectors, not automatic deletion authorities.
No uniquely referenced private top-level function without decorators was found
in the current production-source scan; this does not prove absence of all dead
code or dynamic consumers. No application source is removed on linter evidence.

## Architecture assessment

| Principle | Concrete evidence | Assessment and limitation |
|---|---|---|
| Inheritance | `core/strategies/base.py` ABC, concrete strategies in `core/strategies/`, factory/legacy parity tests | Shared strategy contract has many real implementations. Preserve aliases and shared behavior. More inheritance is not needed. |
| Polymorphism | `core/strategy/contracts.py:StrategyExecutor`, `registry.py`, `runner.py:execute`; `test/unit/strategy/test_strategy_extensibility_proof.py` | Registered executors dispatch without family-specific runner branches; test adds a new family and alias without caller changes. Retain the registry/factory. |
| Encapsulation | `StrategySpec` recursively freezes containers; registry private records/aliases; settings isolation tests | Domain definitions are protected. StrategyContext freezes top-level mappings only and may carry mutable data; `app.create_app()` returns shared state. Do not claim deep immutability everywhere. |
| Reliability | Pipeline run-state persistence, MCP fallback, bounded HTTP health checks; runtime production gates and persistence guards | Existing tests cover error/fallback paths. Live service checks supplement tests; they do not prove upstream market availability or actual LINE delivery. |
| Maintainability | app/core/jobs/services boundaries, dependency boundary test, compatibility facades | Ownership is identifiable, but db_helper (2,702 lines), mcp_client (2,004), dashboard_payloads (1,648), backtest runner (1,537), web_server (1,495) remain concentrated. Splitting them requires a separate characterized contract change. |
| Extensibility | Registry aliases, immutable metadata, execution protocol, canonical TQuant strategies, extension proof | Supports adding strategies without editing runner branches. Production data/strategy capability and LINE menu limits still require explicit integration. |

The strategy domain has defensible boundaries. The entire application is not
certified as fully compliant with every architecture ideal. Existing historical
`application-structure` requirements for fully isolated factories, acyclic app
composition and dashboard domain extraction were broader than the current
implementation. This change corrects the factory expectation based on code;
other migration aspirations remain follow-up, not falsely marked implemented.

## Ponytail audit (ranked, pre-cleanup)

delete: unreachable 57-line login form and 2-line test exemption. Nothing; keep the existing redirect routes. [templates/login.html; test/test_web_ui_conventions.py]

delete: unused Flask-Login direct dependency in two manifests. Existing Flask public-dashboard composition. [requirements.runtime.txt; requirements.txt]

net: -61 lines, -1 deps possible.

This is a concrete candidate-size estimate, not a ponytail-gain savings claim.
No speculative abstraction removal is warranted: strategy factories have many
products; flat research modules, root scripts, app exports and aliases are
compatibility surfaces. Unused import warnings in facades are intentionally
not treated as dead code. Large dependency sets include research/notebook/tool
consumers and need reproducible clean-environment proof before further pruning.

## Ponytail debt (read-only ledger)

core/strategies/_tquant.py:17, one as-of date cached per worker. ceiling: one date. upgrade: clear after same-date data refresh.

execution/restart_services.bat:29, reinstall dependencies on container startup. ceiling: health wait up to 20 minutes. upgrade: prebuild an image to shorten startup. no-trigger: no explicit threshold for doing the image work.

jobs/update_database.py:80, fixed data lookback. ceiling: older gaps are outside the window. upgrade: use jobs/backfill_pipeline.py for older gaps.

jobs/update_database.py:419, successful empty response treated as a holiday. ceiling: no authoritative holiday confirmation. upgrade: confirm against an authoritative Taiwan calendar when available.

scripts/ci_check_integration_marker_coverage.py:3, file-level mitigation heuristic. ceiling: flags only files with zero mitigation signals. upgrade: widen when an unmarked, unmocked MySQL-dependent test slips through. Multiline continuation through line 8 included; the marker is in the script's explanatory docstring.

5 markers, 1 with no trigger.

## Retention and cleanup decisions

- REMOVE only the evidence-approved login template, its dead exemption and unused manifest pins. Full evidence is in `deletion_candidates.md`.
- KEEP `4_run_backtest.py`, `5_push_to_line.py`, app/config facades, `scripts/twse_mcp_server.py`, research forwarding modules and old strategy keys. Callers, tests or supported operator commands depend on them.
- KEEP data/ML_Data/outputs/artifacts and notebook examples. Hashes, manifest lineage, research reproducibility and operator use preclude blind deletion.
- KEEP historic docs/specs and archives. They explain migrations; stale statements are not executable dependencies.
- CACHE directories are reproducible but active tests/services can use them; no sweeping cache deletion while processes are running.
- ARCHIVE this completed OpenSpec change locally after verification, preserving proposal/design/specs/tasks and links to durable verification evidence.

## Benchmark disclosure

Ponytail-gain publishes medians for 5 everyday tasks and 3 models: code 6–20%
of baseline (80–94% less), cost 23–53% (47–77% less), speed 3–6 times faster.
These are the skill's supplied external benchmarks, not measurements from this
repository; this audit has not independently rerun those benchmarks.

## Tool constraints

RTK is not installed on PATH; native commands used under the documented debug
exception. OpenSpec 1.3.0 `new change`/`status` reject date-prefixed names.
The installed official spec-driven schema and templates were read directly to
create the date-prefixed artifacts required by AGENTS. Native strict validation
accepts the change and remains the validation gate; workflow CLI status must
not be misreported as passing.

## Verification

See `repository-verification-2026-10-19.md` for baseline/post-change counts,
restart/browser evidence, spec mapping, remaining limitations and commits.
