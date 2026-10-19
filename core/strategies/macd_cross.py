"""Selection source: TQ_MACD交易策略.ipynb."""
from ._tquant import TQuantStrategy, seeded_ema


class MacdCrossStrategy(TQuantStrategy):
    name = 'macd_cross'
    display_name = 'MACD 黃金交叉'
    source_notebook = 'TQ_MACD交易策略.ipynb'
    selection_description = '2330 的 35 日價格窗，12/26 EMA 差值向上穿越 9 日訊號線。'
    window = 35

    def select_history(self, history):
        return super().select_history(history.loc[history.stock_id.eq('2330')])

    def signal(self, group):
        close = group.close_price.reset_index(drop=True)
        dif = (seeded_ema(close, 12) - seeded_ema(close, 26)).dropna()
        bar = dif - seeded_ema(dif, 9)
        return bar.iloc[-2] < 0 and bar.iloc[-1] > 0
