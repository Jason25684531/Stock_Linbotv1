"""Fixed public HTTP budgets. SQLite reservations commit before transmission; crashes never refund them.

The legacy cost ledger is only usable AFTER separate restricted-source authorization, never as public-data permission.
"""
import json
import os
import hashlib
import sqlite3
import uuid
import time
from email.utils import parsedate_to_datetime
from contextlib import closing, contextmanager
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
LEDGER = ROOT / 'outputs/research_budget/usage.jsonl'
MAX_REQUESTS_ENV = 'STOCK_LINBOT_RESEARCH_MAX_REQUESTS'
MAX_COST_ENV = 'STOCK_LINBOT_RESEARCH_MAX_COST'
STATE = ROOT / 'outputs/research_budget'
ROUND = '2026-10-28-public-research'
CAPS = {'od_p6': 385, 't1b': 10, 'calendar_2026': 1}
LEGACY = {
    'od_p6': (ROOT / 'outputs/p0_p4/2026-10-24/od_p6_request_ledger.jsonl', 3),
    't1b': (ROOT / 'outputs/finlab_recovery/2026-10-24/t1b_official/t1b_request_ledger.jsonl', 4),
}
VERSION = 1


class BudgetDenied(PermissionError):
    pass


def _paths():
    return Path(STATE) / 'public_requests.sqlite3', Path(STATE) / 'public_requests.anchor.json'


@contextmanager
def connection():
    """Never create a missing ledger during use; its independent anchor must match."""
    db, anchor = _paths()
    handle = None
    try:
        stamp = json.loads(anchor.read_text(encoding='utf-8'))
        if stamp['round'] != ROUND or stamp['version'] != VERSION or not db.is_file():
            raise ValueError('ledger or initialization anchor mismatch')
        handle = sqlite3.connect(db.as_uri() + '?mode=rw', uri=True, timeout=5, isolation_level=None)
        handle.row_factory = sqlite3.Row
        handle.execute('PRAGMA synchronous=FULL')
        metadata = dict(handle.execute('SELECT name,value FROM metadata'))
        if metadata.get('generation') != stamp['generation'] or metadata.get('round') != ROUND:
            raise ValueError('ledger generation mismatch')
        if handle.execute('PRAGMA quick_check').fetchone()[0] != 'ok':
            raise ValueError('ledger integrity check failed')
        yield handle
    except BudgetDenied:
        raise
    except (OSError, ValueError, KeyError, TypeError, sqlite3.Error) as exc:
        raise BudgetDenied(f'BUDGET_STATE_INVALID: {type(exc).__name__}') from exc
    finally:
        if handle is not None:
            handle.close()


