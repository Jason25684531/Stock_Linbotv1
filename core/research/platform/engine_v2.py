"""Backtest engine V2: the unchanged BacktestEngine plus a fill ledger and versioned lot rules (change 2026-11-12, design D3).

Every real fill produces exactly one row with a stable order-derived id; attempts stay in execution_events and link to
it by fill_id. Corporate-action settlements are already one record each in corporate_action_events and are never a second fill.
A fill that raises restores cash, positions, trades and fills, so a retry books it once. The parent's trading logic is not copied,
except LOT_ODD's buy (see _buy_odd).
"""
from __future__ import annotations

import contextlib
import copy
import random

from core.backtest import runner

ENGINE_VERSION = 'ENGINE_V2_FILL_LEDGER_1'
RULES = {'LOT_ENGINE_V1': True, 'LOT_BOARD_1000': False, 'LOT_ODD': False}  # rule -> day-by-day comparable with V1 runs
BOARD_LOT = 1000


def fill_id(day, side, stock_id, signal_date, order_id=None, event_id=None):
    base = f'{day}|{side}|{stock_id}|{signal_date}'
    return f'{side}|{stock_id}|{order_id}|{event_id}' if event_id is not None else f'{side}|{stock_id}|{order_id}|{day}' if order_id is not None else base


