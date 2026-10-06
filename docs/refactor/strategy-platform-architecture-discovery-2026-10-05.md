# Strategy Platform Brownfield Discovery Report

Date: 2026-10-05  
Scope: discovery, architecture clarification, C1 proposal/design/spec/tasks, and over-engineering audit  
Implementation status: **C1 contracts/tests added; existing execution paths unchanged**

## Evidence and method

This report is based on the current worktree, not on an earlier plan. The
inspected evidence includes `AGENTS.md`, `README.md`, `strategy_settings.json`,
the strategy, research, runtime, and backtest packages, `jobs/`, `app/`, the
characterization/runtime tests, OpenSpec specifications, and the dated
`docs/refactor/` inventories.

The repository's prescribed `rtk` command is not installed in this PowerShell
environment (`rtk` is not recognized). Read-only native commands were used as a
fallback. No reset, checkout, delete, output relocation, strategy algorithm,
runtime caller, or production execution path was changed. The local DB volume
was recovered only after an explicit user request and a separate backup.

The active OpenSpec change is
`strategy-platform-contract-2026-10-05`. Repository policy prefers
date-leading names, but the installed CLI rejects them; the existing
2026-09-21 governance record requires a letter-leading ID with the ISO date
suffix until that tooling conflict is resolved.

## A. Current Architecture Map

### A1. Strategy identities

The current canonical registry contains exactly these seven IDs, in the order
used by `StrategyManager.CANONICAL_REGISTRY`:

| Canonical ID | Legacy IDs / aliases | Current implementation |
|---|---|---|
| `hybrid_trend_rank` | `v31`, `v31_hybrid` | `HybridTrendRankStrategy` |
| `defensive_low_volatility` | `v33`, `v33_low_vol` | `DefensiveLowVolatilityStrategy` |
| `growth_momentum_breakout` | `v34`, `v34_turbo` | `GrowthMomentumBreakoutStrategy` |
| `quality_growth` | `v35`, `v35_innovation` | `QualityGrowthStrategy` |
| `institutional_flow_confirmation` | `v36`, `v36_chip_momentum` | `InstitutionalFlowConfirmationStrategy` |
| `mean_reversion` | `v37`, `v37_mean_reversion` | `MeanReversionStrategy` |
| `quality_value_low_volatility` | `v38`, `v38_value_dividend` | `QualityValueLowVolatilityStrategy` |

`StrategyManager.list_strategies()` exposes the seven canonical IDs only.
`STRATEGY_REGISTRY` still contains canonical and long legacy keys for input and
historical compatibility. The independent Fundamental runtime ID is
`fundamental_g2g3_top5_reb60_score_weighted_v1`; it is deliberately not a live
legacy strategy and does not enter the seven-item user-facing listing.

`strategy_settings.json` currently has `version: 3.0`, active strategy
`quality_value_low_volatility`, a legacy-key random pool (`v35_innovation`,
`v36_chip_momentum`, `v38_value_dividend`), per-strategy overrides, and
backtest defaults. This is the implementation evidence; older documents that
describe another active strategy are stale.

### A2. Existing responsibilities