def initialize():
    """Explicit, one-time migration under the same cross-process lock on Windows and Linux. No fresh allowance on restart."""
    from core.research.sources.twse import _exclusive
    Path(STATE).mkdir(parents=True, exist_ok=True)
    with _exclusive(Path(STATE) / 'initialize.lock'):
        db, anchor = _paths()
        if db.exists() or anchor.exists():
            return status()  # partially initialized state is deliberately NOT repaired to zero
        imports, provenance = [], {}
        for job, (path, floor) in LEGACY.items():
            try:
                raw = Path(path).read_bytes()
                rows = [json.loads(line) for line in raw.decode('utf-8').splitlines() if line.strip()]
                sent = [(i, row) for i, row in enumerate(rows) if row['event'] == 'SENT']
                if len(sent) < floor or len(sent) > CAPS[job]:
                    raise ValueError('incomplete or excessive historical usage')
                for i, row in sent:
                    if not all(isinstance(row.get(k), str) and row[k] for k in ('ts', 'dataset', 'date', 'url')):
                        raise ValueError('malformed historical attempt')
                    imports.append((f'legacy:{job}:{i}', job, f'legacy:{job}:{i}', row['ts'], json.dumps(row, ensure_ascii=False)))
                provenance[job] = {'sha256': hashlib.sha256(raw).hexdigest(), 'attempts': len(sent), 'path': str(path)}
            except (OSError, ValueError, KeyError, TypeError) as exc:
                raise BudgetDenied(f'BUDGET_HISTORY_UNKNOWN: {job}') from exc
        generation = uuid.uuid4().hex
        # Write the anchor FIRST: a crash during initialization cannot make the next start grant a fresh allowance.
        stamp = {'round': ROUND, 'version': VERSION, 'generation': generation, 'legacy': provenance,
                 'calendar_history': 'UNKNOWN', 'note': 'No authoritative attempt ledger establishes calendar usage; disabled pending reconciliation.'}
        with anchor.open('x', encoding='utf-8') as stream:
            json.dump(stamp, stream, ensure_ascii=False, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        with closing(sqlite3.connect(db, timeout=5)) as conn, conn:
            conn.executescript('''
                PRAGMA synchronous=FULL;
                CREATE TABLE metadata (name TEXT PRIMARY KEY, value TEXT NOT NULL);
                CREATE TABLE attempts (
                    id TEXT PRIMARY KEY, job TEXT NOT NULL, request_key TEXT NOT NULL,
                    reserved_at TEXT NOT NULL, context TEXT NOT NULL, outcome TEXT NOT NULL,
                    released_at REAL, status INTEGER, sha256 TEXT, body BLOB, error TEXT);
                CREATE INDEX request_attempts ON attempts(request_key,outcome);
                CREATE TABLE cooldowns (rate_group TEXT PRIMARY KEY, until_at REAL NOT NULL, reason TEXT NOT NULL);
            ''')
            conn.executemany('INSERT INTO metadata VALUES (?,?)', [('generation', generation), ('round', ROUND), ('calendar_history', 'UNKNOWN')])
            conn.executemany('INSERT INTO attempts(id,job,request_key,reserved_at,context,outcome) VALUES (?,?,?,?,?,\'LEGACY\')', imports)
        return status()


def _counts(conn):
    counts = {job: 0 for job in CAPS}
    for row in conn.execute('SELECT job,COUNT(*) FROM attempts GROUP BY job'):
        if row[0] not in CAPS:
            raise ValueError('unknown stored budget job')
        counts[row[0]] = row[1]
    return counts


def status():
    with connection() as conn:
        counts = _counts(conn)
        return {'round': ROUND, 'total_limit': 396, 'used': sum(counts.values()), 'used_is_lower_bound': True, 'unknown_budget_reserved': 1,
                'jobs': {job: {'limit': cap, 'used': None if job == 'calendar_2026' else counts[job],
                               'remaining': None if job == 'calendar_2026' else max(0, cap - counts[job]),
                               'status': 'BUDGET_HISTORY_UNKNOWN' if job == 'calendar_2026' else 'READY'} for job, cap in CAPS.items()}}


def _check_cooldown(conn, rate_group):
    row = conn.execute('SELECT until_at,reason FROM cooldowns WHERE rate_group=?', (rate_group,)).fetchone()
    if row and row['until_at'] > time.time():
        raise BudgetDenied(f'SOURCE_COOLDOWN: {rate_group}; retry after {row["until_at"]:.3f}; {row["reason"]}')


def _check(conn, job, rate_group=None):
    if job not in CAPS:
        raise BudgetDenied('BUDGET_UNASSIGNED: no approved fixed request plan for this purpose')
    if job == 'calendar_2026':
        raise BudgetDenied('BUDGET_HISTORY_UNKNOWN: calendar_2026; no assumption of zero prior usage')
    if rate_group:
        _check_cooldown(conn, rate_group)
    counts = _counts(conn)
    cap = CAPS[job]
    raw = os.environ.get(MAX_REQUESTS_ENV)
    if raw is not None:
        # An environment setting can only LOWER a hard cap. Invalid input fails closed.
        configured = int(raw)
        if configured < 0:
            raise ValueError('negative request cap')
        cap = min(cap, configured)
    if counts[job] >= cap or sum(counts.values()) + 1 >= 396:  # reserve the entire unknown calendar allowance, never count it as zero capacity
        raise BudgetDenied(f'BUDGET_EXHAUSTED: {job} {counts[job]}/{cap}')


def check(job, *, rate_group=None):
    if job not in CAPS:
        raise BudgetDenied('BUDGET_UNASSIGNED: no approved fixed request plan for this purpose')
    with connection() as conn:
        _check(conn, job, rate_group)


def reserve(context):
    """The ONLY HTTP debit. No caller-supplied amount, cap, path or round; every reservation counts as one attempt forever."""
    with connection() as conn:
        conn.execute('BEGIN IMMEDIATE')
        try:
            _check(conn, context.job, context.rate_group)
            prior = conn.execute("SELECT outcome FROM attempts WHERE request_key=? AND outcome IN ('RESERVED','UNKNOWN','VERIFIED')", (context.key,)).fetchone()
            if prior:
                raise BudgetDenied(f'REQUEST_ALREADY_{prior[0]}: reuse verified data or reconcile the uncertain attempt')
            attempt = uuid.uuid4().hex
            conn.execute('INSERT INTO attempts(id,job,request_key,reserved_at,context,outcome) VALUES (?,?,?,?,?,?)',
                         (attempt, context.job, context.key, datetime.now(timezone.utc).isoformat(), context.audit_json(), 'RESERVED'))
            conn.execute('COMMIT')
            return attempt
        except BaseException:
            conn.execute('ROLLBACK')
            raise


def finish(attempt, *, outcome, released_at=None, response=None, error=None):
    with connection() as conn:
        conn.execute('BEGIN IMMEDIATE')
        body = response.content if response is not None else None
        row = conn.execute('SELECT context FROM attempts WHERE id=? AND outcome=\'RESERVED\'', (attempt,)).fetchone()
        if row is None:
            raise BudgetDenied('AUDIT_INVALID_ATTEMPT: no reserved attempt to finish')
        changed = conn.execute('UPDATE attempts SET outcome=?,released_at=?,status=?,sha256=?,body=?,error=? WHERE id=? AND outcome=\'RESERVED\'',
                               (outcome, released_at, response.status_code if response is not None else None,
                                hashlib.sha256(body).hexdigest() if body is not None else None, body, error, attempt)).rowcount
        if changed != 1:
            raise BudgetDenied('AUDIT_INVALID_ATTEMPT: no reserved attempt to finish')
        if response is not None:
            cooldown = response_cooldown(response)
            if cooldown is not None:
                group = json.loads(row['context'])['source']
                conn.execute('INSERT INTO cooldowns VALUES (?,?,?) ON CONFLICT(rate_group) DO UPDATE SET until_at=MAX(until_at,excluded.until_at),reason=excluded.reason',
                             (group, cooldown, 'upstream rate limit or blocking response'))
        conn.execute('COMMIT')


def response_cooldown(response):
    text = (response.content.decode('utf-8', errors='replace') + response.content.decode('big5', errors='replace')).lower()
    if response.status_code != 429 and not response.headers.get('Retry-After') and not any(word in text for word in ('captcha', 'too many requests', 'access denied', '查詢過量', '驗證碼', '系統忙碌')):
        return None
    raw = response.headers.get('Retry-After', '')
    now = time.time()
    try:
        delay = float(raw)
        if not 0 <= delay <= 31536000:
            raise ValueError('invalid delay')
        return now + max(3.2, delay)
    except (ValueError, TypeError):
        try:
            return max(now + 3.2, parsedate_to_datetime(raw).timestamp())
        except (ValueError, TypeError, OverflowError, OSError, IndexError):
            return now + 3600  # no usable upstream instruction: stop conservatively, never immediately retry a block


def audit_rows():
    with connection() as conn:
        return [dict(row) for row in conn.execute('SELECT id,job,request_key,reserved_at,outcome,released_at,status,sha256,error FROM attempts ORDER BY rowid')]


def _rows():
    if not LEDGER.is_file():
        return []
    return [json.loads(line) for line in LEDGER.read_text(encoding='utf-8').splitlines() if line.strip()]


def used() -> dict:
    rows = _rows()
    return {'requests': sum(int(r['requests']) for r in rows), 'cost': sum(float(r['cost']) for r in rows)}


def charge(operation: str, requests: int = 1, cost: float = 0.0, official: bool = False) -> str:
    """'' and one ledger row when within budget; otherwise the refusal reason and nothing is recorded. ponytail: read-then-append, one process
    at a time; a file lock like task_controller's if research ever fetches in parallel."""
    if official:
        return 'public HTTP requires a validated canonical request context, not official=True'
    try:
        max_requests, max_cost = os.environ.get(MAX_REQUESTS_ENV), os.environ.get(MAX_COST_ENV)
        if max_requests is None:
            return f'no Research budget configured ({MAX_REQUESTS_ENV} unset)'
        total = used()
        if total['requests'] + requests > int(max_requests):
            return f"Research request budget exhausted: {total['requests']} of {int(max_requests)} used"
        if max_cost is not None and total['cost'] + cost > float(max_cost):
            return f"Research cost budget exhausted: {total['cost']} of {float(max_cost)} used"
        LEDGER.parent.mkdir(parents=True, exist_ok=True)
        with LEDGER.open('a', encoding='utf-8') as handle:
            handle.write(json.dumps({'at': datetime.now(timezone.utc).isoformat(timespec='seconds'), 'operation': operation,
                                     'requests': requests, 'cost': cost}) + '\n')
        return ''
    except (OSError, ValueError, KeyError) as exc:
        return f'Research budget unreadable: {type(exc).__name__}: {exc}'[:200]
