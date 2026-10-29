"""Deterministic Daily Runner: one research-shadow signal per official session, without an open Claude Code session.

Started by an external trigger (Windows Task Scheduler, after logon, by hand). One pass: make sure today's task exists, then
task_controller.run_once(). Never loops, never invents data, never writes PROSPECTIVE after the deadline.
Per session (Asia/Taipei):
  closed day                         -> DONE (SKIPPED_CLOSED), nothing recorded
  deadline passed, no signal         -> BLOCKED record in the research ledger, DONE (BLOCKED_DEADLINE)
  MI_INDEX not cached, before 15:00  -> WAITING_DATA until 15:00 (publication is not assumed before the close)
  MI_INDEX not cached, after 15:00   -> public policy + assigned fixed budget (no Owner grant); unassigned budget -> BLOCKED
  fetched but not a trading day while the calendar expected one -> BLOCKED (maybe unpublished; the one-request budget is spent)
  cached                             -> audit, ready, shadow, verify chain -> DONE
"""
import argparse
import json
import sys
from datetime import datetime, time, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts import research_workflow as rw  # noqa: E402
from scripts import task_controller as tc  # noqa: E402

TPE = timezone(timedelta(hours=8))
PUBLISH_AFTER = time(15, 0)  # earliest local time a check is useful; publication itself is verified from the payload
REQUEST_LEDGER = ROOT / 'outputs/daily_runner/request_ledger.jsonl'
GOAL = 'DAILY-RESEARCH-SHADOW'


def task_id(day: str) -> str:
    return f'T-DAILY-SHADOW-{day}'


def ensure_task(day: str, root=tc.STATE) -> dict:
    tc.create(task_id(day), f'RESEARCH_ONLY A191_132 shadow signal for session {day}', ['signal recorded before the deadline, or BLOCKED / SKIPPED with reason'],
              'forward-tracker', GOAL, root=root, handler='daily_shadow', priority='P1', params={'date': day})
    return tc.refresh_public_fetch_blocker(task_id(day), root)


def fetch_index(day: str):
    """The existing single-request path: RequestGate pacing, budget and SENT-before-request ledger (scripts/official_data_recovery.py)."""
    from core.research.sources.twse import RequestGate
    from scripts.official_data_recovery import acquire_index
    return acquire_index(day, RequestGate(minimum_interval=3.2), REQUEST_LEDGER, rw.MI_INDEX)


def index_permission(day):
    from core.runtime import public_data, operation_policy as policy
    from core.research.sources.twse import MI_INDEX_URL
    context = public_data.context(MI_INDEX_URL, params={'date': day.replace('-', ''), 'type': 'ALL', 'response': 'json'})
    hit = public_data.cached(context)
    if hit is not None:
        return hit  # local verified data needs neither a new HTTP budget nor an Owner grant
    if policy.current_mode() is policy.ExecutionMode.PRODUCTION:
        grant, reason = tc.usable_grant('official_fetch', 'MI_INDEX', day, tc.GRANTS)
        if grant is None:
            raise policy.AuthorizationDenied(reason)
    return policy.enforce(policy.Operation.DOWNLOAD_MARKET_DATA, dataset='MI_INDEX', request_context=context)


def daily_shadow(task: dict, root, now=None, fetch=fetch_index, grants=None):
    import pandas as pd
    day = task['params']['date']
    tid = task['task_id']
    now = now or datetime.now(timezone.utc)
    days, (closures, years) = rw.load_sessions(), rw.load_closures()
    status = rw.session_status(pd.Timestamp(day), days, closures, years)
    if status in ('CLOSED_SCHEDULED', 'CLOSED_OBSERVED'):
        return tc.set_status(tid, 'DONE', root, outcome='SKIPPED_CLOSED', next_action=None)
    if status == 'CONFLICT':
        return tc.block(tid, 'DATA', f'{day}: scheduled closure but MI_INDEX shows trading', 'Owner resolves the calendar', owner=True, root=root)
    deadline = rw.deadline_utc(day, days, closures, years)
    if now >= deadline:
        tc.step(tid, f'blocked|{day}', lambda: rw.record_blocked(day, 'A191_132', f'no signal before the deadline {deadline.isoformat()}')['sha256'], root)
        return tc.set_status(tid, 'DONE', root, outcome='BLOCKED_DEADLINE', next_action=None)
    if status != 'OPEN_OBSERVED':
        local = now.astimezone(TPE)
        if local.date().isoformat() < day or local.time() < PUBLISH_AFTER and local.date().isoformat() == day:
            check = datetime.combine(pd.Timestamp(day).date(), PUBLISH_AFTER, TPE).astimezone(timezone.utc)
            return tc.wait_data(tid, day, check.isoformat(timespec='seconds'), 'session not closed yet', root)
        attempt = f'fetch-attempt|MI_INDEX|{day}'
        if attempt in tc.load(tid, root)['done_steps'] and f'fetch|MI_INDEX|{day}' not in tc.load(tid, root)['done_steps']:
            return tc.block(tid, 'DATA', f'MI_INDEX {day}: an earlier request did not complete; one request per date', 'Owner checks the request ledger', owner=True, root=root)
        try:
            index_permission(day)  # precheck consumes nothing; the actual HTTP function enforces again at transmission
        except PermissionError as exc:
            return tc.block(tid, 'DATA', f'MI_INDEX {day}: {exc}', 'approved fixed Research budget or verified cached data; no official_fetch grant needed', root=root)
        tc.step(tid, attempt, lambda: datetime.now(timezone.utc).isoformat(timespec='seconds'), root)  # recorded BEFORE the request: never two requests
        try:
            kind = tc.step(tid, f'fetch|MI_INDEX|{day}', lambda: fetch(day), root)
        except Exception as exc:  # network / budget: no retry, the Owner decides
            return tc.block(tid, 'DATA', f'MI_INDEX {day} request failed: {type(exc).__name__}: {exc}'[:300], 'Owner decides; one request per date', owner=True, root=root)
        if kind != 'TRADING_DAY':
            return tc.block(tid, 'DATA', f'MI_INDEX {day} returned {kind} while the calendar expected a session', 'Owner checks publication; no retry beyond the grant', owner=True, root=root)
        days = rw.load_sessions()
    tc.step(tid, f'audit|{day}', lambda: (rw.cmd_audit(), 'ok')[1], root)
    blockers = rw.readiness(day, days, closures, years, now=now)['blockers']
    blockers = [b for b in blockers if not b.startswith(rw.UNVERIFIED_SCHEDULE)]  # a cached session is confirmed by exchange data
    if blockers:
        again = (now + timedelta(hours=1)).isoformat(timespec='seconds')
        return tc.wait_data(tid, day, again, '; '.join(blockers), root)
    tc.step(tid, f'shadow|{rw.STRATEGY_VERSION}|{day}', lambda: (rw.cmd_shadow(day), 'recorded')[1], root)
    from core.research.shadow_selection import verify
    chain = verify(rw.SHADOW)
    if chain['status'] != 'PASS':
        return tc.block(tid, 'PRODUCTION', f'research ledger chain {chain}', 'Owner inspects the ledger', owner=True, root=root)
    paper = tc.step(tid, f'paper|{PAPER_ID}|{day}', lambda: paper_session(day, rw.load_sessions()), root)
    if paper['reconcile'] != 'PASS' or paper['status'] != 'ACTIVE':
        return tc.block(tid, 'PRODUCTION', f'paper account {paper}', 'Owner reviews the paper account', owner=True, root=root)
    return tc.set_status(tid, 'DONE', root, outcome='RECORDED', chain=chain, paper=paper, next_action='timing turns PROSPECTIVE once the next session is cached')


