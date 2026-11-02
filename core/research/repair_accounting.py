"""Opt-in raw-price Research accounting; official corporate-action certification is not yet available."""
from __future__ import annotations

from copy import deepcopy
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
import math
import re

from core.backtest.costs import CostModel
from core.backtest.execution import CAP, cap_shares, fill_check
from core.runtime import operation_policy


class DataBlocked(ValueError):
    """Required market, corporate-action or settlement evidence is absent."""


class UnresolvedCorporateAction(DataBlocked):
    def __init__(self, *, date, stock_id, holdings, missing_terms):
        self.details = {'status': 'NOT_COMPUTABLE', 'reason': 'UNRESOLVED_CORPORATE_ACTION_HELD',
                        'date': date, 'stock_id': stock_id, 'holdings': holdings,
                        'missing_terms': missing_terms, 'stop_reason': 'Required corporate-action terms are unavailable.'}
        super().__init__('UNRESOLVED_CORPORATE_ACTION_HELD')


TAIPEI = timezone(timedelta(hours=8))
HYPOTHESIS = 'A191_132:PIT-RAW-ACTION-12CFG-V1'


def registration() -> dict:
    """Fixed A191 repair hypothesis; registration alone does not consume a trial."""
    return {'hypothesis_id': HYPOTHESIS, 'strategy': 'A191_132',
            'hypothesis': 'Evaluate the same 12 archived A191_132 configurations with point-in-time selection, raw-price corporate actions and realistic order accounting.',
            'falsification_test': 'With certified data and credible fills, reject when no configuration passes unchanged g1-g5; incomplete evidence is BLOCKED.',
            'data_window': 'development window 2023-02-13..2025-12-29; no 2026 OOS', 'trial_budget': '12',
            'source': 'strategy_search_manifest.csv sha256 0db6a8a98906dc9312ce1ba8fc2378c9c6e6e51475a03fbe6148e9eb1ae31b63'}


def _timestamp(value):
    try:
        parsed = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            raise ValueError('timezone required')
        return parsed.astimezone(timezone.utc)
    except (TypeError, ValueError) as exc:
        raise DataBlocked('INVALID_SIGNAL_OR_AVAILABILITY_TIMESTAMP') from exc


def _day(value):
    try:
        return date.fromisoformat(value).isoformat()
    except (TypeError, ValueError) as exc:
        raise DataBlocked('INVALID_DATE') from exc


def _number(value, name, *, positive=False):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0 or (positive and value == 0):
        raise DataBlocked(f'INVALID_{name.upper()}')
    return float(value)


UNRESOLVED_POLICIES = ('STOP', 'FORFEIT_UNKNOWN_ENTITLEMENTS')
EXIT_ACTION = 'reference_change_exit'  # V5: exit at the official pre-suspension close on the resumption session
EXIT_SUSPENSION_LIMIT = 30  # sessions a suspension may last when a resumption session is already on file


def _forfeitable(missing, policy):
    """Only dividend/rights unknowns may be forfeited; share-count or trading-status unknowns always stop."""
    from core.research.research_mode import STOP_TERMS
    return policy == 'FORFEIT_UNKNOWN_ENTITLEMENTS' and not (set(missing) & STOP_TERMS)


def _forfeit_value(shares, value_per_share):
    if value_per_share is None:
        return None
    value = _number(value_per_share, 'value_per_share')
    return float(Decimal(str(value)) * shares)


OVERLAP_POLICIES = ('STOP', 'HOLDINGS_ONLY')


