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
