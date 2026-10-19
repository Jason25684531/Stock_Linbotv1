"""Selection source: TQ_布林通道交易策略.ipynb."""
from ._tquant import TQuantStrategy


class BollingerReversionStrategy(TQuantStrategy):
    name = 'bollinger_reversion'
    display_name = '布林下軌反轉'
    source_notebook = 'TQ_布林通道交易策略.ipynb'
    selection_description = '收盤觸及 20 日均價減去 2 倍母體標準差。'
    window = 20

    def signal(self, group):
        close = group.close_price
        return close.iloc[-1] <= close.mean() - 2 * close.std(ddof=0)
