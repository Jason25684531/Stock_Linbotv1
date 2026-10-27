"""Full paper-trading lifecycle on isolated synthetic data: data -> ranking -> signal -> orders -> fills -> cash -> positions -> P&L -> risk -> report.
Nothing here touches outputs/research_shadow or outputs/paper_trading; the label is PAPER_REHEARSAL throughout."""
import hashlib
import json
from pathlib import Path

import numpy as np
import pytest

from scripts import paper_trading as pt

ROOT = Path(__file__).resolve().parents[1]
WATCHED = [ROOT / 'outputs/research_shadow/ledger.jsonl', ROOT / 'outputs/research_shadow/head.json']
STOCKS = [f'S{i:02d}' for i in range(10)]


def synthetic(days=30, seed=7):
    rng = np.random.default_rng(seed)
    price = {s: 50.0 + 10 * i for i, s in enumerate(STOCKS)}
    sessions = []
    for d in range(days):
        day, bars = f'D{d:02d}', {}
        for s in STOCKS:
            prev = price[s]
            price[s] = round(prev * (1 + rng.normal(0, 0.01)), 2)
            up, down = round(prev * 1.1, 2), round(prev * 0.9, 2)
            bars[s] = {'open': price[s], 'high': price[s] * 1.01, 'low': price[s] * 0.99, 'close': price[s], 'volume': 10_000, 'amount': price[s] * 10_000 * (1 + i_amount(s)),
                       'prior_amount': prev * 10_000 * (1 + i_amount(s)),
                       'limit_up': up, 'limit_down': down}
        sessions.append((day, bars))
    return sessions


def i_amount(s):
    return int(s[1:]) * 50  # S09 trades the most


def rank(bars, top=5):
    """Signal: top-N by traded value, equal weight (a stand-in for the archived A191_132 ranking)."""
    names = sorted(bars, key=lambda s: -bars[s]['amount'])[:top]
    return {s: 1.0 / top for s in names}


def fingerprint():
    return [hashlib.sha256(p.read_bytes()).hexdigest() if p.is_file() else None for p in WATCHED]


@pytest.fixture
def before():
    snap = fingerprint()
    yield
    assert fingerprint() == snap  # the formal research ledger is never written by a rehearsal


def test_full_lifecycle_reconciles_every_session(tmp_path, before):
    sessions = synthetic()
    pt.open_account(tmp_path, 1_000_000.0, 'E2E')
    prev = None
    for i, (day, bars) in enumerate(sessions):
        closes = {s: b['close'] for s, b in bars.items()}
        fills = pt.execute(tmp_path, day, bars, previous_session=prev)
        state = pt.mark(tmp_path, day, closes)
        if i % 5 == 0:
            pt.plan(tmp_path, day, rank(bars), closes)
        assert pt.reconcile(tmp_path)['status'] == 'PASS'
        assert state['cash'] >= 0 and all(n > 0 for n in state['positions'].values())
        assert all(f['status'] in ('FILLED', 'PARTIAL') or f['filled_shares'] == 0 for f in fills)
        prev = day
    state = pt.load(tmp_path)
    assert state['label'] == 'PAPER_REHEARSAL' and state['broker'] == 'DISABLED' and state['status'] == 'ACTIVE'
    nav = [json.loads(l) for l in (tmp_path / 'nav.jsonl').read_text().splitlines()]
    assert len(nav) == 30 and all(n['nav'] >= n['cash'] >= 0 for n in nav)  # P&L is marked from positions, never invented
    orders = [json.loads(l) for l in (tmp_path / 'orders.jsonl').read_text().splitlines()]
    assert orders and len(state['positions']) <= 5 and all(o['shares'] > 0 for o in orders)
    # report: last NAV equals cash + marked positions at the last close
    last_closes = {s: b['close'] for s, b in sessions[-1][1].items()}
    assert nav[-1]['nav'] == pytest.approx(state['cash'] + sum(n * last_closes[s] for s, n in state['positions'].items()))


def test_insufficient_cash_rejects_excess_and_never_shorts(tmp_path, before):
    pt.open_account(tmp_path, 10_000.0, 'E2E')
    state = pt.load(tmp_path)
    state['pending'] = [{'stock_id': 'S01', 'side': 'buy', 'shares': 1000, 'plan_date': 'D0'}, {'stock_id': 'S02', 'side': 'sell', 'shares': 5, 'plan_date': 'D0'}]
    pt._save(tmp_path / 'state.json', state)
    fills = {f['stock_id']: f for f in pt.execute(tmp_path, 'D1', {'S01': {'open': 100, 'high': 101, 'low': 99, 'close': 100, 'volume': 1, 'amount': 1e9, 'prior_amount': 1e9, 'limit_up': 110, 'limit_down': 90},
                                                                       'S02': {'open': 100, 'high': 101, 'low': 99, 'close': 100, 'volume': 1, 'amount': 1e9, 'prior_amount': 1e9, 'limit_up': 110, 'limit_down': 90}})}
    state = pt.load(tmp_path)
    assert fills['S01']['status'] == 'PARTIAL' and state['cash'] >= 0
    assert fills['S02']['filled_shares'] == 0 and 'S02' not in state['positions']  # nothing held: no short is created


def test_missing_price_and_drawdown_halt_and_stop_new_orders(tmp_path, before):
    sessions = synthetic(3)
    pt.open_account(tmp_path, 1_000_000.0, 'E2E')
    day, bars = sessions[0]
    closes = {s: b['close'] for s, b in bars.items()}
    pt.plan(tmp_path, day, rank(bars), closes)
    day, bars = sessions[1]
    pt.execute(tmp_path, day, bars, previous_session=sessions[0][0])
    held = list(pt.load(tmp_path)['positions'])
    state = pt.mark(tmp_path, day, {s: b['close'] for s, b in bars.items() if s != held[0]})
    assert state['status'] == 'HALTED' and 'no close' in state['halt_reason']
    assert pt.plan(tmp_path, day, rank(bars), closes) == []  # drawdown halt: test_paper_trading.py::test_kill_switch_on_drawdown_and_missing_close


def test_tampered_state_is_caught_not_booked_as_pnl(tmp_path, before):
    pt.open_account(tmp_path, 1_000_000.0, 'E2E')
    pt.plan(tmp_path, 'D0', {'S01': 0.1}, {'S01': 100.0})
    pt.execute(tmp_path, 'D1', {'S01': {'open': 100, 'high': 101, 'low': 99, 'close': 100, 'volume': 1, 'amount': 1e9, 'prior_amount': 1e9, 'limit_up': 110, 'limit_down': 90}})
    state = pt.load(tmp_path)
    state['cash'] += 12_345.0  # a phantom gain
    pt._save(tmp_path / 'state.json', state)
    assert pt.reconcile(tmp_path)['status'] == 'FAIL' and pt.load(tmp_path)['status'] == 'HALTED'