def simulate(*, bars, orders, actions, action_coverage, initial_cash, cost, slippage, close_hook=None, fractional_policy='BLOCK', unresolved_events=(), max_stale_sessions=None, unresolved_policy='STOP', overlap_policy='STOP'):
    """Execute explicit market orders against raw bars and explicit action terms.

    This prototype cannot certify caller-provided corporate actions or coverage. Every result is
    labelled BLOCKED_UNVERIFIED_OFFICIAL_TERMS, regardless of supplied data. It performs no I/O.
    """
    if operation_policy.current_mode() is not operation_policy.ExecutionMode.RESEARCH:
        raise DataBlocked('RESEARCH_MODE_REQUIRED')
    if fractional_policy not in ('BLOCK', 'DROP'):
        raise DataBlocked('INVALID_FRACTIONAL_POLICY')
    if unresolved_policy not in UNRESOLVED_POLICIES:
        raise DataBlocked('INVALID_UNRESOLVED_POLICY')
    if overlap_policy not in OVERLAP_POLICIES:
        raise DataBlocked('INVALID_OVERLAP_POLICY')
    if max_stale_sessions is not None and (isinstance(max_stale_sessions, bool) or not isinstance(max_stale_sessions, int) or max_stale_sessions < 0):
        raise DataBlocked('INVALID_STALE_LIMIT')
    cash = _number(initial_cash, 'initial_cash', positive=True)
    slip = _number(slippage, 'slippage')
    if slip >= 1 or not isinstance(cost, CostModel):
        raise DataBlocked('INVALID_COST_OR_SLIPPAGE')
    for value, name in ((cost.fee_rate, 'fee_rate'), (cost.tax_rate, 'tax_rate'), (cost.minimum_fee, 'minimum_fee')):
        _number(value, name)
    if not isinstance(bars, dict) or not bars or not isinstance(action_coverage, dict) or not isinstance(actions, list) or not isinstance(orders, list):
        raise DataBlocked('MISSING_ACTION_COVERAGE_OR_BARS')
    calendar = sorted(_day(day) for day in bars)
    if len(set(calendar)) != len(calendar) or any(day not in bars for day in calendar):
        raise DataBlocked('INVALID_CALENDAR')
    if calendar[-1] > '2025-12-31':
        raise DataBlocked('OOS_BLOCKED')
    coverage = {str(asset): {_day(day) for day in days} for asset, days in action_coverage.items()}
    unresolved_by_date = {}
    for event in unresolved_events:
        try:
            day, asset, missing = _day(event['ex_date']), str(event['stock_id']), event['missing_terms']
        except (KeyError, TypeError) as exc:
            raise DataBlocked('INVALID_UNRESOLVED_EVENT') from exc
        if day not in bars or not asset or not isinstance(missing, list) or not missing or not all(isinstance(v, str) and v for v in missing):
            raise DataBlocked('INVALID_UNRESOLVED_EVENT')
        unresolved_by_date.setdefault(day, []).append((asset, missing, event.get('source_event_date'), event.get('value_per_share')))
    action_by_date = {}
    seen = set()
    exit_schedule = {}
    for action in actions:
        try:
            day, asset, kind = _day(action['ex_date']), str(action['stock_id']), action['type']
            source, source_hash = action['source'], action['source_sha256']
            if kind == EXIT_ACTION:
                cash_per, ratio, cash_day, stock_day = 0.0, 0.0, None, None
                extra = {'pre_close': _number(action['pre_close'], 'pre_close', positive=True),
                         'reference_price': _number(action['reference_price'], 'reference_price', positive=True), 'reason': str(action['reason'])}
            else:
                cash_per = _number(action['cash_per_share'], 'cash_per_share')
                ratio = _number(action['stock_ratio'], 'stock_ratio')
                cash_day = _day(action['cash_pay_date']) if cash_per else None
                stock_day = _day(action['stock_credit_date']) if ratio else None
                extra = None
        except (KeyError, TypeError) as exc:
            raise DataBlocked('MISSING_ACTION_TERMS') from exc
        if (not asset or not isinstance(source, str) or not source.strip() or not isinstance(source_hash, str)
                or not re.fullmatch(r'[0-9a-fA-F]{64}', source_hash) or (day, asset) in seen or day not in bars or kind not in ('cash', 'stock', 'cash_stock', EXIT_ACTION)
                or (cash_per > 0) != (kind in ('cash', 'cash_stock')) or (ratio > 0) != (kind in ('stock', 'cash_stock'))
                or (cash_day and (cash_day < day or cash_day not in bars)) or (stock_day and (stock_day < day or stock_day not in bars))):
            raise DataBlocked('UNSUPPORTED_OR_INCOMPLETE_ACTION')
        seen.add((day, asset))
        action_by_date.setdefault(day, []).append((asset, kind, cash_per, ratio, cash_day, stock_day, source, source_hash.lower(), extra))
        if kind == EXIT_ACTION:
            exit_schedule.setdefault(asset, []).append(calendar.index(day))
    scheduled = {}
    ids = set()
    def schedule(order):
        try:
            day, asset, side, order_id, shares = _day(order['date']), str(order['stock_id']), order['side'], str(order['id']), order['shares']
            signal, available = _timestamp(order['signal_at']), _timestamp(order['latest_input_available_at'])
        except (KeyError, TypeError) as exc:
            raise DataBlocked('INVALID_ORDER') from exc
        if available > signal or signal.astimezone(TAIPEI).date().isoformat() >= day:
            raise DataBlocked('ORDER_LOOKAHEAD_BLOCKED')
        if day not in bars or not asset or not order_id or order_id in ids or side not in ('buy', 'sell') or isinstance(shares, bool) or not isinstance(shares, int) or shares <= 0:
            raise DataBlocked('INVALID_ORDER')
        ids.add(order_id)
        scheduled.setdefault(day, []).append({'id': order_id, 'stock_id': asset, 'side': side, 'remaining': shares,
                                              'signal_at': signal.isoformat(), 'latest_input_available_at': available.isoformat()})
    for order in orders:
        schedule(order)
    holdings, receivable_cash, receivable_stock, pending, ledger, daily = {}, [], [], [], [], []
    last_close, generated_orders, cancellations, stale_count = {}, [], [], {}

    def log(day, kind, **data):
        ledger.append({'date': day, 'kind': kind, **data})

    for day_index, day in enumerate(calendar):
        if max_stale_sessions is not None:
            held = set(holdings) | {claim['stock_id'] for claim in receivable_stock}
            stale_count = {asset: (stale_count.get(asset, 0) + 1 if asset not in bars[day] or bars[day][asset].get('volume', 0) <= 0 else 0)
                           for asset in held}
            for asset, count in stale_count.items():
                # An announced reference change explains the suspension until its resumption session.
                scheduled_exit = any(day_index < index <= day_index + EXIT_SUSPENSION_LIMIT for index in exit_schedule.get(asset, ()))
                if count > max_stale_sessions and not scheduled_exit:
                    shares = holdings.get(asset, 0) + sum(claim['shares'] for claim in receivable_stock if claim['stock_id'] == asset)
                    raise UnresolvedCorporateAction(date=day, stock_id=asset, holdings=shares,
                                                    missing_terms=['suspension_or_delisting_status'])
        for asset, missing, source_event_date, value_per_share in unresolved_by_date.get(day, ()):
            shares = holdings.get(asset, 0) + sum(r['shares'] for r in receivable_stock if r['stock_id'] == asset)
            if shares and _forfeitable(missing, unresolved_policy):
                log(day, 'ENTITLEMENT_FORFEITED', stock_id=asset, shares=shares, missing_terms=list(missing),
                    source_event_date=source_event_date, value_per_share=value_per_share,
                    forfeited_value=_forfeit_value(shares, value_per_share))
                if asset not in bars[day]:
                    last_close.pop(asset, None)  # the pre-event mark no longer values the position
            elif shares:
                failure = UnresolvedCorporateAction(date=day, stock_id=asset, holdings=shares, missing_terms=missing)
                if source_event_date:
                    failure.details['source_event_date'] = source_event_date
                raise failure
        for asset in set(holdings) | {r['stock_id'] for r in receivable_stock}:
            if day not in coverage.get(asset, set()):
                raise DataBlocked(f'ACTION_COVERAGE_UNKNOWN:{asset}:{day}')
        for asset, kind, cash_per, ratio, cash_day, stock_day, source, source_hash, extra in action_by_date.get(day, []):
            shares = holdings.get(asset, 0)  # ex-date entitlement belongs to pre-open holders
            uncredited = sum(r['shares'] for r in receivable_stock if r['stock_id'] == asset)
            if shares and uncredited and overlap_policy == 'STOP':
                raise DataBlocked('OVERLAPPING_STOCK_ENTITLEMENT')
            overlap = {'uncredited_shares': uncredited} if overlap_policy == 'HOLDINGS_ONLY' else {}
            if kind == EXIT_ACTION:
                log(day, 'ACTION', stock_id=asset, action_type=kind, pre_close=extra['pre_close'], reference_price=extra['reference_price'],
                    reason=extra['reason'], source=source, source_sha256=source_hash, basis_shares=shares, **overlap)
                exited = shares + (uncredited if overlap_policy == 'HOLDINGS_ONLY' else 0)
                if exited:
                    gross = exited * extra['pre_close']
                    fee, tax = cost.buy_cost(gross), int(gross * cost.tax_rate)
                    cash += gross - fee - tax
                    holdings.pop(asset, None)
                    if overlap_policy == 'HOLDINGS_ONLY':
                        receivable_stock[:] = [r for r in receivable_stock if r['stock_id'] != asset]
                    log(day, 'FORCED_EXIT', stock_id=asset, shares=exited, price=extra['pre_close'], notional=gross, fee=fee, tax=tax, reason=extra['reason'])
                continue
            log(day, 'ACTION', stock_id=asset, action_type=kind, cash_per_share=cash_per, stock_ratio=ratio,
                cash_pay_date=cash_day, stock_credit_date=stock_day, source=source, source_sha256=source_hash, basis_shares=shares, **overlap)
            if shares and cash_per:
                amount = float(Decimal(str(cash_per)) * shares)
                receivable_cash.append({'date': cash_day, 'stock_id': asset, 'amount': amount})
                log(day, 'CASH_ENTITLEMENT', stock_id=asset, amount=amount)
            if shares and ratio:
                try:
                    entitlement = Decimal(str(ratio)) * shares
                    if entitlement != entitlement.to_integral_value() and fractional_policy == 'BLOCK':
                        raise DataBlocked('FRACTIONAL_STOCK_SETTLEMENT_UNKNOWN')
                except InvalidOperation as exc:
                    raise DataBlocked('INVALID_STOCK_RATIO') from exc
                count = int(entitlement)
                if entitlement != count:
                    try:
                        price = _number(bars[day][asset]['close'], 'fractional_close', positive=True)
                    except (KeyError, TypeError) as exc:
                        raise DataBlocked('FRACTIONAL_CLOSE_UNAVAILABLE') from exc
                    fraction = float(entitlement - count)
                    log(day, 'FRACTIONAL_DROPPED', stock_id=asset, shares=fraction,
                        unmodeled_fractional_value=fraction * price)
                receivable_stock.append({'date': stock_day, 'stock_id': asset, 'shares': count})
                log(day, 'STOCK_ENTITLEMENT', stock_id=asset, shares=count)
        for item in list(receivable_cash):
            if item['date'] == day:
                cash += item['amount']
                log(day, 'CASH_CREDIT', stock_id=item['stock_id'], amount=item['amount'])
                receivable_cash.remove(item)
        for item in list(receivable_stock):
            if item['date'] == day:
                holdings[item['stock_id']] = holdings.get(item['stock_id'], 0) + item['shares']
                log(day, 'STOCK_CREDIT', stock_id=item['stock_id'], shares=item['shares'])
                receivable_stock.remove(item)
        pending.extend(scheduled.get(day, []))
        used = {}
        for order in sorted(list(pending), key=lambda x: (x['side'] != 'sell', x['id'])):
            asset, side, requested = order['stock_id'], order['side'], order['remaining']
            bar = bars[day].get(asset)
            try:
                reason = fill_check(side, bar)
                if reason is None and day_index and asset not in bars[calendar[day_index - 1]]:
                    reason = 'REJECTED_PRIOR_AMOUNT_UNVERIFIABLE'  # no previous-session trade: the 5% cap base is undefined
                if reason is None:
                    price = _number(bar['open'], 'open', positive=True) * (1 + slip if side == 'buy' else 1 - slip)
                    prior = _number(bar.get('prior_amount'), 'prior_amount', positive=True)
                    available = max(0.0, CAP * prior - used.get(asset, 0.0))
                    allowed = min(cap_shares(requested, price, prior), int((available + 1e-9) / price))
                    if side == 'sell':
                        allowed = min(allowed, holdings.get(asset, 0))
                        if allowed and cash + allowed * price - cost.sell_cost(allowed * price) < 0:
                            allowed = 0
                    else:
                        low, high = 0, allowed
                        while low < high:
                            mid = (low + high + 1) // 2
                            gross = mid * price
                            if gross + cost.buy_cost(gross) <= cash:
                                low = mid
                            else:
                                high = mid - 1
                        allowed = low
                    if not allowed:
                        reason = 'CAP_CASH_OR_HOLDINGS'
            except (AttributeError, TypeError, KeyError, ValueError) as exc:
                if isinstance(exc, DataBlocked):
                    reason = str(exc)
                else:
                    reason = 'INVALID_BAR'
            filled = allowed if reason is None else 0
            if filled:
                gross = filled * price
                fee = cost.buy_cost(gross)
                tax = int(gross * cost.tax_rate) if side == 'sell' else 0
                cash += (-gross - fee if side == 'buy' else gross - fee - tax)
                holdings[asset] = holdings.get(asset, 0) + (filled if side == 'buy' else -filled)
                if not holdings[asset]:
                    del holdings[asset]
                used[asset] = used.get(asset, 0.0) + gross
                order['remaining'] -= filled
                log(day, 'FILL', order_id=order['id'], stock_id=asset, side=side, shares=filled,
                    price=price, notional=gross, fee=fee, tax=tax)
            log(day, 'ORDER_STATUS', order_id=order['id'], stock_id=asset, side=side, signal_at=order['signal_at'],
                latest_input_available_at=order['latest_input_available_at'], status='FILLED' if not order['remaining'] else ('PARTIAL' if filled else 'UNFILLED'),
                requested=requested, filled=filled, remaining=order['remaining'], reason=reason)
            if not order['remaining']:
                pending.remove(order)
        required = set(holdings) | {r['stock_id'] for r in receivable_stock}
        marks, stale_marks = {}, {}
        for asset, *_ in action_by_date.get(day, []):
            if asset not in bars[day]:
                last_close.pop(asset, None)  # an ex-event invalidates the pre-event mark
        for asset in required:
            if day not in coverage.get(asset, set()):
                raise DataBlocked(f'ACTION_COVERAGE_UNKNOWN:{asset}:{day}')
            try:
                if asset not in bars[day]:
                    marks[asset], stale_marks[asset] = last_close[asset]
                else:
                    marks[asset] = _number(bars[day][asset]['close'], 'close', positive=True)
            except (KeyError, TypeError) as exc:
                raise DataBlocked(f'OFFICIAL_MARK_MISSING:{asset}:{day}') from exc
        cash_due = sum(item['amount'] for item in receivable_cash)
        stock_due = sum(item['shares'] * marks[item['stock_id']] for item in receivable_stock)
        nav = cash + cash_due + stock_due + sum(shares * marks[asset] for asset, shares in holdings.items())
        if not math.isfinite(nav) or not math.isfinite(cash):
            raise DataBlocked('NONFINITE_ACCOUNT_STATE')
        daily.append({'date': day, 'cash': cash, 'positions': dict(holdings), 'cash_receivable': cash_due,
                      'cash_receivable_claims': [dict(item) for item in receivable_cash],
                      'stock_receivable': [dict(item) for item in receivable_stock], 'marks': marks,
                      'stale_marks': stale_marks, 'nav': nav})
        if close_hook is not None:
            decision = close_hook(deepcopy(daily[-1]), deepcopy(pending))
            if decision is not None:
                cancel = decision.get('cancel', [])
                new_orders = decision.get('orders', [])
                pending_ids = {item['id'] for item in pending}
                if len(cancel) != len(set(cancel)) or not set(cancel) <= pending_ids:
                    raise DataBlocked('INVALID_CANCELLATION')
                for order_id in cancel:
                    item = next(item for item in pending if item['id'] == order_id)
                    log(day, 'CANCEL', order_id=order_id, stock_id=item['stock_id'], side=item['side'], remaining=item['remaining'])
                    cancellations.append({'date': day, 'order_id': order_id})
                    pending.remove(item)
                next_index = day_index + 1
                for order in new_orders:
                    if (next_index >= len(calendar) or order.get('date') != calendar[next_index]
                            or _timestamp(order.get('signal_at')).astimezone(TAIPEI).date().isoformat() != day):
                        raise DataBlocked('ORDER_NOT_NEXT_SESSION')
                    schedule(order)
                    generated_orders.append(deepcopy(order))
        for asset, bar in bars[day].items():
            try:
                last_close[asset] = (_number(bar['close'], 'close', positive=True), day)
            except (KeyError, TypeError, DataBlocked):
                last_close.pop(asset, None)
    return {'label': 'DIAGNOSTIC_ONLY_RAW_ACCOUNTING', 'data_status': 'BLOCKED_UNVERIFIED_OFFICIAL_TERMS',
            'price_basis': 'CALLER_SUPPLIED_RAW_UNVERIFIED', 'daily': daily, 'ledger': ledger, 'pending_orders': pending,
            'generated_orders': generated_orders, 'cancellations': cancellations,
            'stock_receivable_valuation': 'SAME_STOCK_CLOSE_UNVERIFIED',
            'cost': {'fee_rate': cost.fee_rate, 'tax_rate': cost.tax_rate, 'minimum_fee': cost.minimum_fee}, 'slippage': slip}