| Area | Current owner | Actual responsibility |
|---|---|---|
| Strategy metadata/aliases/factory | `core/strategy_manager.py` | Canonical and legacy registries, alias resolution, lazy import/instance cache, metadata, active/persistence/random pools, settings migration and persistence, fallback behavior, backtest defaults, Fundamental runtime registration. |
| Strategy abstraction | `core/strategies/base.py` | Abstract properties, candidate filtering contract, runtime overrides, common exit/stop/take-profit/hold logic, real-stock filter, market filter through DB/config, attribute validation, config export. |
| Legacy implementations | `core/strategies/*.py` | Seven concrete pandas-based filters/rankers. The `v31_*` through `v38_*` modules are thin alias re-exports and must remain supported. |
| V30 compatibility facade | `core/strategy.py` | Historical V30/V31 helpers, message formatting, pivot and candidate compatibility functions. It delegates some strategy loading but is a separate presentation/legacy surface. |
| Backtest | `core/backtest/runner.py` | Canonical single and portfolio engines, strategy-name expansion/resolution through `StrategyManager`, signal/exit/execution/cost/slippage/accounting, persistence and CLI plan. `jobs/run_backtest.py` is the public compatibility CLI; `4_run_backtest.py` delegates to it. |
| Daily runtime | `jobs/run_daily.py` | DB market read, technical indicators, financial/revenue joins, model loading, strategy filtering, AI score/news adjustments, persistence to `daily_recommendations`, and Fundamental production call. |
| Web | `app/__init__.py`, `app/web_server.py` | Flask initialization, strategy APIs and daily signals, direct Manager access, Fundamental special case, compatibility payloads, HTML/JSON presentation. |
| LINE | `app/line_bot.py`, `app/line_flows.py`, `app/line_commands.py` | User aliases/postbacks, strategy picker, recommendation formatting, persisted recommendation reads, and direct Manager access. |
| Scheduler | `jobs/scheduler.py` | The only scheduler owner. It defines job/pipeline steps, argument normalization, run status, stop-on-error/dry-run behavior, and invokes jobs; it does not own strategy algorithms. |
| Research | `core/research/` | Source/provenance, normalization, adjustment, factors, PIT fundamentals, universe, selection, target weights, validation, OOS/robustness, and research artifacts. Legacy flat modules remain re-export/import compatibility paths. |
| Fundamental freeze | `core/research/fundamental_strategy_validation.py` plus the immutable `FrozenStrategySpec.json` | Candidate scoring/grid, deterministic ranking/target weights, fingerprint generation, freeze immutability, OOS availability/verdict, artifact comparison, and engine parity. |
| Fundamental shadow | `core/runtime/fundamental_shadow.py` | Strict frozen-spec loading, fingerprint/parameter checks, PIT/universe adapter, health/freshness/factor gates, deterministic selection rows, validation, shadow artifacts, parity/replay evidence, and order isolation. |
| Fundamental production gate | `core/runtime/fundamental_production.py` | Evidence/hash/gate/OOS checks, feature flag, fail-closed status, normalized output, and an always-disabled broker boundary. |
| Runtime operation | `core/runtime/fundamental_operation.py`, `fundamental_advancement.py`, `fresh_oos_promotion.py`, and the corresponding jobs | Snapshot lineage, append-only OOS ledger, availability and promotion evidence. These are Fundamental-specific operational layers, not a second strategy registry. |

### A3. Coupling and data flow

```text
TWSE/MOPS/MCP/DB
       │
       ├── jobs/run_daily.py ── indicators/financials/news/model ──┐
       │                                                            │
       └── core/research/* ── PIT/factors/selection/weights         │
                                                                    ▼
             StrategyManager ── concrete BaseStrategy.filter_candidates
                    │                       │
                    ├── core/backtest/runner.py                    │
                    ├── jobs/run_daily.py ── DB daily_recommendations
                    ├── app/Web ─────────── JSON/templates         │
                    └── app/LINE ────────── Flex/text              │

Fundamental branch:
FrozenStrategySpec → FrozenStrategyLoader → CanonicalSelectionAdapter
                  → shadow health/selection → production evidence gate
                  → normalize_recommendations → Web/LINE compatibility reader
```

The legacy branch has no shared `StrategySpec`, `StrategyExecutor`, or typed
recommendation contract. It passes concrete objects and DataFrames between
jobs, DB persistence, and presentation. Fundamental has a stronger contract,
but that contract is intentionally isolated and already fail-closed.

### A4. Required compatibility and evidence surfaces

The following must stay in place until separately approved evidence exists:

- `4_run_backtest.py`, `5_push_to_line.py`, `jobs/run_backtest.py`, and all
  documented scheduler commands.
