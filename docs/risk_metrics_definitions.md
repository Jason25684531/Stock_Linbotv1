# Risk metric definitions

All values are calculated from post-cost equity and closed trades. Annualization uses 252 sessions, risk-free rate defaults to zero, and Sortino MAR is zero. A metric is `MetricValue(value=None, reason=...)` when a sample is insufficient, volatility or downside deviation is zero, or no losing trade exists; zero is never used to mask an unavailable value.

The exported set is total return, CAGR, annualized return/volatility, downside deviation, Sharpe, Sortino, maximum drawdown, drawdown/recovery duration, Calmar, win rate, profit factor, payoff ratio, maximum consecutive losses, exposure, turnover and trade count.