def _independent_expected_fill(order, bar, cash, holdings, used, cost, slippage, prior_known=True):
    """Independently derive the fill expected from the inputs and pre-order state."""
    try:
        if bar is None or any(bar.get(k) is None or not math.isfinite(bar[k]) for k in ('limit_up', 'limit_down')):
            return 0, None, 'REJECTED_NO_BAR'
        fields = [bar.get(k) for k in ('open', 'high', 'low', 'close', 'volume', 'amount')]
        if any(v is None or not math.isfinite(v) for v in fields) or bar['volume'] <= 0 or bar['amount'] <= 0 or bar['close'] <= 0:
            return 0, None, 'REJECTED_NO_TRADE'
        locked = bar['high'] == bar['low'] == bar['open']
        if locked and order['side'] == 'buy' and bar['open'] >= bar['limit_up']:
            return 0, None, 'REJECTED_LOCKED_LIMIT_UP'
        if locked and order['side'] == 'sell' and bar['open'] <= bar['limit_down']:
            return 0, None, 'REJECTED_LOCKED_LIMIT_DOWN'
        if not prior_known:
            return 0, None, 'REJECTED_PRIOR_AMOUNT_UNVERIFIABLE'
        price = _number(bar['open'], 'open', positive=True) * (1 + slippage if order['side'] == 'buy' else 1 - slippage)
        prior = _number(bar.get('prior_amount'), 'prior_amount', positive=True)
        available = max(0.0, CAP * prior - used.get(order['stock_id'], 0.0))
        allowed = min(order['remaining'], int(CAP * prior / price), int((available + 1e-9) / price))
        if order['side'] == 'sell':
            allowed = min(allowed, holdings.get(order['stock_id'], 0))
            notional = allowed * price
            fee = max(int(notional * cost.fee_rate), cost.minimum_fee) + int(notional * cost.tax_rate)
            if allowed and cash + notional - fee < 0:
                allowed = 0
        else:
            low, high = 0, allowed
            while low < high:
                mid = (low + high + 1) // 2
                notional = mid * price
                fee = max(int(notional * cost.fee_rate), cost.minimum_fee)
                if notional + fee <= cash:
                    low = mid
                else:
                    high = mid - 1
            allowed = low
        return (allowed, price, None) if allowed else (0, price, 'CAP_CASH_OR_HOLDINGS')
    except (AttributeError, TypeError, KeyError, ValueError) as exc:
        return 0, None, str(exc) if isinstance(exc, DataBlocked) else 'INVALID_BAR'


