"""Selection source: TQ_MAD_移動均線距離策略.ipynb."""
import pandas as pd
from ._tquant import TQuantStrategy


class MadDistanceStrategy(TQuantStrategy):
    name = 'mad_distance'
    display_name = 'MAD 均線距離'
    source_notebook = 'TQ_MAD_移動均線距離策略.ipynb'
    selection_description = '21/200 均線比位居前 10% 且超過 1+橫截面標準差，同時符合三項價格結構條件。'
    window = 200
    history_columns = ('close_price', 'high_price', 'low_price')

    def select_history(self, history):
        ratios, price_action = {}, {}
        for sid, group in self.groups(history):
            close = group.close_price
            ratios[sid] = close.iloc[-21:].mean() / close.mean()
            recent = group.tail(20)
            amplitude = recent.high_price - recent.low_price
            price_action[sid] = (close.iloc[-1] / recent.high_price.max() > 0.88
                                 and close.iloc[-10:].min() > close.iloc[-20:-10].min()
                                 and amplitude.iloc[-5:].mean() < amplitude.iloc[-15:-5].mean())
        ratios = pd.Series(ratios, dtype=float)
        if len(ratios) < 50:
            return []
        mask = (ratios.rank(pct=True) > .9) & (ratios > 1 + ratios.std()) & pd.Series(price_action)
        return ratios.index[mask].tolist()
