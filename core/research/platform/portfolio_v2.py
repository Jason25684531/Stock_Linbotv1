"""Shared-capital portfolio (change 2026-11-12, design D7).

One Book owns the cash, every position and its owning sleeve. Each strategy sleeve is an unchanged V2 engine whose portfolio is a
SleeveView of the Book, so sleeves only propose orders; fills debit and credit the one cash account. Every session runs in a fixed
order across sleeves in catalog order: corporate actions, open fills (cash contention resolved by that order), valuation, close
signals. Rules: ALLOC_V1 (a sleeve's position budget = portfolio capital x weight x POSITION_SIZE, 3 holdings per sleeve),
CONTEND_V1 (catalog order; an unfilled buy while shared cash is below the sleeve's full position budget is CASH_CONTENTION; a stock
held by another sleeve is OVERLAP_SKIPPED), REBAL_NONE or REBAL_ANNUAL_V1 (first session of a year resets each sleeve's budget base
to weight x previous NAV; nothing is sold).
"""
from __future__ import annotations

import copy
import math
from types import MappingProxyType

from core.backtest import runner

RULES = {'allocation': 'ALLOC_V1', 'contention': 'CONTEND_V1'}
REBALANCES = ('REBAL_NONE', 'REBAL_ANNUAL_V1')
MIN_WEIGHT = 0.05


def check_weights(members, weights=None) -> list[float]:
    if not members or len(set(members)) != len(members):
        raise ValueError('MEMBERS_EMPTY_OR_DUPLICATED')
    if weights is None:
        weights = [1 / len(members)] * len(members)
    if len(weights) != len(members) or any(not isinstance(w, (int, float)) or isinstance(w, bool) for w in weights):
        raise ValueError('WEIGHTS_SHAPE')
    if any(not math.isfinite(w) or w < MIN_WEIGHT for w in weights) or abs(sum(weights) - 1) > 1e-9:
        raise ValueError(f'WEIGHTS_INVALID: each >= {MIN_WEIGHT}, sum = 1')
    return [float(w) for w in weights]


class Book:
    def __init__(self, cash):
        self.cash, self.positions, self.owner, self.rejections = float(cash), {}, {}, []

    def checkpoint(self):
        return copy.deepcopy((self.cash, self.positions, self.owner, len(self.rejections)))

    def rollback(self, state):
        self.cash, positions, owner, rejected = state
        self.positions.clear()
        self.positions.update(positions)
        self.owner.clear()
        self.owner.update(owner)
        del self.rejections[rejected:]

    def reject(self, day, sleeve, stock_id, reason):
        self.rejections.append({'date': day, 'sleeve': sleeve, 'stock_id': stock_id, 'reason': reason})


class SleeveView:
    """core.backtest.portfolio.Portfolio's interface for one sleeve over the shared Book."""

    def __init__(self, book, name):
        self.book, self.name = book, name

    @property
    def cash(self):
        return self.book.cash

    @property
    def positions(self):
        return MappingProxyType({sid: info for sid, info in self.book.positions.items() if self.book.owner[sid] == self.name})

    def debit(self, amount):
        if amount > self.book.cash:
            raise ValueError('insufficient cash')
        self.book.cash -= amount

    def credit(self, amount):
        self.book.cash += amount

    def open_position(self, stock_id, position):
        if stock_id in self.book.positions:
            raise ValueError(f'OVERLAP: {stock_id} held by {self.book.owner[stock_id]}')
        self.book.positions[stock_id], self.book.owner[stock_id] = position, self.name

    def close_position(self, stock_id):
        self.book.owner.pop(stock_id)
        return self.book.positions.pop(stock_id)

    def checkpoint(self):
        return self.book.checkpoint()

    def rollback(self, state):
        self.book.rollback(state)


def sleeve_engine(base):
    """A V2 engine class whose buys respect CONTEND_V1 on the shared Book."""

    class Sleeve(base):
        def buy(self, stock_id, price, date_str):
            book, name = self.portfolio.book, self.portfolio.name
            if stock_id in book.positions and book.owner[stock_id] != name:
                book.reject(date_str, name, stock_id, 'OVERLAP_SKIPPED')
                return
            cash, held, count = self.capital, stock_id in self.positions, len(self.positions)
            super().buy(stock_id, price, date_str)
            limit = self._get_max_holdings(getattr(self, '_execution_asof', None) or date_str)
            if not held and stock_id not in self.positions and count < limit and self.initial_capital * runner.POSITION_SIZE > cash:
                book.reject(date_str, name, stock_id, 'CASH_CONTENTION')

    Sleeve.__name__ = f'Sleeve_{base.__name__}'
    return Sleeve


def run_days(sleeves, weights, book, sessions, rebalance='REBAL_NONE') -> dict:
    """The session loop of BacktestEngine.run, interleaved across sleeves; returns NAV and the per-session positions (+ CASH row)."""
    if rebalance not in REBALANCES:
        raise ValueError(f'UNKNOWN_REBALANCE: {rebalance}')
    nav, rows = [], []
    for i, day in enumerate(sessions):
        if rebalance == 'REBAL_ANNUAL_V1' and i and day[:4] != sessions[i - 1][:4]:
            for engine, weight in zip(sleeves, weights):
                engine.initial_capital = nav[-1][1] * weight
        for engine in sleeves:
            engine._apply_corporate_actions(day)
        for engine in sleeves:
            engine.execute_pending_open(day)
        trends = [engine.get_market_trend(day) for engine in sleeves]
        value = book.cash
        for engine in sleeves:
            for sid, info in engine.positions.items():
                mark = engine._mark_position(sid, info, day)
                value += mark
                rows.append({'date': day, 'stock_id': sid, 'shares': info['shares'], 'value': mark, 'sleeve': engine.portfolio.name})
        rows.append({'date': day, 'stock_id': 'CASH', 'shares': None, 'value': book.cash, 'sleeve': None})
        nav.append([day, value])
        for engine, trend in zip(sleeves, trends):
            engine.process_close_signals(day, trend)
    return {'nav': nav, 'positions_daily': rows}