def reconcile(initial_cash, result, *, bars, orders, actions, action_coverage, cost, slippage, cancellations=None, fractional_policy='BLOCK',
              unresolved_events=(), unresolved_policy='STOP', overlap_policy='STOP'):
    """Rebuild each daily state from the event ledger, independently of the simulator's state."""
    if overlap_policy not in OVERLAP_POLICIES:
        return {'status': 'FAIL', 'reason': 'INVALID_OVERLAP_POLICY'}
    cash, positions, cash_due, stock_due, pending_cash, pending_stock = float(initial_cash), {}, 0.0, {}, {}, {}
    pending_orders, last_close = {}, {}
    records = result['ledger']
    if fractional_policy not in ('BLOCK', 'DROP'):
        return {'status': 'FAIL', 'reason': 'INVALID_FRACTIONAL_POLICY'}
    if unresolved_policy not in UNRESOLVED_POLICIES:
        return {'status': 'FAIL', 'reason': 'INVALID_UNRESOLVED_POLICY'}
    unresolved_by_date = {}
    for event in unresolved_events:
        try:
            unresolved_by_date.setdefault(_day(event['ex_date']), []).append(event)
        except (KeyError, TypeError, DataBlocked):
            return {'status': 'FAIL', 'reason': 'INVALID_UNRESOLVED_EVENT'}
    if len({o['id'] for o in orders}) != len(orders):
        return {'status': 'FAIL', 'reason': 'DUPLICATE_ORDER'}
    try:
        if any(_day(day) > '2025-12-31' for day in bars):
            return {'status': 'FAIL', 'reason': 'OOS_BLOCKED'}
    except DataBlocked:
        return {'status': 'FAIL', 'reason': 'INVALID_CALENDAR'}
    if not result['daily'] or [r['date'] for r in result['daily']] != sorted(bars) or any(e['date'] not in bars for e in records):
        return {'status': 'FAIL', 'reason': 'INCOMPLETE_CALENDAR_OR_LEDGER'}
    if any(e.get('kind') not in ('ACTION', 'CASH_ENTITLEMENT', 'CASH_CREDIT', 'STOCK_ENTITLEMENT', 'STOCK_CREDIT', 'FRACTIONAL_DROPPED', 'FILL', 'ORDER_STATUS', 'CANCEL', 'ENTITLEMENT_FORFEITED', 'FORCED_EXIT') for e in records):
        return {'status': 'FAIL', 'reason': 'UNKNOWN_LEDGER_EVENT'}
    cancellations = cancellations or []
    if len({(c['date'], c['order_id']) for c in cancellations}) != len(cancellations) or any(c['date'] not in bars for c in cancellations):
        return {'status': 'FAIL', 'reason': 'INVALID_CANCELLATIONS'}
    if result['cost'] != {'fee_rate': cost.fee_rate, 'tax_rate': cost.tax_rate, 'minimum_fee': cost.minimum_fee} or result['slippage'] != slippage:
        return {'status': 'FAIL', 'reason': 'COST_MODEL_MISMATCH'}
    calendar = sorted(bars)
    previous_session = dict(zip(calendar[1:], calendar))
    # ponytail: scan the ledger per day; index by date if full-universe profiling shows a bottleneck.
    for row in result['daily']:
        used = {}
        day_events = [e for e in records if e['date'] == row['date']]
        # The 5% liquidity cap must come from the previous session's traded value, not a caller-supplied label.
        previous = previous_session.get(row['date'])
        for asset in {e.get('stock_id') for e in day_events if e['kind'] in ('FILL', 'ORDER_STATUS')}:
            bar, prior_bar = bars[row['date']].get(asset), bars.get(previous, {}).get(asset) if previous else None
            if bar is not None and prior_bar is not None and bar.get('prior_amount') != prior_bar.get('amount'):
                return {'status': 'FAIL', 'date': row['date'], 'reason': 'PRIOR_AMOUNT_MISMATCH'}
        for asset in set(positions) | set(stock_due):
            if row['date'] not in action_coverage.get(asset, ()):
                return {'status': 'FAIL', 'date': row['date'], 'reason': 'ACTION_COVERAGE_UNKNOWN'}
        for order in (o for o in orders if o['date'] == row['date']):
            if order['id'] in pending_orders:
                return {'status': 'FAIL', 'date': row['date'], 'reason': 'DUPLICATE_ORDER'}
            pending_orders[order['id']] = {**order, 'remaining': order['shares']}
        expected_order_ids = set(pending_orders)
        statuses = [e for e in day_events if e['kind'] == 'ORDER_STATUS']
        if len(statuses) != len(expected_order_ids) or {e['order_id'] for e in statuses} != expected_order_ids:
            return {'status': 'FAIL', 'date': row['date'], 'reason': 'ORDER_COUNT_MISMATCH'}
        if [e['order_id'] for e in statuses] != [o['id'] for o in sorted(pending_orders.values(), key=lambda x: (x['side'] != 'sell', x['id']))]:
            return {'status': 'FAIL', 'date': row['date'], 'reason': 'ORDER_SEQUENCE_MISMATCH'}
        expected_cancels = [c['order_id'] for c in cancellations if c['date'] == row['date']]
        actual_cancels = [e['order_id'] for e in day_events if e['kind'] == 'CANCEL']
        if expected_cancels != actual_cancels or any(e['kind'] != 'CANCEL' for e in day_events[len(day_events) - len(actual_cancels):] if actual_cancels):
            return {'status': 'FAIL', 'date': row['date'], 'reason': 'CANCELLATION_SEQUENCE_MISMATCH'}
        first_trade = next((i for i, e in enumerate(day_events) if e['kind'] in ('FILL', 'ORDER_STATUS')), len(day_events))
        if any(e['kind'] in ('ACTION', 'CASH_ENTITLEMENT', 'STOCK_ENTITLEMENT', 'FRACTIONAL_DROPPED', 'CASH_CREDIT', 'STOCK_CREDIT', 'ENTITLEMENT_FORFEITED', 'FORCED_EXIT') for e in day_events[first_trade:]):
            return {'status': 'FAIL', 'date': row['date'], 'reason': 'ACTION_ORDER_MISMATCH'}
        # Forfeits are derived from this independent position state, never from the simulator's claim.
        expected_forfeits = []
        for event in unresolved_by_date.get(row['date'], ()):
            asset = str(event['stock_id'])
            held = positions.get(asset, 0) + stock_due.get(asset, 0)
            if held and _forfeitable(event['missing_terms'], unresolved_policy):
                try:
                    value = _forfeit_value(held, event.get('value_per_share'))
                except DataBlocked:
                    return {'status': 'FAIL', 'date': row['date'], 'reason': 'INVALID_FORFEIT_VALUE'}
                expected_forfeits.append((asset, held, tuple(event['missing_terms']), event.get('source_event_date'), value))
            elif held:
                return {'status': 'FAIL', 'date': row['date'], 'reason': 'UNRESOLVED_HELD_EVENT_NOT_STOPPED'}
        actual_forfeits = [(e.get('stock_id'), e.get('shares'), tuple(e.get('missing_terms') or ()), e.get('source_event_date'), e.get('forfeited_value'))
                           for e in day_events if e['kind'] == 'ENTITLEMENT_FORFEITED']
        if sorted(expected_forfeits, key=repr) != sorted(actual_forfeits, key=repr):
            return {'status': 'FAIL', 'date': row['date'], 'reason': 'FORFEIT_MISMATCH'}
        inputs = [a for a in actions if a['ex_date'] == row['date']]
        logged_actions = [e for e in day_events if e['kind'] == 'ACTION']
        if len(inputs) != len(logged_actions):
            return {'status': 'FAIL', 'date': row['date'], 'reason': 'ACTION_COUNT_MISMATCH'}
        expected_entitlements = []
        expected_dropped = []
        expected_exits = []
        for action in inputs:
            asset = str(action['stock_id'])
            matches = [e for e in logged_actions if e['stock_id'] == asset]
            if len(matches) != 1:
                return {'status': 'FAIL', 'date': row['date'], 'reason': 'ACTION_ID_MISMATCH'}
            event, shares = matches[0], positions.get(asset, 0)
            uncredited = stock_due.get(asset, 0)
            if shares and uncredited and overlap_policy == 'STOP':
                return {'status': 'FAIL', 'date': row['date'], 'reason': 'OVERLAPPING_STOCK_ENTITLEMENT'}
            overlap = {'uncredited_shares': uncredited} if overlap_policy == 'HOLDINGS_ONLY' else {}
            if action['type'] == EXIT_ACTION:
                try:
                    pre_close = _number(action['pre_close'], 'pre_close', positive=True)
                    expected = {'action_type': EXIT_ACTION, 'pre_close': pre_close, 'reference_price': _number(action['reference_price'], 'reference_price', positive=True),
                                'reason': str(action['reason']), 'source': action['source'], 'source_sha256': action['source_sha256'].lower(), 'basis_shares': shares, **overlap}
                except (KeyError, TypeError, DataBlocked):
                    return {'status': 'FAIL', 'date': row['date'], 'reason': 'EXIT_TERMS_MISSING'}
                if any(event.get(k) != value for k, value in expected.items()):
                    return {'status': 'FAIL', 'date': row['date'], 'reason': 'ACTION_TERMS_MISMATCH'}
                exited = shares + (uncredited if overlap_policy == 'HOLDINGS_ONLY' else 0)
                if exited:
                    notional = exited * pre_close
                    expected_exits.append((asset, exited, pre_close, notional, max(int(notional * cost.fee_rate), cost.minimum_fee), int(notional * cost.tax_rate), str(action['reason'])))
                continue
            expected = {'action_type': action['type'], 'cash_per_share': float(action['cash_per_share']), 'stock_ratio': float(action['stock_ratio']),
                        'cash_pay_date': action['cash_pay_date'] if action['cash_per_share'] else None,
                        'stock_credit_date': action['stock_credit_date'] if action['stock_ratio'] else None,
                        'source': action['source'], 'source_sha256': action['source_sha256'].lower(), 'basis_shares': shares, **overlap}
            if any(event.get(k) != value for k, value in expected.items()):
                return {'status': 'FAIL', 'date': row['date'], 'reason': 'ACTION_TERMS_MISMATCH'}
            if shares and action['cash_per_share']:
                amount = float(Decimal(str(action['cash_per_share'])) * shares)
                expected_entitlements.append(('CASH_ENTITLEMENT', asset, amount))
                pending_cash.setdefault((action['cash_pay_date'], asset), []).append(amount)
            if shares and action['stock_ratio']:
                exact = Decimal(str(action['stock_ratio'])) * shares
                if exact != exact.to_integral_value() and fractional_policy == 'BLOCK':
                    return {'status': 'FAIL', 'date': row['date'], 'reason': 'FRACTIONAL_STOCK_SETTLEMENT_UNKNOWN'}
                count = int(exact)
                if exact != count:
                    try:
                        price = _number(bars[row['date']][asset]['close'], 'fractional_close', positive=True)
                    except (KeyError, TypeError, DataBlocked):
                        return {'status': 'FAIL', 'date': row['date'], 'reason': 'FRACTIONAL_CLOSE_UNAVAILABLE'}
                    fraction = float(exact - count)
                    expected_dropped.append((asset, fraction, fraction * price))
                expected_entitlements.append(('STOCK_ENTITLEMENT', asset, count))
                pending_stock.setdefault((action['stock_credit_date'], asset), []).append(count)
        actual_entitlements = [(e['kind'], e['stock_id'], e['amount'] if e['kind'] == 'CASH_ENTITLEMENT' else e['shares'])
                               for e in day_events if e['kind'] in ('CASH_ENTITLEMENT', 'STOCK_ENTITLEMENT')]
        if sorted(expected_entitlements) != sorted(actual_entitlements):
            return {'status': 'FAIL', 'date': row['date'], 'reason': 'ENTITLEMENT_MISMATCH'}
        actual_dropped = [(e.get('stock_id'), e.get('shares'), e.get('unmodeled_fractional_value'))
                          for e in day_events if e['kind'] == 'FRACTIONAL_DROPPED']
        if sorted(expected_dropped) != sorted(actual_dropped):
            return {'status': 'FAIL', 'date': row['date'], 'reason': 'FRACTIONAL_DROPPED_MISMATCH'}
        actual_exits = [(e.get('stock_id'), e.get('shares'), e.get('price'), e.get('notional'), e.get('fee'), e.get('tax'), e.get('reason'))
                        for e in day_events if e['kind'] == 'FORCED_EXIT']
        if sorted(expected_exits) != sorted(actual_exits):
            return {'status': 'FAIL', 'date': row['date'], 'reason': 'FORCED_EXIT_MISMATCH'}
        for asset, shares, _, notional, fee, tax, _ in expected_exits:
            cash += notional - fee - tax
            positions.pop(asset, None)
            if overlap_policy == 'HOLDINGS_ONLY' and asset in stock_due:  # uncredited shares left with the forced exit
                for key in [key for key in pending_stock if key[1] == asset]:
                    del pending_stock[key]
                del stock_due[asset]
        checked_fills = {}
        for index, event in enumerate(day_events):
            kind, asset = event['kind'], event.get('stock_id')
            if kind == 'CASH_ENTITLEMENT':
                cash_due += event['amount']
            elif kind == 'CASH_CREDIT':
                key = row['date'], asset
                if event['amount'] not in pending_cash.get(key, []):
                    return {'status': 'FAIL', 'date': row['date'], 'reason': 'CASH_SETTLEMENT_MISMATCH'}
                pending_cash[key].remove(event['amount'])
                if not pending_cash[key]:
                    del pending_cash[key]
                cash_due -= event['amount']
                cash += event['amount']
            elif kind == 'STOCK_ENTITLEMENT':
                stock_due[asset] = stock_due.get(asset, 0) + event['shares']
            elif kind == 'STOCK_CREDIT':
                key = row['date'], asset
                if event['shares'] not in pending_stock.get(key, []):
                    return {'status': 'FAIL', 'date': row['date'], 'reason': 'STOCK_SETTLEMENT_MISMATCH'}
                pending_stock[key].remove(event['shares'])
                if not pending_stock[key]:
                    del pending_stock[key]
                stock_due[asset] -= event['shares']
                if not stock_due[asset]:
                    del stock_due[asset]
                positions[asset] = positions.get(asset, 0) + event['shares']
            elif kind == 'FILL':
                direction = 1 if event['side'] == 'buy' else -1
                try:
                    pending = pending_orders[event['order_id']]
                    if (pending['stock_id'] != asset or pending['side'] != event['side'] or index + 1 >= len(day_events)
                            or day_events[index + 1]['kind'] != 'ORDER_STATUS' or day_events[index + 1]['order_id'] != event['order_id']):
                        return {'status': 'FAIL', 'date': row['date'], 'reason': 'ORPHAN_OR_MISMATCHED_FILL'}
                    bar = bars[row['date']].get(asset)
                    expected_shares, independently_priced, expected_reason = _independent_expected_fill(
                        pending, bar, cash, positions, used, cost, slippage, prior_known=previous is None or asset in bars[previous])
                    if expected_shares <= 0 or event['shares'] != expected_shares:
                        return {'status': 'FAIL', 'date': row['date'], 'reason': 'UNEXPECTED_FILL_QUANTITY'}
                    checked_fills[event['order_id']] = (expected_shares, expected_reason)
                    expected_price = _number(bar['open'], 'open', positive=True) * (1 + direction * slippage)
                    expected_notional = event['shares'] * expected_price
                    expected_fee = max(int(expected_notional * cost.fee_rate), cost.minimum_fee)
                    expected_tax = int(expected_notional * cost.tax_rate) if direction == -1 else 0
                    used[asset] = used.get(asset, 0.0) + expected_notional
                    if (event['side'] not in ('buy', 'sell') or event['shares'] <= 0 or fill_check(event['side'], bar) is not None
                            or used[asset] > CAP * _number(bar['prior_amount'], 'prior_amount', positive=True) + 1e-8
                            or not math.isclose(independently_priced, expected_price, rel_tol=1e-9, abs_tol=1e-12)
                            or not math.isclose(event['price'], expected_price, rel_tol=1e-9, abs_tol=1e-12)
                            or not math.isclose(event['notional'], expected_notional, rel_tol=1e-9, abs_tol=1e-12)
                            or event['fee'] != expected_fee or event['tax'] != expected_tax):
                        return {'status': 'FAIL', 'date': row['date'], 'reason': 'FILL_OR_COST_MISMATCH'}
                except (KeyError, TypeError, ValueError):
                    return {'status': 'FAIL', 'date': row['date'], 'reason': 'FILL_OR_COST_MISSING'}
                positions[asset] = positions.get(asset, 0) + direction * event['shares']
                if not positions[asset]:
                    del positions[asset]
                cash -= direction * event['notional'] + event['fee'] + event['tax']
                if cash < -1e-9 or any(shares < 0 for shares in positions.values()):
                    return {'status': 'FAIL', 'date': row['date'], 'reason': 'NEGATIVE_CASH_OR_POSITION'}
            elif kind == 'ORDER_STATUS':
                try:
                    signal, available = _timestamp(event['signal_at']), _timestamp(event['latest_input_available_at'])
                    pending = pending_orders[event['order_id']]
                    fills = [e for e in day_events if e['kind'] == 'FILL' and e['order_id'] == event['order_id']]
                    expected_filled = sum(e['shares'] for e in fills)
                    if fills:
                        independently_filled, independently_reason = checked_fills[event['order_id']]
                    else:
                        independently_filled, _, independently_reason = _independent_expected_fill(
                            pending, bars[row['date']].get(pending['stock_id']), cash, positions, used, cost, slippage,
                            prior_known=previous is None or pending['stock_id'] in bars[previous])
                    if (available > signal or signal.astimezone(TAIPEI).date().isoformat() >= row['date']
                            or len(fills) > 1 or event['stock_id'] != pending['stock_id'] or event['side'] != pending['side']
                            or event['filled'] != independently_filled or event['reason'] != independently_reason
                            or signal != _timestamp(pending['signal_at']) or available != _timestamp(pending['latest_input_available_at'])
                            or event['requested'] != pending['remaining'] or event['filled'] != expected_filled
                            or event['requested'] != event['filled'] + event['remaining'] or event['remaining'] < 0
                            or event['status'] != ('FILLED' if not event['remaining'] else ('PARTIAL' if event['filled'] else 'UNFILLED'))):
                        return {'status': 'FAIL', 'date': row['date'], 'reason': 'ORDER_EVIDENCE_MISMATCH'}
                    pending['remaining'] = event['remaining']
                    if not event['remaining']:
                        del pending_orders[event['order_id']]
                except (KeyError, TypeError, ValueError):
                    return {'status': 'FAIL', 'date': row['date'], 'reason': 'ORDER_EVIDENCE_MISSING'}
            elif kind == 'CANCEL':
                pending = pending_orders.get(event['order_id'])
                if (pending is None or event.get('stock_id') != pending['stock_id'] or event.get('side') != pending['side']
                        or event.get('remaining') != pending['remaining']):
                    return {'status': 'FAIL', 'date': row['date'], 'reason': 'CANCELLATION_MISMATCH'}
                del pending_orders[event['order_id']]
        if any(day <= row['date'] for day, _ in (*pending_cash, *pending_stock)):
            return {'status': 'FAIL', 'date': row['date'], 'reason': 'SETTLEMENT_MISSING'}
        expected_cash_claims = sorted((day, asset, amount) for (day, asset), amounts in pending_cash.items() for amount in amounts)
        recorded_cash_claims = sorted((claim['date'], claim['stock_id'], claim['amount']) for claim in row['cash_receivable_claims'])
        expected_stock_claims = sorted((day, asset, shares) for (day, asset), counts in pending_stock.items() for shares in counts)
        recorded_stock_claims = sorted((claim['date'], claim['stock_id'], claim['shares']) for claim in row['stock_receivable'])
        if expected_cash_claims != recorded_cash_claims or expected_stock_claims != recorded_stock_claims:
            return {'status': 'FAIL', 'date': row['date'], 'reason': 'RECEIVABLE_CLAIM_MISMATCH'}
        expected_stock = {asset: sum(item['shares'] for item in row['stock_receivable'] if item['stock_id'] == asset)
                          for asset in {item['stock_id'] for item in row['stock_receivable']}}
        if set(row['marks']) != set(positions) | set(stock_due):
            return {'status': 'FAIL', 'date': row['date'], 'reason': 'MARK_SET_MISMATCH'}
        if any(row['date'] not in action_coverage.get(asset, ()) for asset in row['marks']):
            return {'status': 'FAIL', 'date': row['date'], 'reason': 'ACTION_COVERAGE_UNKNOWN'}
        day = row['date']
        for action in inputs:
            if str(action['stock_id']) not in bars[day]:
                last_close.pop(str(action['stock_id']), None)
        for asset, *_ in expected_forfeits:
            if asset not in bars[day]:
                last_close.pop(asset, None)
        expected_stale = {}
        try:
            for asset, mark in row['marks'].items():
                if asset not in bars[day]:
                    expected, expected_stale[asset] = last_close[asset]
                else:
                    expected = _number(bars[day][asset]['close'], 'close', positive=True)
                if not math.isclose(mark, expected, rel_tol=1e-9, abs_tol=1e-12):
                    return {'status': 'FAIL', 'date': day, 'reason': 'MARK_MISMATCH'}
        except (KeyError, TypeError, DataBlocked):
            return {'status': 'FAIL', 'date': day, 'reason': 'MARK_MISSING'}
        if row.get('stale_marks', {}) != expected_stale:
            return {'status': 'FAIL', 'date': day, 'reason': 'STALE_MARK_EVIDENCE_MISMATCH'}
        for asset, bar in bars[day].items():
            try:
                last_close[asset] = (_number(bar['close'], 'close', positive=True), day)
            except (KeyError, TypeError, DataBlocked):
                last_close.pop(asset, None)
        cash_due = sum(sum(amounts) for amounts in pending_cash.values())  # rebuilt from claims, not accumulated: no float drift over years
        nav = cash + cash_due + sum((positions.get(asset, 0) + stock_due.get(asset, 0)) * mark for asset, mark in row['marks'].items())
        if positions != row['positions'] or stock_due != expected_stock or not all(math.isclose(a, b, rel_tol=1e-9, abs_tol=1e-12)
                                                                      for a, b in ((cash, row['cash']), (cash_due, row['cash_receivable']), (nav, row['nav']))):
            return {'status': 'FAIL', 'date': row['date'], 'reason': 'STATE_MISMATCH',
                    'detail': {'positions_match': positions == row['positions'], 'stock_due_match': stock_due == expected_stock,
                               'cash': (cash, row['cash']), 'cash_receivable': (cash_due, row['cash_receivable']), 'nav': (nav, row['nav'])}}
    if {o['id']: o['remaining'] for o in result['pending_orders']} != {key: o['remaining'] for key, o in pending_orders.items()}:
        return {'status': 'FAIL', 'reason': 'PENDING_ORDER_MISMATCH'}
    return {'status': 'PASS', 'days': len(result['daily'])}
