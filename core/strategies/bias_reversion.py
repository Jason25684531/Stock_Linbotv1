"""Selection source: TQ_乖離率策略.ipynb."""
import numpy as np
from ._tquant import TQuantStrategy


class BiasReversionStrategy(TQuantStrategy):
    name = 'bias_reversion'
    display_name = '乖離率反轉'
    source_notebook = 'TQ_乖離率策略.ipynb'
    selection_description = '收盤低於 8 日窗去除末兩日的最低收盤，且低於 7 日指數加權均價。'
    window = 8

    def signal(self, group):
        close = group.close_price.to_numpy()
        weights = (1 / 7) ** np.arange(6, -1, -1)
        return close[-1] < close[:-2].min() and close[-1] < np.average(close[-7:], weights=weights)