class BacktestEngineV2(runner.BacktestEngine):
    rule = 'LOT_ENGINE_V1'
    comparable_with_v1 = True

    @property
    def fills(self) -> dict:
        return self.__dict__.setdefault('_fills', {})  # lazily: engines built without __init__ (tests) have it too

    def fill_rows(self) -> list[dict]:
        return list(self.fills.values())

    def _record_fill(self, row):
        self.fills.setdefault(row['fill_id'], row)  # idempotent: a repeated event id changes nothing

    def _already_recorded(self, day, side, stock_id, order_id, event_id, raw_price):
        signal = getattr(self, '_execution_asof', None) or day
        if event_id is not None:
            for row in self.fills.values():
                if row.get('event_id') == event_id:
                    original = (row.get('order_id'), row['side'], row['stock_id'], row['date'],
                                row.get('signal_date'), row.get('raw_price'))
                    replay = (order_id, side, stock_id, day, signal, float(raw_price))
                    if original != replay:
                        raise ValueError('FILL_EVENT_ID_CONFLICT')
                    return True
        if fill_id(day, side, stock_id, signal, order_id, event_id) in self.fills:
            return True
        return order_id is not None and any(
            row.get('order_id') == order_id and row['side'] == side and row['stock_id'] == stock_id
            and row['status'] == 'FILLED' for row in self.fills.values()
        )

    @contextlib.contextmanager
    def _transaction(self):
        shared = hasattr(self.portfolio, 'checkpoint')  # a portfolio sleeve: roll the shared book back in place, never replace the view
        book = self.portfolio.checkpoint() if shared else copy.deepcopy(self.portfolio)
        names = ('trades', 'trade_count', 'win_count', '_fills', '_applied_action_ids', 'corporate_action_events',
                 'pending_orders', 'execution_events', 'unchecked_fill_orders', '_execution_asof', '_fill', '_exec_price', '_side',
                 '_v2_order_sequence', '_v2_order_id', '_v2_event_id')
        saved = {name: copy.deepcopy(self.__dict__[name]) for name in names if name in self.__dict__}
        random_state = random.getstate()
        paid = getattr(self.costs, 'paid', None)
        try:
            yield
        except BaseException:
            if shared:
                self.portfolio.rollback(book)
            else:
                self.portfolio = book
            for name in names:
                if name in saved:
                    self.__dict__[name] = saved[name]
                else:
                    self.__dict__.pop(name, None)
            random.setstate(random_state)
            if paid is not None:
                self.costs.paid = paid
            raise

    def _apply_execution_slippage(self, price, side):
        self._exec_price = super()._apply_execution_slippage(price, side)
        return self._exec_price

    def _capped(self, shares, price):
        shares = super()._capped(shares, price)
        if self.rule == 'LOT_BOARD_1000' and getattr(self, '_side', None) == 'BUY':
            shares -= shares % BOARD_LOT
            if getattr(self, '_fill', None) and self._fill.get('checked'):
                self._fill['value'] = shares * price
        return shares

    def _fill_row(self, side, sid, day, raw_price, shares, cash_before):
        price = self._exec_price
        notional = shares * price
        delta = self.capital - cash_before
        tax = int(notional * getattr(self.costs, 'model', self.costs).tax_rate) if side == 'SELL' else 0
        fee = (-delta - notional) if side == 'BUY' else (notional - delta - tax)
        signal = getattr(self, '_execution_asof', None) or day
        order_id = getattr(self, '_v2_order_id', None)
        event_id = getattr(self, '_v2_event_id', None)
        return {'fill_id': fill_id(day, side, sid, signal, order_id, event_id), 'order_id': order_id, 'event_id': event_id, 'date': day, 'signal_date': signal, 'side': side, 'stock_id': sid,
                'shares': shares, 'raw_price': float(raw_price), 'price': price, 'notional': notional, 'fee': fee, 'tax': tax,
                'slippage': abs(price - float(raw_price)) * shares, 'cash_delta': delta, 'cash_after': self.capital}

    def buy(self, stock_id, price, date_str, *, order_id=None, event_id=None):
        previous_order_id = getattr(self, '_v2_order_id', None)
        previous_event_id = getattr(self, '_v2_event_id', None)
        order_id = order_id if order_id is not None else previous_order_id
        event_id = event_id if event_id is not None else previous_event_id
        if self._already_recorded(date_str, 'BUY', stock_id, order_id, event_id, price):
            return
        held, cash = stock_id in self.positions, self.capital
        with self._transaction():
            self._v2_order_id = order_id
            self._v2_event_id = event_id
            self._side = 'BUY'
            try:
                (self._buy_odd if self.rule == 'LOT_ODD' else super().buy)(stock_id, price, date_str)
            finally:
                self._side = None
            if not held and stock_id in self.positions:
                row = self._fill_row('BUY', stock_id, date_str, price, self.positions[stock_id]['shares'], cash)
                capped = (getattr(self, '_fill', None) or {}).get('capped_to')
                self._record_fill(row | {'status': 'PARTIAL' if capped else 'FILLED'})
            self._v2_order_id = previous_order_id
            self._v2_event_id = previous_event_id

    def sell(self, stock_id, price, date_str, reason, *, order_id=None, event_id=None):
        previous_order_id = getattr(self, '_v2_order_id', None)
        previous_event_id = getattr(self, '_v2_event_id', None)
        order_id = order_id if order_id is not None else previous_order_id
        event_id = event_id if event_id is not None else previous_event_id
        if self._already_recorded(date_str, 'SELL', stock_id, order_id, event_id, price):
            return
        if stock_id not in self.positions:
            return
        held, cash = self.positions[stock_id]['shares'], self.capital
        with self._transaction():
            self._v2_order_id = order_id
            self._v2_event_id = event_id
            super().sell(stock_id, price, date_str, reason)
            left = self.positions[stock_id]['shares'] if stock_id in self.positions else 0
            if left < held:
                row = self._fill_row('SELL', stock_id, date_str, price, held - left, cash)
                self._record_fill(row | {'status': 'PARTIAL' if left else 'FILLED', 'reason': reason})
            self._v2_order_id = previous_order_id
            self._v2_event_id = previous_event_id

    def execute_pending_open(self, session):
        with self._transaction():
            if self.execution_contract != 'next_session_open_v2':
                return
            orders = self.pending_orders
            for source in orders:
                order = dict(source)
                if 'order_id' not in order:
                    self._v2_order_sequence = getattr(self, '_v2_order_sequence', 0) + 1
                    owner = getattr(self.portfolio, 'name', 'single')
                    order['order_id'] = f'{owner}:order-{self._v2_order_sequence}'
                self._v2_order_id = order['order_id']
                self._v2_event_id = order.get('event_id')
                self.pending_orders = [order]
                first = len(self.execution_events)
                super().execute_pending_open(session)
                event = self.execution_events[first]
                if event['status'] != 'UNFILLED':
                    event['fill_id'] = fill_id(session, event['side'], event['stock_id'], event['signal_date'], order['order_id'], order.get('event_id'))
            self.pending_orders = []
            self._v2_order_id = None
            self._v2_event_id = None

    def _apply_corporate_actions(self, date_str):
        with self._transaction():
            super()._apply_corporate_actions(date_str)

    def _buy_odd(self, stock_id, price, date_str):
        # ponytail: BacktestEngine.buy without its one-board-lot budget check. runner.py is a V1 identity file, so it cannot gain a
        # hook; keep this in step with runner.BacktestEngine.buy (test_platform_v2_engine covers both rules).
        if len(self.positions) >= self._get_max_holdings(getattr(self, '_execution_asof', None) or date_str) or stock_id in self.positions:
            return
        budget = self.initial_capital * runner.POSITION_SIZE
        if budget > self.capital:
            budget = self.capital * 0.9
        actual = self._apply_execution_slippage(price, 'buy')
        shares = self._capped(int(budget / price), actual)
        if shares <= 0:
            return
        cost = shares * actual
        fee = self.costs.buy_cost(cost)
        if self.capital < cost + fee:
            return
        self.portfolio.debit(cost + fee)
        self.portfolio.open_position(stock_id, {'shares': shares, 'cost': actual, 'total_cost': cost + fee, 'days': 0,
                                                'stop_loss': price * (1 - self.stop_loss_pct), 'highest': actual, 'buy_date': date_str})


def rule_engine(rule):
    """The engine class for one lot rule; non-default rules are flagged as not comparable day by day with V1."""
    if rule not in RULES:
        raise ValueError(f'UNKNOWN_RULE_VERSION: {rule}')
    return type(f'BacktestEngineV2_{rule}', (BacktestEngineV2,), {'rule': rule, 'comparable_with_v1': RULES[rule]})
