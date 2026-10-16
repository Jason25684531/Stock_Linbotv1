# Risk metric definitions

All values are calculated from post-cost equity and closed trades. Annualization uses 252 sessions, risk-free rate defaults to zero, and Sortino MAR is zero. A metric is `MetricValue(value=None, reason=...)` when a sample is insufficient, volatility or downside deviation is zero, or no losing trade exists; zero is never used to mask an unavailable value.

The exported set is total return, CAGR, annualized return/volatility, downside deviation, Sharpe, Sortino, maximum drawdown, drawdown/recovery duration, Calmar, win rate, profit factor, payoff ratio, maximum consecutive losses, exposure, turnover and trade count.

Drawdown duration counts sessions from the peak preceding the maximum drawdown to its trough. Recovery duration counts sessions from that trough to the first later observation reaching the same peak, even if a later all-time high occurs. Both durations are zero for a curve with no drawdown; recovery is unavailable, with a reason, when that peak has not been regained. These are reporting-only values and never alter orders or allocation.

Research metric comparisons retain their declared absolute/relative tolerances. A relative difference with a zero baseline is unavailable (`zero_denominator`), while the absolute difference remains valid and can establish a match. Missing/nonfinite source values and undefined Empyrical values are null with explicit reasons; frozen historical reports are retained unchanged.
