"""Isolated ranking correction. Production strategies and execution rules stay intact."""
from contextlib import contextmanager
from core.research.strategy_v4 import rank_scores
from unittest.mock import patch

import pandas as pd

from core.strategies import _tquant
from core.strategies.mad_distance import MadDistanceStrategy
from core.strategies.volatility_adjusted_momentum import VolatilityAdjustedMomentumStrategy

STRATEGIES = {'mad_distance': MadDistanceStrategy, 'volatility_adjusted_momentum': VolatilityAdjustedMomentumStrategy}
ORIGINAL_SELECT = {name: cls.select_history for name, cls in STRATEGIES.items()}


def ranked_history(self, history):
    groups = list(self.groups(history))
    if self.name == 'mad_distance':
        qualified = set(ORIGINAL_SELECT[self.name](self, history))
        scores = {sid: g.close_price.iloc[-21:].mean() / g.close_price.mean() for sid, g in groups if sid in qualified}
    else:
        liquidity = {sid: (g.close_price * g.volume).mean() for sid, g in groups}
        liquid = set(sorted(liquidity, key=lambda sid: (-liquidity[sid], sid))[:100])
        scores = {}
        for sid, group in groups:
            close = group.close_price.iloc[-22:]
            volatility = close.pct_change().dropna().std(ddof=0)
            if sid in liquid and volatility >= 1e-8:
                scores[sid] = (close.iloc[-1] / close.iloc[0] - 1) / volatility
    ranked = rank_scores(scores)
    return ranked if self.name == 'mad_distance' else ranked[:10]


def ranked_filter(self, frame):
    frame = frame.reset_index(drop=True)
    if frame.empty:
        return frame.copy()
    if frame.trade_date.nunique() != 1:
        raise ValueError('TQuant selection requires one as-of date')
    codes = frame.stock_id.astype(str)
    if codes.duplicated().any():
        raise ValueError('DUPLICATE_DAILY_CANDIDATE')
    asof = pd.Timestamp(self._extract_date_str(frame))
    history = _tquant.load_history(asof.strftime('%Y-%m-%d'))
    history = history.loc[history.trade_date.le(asof)].sort_values(['stock_id', 'trade_date'])
    if history.duplicated(['stock_id', 'trade_date']).any():
        raise ValueError('DUPLICATE_HISTORY')
    if history.empty or history.trade_date.max() != asof:
        return frame.iloc[:0].copy()
    order = {sid: rank for rank, sid in enumerate(self.select_history(history))}
    ranks = codes.map(order)
    return frame.loc[ranks.dropna().sort_values(kind='stable').index].copy()


@contextmanager
def fixed_selection(strategy_id):
    """Only use within a dedicated single-worker research process."""
    cls = STRATEGIES[strategy_id]
    with patch.object(cls, 'select_history', ranked_history), patch.object(cls, 'filter_candidates', ranked_filter):
        yield
