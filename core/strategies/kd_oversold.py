"""Selection source: TQ_KD指標回測實戰.ipynb."""
from ._tquant import TQuantStrategy


class KdOversoldStrategy(TQuantStrategy):
    name = 'kd_oversold'
    display_name = 'KD 超賣'
    source_notebook = 'TQ_KD指標回測實戰.ipynb'
    selection_description = '10 日未平滑 Fast Stochastic K ≤ 20。'
    window = 10
    history_columns = ('close_price', 'high_price', 'low_price')

    def signal(self, group):
        high, low = group.high_price.max(), group.low_price.min()
        return high > low and 100 * (group.close_price.iloc[-1] - low) / (high - low) <= 20