- `StrategyManager` public methods, canonical/legacy resolution, historical DB
  strategy strings, and the V31/V33–V38 alias modules.
- Web routes, LINE flows, `jobs/scheduler.py`, and the `daily_recommendations`
  table contract.
- `data/processed/` immutable research evidence, `outputs/` reproducible run
  output, `artifacts/` review evidence/resumable cache, and Fundamental frozen,
  shadow, parity, OOS, and production-gate artifacts.

The legacy recommendation persistence row currently contains
`stock_id`, `trade_date`, `strategy`, `close_price`, `ai_score`, `rsi`,
`volume`, and `news_boost_reason`. The Fundamental normalizer additionally
emits `strategy_id`, `strategy`, `asof_date`, `score`, `rank`, `selected`,
`target_weight`, and `reason`, while preserving compatibility names
`close_price`, `ai_score`, `rsi`, `volume`, and `news_boost_reason`.

## B. Technical Debt Map

### Coupling

1. `StrategyManager` combines registry, alias policy, dynamic factory/cache,
   settings repository, active-strategy policy, persistence policy, and a
   Fundamental registration bridge. It is a useful compatibility facade but is
   not a single-responsibility registry.
2. `BaseStrategy` couples algorithm implementations to pandas, `config.Config`,
   DB-backed market status, print-based diagnostics, and mutable runtime
   overrides. New strategies would inherit these dependencies by default.
3. `jobs/run_daily.py` combines data access, feature engineering, model
   inference, news enrichment, strategy execution, and DB persistence. This is
   the largest strategy-to-transport coupling point.
4. Web and LINE reach into the application package namespace (`sys.modules` or
   `import app as app_pkg`) and call Manager internals such as
   `_get_or_load_strategy`, making import order and hidden dependencies part of
   the behavior.
5. Strategy implementations import `Config` and use DataFrame column names as
   implicit context rather than receiving an explicit settings/data contract.

### Duplicated responsibility

1. Alias normalization is present in `StrategyManager`, Web API mappings, LINE
   postback parsing, and compatibility helpers. All must converge on one
   resolver, but removing local mappings before characterization would break
   input behavior.
2. The legacy DB row and Fundamental normalized row describe overlapping
   recommendation concepts with different names. A mapping boundary is needed;
   replacing either schema is not safe.
3. Concrete strategies repeat metadata properties, missing-column checks,
   staged filtering, and console diagnostics. This is evidence for shared
   contract/adapters, not permission to rewrite V31–V38 algorithms.
4. Fundamental has several operational modules that each validate identity or
   artifacts. Their behavior is coherent and tested, but the generic pieces
   (frozen loading, hashes, gates, shadow status) should only be extracted after
   two real strategy families use the same contract.
5. `core/backtest/runner.py` and the replay adapter both have execution/accounting
   concerns; their documented model difference must remain explicit.

### Legacy compatibility debt

1. Seven canonical IDs coexist with fourteen long/short aliases in input paths,
   settings, tests, and historical DB values. No rename is justified by current
   evidence.
2. Root launchers and old module paths are intentionally retained wrappers.
   `docs/refactor/deletion_candidates.md` and the 2026-09-21 reference map
   provide no complete no-reference evidence for deleting them.
3. `core/strategy.py`, `config.py`, research re-exports, and helper modules may
   look old but remain referenced by Web/LINE/jobs/tests or documented operator
   paths.

### Runtime-specific logic

1. Fundamental constants (factor IDs, PIT version, universe hash, Top5, REB60,
   score weighting, fingerprint) are correctly specific to Fundamental and must
   not move into a generic legacy strategy contract.
2. Fundamental promotion is evidence-driven and fail-closed; `PRODUCTION_DISABLED`,
   `FINGERPRINT_MISMATCH`, stale data, parity failures, and insufficient OOS are
   domain outcomes, not generic fallback-to-live behavior.
