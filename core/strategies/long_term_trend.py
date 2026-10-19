"""Selection source: TQ_長期趨勢策略.ipynb (long entry only)."""
from ._tquant import TQuantStrategy


class LongTermTrendStrategy(TQuantStrategy):
    name = 'long_term_trend'
    display_name = '長期趨勢'
    source_notebook = 'TQ_長期趨勢策略.ipynb'
    selection_description = '251 日窗的最新收盤高於第 -250 與 -125 筆收盤；僅移植多頭進場。'
    window = 251

    def signal(self, group):
        close = group.close_price
        return close.iloc[-1] > close.iloc[-250] and close.iloc[-1] > close.iloc[-125]
