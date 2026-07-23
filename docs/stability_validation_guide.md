# Stability validation guide

1. Split dated data into IS/OOS with `split_is_oos`; never tune parameters on OOS.
2. Create rolling or expanding folds with `walk_forward_folds`; each fold guarantees `train_end < test_start`.
3. Review rolling 3/126/252-session return, volatility, Sharpe, Sortino, drawdown, win rate and turnover.
4. Run seeded bootstrap resampling and report the 95% confidence interval.
5. Scan parameter and cost sensitivity before accepting a strategy.

Generated charts and reports go in `artifacts/`; only README examples are tracked.
