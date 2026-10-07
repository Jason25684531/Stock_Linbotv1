# Strategy Platform Compatibility Reference Audit

Audit performed after the green post-C4 baseline and before any cleanup. The
search covered Python imports, `importlib`, `getattr`, Registry/factory paths,
CLI, scheduler, Web, LINE, tests/monkeypatches, documentation, operator paths,
and historical public-by-use references. A single grep result was not treated as
proof of dead code.

## Evidence sources

- `rg` over `core`, `app`, `jobs`, root CLI wrappers, `test`, `docs`, `README*`,
  `.github`, and deployment/operator files.
- Existing maps: `docs/refactor/file_reference_map.md`,
  `docs/refactor/legacy_compatibility_report.md`,
  `docs/refactor/production_integration_inventory.md`, and
  `docs/refactor/strategy-platform-architecture-discovery-2026-10-05.md`.
- Dynamic loading evidence: `core/strategy_manager.py` uses `importlib` and
  `getattr`; `core/strategy/adapters/legacy.py` lazily loads canonical factories;
  C4 callers resolve through `StrategyRegistry`/`StrategyRunner`.
- Runtime evidence: focused and full suites above, including CLI, scheduler,
  Web/LINE, parity, and rollback characterization tests.

## Candidate decisions

| Candidate | Current responsibility | Static/runtime references | CLI / scheduler / Web / LINE / tests | Docs/operator / external risk | Replacement | Rollback impact | Decision |
|---|---|---|---|---|---|---|---|
| `core/strategy_manager.py` `StrategyManager` | Settings persistence, active/persistence/random pool, cache/lazy factories, warnings/errors, legacy objects, runner facade, Fundamental isolation | Imported throughout app/core/jobs/tests; dynamic `importlib` factory; `get_strategy_runner` builds platform seam | Daily, Backtest, Web, LINE, CLI and scheduler jobs use it; extensive monkeypatch and characterization coverage | Public/private-by-use and historical settings API | Existing `StrategyRegistry`/`StrategyRunner` only for proven lookup/dispatch | Removing it breaks C4 rollback and settings behavior | KEEP |
| `core/strategies/base.py` `BaseStrategy` | Shared filter/exit/market/override behavior for concrete algorithms | Imported by all seven concrete strategies and package exports | Backtest exit path and tests exercise it; indirectly used by every caller | Actual algorithm contract and documented inheritance | None in this change | Removing changes signal/exit behavior | KEEP |
| `core/strategy.py` | Legacy helper exports, V30 compatibility functions, shared `StrategyContext`/result exports | Imported by app, Daily, Backtest, LINE and tests; V30 helpers are direct runtime calls | CLI/backtest and LINE presentation use helpers; characterization tests cover them | Historical import path and operator-by-use possibility | None without a separate migration | Breaks direct rollback/helper path | KEEP |
| Seven concrete canonical implementations (`core/strategies/*.py`) | Actual strategy filters/ranking and exits | Each is loaded by `StrategyManager.CANONICAL_REGISTRY` and legacy adapter factories | Daily/Backtest/CLI/Web/LINE reach them; parity and strategy tests cover all seven | They are algorithms, not dead adapters | `LegacyStrategyAdapter` adapts but does not replace them | Deletion removes production behavior | KEEP |
| Seven alias modules (`core/strategies/v31_*` through `v38_*`) | Historical import/class compatibility | `LEGACY_STRATEGY_REGISTRY`, optimize jobs, tests and external imports | CLI/backtest aliases and tests reference them; scheduler invokes jobs using those keys | Documented historical keys and operator use | Canonical implementations via re-export only | Removing breaks imports and aliases | KEEP |
| `4_run_backtest.py` | Root documented backtest launcher | Wrapper imports `jobs.run_backtest`; tests/fixtures and docs reference it | CLI/operator entrypoint; scheduler-adjacent scripts preserve command | Explicit compatibility surface | `jobs.run_backtest` is implementation, not replacement for public wrapper | Revert required for operator rollback | KEEP |
| `5_push_to_line.py` | Root LINE push launcher | Wrapper imports `jobs.push_to_line`; execution batch/docs reference it | Operator command and LINE scheduled flow | Documented public-by-use command | `jobs.push_to_line` remains internal target | Removing breaks scheduled operator invocation | KEEP |
| `jobs/run_backtest.py` | Canonical CLI wrapper and argument contract | Imports `core.backtest.runner`; characterization tests and docs | CLI flags/defaults/aliases and scheduler invocation | Public command semantics and monkeypatch seams | Existing `core.backtest.runner` internals | Changing it alters exit/flag behavior | KEEP |
| `app/web_server.py` alias map | HTTP parser/display normalization (`v31` -> legacy key) | Route code and Web parity tests | Web canonical/alias requests and payload keys | HTTP compatibility requires current output key | Registry can resolve identity, but parser/output mapping remains presentation-specific | Removing changes HTTP request/payload behavior | KEEP |
| `app/line_flows.py` alias/label maps | Natural-language picker/postback parsing and UI labels | Flow code and LINE interaction tests | Picker, postback, random pool, text/Flex rendering | User-facing labels and postback IDs | Registry cannot replace UI phrase parsing | Removing changes LINE UX contract | KEEP |
| `core/backtest/runner.py` CLI alias maps | CLI parsing and V30/V31 compatibility presets | Parser, engine, tests, docs; runner selection seam | CLI and scheduler-invoked Backtest | Historical flags/presets are public-by-use | Removing changes Backtest inputs | KEEP |
| `jobs/run_daily.py`, `app/line_flows.py`, Backtest `runner=None` seams | Direct selection rollback to C3-compatible behavior | Explicit branches and `test/test_c4_rollback_characterization.py` | Daily/LINE/Backtest rollback only; no new scheduler | Required safe rollback, not dead code | StrategyRunner path is active; direct path remains rollback | Removing eliminates rollback | KEEP |
| `core/strategy/adapters/legacy.py` | Active adapter and lazy seven-strategy registry bridge | `StrategyManager.get_platform_registry`, Runner tests and parity tests | Daily/Backtest/Web/LINE indirectly consume it | C4 approved platform seam | None; adapter is current bridge | Removing breaks C4 routing | KEEP |

## Authority result

The platform registry/runner is the approved execution authority, but no
candidate met `SAFE_TO_REMOVE`. StrategyManager still owns compatibility settings,
cache, public methods, and historical objects. Web, LINE, and CLI maps are
presentation/transport parsers, not safe duplicates to delete. The seven
concrete strategies and BaseStrategy remain algorithm implementations. Unknown or
externally unbounded references default to KEEP.

## Cleanup decision

No logical group has a complete `SAFE_TO_REMOVE` record. `Files removed: NONE`.
No source cleanup or StrategyManager narrowing was performed. The audit is
independently reviewable and rollback is the unchanged archived C4 source seam.
