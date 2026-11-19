"""V8 rule: the V7 revenue momentum book with one added step, keep only the lowest-volatility fraction of the filtered names.

Nothing in scripts/strategy_v7_rules.py changes. low_vol_rule enters the V7 portfolio_rule and then replaces the host selection for the
length of the run, so every V7 patch is restored by the V7 context on exit.
"""
import math
from contextlib import contextmanager
from unittest.mock import patch

from scripts import strategy_v7_rules as v7

V7_PORTFOLIO_RULE = v7.portfolio_rule  # kept at import: the V8 program later points v7.portfolio_rule at low_vol_rule
VOL_WINDOW = 60
REGISTERED_KEEP = (0.5, 1 / 3)


def low_vol_selection(history, revenue, top, min_volume, keep):
    """Names passing the V7 revenue momentum and price trend filters, the lowest `keep` fraction by the standard deviation of daily close
    changes over the last 60 sessions, ranked by the 5 session average change, best first, at most `top`. Uses closes up to the signal day."""
    asof = history.trade_date.max()
    chosen = set(v7.revenue_momentum_ids(revenue, asof)) & set(v7.price_trend_ids(history, min_volume))
    volatility, score = {}, {}
    for sid, group in history[history.stock_id.astype(str).isin(chosen)].groupby('stock_id', sort=True):
        if group.trade_date.iloc[-1] == asof:
            close = group.close_price.astype(float)
            spread = float(close.pct_change(fill_method=None).tail(VOL_WINDOW).std())
            if not math.isnan(spread):
                volatility[str(sid)], score[str(sid)] = spread, float(close.pct_change().tail(5).mean())  # score exactly as in V7
    kept = sorted(volatility, key=lambda s: (volatility[s], s))[:math.ceil(len(volatility) * keep)]
    return sorted(kept, key=lambda s: (-score[s], s))[:top]


@contextmanager
def low_vol_rule(params, sessions, revenue, bench=None):
    """V7 portfolio_rule with the selection replaced by low_vol_selection; params['vol_keep'] must be a registered fraction."""
    from scripts import strategy_v5_rules
    keep = params['vol_keep']
    if not any(abs(keep - registered) < 1e-9 for registered in REGISTERED_KEEP):
        raise ValueError(f'UNREGISTERED_VOL_KEEP {keep}')
    host = strategy_v5_rules.STRATEGY_CLASSES[params['host']]
    with V7_PORTFOLIO_RULE(params, sessions, revenue, bench):
        with patch.object(host, 'select_history', lambda self, history: low_vol_selection(history, revenue, params['top'], params['min_volume'], keep)):
            yield