3. Research owns factor/PIT/selection semantics. Runtime may consume frozen
   artifacts but must never recompute research decisions using a presentation or
   scheduler layer.

### Architecture boundary violations

1. Strategy domain code can reach configuration/DB market filters and emits
   console output; it should eventually depend on injected context and a logger.
2. Jobs own domain orchestration and SQL persistence rather than application
   services/adapters, which makes daily selection hard to replay in isolation.
3. Presentation code knows strategy aliases and some implementation-specific
   fields. It should consume recommendation contracts plus compatibility mapping.
4. The existing runtime is stronger than the legacy path; creating another
   Fundamental runtime would duplicate frozen identity, gate, parity, and
   artifact authority. The correct move is later extraction/generalization.

## C. Proposed Target Architecture

```text
Data adapters
   ↓
Research / feature pipelines (core/research owns PIT and factor semantics)
   ↓
Immutable StrategySpec + lifecycle + validation contracts
   ↓
StrategyRegistry (canonical IDs, aliases, metadata, executor factory only)
   ↓
StrategyRunner → StrategyExecutor
   ├── LegacyStrategyAdapter → existing core/strategies implementations
   ├── FundamentalStrategyAdapter → existing Frozen/Shadow runtime
   ├── RuleBasedExecutor
   └── ModelBasedExecutor (future, only when a second use case exists)
   ↓
Validation / OOS / Frozen / Shadow / Promotion gates
   ↓
Recommendation contract + normalizer
   ↓
Persistence adapter (DB)   Web adapter   LINE adapter   Backtest adapter
```

The target has one platform, not one implementation algorithm. Strategy
definition (`StrategySpec`) is immutable data; execution (`StrategyExecutor`) is
an injected capability. `StrategyRunner` dispatches only through the executor
contract and must not contain strategy-ID branches. The core package must not
import Flask, LINE, HTTP, scheduler, templates, or SQL details.

The first implementation seam is deliberately small: immutable lifecycle/spec,
registry abstraction, executor protocol, and recommendation contracts. Generic
validation/runtime extraction is deferred until C3, after the existing
Fundamental runtime has a parity-preserving adapter and a second concrete use
case is proven.

## D. File-by-file migration map

