"""Selection source: TQ_VAM_波動率調整截面動能_TWN100 .ipynb."""
import numpy as np
import pandas as pd
from ._tquant import TQuantStrategy


class VolatilityAdjustedMomentumStrategy(TQuantStrategy):
    name = 'volatility_adjusted_momentum'
    display_name = 'VAM 波動率調整動量'
    source_notebook = 'TQ_VAM_波動率調整截面動能_TWN100 .ipynb'
    selection_description = '本地個股全市場中，30 日成交金額前 100 名，再取 21 日報酬/母體波動率前 10 名；權重縮放依本專案引擎。'
    window = 30
    history_columns = ('close_price', 'volume')

    def select_history(self, history):
        scores, liquidity = {}, {}
        for sid, group in self.groups(history):
            close = group.close_price.iloc[-22:]
            volatility = close.pct_change().dropna().std(ddof=0)
            scores[sid] = (close.iloc[-1] / close.iloc[0] - 1) / volatility if volatility >= 1e-8 else np.nan
            liquidity[sid] = (group.close_price * group.volume).mean()
        liquid = pd.Series(liquidity, dtype=float).nlargest(100).index
        return pd.Series(scores, dtype=float).reindex(liquid).dropna().nlargest(10).index.tolist()
