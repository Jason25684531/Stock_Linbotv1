"""Shared as-of OHLCV input for notebook selection rules; no execution changes."""
from functools import lru_cache

import numpy as np
import pandas as pd
from sqlalchemy import text

from config import Config
from core.db_helper import get_db_engine
from .base import BaseStrategy

ENGINE_NOTICE = '持股數、再平衡和出場規則依本專案引擎，績效和原 notebook 不同。'


@lru_cache(maxsize=1)
def load_history(asof_date):
    # ponytail: one as-of date cached per worker; clear after same-date data refresh.
    end = pd.Timestamp(asof_date)
    history = pd.read_sql(text('''
        SELECT stock_id, trade_date, close_price, high_price, low_price, volume
        FROM daily_market_data
        WHERE trade_date BETWEEN :start AND :end
        ORDER BY stock_id, trade_date
    '''), get_db_engine(), params={'start': end - pd.Timedelta(days=550), 'end': end})
    history['stock_id'] = history['stock_id'].astype(str).str.strip()
    history['trade_date'] = pd.to_datetime(history['trade_date'])
    anchors = history.loc[history.stock_id.eq(Config.MARKET_SYMBOL), 'trade_date']
    history = history.loc[history.trade_date.isin(anchors)
                          & history.stock_id.str.fullmatch(r'[1-9]\d{3}')]
    return history.drop_duplicates(['stock_id', 'trade_date'], keep='last')


def seeded_ema(values, period):
    """SMA-seeded EMA, matching TA-Lib's EMA initialization."""
    seed = pd.Series([values.iloc[:period].mean()], index=[values.index[period - 1]])
    return pd.concat([seed, values.iloc[period:]]).ewm(span=period, adjust=False).mean()


class TQuantStrategy(BaseStrategy):
    features = ['rsi', 'bias', 'macd_hist', 'volume_ratio']
    target_return = 0.08
    look_ahead_days = 7
    history_columns = ('close_price',)

    @property
    def description(self):
        return f'{self.selection_description} 來源：{self.source_notebook}。{ENGINE_NOTICE}'

    def groups(self, history):
        asof = history.trade_date.max()
        for stock_id, group in history.groupby('stock_id', sort=True):
            group = group.tail(self.window)
            values = group[list(self.history_columns)]
            if (len(group) == self.window and group.trade_date.iloc[-1] == asof
                    and np.isfinite(values.to_numpy(dtype=float)).all()
                    and group.close_price.gt(0).all()):
                yield stock_id, group

    def select_history(self, history):
        return [sid for sid, group in self.groups(history) if self.signal(group)]

    def filter_candidates(self, df):
        if df.empty:
            return df.copy()
        if df.trade_date.nunique() != 1:
            raise ValueError('TQuant selection requires one as-of date')
        asof = self._extract_date_str(df)
        history = load_history(asof)
        # Do not let stale or future rows form a signal for the requested date.
        history = history.loc[history.trade_date.le(pd.Timestamp(asof))]
        if history.empty or history.trade_date.max() != pd.Timestamp(asof):
            return df.iloc[:0].copy()
        selected = self.select_history(history)
        return df.loc[df.stock_id.astype(str).isin(selected)].copy()