| Current file/package | Future responsibility | Disposition | Risk |
|---|---|---|---|
| `core/strategy_manager.py` | Compatibility facade over registry/settings adapters | Keep, then adapt/delegate | Singleton, settings writes, and private callers are public-by-use |
| `core/strategies/base.py` | Legacy implementation base; eventual adapter source | Keep unchanged in C1; adapt in C2 | Exit timing, market filters, and overrides can drift |
| `core/strategies/{hybrid_trend_rank,defensive_low_volatility,growth_momentum_breakout,quality_growth,institutional_flow_confirmation,mean_reversion,quality_value_low_volatility}.py` | Existing algorithm implementations behind `LegacyStrategyAdapter` | Keep; adapt in C2 | Candidate order, relaxed filters, and prints are characterized behavior |
| `core/strategies/v31_*` … `v38_*` | Historical alias modules | Keep as re-export shims | External imports and tests may be unobserved |
| `core/strategy.py` | Legacy V30/V31 facade | Keep; deprecate only with evidence | Web/LINE and historical helpers still reference it |
| `strategy_settings.json` | Existing settings data source behind provider | Keep; no schema rewrite in C1 | Writes and aliases affect active strategy and tests |
| `core/backtest/runner.py` | Backtest adapter/runner consuming executor contract | Keep; adapt in C4 | Signal/execution/cost/slippage sequence must be byte/row stable |
| `jobs/run_backtest.py` | Canonical CLI compatibility module | Keep | CLI monkeypatch surface |
| `4_run_backtest.py` | Legacy CLI launcher | Keep | Public operator entrypoint |
| `jobs/run_daily.py` | Daily orchestration/application service | Keep; integrate in C4 | DB, news, model and persistence side effects |
| `jobs/scheduler.py` | Sole scheduler owner | Keep unchanged through C4 integration | Duplicate scheduler would change ownership |
| `app/__init__.py`, `app/web_server.py` | Web adapter/presentation | Keep; consume normalizer in C4 | Response keys/statuses are baselined |
| `app/line_bot.py`, `app/line_flows.py`, `app/line_commands.py` | LINE transport/presentation | Keep; consume normalizer in C4 | Postback aliases and text/Flex output are baselined |
| `core/research/**` | Research ownership and frozen-input producer | Keep; no C1 behavior change | PIT and evidence are immutable authority |
| `core/runtime/fundamental_shadow.py` | Fundamental adapter and shadow runtime | Keep; extract generic seams in C3 | Fingerprint, gate, parity and no-retarget rules |
| `core/runtime/fundamental_production.py` | Fundamental gate and compatibility normalizer | Keep; generalize only in C3 | Fail-closed production eligibility |
| `core/runtime/fundamental_{operation,advancement}.py`, `fresh_oos_promotion.py` | Fundamental OOS/lineage operations | Keep | Evidence mutation or contamination is unacceptable |
| `core/backtest/research_adapter.py` | Frozen target replay adapter | Keep | Full-liquidate model difference is intentional |
| `core/db_helper.py` | Persistence/data adapter | Keep; not moved into strategy domain | Very high fan-out (2k+ lines, many callers) |
| `docs/refactor/*`, `data/processed/*`, `outputs/*`, `artifacts/*` | Architecture/evidence/output ownership | Keep; update evidence docs | Deletion/relocation requires a separate evidence change |
| New `core/strategy/{contracts,spec,lifecycle,registry}.py` | C1 domain contracts and registry | Add in C1 | Must not import presentation, DB, or concrete algorithms directly |
| New `core/recommendation/{contracts,normalizer}.py` | Shared canonical output and compatibility mapping | Add in C1 | Field mapping must preserve DB/Web/LINE names |

## E. OpenSpec change plan

The four changes remain independently revertible:

| Change | Scope | Explicit non-scope |
|---|---|---|
| C1 `strategy-platform-contract-2026-10-05` (policy date: 2026-10-05) | Immutable `StrategySpec`, lifecycle, contracts, registry abstraction, recommendation contract/normalizer, characterization baseline | No algorithm, runtime, DB, Web, LINE, scheduler or strategy behavior change |
| C2 `YYYY-MM-DD-legacy-strategy-platform-adapter` | `LegacyStrategyAdapter`, Manager facade delegation, canonical/alias parity, V31–V38 output parity | No filter/rank/signal/exit/cost/slippage rewrite |
| C3 `YYYY-MM-DD-unified-validation-runtime` | Validation/frozen/gate/shadow abstractions extracted from existing Fundamental runtime | No second Fundamental runtime, no frozen fingerprint change, no live promotion |
| C4 `YYYY-MM-DD-strategy-platform-integration` | Daily/backtest/DB/Web/LINE integration, normalizer adoption, end-to-end parity and cleanup evidence | No deletion without `deletion_candidates.md` evidence; no scheduler ownership change |

The installed CLI rejects a date-leading new change name, while repository
policy requires date-leading directories. Following the existing 2026-09-21
governance exception, this C1 uses a letter-leading ID with the ISO date suffix;
the original date-leading request is retained as the policy date in this report.

## F. C1 scope and acceptance

C1 is a contract-only change. It must establish one vocabulary without routing
any existing execution through it yet:

- `StrategySpec` is a frozen value object containing strategy ID, display name,
  version, lifecycle status, aliases, universe, required features, parameters,
  ranking/portfolio/execution policies, and metadata.
- `StrategyExecutor` is a shallow protocol/contract. It receives an explicit
  context/spec and returns a contract-shaped selection; it does not know Flask,
  LINE, SQL, or scheduler details.
- `StrategyRegistry` owns canonical registration, alias resolution, metadata,
  lifecycle visibility, and factory resolution only. It does not persist DB
  rows, render output, or run backtests.
