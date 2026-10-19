"""Selection source: TQ_阿隆指標交易策略.ipynb."""
import numpy as np
from ._tquant import TQuantStrategy


class AroonTrendStrategy(TQuantStrategy):
    name = 'aroon_trend'
    display_name = '阿隆趨勢'
    source_notebook = 'TQ_阿隆指標交易策略.ipynb'
    selection_description = '25 日 Aroon Up > 80、Down < 45，收盤低於 5000。'
    window = 25
    history_columns = ('close_price', 'high_price', 'low_price')

    def signal(self, group):
        up = np.argmax(group.high_price.to_numpy()) * 100 / 24
        down = np.argmin(group.low_price.to_numpy()) * 100 / 24
        return up > 80 and down < 45 and group.close_price.iloc[-1] < 5000
