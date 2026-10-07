# Future Strategy Onboarding

Strategy Platform 1.0 has one application execution path. New strategy families
register an executor and spec; callers consume the existing runner and
Recommendation boundary. Do not add a manager, registry, factory layer, or
strategy-ID branch.

## Path A — Simple strategy

```text
Feature / Data
    ↓
StrategySpec
    ↓
Validation
    ↓
StrategyExecutor
    ↓
StrategyRegistry
    ↓
StrategyRunner
    ↓
Recommendation
    ↓
Existing application
```

Use this path for a strategy whose selection semantics are ordinary application
execution. Keep the executor responsible for its own algorithm and expose only
source fields; absent score, rank, weight, selected state, or reason remains
absent rather than being synthesized.

## Path B — Research / frozen strategy

```text
Research
    ↓
StrategySpec
    ↓
Validation
    ↓
Frozen identity
    ↓
Shadow / parity / domain runtime gates
    ↓
StrategyExecutor or runtime adapter
    ↓
StrategyRegistry / application
    ↓
Recommendation
```

Use this path only when the strategy's domain requires frozen identity, shadow,
parity, freshness, OOS, promotion, or other runtime evidence. Those authorities
remain in their existing domain owners. Fundamental is one domain implementation;
its runtime lifecycle is not a universal requirement for every future strategy.

## Required checks

1. Define an immutable `StrategySpec` and validate identity/lifecycle/policies.
2. Implement the smallest `StrategyExecutor` needed by the strategy family.
3. Register it in the appropriate registry (test/isolated registry first).
4. Execute it through `StrategyRunner` and map to `Recommendation`.
5. Add parity and safety tests before any application caller adoption.
6. Do not modify Runner dispatch, Web, LINE, CLI, scheduler, or Backtest
   accounting to recognize a strategy ID.