- `Recommendation` contains `stock_id`, `strategy_id`, `asof_date`, `score`,
  `rank`, `selected`, `target_weight`, and `reason`; optional compatibility
  fields (`close_price`, `ai_score`, `rsi`, `volume`,
  `news_boost_reason`) are mapped at the boundary.
- `StrategyManager` remains untouched as the compatibility facade in C1. A
  registry characterization test proves that adding the new abstractions does
  not alter canonical listing, alias resolution, settings, Fundamental
  registration, or public wrappers.

### Characterization tests required before any C1 implementation

1. Registry: exact seven canonical IDs and all 14 legacy aliases; warning and
   canonical resolution; unknown-key behavior; active/persistence/random pool;
   Fundamental registration identity and `ENABLED_FOR_LIVE=NO`.
2. Legacy strategy output: for deterministic fixtures capture candidate IDs,
   order, rank/selected state, score, target weight, and reason for all seven
   implementations (at minimum the existing V31 and daily V38 fixtures).
3. Backtest: signal date, execution date, direction, price, transaction cost,
   slippage, holdings, trade count, and exact event order; float checks use
   `rtol=1e-9`, `atol=1e-12`.
4. Daily runtime: per-strategy counts, top-five IDs/order, persisted legacy
   columns, heartbeat row, no-candidate behavior, and dry-run non-persistence.
5. Web/LINE: `/api/strategies`, `/api/daily-signals`, strategy picker/postback,
   Fundamental blocked/eligible empty-state behavior, and compatibility fields.
6. Scheduler/CLI: scheduler owner and command/exit behavior; `4_run_backtest.py`
   and `5_push_to_line.py` launchers; legacy strategy flags.
7. Fundamental: frozen fingerprint/field rejection, shadow health/selection,
   REB60 no-retarget, research/runtime parity, production blocked status, and
   broker-disabled status.
8. Evidence: import/reference scan for every retained wrapper/artifact before
   any future cleanup proposal.

The current non-DB baseline is 3 passed tests (`test_strategy_registry_regression`,
`test_legacy_cli`, `test_backtest_seed`). The full 30-test characterization
selection could not complete because the first test required an unavailable
local database; this is a pre-C1 environment blocker, not a reason to weaken
the baseline.

## Over-engineering cleanup recommendation

The safest cuts are consolidation seams, not deletions:

- Centralize alias resolution in the future registry and let Web/LINE retain
  only input-specific parsing. This removes repeated maps without changing
  aliases.
- Replace repeated recommendation field renaming with one normalizer and leave
  legacy DB columns at the persistence boundary.
- Keep one Fundamental runtime and extract generic gate/fingerprint/shadow
  primitives only after a second executor uses them.
- Do not split `core/db_helper.py`, `core/backtest/runner.py`, or app modules
  mechanically; current inventories show high fan-out and no safe seam.
- Do not remove root wrappers, re-export modules, `core/strategy.py`,
  `core/viz_helper.py`, `core/report_helper.py`, research outputs, or unknown
  artifacts. The current deletion evidence authorizes no such removal.
- Avoid adding a dependency, singleton, deep inheritance tree, model registry,
  or generic validation framework in C1. Standard-library dataclasses, enums,
  protocols, importlib, and mappings are sufficient.

## Ponytail audit (ranked, report-only)

These are simplification opportunities, not applied changes:

