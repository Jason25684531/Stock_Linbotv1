# Factor research data limitations

1. This pipeline covers TWSE listed ordinary shares only; TPEx is excluded.
2. Survivorship bias remains, so Rank IC is an upper-bound estimate.
3. Capital-reduction events are not included in adjustment factors.
4. Reconciliation is sampled, not a full-population assertion.
5. Disposition-stock batch auctions can distort daily prices and are not handled.
6. Proxy liquidity is `close * volume` when official amount is unavailable.
7. Adjusted prices are as-of snapshots; runs with different `adjustment_as_of` values are not directly comparable.
8. The daily universe-count median must accompany any quantile analysis; a low median limits its reliability.
9. The raw/adjusted asymmetry is intentional: universe gates use point-in-time `raw_close`; published return factors use continuous `adjusted_*` series.
10. `reversal_5d` is the negated 5-day return (a short-horizon reversal signal); the original, un-negated `return_5d` remains registered as a deprecated, non-canonical factor for existing consumers.
11. `vwap_gap` is `raw_close / raw_vwap - 1`, where `raw_vwap = amount / volume`, entirely on unadjusted prices — it is a deliberate, narrow exception to the general rule that published price factors use `local_adjusted` prices, because same-day traded value and volume carry no adjustment factor to apply. It is null when volume is non-positive or amount is missing. `overnight_gap_20d` (a different, unrelated construct) remains registered as a deprecated, non-canonical factor.
12. Direction `0` denotes **undetermined, pending Rank IC / quantile-return research** (`vwap_gap`, `volume_ratio_20d`, `price_volume_corr_20d`, and the two deprecated factors), not economic neutrality. Confirming these directions is deferred to the D3 Rank IC change (see `docs/research/factor_research_roadmap.md`).
13. `available_at` is a conservative, next-trading-day floor: for a trade date, it equals the observation instant of the *next* trading day present in the loaded dataset, never the row's own trade date. For the last trading day in a loaded window it is null (no known next trading day), rather than guessed from a calendar-day offset. The existing `market_closed_at` field is the observation instant of a trade date's own market activity — it is not, and must not be read as, the instant the data became available.
14. `is_tradable` is a per-row data-contract fact (non-positive volume or degraded/unverified quality marks a row untradable), independent of and narrower than the research universe filter in `universe.py`, which additionally applies security-code and liquidity-threshold rules for research sampling purposes. The two are not interchangeable.
15. `asset_id`, `asof_date`, `factor_id`, and `raw_value` are canonical aliases that mirror `stock_id`, `trade_date`, `factor_name`, and `value` respectively in emitted factor rows; both naming sets are always present with equal values.
