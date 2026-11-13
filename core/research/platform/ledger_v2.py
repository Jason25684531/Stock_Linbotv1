"""Fill Ledger V2 reconciliation (change 2026-11-12, design D3).

Rebuilds each session's cash and shares from the fills and the booked corporate actions only (actions settle before the open fills),
then checks them against the engine's recorded positions and NAV. Tolerance 0.01 + 1e-9 x NAV. The first day that differs makes the
run NOT_PUBLISHABLE; the web shows the reason instead of performance.
"""
from __future__ import annotations

from collections import defaultdict
from math import floor, isfinite


def _tolerance(nav):
    return 0.01 + 1e-9 * abs(nav)


def _finite(value):
    try:
        return isfinite(value)
    except (TypeError, ValueError, OverflowError):
        return False


def reconcile(initial_capital, nav, fills, actions, positions, open_positions) -> dict:
    def fail(day, reason):
        return {'status': 'NOT_PUBLISHABLE', 'first_mismatch': day, 'reason': reason, 'days_checked': checked}

    checked = 0
    if not nav:
        return fail(None, 'NO_SESSIONS')
    if not _finite(initial_capital):
        return fail(None, 'NONFINITE initial_capital')
    if initial_capital < 0:
        return fail(None, 'NEGATIVE initial_capital')
    days = [day for day, _ in nav]
    if any(not isinstance(day, str) or not day.strip() for day in days):
        return fail(None, 'INVALID_NAV_SESSION')
    for previous, day in zip(days, days[1:]):
        if day <= previous:
            return fail(day, 'NAV_SESSION_ORDER: dates must be unique and chronological')
    sessions = set(days)
    for group, rows in (('actions', actions), ('fills', fills), ('positions', positions)):
        for row in rows:
            required = {'date', 'stock_id'} | ({'shares', 'side', 'cash_delta', 'fill_id'} if group == 'fills' else {'value'} if group == 'positions' else set())
            if not isinstance(row, dict) or not required.issubset(row):
                return fail(row.get('date') if isinstance(row, dict) else None, f'MISSING_FIELDS {group}')
            if not isinstance(row['stock_id'], str) or not row['stock_id'].strip():
                return fail(row['date'], f'INVALID_STOCK_ID {group}')
            if group == 'positions' and row['stock_id'] != 'CASH' and 'shares' not in row:
                return fail(row['date'], 'MISSING_FIELDS positions')
            if group == 'actions' and row.get('status') not in ('BOOKED', 'DUPLICATE_SUPPRESSED', 'UNRESOLVED_NOT_BOOKED'):
                return fail(row['date'], 'INVALID_ACTION_STATUS')
            if row['date'] not in sessions:
                return fail(row['date'], f'OUTSIDE_NAV {group}')
    by_day = defaultdict(lambda: {'actions': [], 'fills': [], 'rows': []})
    for row in actions:
        by_day[row['date']]['actions'].append(row)
    for row in fills:
        by_day[row['date']]['fills'].append(row)
    for row in positions:
        by_day[row['date']]['rows'].append(row)
    cash, held, seen_fills, seen_actions = float(initial_capital), {}, set(), set()
    for day, value in nav:
        today = by_day.get(day, {'actions': [], 'fills': [], 'rows': []})
        if not _finite(value):
            return fail(day, 'NONFINITE NAV')
        fields = {'actions': ('shares_before', 'shares_after', 'cash_credited'),
                  'fills': ('shares', 'cash_delta', 'raw_price', 'price', 'notional', 'fee', 'tax', 'slippage', 'cash_after'),
                  'rows': ('value',)}
        for group, keys in fields.items():
            for row in today[group]:
                for key in keys:
                    if key in row and not _finite(row[key]):
                        return fail(day, f'NONFINITE {group}.{key}')
                if group == 'rows' and row['stock_id'] != 'CASH' and not _finite(row['shares']):
                    return fail(day, 'NONFINITE rows.shares')
        for row in today['actions']:
            action_id = row.get('event_id')
            if not isinstance(action_id, str) or not action_id.strip():
                return fail(day, 'INVALID_OR_DUPLICATE_ACTION_ID')
            if row['status'] == 'DUPLICATE_SUPPRESSED':
                if action_id not in seen_actions:
                    return fail(day, 'ORPHAN_DUPLICATE_ACTION')
                continue
            if action_id in seen_actions:
                return fail(day, 'INVALID_OR_DUPLICATE_ACTION_ID')
            seen_actions.add(action_id)
            if row['status'] == 'UNRESOLVED_NOT_BOOKED':
                continue
            if not all(key in row and _finite(row[key]) for key in ('shares_before', 'shares_after', 'cash_credited', 'share_multiplier', 'cash_per_share')):
                return fail(day, 'MISSING_FIELDS actions')
            if held.get(row['stock_id'], 0) != row['shares_before']:
                return fail(day, f"ACTION_SHARES {row['stock_id']}: ledger {held.get(row['stock_id'], 0)} != {row['shares_before']}")
            if row['shares_after'] < 0:
                return fail(day, 'NEGATIVE_ACTION_SHARES')
            exact = row['shares_before'] * row['share_multiplier']
            if not _finite(exact):
                return fail(day, 'NONFINITE action shares')
            expected_shares = floor(exact + 1e-9)
            expected_cash = row['shares_before'] * row['cash_per_share'] + (exact - expected_shares) * 10.0
            if not _finite(expected_cash) or row['share_multiplier'] < 0 or row['cash_per_share'] < 0 or row['shares_after'] != expected_shares or abs(row['cash_credited'] - expected_cash) > _tolerance(expected_cash):
                return fail(day, 'ACTION_TERMS_MISMATCH')
            held[row['stock_id']] = row['shares_after']
            cash += row['cash_credited']
            if cash < 0:
                return fail(day, 'NEGATIVE_CASH_AFTER_ACTION')
        for row in today['fills']:
            fill_id = row.get('fill_id')
            if not isinstance(fill_id, str) or not fill_id.strip():
                return fail(day, 'INVALID_FILL_ID')
            if fill_id in seen_fills:
                return fail(day, f'DUPLICATE_FILL_ID {fill_id}')
            seen_fills.add(fill_id)
            if row.get('side') not in ('BUY', 'SELL'):
                return fail(day, 'INVALID_FILL_SIDE')
            if row['shares'] <= 0 or row['shares'] != floor(row['shares']):
                return fail(day, 'INVALID_FILL_SHARES')
            if any(key not in row or not _finite(row[key]) for key in ('price', 'notional', 'fee', 'tax', 'cash_delta', 'cash_after')):
                return fail(day, 'MISSING_OR_INVALID_FILL_ACCOUNTING')
            if row['price'] <= 0 or min(row['notional'], row['fee'], row['tax']) < 0:
                return fail(day, 'INVALID_FILL_ACCOUNTING')
            notional = row['shares'] * row['price']
            expected = -(notional + row['fee'] + row['tax']) if row['side'] == 'BUY' else notional - row['fee'] - row['tax']
            if abs(row['notional'] - notional) > _tolerance(notional) or abs(row['cash_delta'] - expected) > _tolerance(notional):
                return fail(day, 'FILL_AMOUNT_MISMATCH')
            held[row['stock_id']] = held.get(row['stock_id'], 0) + (row['shares'] if row['side'] == 'BUY' else -row['shares'])
            cash += row['cash_delta']
            if held[row['stock_id']] < 0 or cash < 0:
                return fail(day, 'NEGATIVE_CASH_OR_SHARES')
            if abs(cash - row['cash_after']) > _tolerance(cash):
                return fail(day, 'FILL_CASH_AFTER_MISMATCH')
        held = {sid: shares for sid, shares in held.items() if shares}
        if not _finite(cash) or any(not _finite(shares) for shares in held.values()):
            return fail(day, 'NONFINITE ledger cash or shares')
        cash_rows = [r['value'] for r in today['rows'] if r['stock_id'] == 'CASH']
        recorded = {r['stock_id']: r['shares'] for r in today['rows'] if r['stock_id'] != 'CASH'}
        if len(recorded) != sum(r['stock_id'] != 'CASH' for r in today['rows']):
            return fail(day, 'DUPLICATE_POSITION')
        if len(cash_rows) != 1 or abs(cash - cash_rows[0]) > _tolerance(value):
            return fail(day, f'CASH: ledger {cash:.2f} != engine {cash_rows}')
        if recorded != held:
            return fail(day, f'SHARES: ledger {held} != engine {recorded}')
        marked = cash + sum(r['value'] for r in today['rows'] if r['stock_id'] != 'CASH')
        if not _finite(marked):
            return fail(day, 'NONFINITE marked NAV')
        if abs(marked - value) > _tolerance(value):
            return fail(day, f'NAV: cash + positions {marked:.2f} != NAV {value:.2f}')
        checked += 1
    if any(not isinstance(r, dict) or not isinstance(r.get('stock_id'), str) or not r['stock_id'].strip() or 'shares' not in r or not _finite(r['shares']) for r in open_positions):
        return fail(nav[-1][0], 'NONFINITE open_positions.shares')
    end = {r['stock_id']: r['shares'] for r in open_positions}
    if len(end) != len(open_positions):
        return fail(nav[-1][0], 'DUPLICATE_OPEN_POSITION')
    if end != held:
        return fail(nav[-1][0], f'END_POSITIONS: ledger {held} != open_positions {end}')
    return {'status': 'PUBLISHABLE', 'first_mismatch': None, 'reason': None, 'days_checked': checked}


def daily_ledger(nav, positions) -> list[dict]:
    """One row per session: cash, marked positions, NAV and number of holdings, from the engine's recorded positions."""
    rows = defaultdict(list)
    for row in positions:
        rows[row['date']].append(row)
    out = []
    for day, value in nav:
        today = rows.get(day, [])
        held = [r for r in today if r['stock_id'] != 'CASH']
        cash = next((r['value'] for r in today if r['stock_id'] == 'CASH'), None)
        out.append({'date': day, 'cash': cash, 'positions_value': sum(r['value'] for r in held), 'nav': value, 'holdings': len(held)})
    return out