1. `shrink:` consolidate the repeated alias maps in Web/LINE/compatibility helpers behind one registry resolver; retain parser-specific short-label handling. [`app/web_server.py`, `app/line_flows.py`, `core/strategy_manager.py`]
2. `yagni:` avoid introducing a generic `RuleBasedStrategy`/`ModelBasedStrategy` hierarchy in C1; there is no second implementation requiring it yet. Use one executor protocol and adapters. [`core/strategy/` target]
3. `shrink:` map legacy and Fundamental recommendation columns once at the boundary instead of teaching each presentation surface both schemas. [`jobs/run_daily.py`, `core/runtime/fundamental_production.py`, `app/`]
4. `shrink:` keep `StrategyManager` as a facade but move only pure metadata/alias/factory records first; settings persistence and active-policy code should not be copied into a second manager. [`core/strategy_manager.py`]
5. `yagni:` defer a generic validation/runtime framework until C3 has two actual executor families; Fundamental already owns a working frozen/shadow/gate path. [`core/runtime/`, `core/research/`]
6. `native:` use stdlib `dataclasses(frozen=True)`, `Enum`, `Protocol`, `MappingProxyType`, and `importlib` for C1 contracts; no third-party dependency is justified. [`requirements*.txt`, future `core/strategy/`]
7. `shrink:` preserve the documented replay adapter model difference rather than adding a second configurable rebalance mode; one behavior is currently the characterized contract. [`core/backtest/research_adapter.py`]

No deletion candidate has complete no-reference evidence. The current hygiene
map correctly retains wrappers, facades, helper modules, and outputs.

## Ponytail debt ledger

| Location | Simplification | Ceiling / upgrade trigger |
|---|---|---|
| `core/backtest/research_adapter.py:9` | Fully liquidates and rebuilds target positions each execution date | Ceiling: differs from VectorBT daily re-target; revisit when execution accounting is intentionally reconciled in the referenced design decision. **No explicit trigger.** |
| `core/research/attribution.py:27` | Reuses the TWSE schema-correct closing-table lookup | Ceiling: avoids re-deriving MI_INDEX ordering; no upgrade path stated. **No explicit trigger.** |
| `core/research/strategy_search.py:298` | Mirrors job quote-frame construction to avoid importing `jobs` from `core` | Ceiling: duplicated loader; revisit when a lower-layer shared source adapter exists. **Trigger is architectural, not dated.** |
| `core/research/strategy_search.py:335` | Reuses the Day-2 `momentum_20d` research partition as the shared universe | Ceiling: depends on that partition remaining canonical; revisit when the approved universe contract changes. **Trigger is architectural, not dated.** |
| `scripts/ci_check_integration_marker_coverage.py:3` | CI heuristic intentionally under-flags unmarked DB-dependent tests | Ceiling: false negatives; widen when an unmarked/unmocked MySQL test slips through. |

Result: **5 markers; 1 has an explicit upgrade trigger, 2 have implicit
architectural triggers, and 2 have no explicit trigger.** The two no-trigger
comments should be clarified in a future documentation-only debt change; this
report does not edit them.

## Ponytail gain scoreboard

These are the published Ponytail benchmark medians, not measurements of this
repository and not a claimed per-repo saving:

```text
  ponytail gain                     benchmark median · 5 tasks · 3 models

  Lines of code   no-skill  ████████████████████  100%
                  ponytail  ██▌·················    6–20%   ▼ 80–94%
  Cost            no-skill  ████████████████████  100%
                  ponytail  █████▌··············   23–53%  ▼ 47–77%
  Speed           ponytail  ▸ 3–6× faster

  This repo:  ponytail-debt (shortcuts deferred)
              ponytail-audit (what remains cuttable)
```

Potential simplification is intentionally not converted into a per-repository
line-count claim. No dependencies are proposed for removal from the broad
research environment without import and reproducibility evidence.

`net: -180–320 lines, -0 deps possible.` This is an audit estimate of
consolidation opportunities, not a measured change and not an authorization to
delete anything.

## C1 baseline environment update (2026-10-05)

The existing Docker volume `stock_linbotv1_mysql_data` was present and
non-empty (approximately 1.25 GB). The project MySQL service was not running;
after an authorized local credential recovery, the volume was backed up to a
user-local temporary archive before account changes. `stock_ai_mysql` is now
healthy on `localhost:3306`, and the application `trader` connection executes
`SELECT 1` successfully. No production Python module was changed.

