"""Selection source: TQ_趨勢跟蹤策略.ipynb (long entry only)."""
from ._tquant import TQuantStrategy


class TrendFollowingStrategy(TQuantStrategy):
    name = 'trend_following'
    display_name = '趨勢跟蹤'
    source_notebook = 'TQ_趨勢跟蹤策略.ipynb'
    selection_description = '81 日窗的 40 日 EMA 高於 80 日 EMA，且收盤為近 50 日最高；僅移植多頭進場。'
    window = 81

    def signal(self, group):
        close = group.close_price
        return (close.ewm(span=40).mean().iloc[-1] > close.ewm(span=80).mean().iloc[-1]
                and close.iloc[-1] == close.iloc[-50:].max())
