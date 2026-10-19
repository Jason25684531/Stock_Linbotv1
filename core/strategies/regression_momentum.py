"""Selection source: TQ_動量策略.ipynb (final make_pipeline definition)."""
import numpy as np
import pandas as pd
from scipy.stats import linregress
from ._tquant import TQuantStrategy


class RegressionMomentumStrategy(TQuantStrategy):
    name = 'regression_momentum'
    display_name = '迴歸動量'
    source_notebook = 'TQ_動量策略.ipynb'
    selection_description = '252 日對數價格迴歸年化斜率乘 R² 前 20 名，與平均成交金額前 150 名取交集。'
    window = 252
    history_columns = ('close_price', 'volume')

    def select_history(self, history):
        scores, liquidity = {}, {}
        for sid, group in self.groups(history):
            regression = linregress(np.arange(self.window), np.log(group.close_price))
            scores[sid] = np.expm1(regression.slope * 252) * 100 * regression.rvalue ** 2
            liquidity[sid] = (group.close_price * group.volume).mean()
        top = pd.Series(scores, dtype=float).dropna().nlargest(20).index
        liquid = pd.Series(liquidity, dtype=float).rank(ascending=False, method='first').le(150)
        return [sid for sid in top if liquid[sid]]