Characterization evidence after DB recovery:

- 29 tests excluding the daily DB baseline: 28 passed and 1 expected failure.
- The three atomic DB persistence tests passed.
- The daily baseline ran against the real DB but failed its existing assertion:
  current counts were `v34_turbo=548`, `v35_innovation=466`, and
  `v38_value_dividend=120`, versus fixture values 543, 446, and 116. The
  current DB setting is `mode=aggressive`; no setting or assertion was changed
  to conceal this pre-existing baseline drift.
- The initial full run also exposed Windows `cp950` output encoding; rerunning
  with UTF-8 avoided that environmental noise and reproduced the count drift.
- Fundamental runtime characterization remained behaviorally isolated, but
  its targeted suite reported 18 passed and 4 failures with the existing
  evidence returning `HISTORICAL_DRIFT` where older tests expect
  `INSUFFICIENT_OOS`, `PRODUCTION_DISABLED`, or a synthetic `PASS`. No frozen
  fingerprint, gate, or fixture was changed to conceal this pre-existing
  evidence drift.

The requested `core/strategy/` package collided with the existing public
`core/strategy.py` module. C1 resolves this without changing that file or its
callers: the new package exports only domain contracts, while its `__init__.py`
lazy-loads the original module only when an old V30/V31 name is requested.
Existing compatibility tests pass, and domain imports do not load DB,
presentation, or scheduler dependencies.

## Current C1 status

C1 contract implementation is complete through the domain package and tests;
the remaining work is verification and handoff. No strategy algorithm, runtime
caller, DB schema, scheduler ownership, Web/LINE behavior, historical strategy
key, or Fundamental fingerprint was changed.

## C1 deterministic characterization remediation (2026-10-05)

The permanent expected-value oracle is now isolated from the mutable local DB:

- `test/fixtures/legacy_strategy_platform.py` is a minimal feature matrix at
  `2026-04-10`, reconstructed from the existing legacy strategy test schemas
  and Config thresholds. It fixes the input rows, runtime overrides, and
  `FORCE_BULL_MARKET=true`; it does not fabricate score/rank/weight/reason
  fields that legacy strategies do not emit.
- `test/characterization/test_legacy_strategy_selection_deterministic.py`
  executes the existing `StrategyManager` and concrete strategies for all
  seven canonical IDs plus all fourteen aliases. It compares candidate IDs and
  order and explicitly records unavailable semantic columns as unavailable.
- `test/characterization/test_daily_selection_deterministic.py` freezes the
  data-source boundary while running the existing daily orchestration,
  persistence row shaping, heartbeat, and dry-run path.
- `test/fixtures/baseline/fundamental_production_evidence_2026-09-17.json` is
  copied from the repository's 2026-09-17 shadow/promotion evidence. The
  Fundamental tests now read this fixture and never use the mutable
  `outputs/fundamental_runtime_shadow/operate-*` directory as an oracle.

The former daily test is explicitly a live DB smoke test. Its current counts
(`v34_turbo=548`, `v35_innovation=466`, `v38_value_dividend=120`) remain
diagnostic only; they are not expected parity values.

The four prior Fundamental `HISTORICAL_DRIFT` failures were Case D evidence
drift, not C1 behavior: the current generated `operate-*` manifest has parity
under a nested `parity` object and omits the top-level
`HISTORICAL_SELECTION_DRIFT` field required by the unchanged fail-closed gate.
The frozen 2026-09-17 fixture retains the authoritative top-level evidence and
all gate-order tests now pass. No production gate, fingerprint, import, or
runtime behavior was changed.

Verification classification:

- Contract: C1 domain and dependency-boundary tests.
- Deterministic characterization: seven strategies/aliases, daily, seeded
  backtest, Web/Fundamental payloads, LINE, CLI, and scheduler tests.
- Integration smoke: the real MySQL atomic persistence tests and the renamed
  live daily DB smoke test.
