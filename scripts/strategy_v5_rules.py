"""V5 correction table: one isolated fix per documented execution defect. Production strategy files, the engine and config stay untouched.

Every V5 run is bound to this file's hash, so keep runner and report code out of it. Apply a correction only inside a dedicated
single-worker process: patch.object changes the class for the whole process while the context is open.
"""
from contextlib import ExitStack, contextmanager
from unittest.mock import patch

from core.strategies import mean_reversion as _mean_reversion
from core.strategies.defensive_low_volatility import DefensiveLowVolatilityStrategy
from core.strategies.mad_distance import MadDistanceStrategy
from core.strategies.mean_reversion import MeanReversionStrategy
from core.strategies.regression_momentum import RegressionMomentumStrategy
from core.strategies.volatility_adjusted_momentum import VolatilityAdjustedMomentumStrategy
from scripts.strategy_v4_ranking import ranked_filter, ranked_history

STRATEGY_CLASSES = {'mad_distance': MadDistanceStrategy, 'volatility_adjusted_momentum': VolatilityAdjustedMomentumStrategy,
                    'regression_momentum': RegressionMomentumStrategy, 'mean_reversion': MeanReversionStrategy,
                    'defensive_low_volatility': DefensiveLowVolatilityStrategy}
BB_WIDTH_MAX_RATIO = 0.15  # the limit 15 was written in percent; calculate_bb_width returns a ratio (core/calc_indicators.py:279-290)
_DEFENSIVE_FILTER = DefensiveLowVolatilityStrategy.filter_candidates


def order_by_relative_volatility(picks):
    """Calmest first by std_20 as a share of price; a stock without a positive price goes last."""
    if picks.empty or 'std_20' not in picks or 'close_price' not in picks:
        return picks
    relative = picks.std_20 / picks.close_price.where(picks.close_price > 0)
    return picks.assign(_v5_rel=relative).sort_values(['_v5_rel', 'stock_id'], kind='stable', na_position='last').drop(columns='_v5_rel')


def _defensive_filter(self, df):
    return order_by_relative_volatility(_DEFENSIVE_FILTER(self, df))


CORRECTIONS = {
    'mad_distance': {'defect': 'I13', 'hook': 'select_history + filter_candidates (V4 ranking-only-v1)',
                     'patches': lambda c: [patch.object(c, 'select_history', ranked_history), patch.object(c, 'filter_candidates', ranked_filter)]},
    'volatility_adjusted_momentum': {'defect': 'I12', 'hook': 'select_history + filter_candidates (V4 ranking-only-v1)',
                                     'patches': lambda c: [patch.object(c, 'select_history', ranked_history), patch.object(c, 'filter_candidates', ranked_filter)]},
    'regression_momentum': {'defect': 'I11', 'hook': 'filter_candidates keeps select_history order (V4 filter)',
                            'patches': lambda c: [patch.object(c, 'filter_candidates', ranked_filter)]},
    'mean_reversion': {'defect': 'I01', 'hook': 'V37_BB_WIDTH_MAX 15 -> 0.15',
                       'patches': lambda c: [patch.object(_mean_reversion.Config, 'V37_BB_WIDTH_MAX', BB_WIDTH_MAX_RATIO)]},
    'defensive_low_volatility': {'defect': 'I05', 'hook': 'order by std_20 / close instead of std_20 in TWD',
                                 'patches': lambda c: [patch.object(c, 'filter_candidates', _defensive_filter)]},
}


@contextmanager
def applied(strategy_id):
    entry = CORRECTIONS[strategy_id]
    with ExitStack() as stack:
        for p in entry['patches'](STRATEGY_CLASSES[strategy_id]):
            stack.enter_context(p)
        yield entry
