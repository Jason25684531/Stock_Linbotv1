"""Selection source: TQ_逆勢交易策略.ipynb."""
from ._tquant import TQuantStrategy


class ContrarianPullbackStrategy(TQuantStrategy):
    name = 'contrarian_pullback'
    display_name = '逆勢回檔'
    source_notebook = 'TQ_逆勢交易策略.ipynb'
    selection_description = '依原碼使用 21 日窗，40/80 EMA 多頭，距近 20 日高點除以日報酬標準差百分值小於 -3。'
    window = 21

    def signal(self, group):
        close = group.close_price
        volatility = close.pct_change().dropna().std() * 100
        return (volatility > 0
                and close.ewm(span=40).mean().iloc[-1] > close.ewm(span=80).mean().iloc[-1]
                and (close.iloc[-1] - close.iloc[-20:].max()) / volatility < -3)
