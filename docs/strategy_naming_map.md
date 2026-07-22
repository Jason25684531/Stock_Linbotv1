# Strategy naming map

Canonical IDs are used for new integrations. Historical database rows, CLI flags and `strategy_settings.json` keep their legacy key and resolve through `StrategyMetadata`.

| Canonical ID | Deprecated IDs |
|---|---|
| hybrid_trend_rank | v31, v31_hybrid |
| defensive_low_volatility | v33, v33_low_vol |
| growth_momentum_breakout | v34, v34_turbo |
| quality_growth | v35, v35_innovation |
| institutional_flow_confirmation | v36, v36_chip_momentum |
| mean_reversion | v37, v37_mean_reversion |
| quality_value_low_volatility | v38, v38_value_dividend |

Legacy IDs raise `DeprecationWarning`; no stored value is rewritten. V38 is not called a dividend strategy because the audited implementation has no yield factor.