PAPER_ID = 'A191_132-PAPER-REHEARSAL-TOP20-REB20-EQUAL'  # registered: A191_132:PAPER-REHEARSAL-TOP20-REB20-EQUAL
PAPER_ROOT = ROOT / 'outputs/paper_trading' / PAPER_ID
PAPER_TOP, PAPER_REBALANCE, PAPER_CASH = 20, 20, 10_000_000.0


def paper_session(day, days, root=None):
    """Fill yesterday's orders at today's open, mark at the close, reconcile, and on rebalance days plan from today's ranking."""
    from core.research.shadow_selection import _lines
    from scripts import paper_trading as pt
    root = root or PAPER_ROOT
    import pandas as pd
    pt.open_account(root, PAPER_CASH, PAPER_ID)
    bars, closes = pt.session_bars(day, days)
    sessions = sorted(d.date().isoformat() for d, (t, *_) in days.items() if t and d <= pd.Timestamp(day))
    if day not in sessions:
        pt.halt(root, f'{day} is not an official session in the calendar')
        return {'fills': {}, 'reconcile': 'NOT_RUN', 'status': 'HALTED', 'pending': len(pt.load(root)['pending']), 'label': pt.LABEL}
    previous = sessions[-2] if len(sessions) > 1 else ''  # '' rejects any pending order as stale: no known previous session
    fills = pt.execute(root, day, bars, previous)
    pt.mark(root, day, closes)
    check = pt.reconcile(root)
    state = pt.load(root)
    start = state['marked'][0]
    if start not in sessions:
        pt.halt(root, f'first paper session {start} is not in the calendar; rebalance cadence unknown')
    elapsed = len([s for s in sessions if s >= start]) - 1  # official sessions since the first one, so a skipped day does not shift the cadence
    if pt.load(root)['status'] == 'ACTIVE' and elapsed % PAPER_REBALANCE == 0:
        signals = [r for r in _lines(rw.SHADOW) if r.get('kind', 'SIGNAL') == 'SIGNAL' and r['date'] == day and r['strategy'] == 'A191_132']
        if not signals:
            pt.halt(root, f'rebalance day {day} has no A191_132 signal')
        else:
            picks = [p['stock_id'] for p in signals[-1]['picks'][:PAPER_TOP]]
            pt.plan(root, day, {s: 1 / PAPER_TOP for s in picks}, closes)
    state = pt.load(root)
    return {'fills': {f['status']: sum(1 for x in fills if x['status'] == f['status']) for f in fills}, 'reconcile': check['status'],
            'status': state['status'], 'pending': len(state['pending']), 'cash': state['cash'], 'label': pt.LABEL}


def target_day(now=None) -> str:
    """The session this pass is about: today in Taipei if it is a weekday, else the last weekday (the calendar decides if it traded)."""
    import pandas as pd
    local = (now or datetime.now(timezone.utc)).astimezone(TPE).date()
    return (pd.Timestamp(local) - pd.offsets.BDay(0 if local.weekday() < 5 else 1)).date().isoformat()


def main(day=None):
    ensure_task(day or target_day())
    print(json.dumps(tc.run_once({'daily_shadow': daily_shadow}), ensure_ascii=False, indent=1))


if __name__ == '__main__':
    sys.stdout.reconfigure(encoding='utf-8')
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--date')
    main(parser.parse_args().date)
