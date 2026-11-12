"""Market data update service (D9): one locked, idempotent, retried entry point for the market database update.

Module-level imports are stdlib only. The default adapters import jobs/update_database.py and core.* lazily, so importing
this module loads no DB, LINE, broker, run_daily or recommendation code, and the service never calls any of them.
Every default network adapter pre-checks each endpoint with its exact request context through the existing download gate. Writing the database to
2026 does not open 2026 to research: research readers keep their own development-window checks.

CLI: python -m services.market_data_update --daily | --manual | --backfill 2025-01-02,2025-01-03 | --verify-scheduled [--datasets a,b] [--force]
Environment: STOCK_LINBOT_UPDATE_ROOT (isolated root), STOCK_LINBOT_TRIGGER (scheduled | manual, written into each record).
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import secrets
import sys
import time
from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_ROOT = ROOT / 'outputs' / 'market_data_updates'
DATASETS = ('prices_volume', 'monthly_revenue', 'financials', 'margin', 'corporate_actions', 'benchmark', 'benchmark_tri')
MODES = ('daily', 'manual', 'backfill')
MAX_ATTEMPTS = 3
BACKOFF = (2, 4)  # seconds before attempts 2 and 3
MAX_BACKFILL_DATES = 31
DEV_END = '2025-12-31'  # core.research.strategy_signal.DEV_END: the sealed research window ends here; routine commits start after it
STALE_LOCK_SECONDS = 2 * 3600
MIN_VALID_MARKET_ROWS_FALLBACK = 100  # mirrors core.db_helper.MIN_VALID_MARKET_ROWS when that import is unavailable
PIT_DATASETS = ('financials', 'monthly_revenue')
STORED = ('SUCCESS', 'PIT_UNVERIFIED')  # fetched with integrity ok: the idempotency key is recorded
FAILING = ('FAILED', 'INTEGRITY_INVALID')
HOLIDAY_NOTE = 'expected date from core/research/trading_calendar (observed sessions and imported closures); the data is older'
COVERAGE_GAPS = (
    {'item': 'splits', 'status': 'NOT_COVERED', 'reason': 'TWT49U covers ex-rights/ex-dividend only; no lawful source implemented'},
    {'item': 'capital_reductions', 'status': 'NOT_COVERED', 'reason': 'TWT49U covers ex-rights/ex-dividend only; no lawful source implemented'},
)


class UpdateInProgress(RuntimeError):
    """Another process holds outputs/market_data_updates/.lock."""


# ---------------------------------------------------------------- lock

def _pid_alive(pid: int) -> bool:
    try:
        import psutil
        return psutil.pid_exists(pid)
    except ImportError:
        pass
    if os.name == 'nt':
        return True  # ponytail: os.kill(pid, 0) terminates the target on Windows; without psutil a lock is never presumed dead; revisit when a safe liveness check is available.
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _lock_is_stale(path: Path) -> bool:
    try:
        pid_text, _, started = path.read_text(encoding='utf-8').partition('\n')
        pid = int(pid_text.strip())
    except (OSError, ValueError):
        return False  # unreadable or half-written: never removed automatically
    try:
        started_at = datetime.fromisoformat(started.strip()).timestamp()
    except ValueError:
        started_at = path.stat().st_mtime
    return time.time() - started_at > STALE_LOCK_SECONDS and not _pid_alive(pid)


@contextmanager
def acquire_lock(root=DEFAULT_ROOT):
    """Cross-process lock shared with jobs/update_database.py; raises UpdateInProgress when held."""
    path = Path(root) / '.lock'
    path.parent.mkdir(parents=True, exist_ok=True)
    for _ in range(2):
        try:
            fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            break
        except FileExistsError:
            if not _lock_is_stale(path):
                raise UpdateInProgress(str(path)) from None
            path.unlink(missing_ok=True)
    else:
        raise UpdateInProgress(str(path))
    try:
        os.write(fd, f'{os.getpid()}\n{datetime.now(timezone.utc).isoformat()}\n'.encode('ascii'))
    finally:
        os.close(fd)
    try:
        yield path
    finally:
        path.unlink(missing_ok=True)


# ---------------------------------------------------------------- files

def _read_json(path: Path, default):
    return json.loads(path.read_text(encoding='utf-8')) if path.exists() else default


def _write_json(path: Path, payload) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + '.tmp')
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding='utf-8')
    os.replace(tmp, path)


def load_schedule(root=DEFAULT_ROOT) -> dict:
    schedule = {'enabled': False, 'time': '18:30', 'os_task_verified': False}
    schedule.update(_read_json(Path(root) / 'schedule.json', {}))
    return schedule


def save_schedule(root, enabled: bool, time_text: str) -> dict:
    """Store the daily toggle. os_task_verified is never set here; only the Owner records it after verifying the OS task."""
    datetime.strptime(time_text, '%H:%M')
    schedule = load_schedule(root)
    schedule.update(enabled=bool(enabled), time=time_text)
    schedule.setdefault('os_task_verified', False)
    _write_json(Path(root) / 'schedule.json', schedule)
    return schedule


# ---------------------------------------------------------------- dates and checks

def _as_date(value) -> date:
    return value if isinstance(value, date) else date.fromisoformat(str(value)[:10])


def latest_weekday(day: date) -> date:
    while day.weekday() >= 5:
        day -= timedelta(days=1)
    return day


def validate_dates(dates, today: date, limit: int = MAX_BACKFILL_DATES) -> list[str]:
    """YYYY-MM-DD strings, none in the future, at most `limit` distinct dates; raises ValueError otherwise."""
    if isinstance(dates, str):
        dates = dates.split(',')
    out = set()
    for raw in dates or []:
        text = str(raw).strip()
        try:
            day = datetime.strptime(text, '%Y-%m-%d').date()
        except ValueError:
            raise ValueError(f'INVALID_DATE_FORMAT: {text!r} (expected YYYY-MM-DD)') from None
        if day.isoformat() != text:
            raise ValueError(f'INVALID_DATE_FORMAT: {text!r} (expected YYYY-MM-DD)')
        if day > today:
            raise ValueError(f'FUTURE_DATE: {text}')
        out.add(text)
    if not out:
        raise ValueError('DATES_REQUIRED')
    if len(out) > limit:
        raise ValueError(f'TOO_MANY_DATES: {len(out)} > {limit}')
    return sorted(out)


def _min_valid_rows() -> int:
    try:
        from core.db_helper import MIN_VALID_MARKET_ROWS
        return int(MIN_VALID_MARKET_ROWS)
    except Exception:
        return MIN_VALID_MARKET_ROWS_FALLBACK


def check_result(name: str, day: str, payload: dict, expected=None, calendar_state=None) -> dict:
    """Integrity, freshness and point-in-time checks over one adapter result. expected / calendar_state come from the trading calendar;
    a CALENDAR_UNVERIFIED expected date makes an old date FRESHNESS_UNKNOWN, never STALE."""
    if payload.get('status'):  # adapter-declared: NOT_COVERED, COVERED_BY, IMPLEMENTED_BUT_AUTH_BLOCKED, DATA_BLOCKED, SOURCE_SCOPE_MISMATCH
        return {'status': payload['status']}
    rows = payload.get('rows')
    problems = []
    if name == 'prices_volume':
        min_rows = int(payload.get('min_rows') or _min_valid_rows())
        if rows is None or rows < min_rows:
            problems.append(f'rows {rows} < MIN_VALID_MARKET_ROWS {min_rows}')
    required_empty = list(payload.get('required_empty') or [])
    if required_empty:
        problems.append(f'required columns empty: {required_empty}')
    expected = str(expected or latest_weekday(_as_date(day)).isoformat())
    actual = payload.get('latest_trade_date')
    stale = actual is not None and str(actual)[:10] < expected
    unknown = stale and calendar_state == 'CALENDAR_UNVERIFIED'
    pit_verified = bool(payload.get('has_announcement_date')) if name in PIT_DATASETS else None
    checks = {'rows': rows, 'integrity_ok': not problems, 'integrity_problems': problems,
              'expected_latest_trade_date': expected, 'expected_date_calendar': calendar_state, 'latest_trade_date': actual,
              'stale': stale and not unknown, 'pit_verified': pit_verified}
    if stale:
        checks['note'] = 'expected date is CALENDAR_UNVERIFIED: freshness cannot be judged' if unknown else HOLIDAY_NOTE
    status = ('INTEGRITY_INVALID' if problems else 'FRESHNESS_UNKNOWN' if unknown else 'STALE' if stale
              else 'PIT_UNVERIFIED' if pit_verified is False else 'SUCCESS')
    return {'status': status, 'checks': checks}


# ---------------------------------------------------------------- default adapters

def _taiex_close(payload) -> float | None:
    for table in (payload or {}).get('tables') or []:
        fields = table.get('fields') or []
        if '收盤指數' not in fields:
            continue
        for row in table.get('data') or []:
            if str(row[0]).strip() == '發行量加權股價指數':
                try:
                    return float(str(row[fields.index('收盤指數')]).replace(',', ''))
                except ValueError:
                    return None
    return None


# ---------------------------------------------------------------- sources (change 2026-11-09-autonomous-data-readiness-v1, design D6)

# How each dataset reaches the database: SPLIT = fetch / stage / validate / commit / re-read; PARTIAL_UNSPLITTABLE = the existing write
# cannot be separated from cleaning, so it is never committed here; FETCH_ONLY = file caches only, no database target.
SPLIT_MODE = {'prices_volume': 'SPLIT', 'financials': 'SPLIT', 'monthly_revenue': 'SPLIT', 'corporate_actions': 'FETCH_ONLY',
              'benchmark': 'FETCH_ONLY', 'margin': 'COVERED_BY', 'benchmark_tri': 'LOCAL'}
PRIMARY_KEYS = {'prices_volume': ('stock_id', 'trade_date'), 'financials': ('stock_id', 'year', 'quarter'), 'monthly_revenue': ('stock_id', 'year', 'month')}
REQUIRED_COLUMNS = {'prices_volume': ('stock_id', 'trade_date', 'close_price'), 'financials': ('stock_id', 'year', 'quarter', 'eps'),
                    'monthly_revenue': ('stock_id', 'year', 'month', 'revenue')}
FAILING = FAILING + ('SCHEMA_DRIFT', 'SOURCE_SCOPE_MISMATCH', 'COMMIT_VERIFY_FAILED', 'COMMIT_FAILED', 'STAGING_HASH_MISMATCH', 'STAGE_FAILED',
                     'RECONCILIATION_MISMATCH', 'RECONCILIATION_UNAVAILABLE', 'RECONCILIATION_REQUIRED', 'COMMIT_AUTH_BLOCKED', 'BACKUP_REQUIRED',
                     'COMMIT_FLOOR_VIOLATION', 'IMPLEMENTED_BUT_AUTH_BLOCKED')  # a refused or unreconciled unit is an error, never COMPLETED
REQUIRED_DAILY = ('prices_volume', 'financials', 'corporate_actions', 'benchmark')
TWSE = 'https://www.twse.com.tw/rwd/zh'
TPEX = 'https://www.tpex.org.tw'
CLOSED = ('CLOSED_OBSERVED', 'CLOSED_SCHEDULED')


class EndpointBlocked(PermissionError):
    """One reason per endpoint that may not be requested; a retry cannot change the decision."""

    def __init__(self, reasons):
        super().__init__('; '.join(reasons))
        self.reasons = list(reasons)


def _market() -> str:
    from config import Config
    return Config.MCP_DEFAULT_MARKET


SCOPES = ('TWSE_ONLY', 'ALL')


def resolve_market_scope(explicit=None) -> str:
    """Explicit value, else STOCK_LINBOT_MARKET_SCOPE, else TWSE_ONLY (decision.md D2: 2026 runs TWSE_ONLY; the web manual update and
    every other caller without the variable must not ask for TPEx). The ingestion authority narrows it further to the grant's scope."""
    value = explicit or os.environ.get('STOCK_LINBOT_MARKET_SCOPE') or 'TWSE_ONLY'
    if value not in SCOPES:
        raise ValueError(f'UNKNOWN_MARKET_SCOPE: {value}')
    return value


def classify_mi_index(payload, day, today) -> str:
    """TRADING | CLOSED_OBSERVED | NOT_YET_PUBLISHED | ANOMALY (decision.md D5). Only TWSE's explicit no-data reply for a day before today
    is a closure: the same reply for today means not published yet; busy pages, errors and empty bodies are never a closure."""
    from core.research.sources.twse import NON_TRADING_STAT
    payload = payload if isinstance(payload, dict) else {}
    if payload.get('stat') == 'OK' and any('每日收盤行情' in (t.get('title') or '') for t in payload.get('tables') or []):
        return 'TRADING'
    if payload.get('stat') == NON_TRADING_STAT and not payload.get('tables'):
        return 'CLOSED_OBSERVED' if _as_date(day) < _as_date(today) else 'NOT_YET_PUBLISHED'
    return 'ANOMALY'


def reconcile_mi_index(payload) -> dict:
    """Official totals check (G6): traded value and shares summed over the full daily quotation table must equal the 總計(1~15) row of the
    same MI_INDEX reply. MATCH needs exact equality; anything unparseable is UNAVAILABLE. Both block the price commit unless MATCH."""
    def number(text):
        return int(str(text).replace(',', '').strip())
    try:
        tables = payload.get('tables') or []
        quotes = next(t for t in tables if '每日收盤行情' in (t.get('title') or ''))
        summary = next(t for t in tables if '大盤統計資訊' in (t.get('title') or ''))
        total = next(row for row in summary['data'] if str(row[0]).startswith('總計'))
        fields, sfields = quotes['fields'], summary['fields']
        computed = {'value': sum(number(r[fields.index('成交金額')]) for r in quotes['data']),
                    'shares': sum(number(r[fields.index('成交股數')]) for r in quotes['data']), 'rows': len(quotes['data'])}
        published = {'value': number(total[sfields.index('成交金額(元)')]), 'shares': number(total[sfields.index('成交股數(股)')])}
    except (StopIteration, KeyError, ValueError, IndexError, TypeError, AttributeError) as exc:
        return {'status': 'UNAVAILABLE', 'reason': f'{type(exc).__name__}: {exc}'[:200]}
    # Shares must be equal: every security row is present. Traded value: TWSE's total carries a small positive residual over the per-security
    # rows (59 cached sessions 2026-07..10: 2.7e-7..1.8e-6 relative, shares always equal), so value may trail by at most VALUE_RESIDUAL_MAX.
    residual = (published['value'] - computed['value']) / published['value'] if published['value'] else None
    ok = computed['shares'] == published['shares'] and residual is not None and 0 <= residual <= VALUE_RESIDUAL_MAX
    return {'status': 'MATCH' if ok else 'MISMATCH', 'computed': computed, 'published': published, 'value_residual': residual}


VALUE_RESIDUAL_MAX = 1e-5


def record_closure(path, day, payload) -> None:
    """Evidence of an observed closure for the trading calendar: the date, the reply's SHA256 and when it was seen."""
    path = Path(path)
    closures = _read_json(path, {})
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True).encode('utf-8')
    closures[str(day)] = {'sha256': hashlib.sha256(raw).hexdigest(), 'stat': payload.get('stat'),
                          'recorded_at': datetime.now(timezone.utc).isoformat(timespec='seconds')}
    _write_json(path, closures)


def twse_only_prices(day, today=None, request=None, closures=None, margin=None) -> dict:
    """prices_volume for listed shares, fetched in-process (no MCP hop): MI_INDEX first (closure / not published / anomaly), official
    reconciliation, then the snapshot (same request, served from the verified cache), T86 flows and TWSE margin. TPEx is never requested."""
    import pandas as pd
    from core.crawlers import chip_data_scraper
    from core.runtime import public_data
    from jobs import update_database as ud
    from services.mcp import server
    request = request or public_data.request
    ymd = str(day).replace('-', '')
    response = request('GET', f'{TWSE}/afterTrading/MI_INDEX?date={ymd}&type=ALL&response=json', timeout=20)
    source = 'VERIFIED_CACHE' if getattr(response, 'research_cache_hit', False) else 'OFFICIAL_HTTP'  # a cache is never shown as a fresh fetch
    payload = response.json()
    kind = classify_mi_index(payload, day, today or date.today())
    if kind == 'CLOSED_OBSERVED':
        record_closure(closures or DEFAULT_ROOT / 'calendar' / 'closures.json', day, payload)
        return {'status': 'CLOSED_OBSERVED', 'note': 'TWSE reports no trading data for this past date'}
    if kind == 'NOT_YET_PUBLISHED':
        return {'status': 'NOT_YET_PUBLISHED', 'note': 'TWSE has not published this session yet'}
    if kind == 'ANOMALY':
        raise RuntimeError(f"MI_INDEX anomaly (not a closure): stat={str(payload.get('stat'))[:60]!r}")
    reconciliation = reconcile_mi_index(payload)
    if reconciliation['status'] != 'MATCH':
        return {'status': f"RECONCILIATION_{reconciliation['status']}", 'reconciliation': reconciliation}
    snapshot = server.snapshot_frame(payload, str(day))
    try:
        flow = server.flow_frame(request('GET', f'{TWSE}/fund/T86?date={ymd}&selectType=ALL&response=json', timeout=20).json(), str(day))
    except FileNotFoundError:  # rehearsal cache without this reply
        flow = pd.DataFrame()
    warnings = [] if not flow.empty else [f'T86 flows not available for {day}: written as NULL, refetched on the next catch-up']
    for frame in (snapshot, flow):
        if not frame.empty:
            frame['trade_date'] = str(day)
    merged = ud.merge_market_data(snapshot, flow, str(day))
    margin = (margin or chip_data_scraper.fetch_margin_balance_twse)(str(day))
    if margin is not None and not margin.empty:
        margin = margin.assign(stock_id=margin['stock_id'].astype(str).str.strip())
        merged = merged.merge(margin, on='stock_id', how='left')
    else:
        warnings.append(f'MI_MARGN not available for {day}: MARGIN_PENDING')
    frame = ud.clean_market_frame(merged, str(day))
    return {'frame': frame, 'rows': len(frame), 'min_rows': _min_valid_rows(), 'latest_trade_date': str(day), 'required_empty': [],
            'reconciliation': reconciliation, 'universe': 'TWSE_LISTED_ONLY', 'tpex': 'NOT_COVERED', 'warnings': warnings, 'source': source,
            'note': 'in-process TWSE MI_INDEX + T86 + MI_MARGN, reconciled with the official total row, cleaned'}


def twse_only_financials(day) -> dict:
    """financials for listed companies, fetched in-process with the MCP server's own scraper and validated by the existing payload contract
    (the MCP process is outside the ingestion scope). Only periods after the commit floor are ever committed."""
    from core.update_financials_mops import prepare_financial_dataframe
    from services.mcp import server
    year, quarter = _financial_quarter(_as_date(day))
    west = year + 1911
    raw = server.fetch_historical_financial_statements(west, quarter, 'TWSE')
    frame = prepare_financial_dataframe({'period': {'year': west, 'quarter': quarter}, 'unit': 'TWD',
                                         'records': server._records_from_frame(raw)}, west, quarter)
    return {'frame': frame, 'rows': len(frame), 'latest_trade_date': None, 'has_announcement_date': False, 'required_empty': [],
            'universe': 'TWSE_LISTED_ONLY', 'note': f'ROC {year} Q{quarter} sii in-process; financial_statements has no announcement-date column'}


def ensure_calendar_year(year, root, closures=None, fetch=None) -> dict | None:
    """D5: import the official TWSE holiday schedule once when `year` has none (one request through the ingestion authority). A failure
    is recorded and the update goes on; the LINE gate keeps refusing an unverified session."""
    from core.runtime import public_data
    from scripts import research_workflow as rw
    closures = Path(closures or rw.CLOSURES)
    if int(year) in rw.load_closures(closures)[1]:
        return None
    out = Path(root) / 'calendar' / f"holidays_{year}_{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}"
    try:
        with public_data.ingestion_scope('calendar', 'TWSE_ONLY'):
            result = (fetch or rw.fetch_holidays)(out, str(year), closures)
    except Exception as exc:  # unsupported year, refusal or transport error: recorded, never fatal
        result = {'status': 'BLOCKED', 'reason': f'{type(exc).__name__}: {exc}'[:300]}
    _write_json(Path(root) / 'calendar' / f'import_{year}.json', result)
    return result


def _revenue_months(d: date):
    """Same months as update_database.run_monthly_revenue_update, keyed on the requested date (western years)."""
    year, month = (d.year, d.month) if d.day >= 12 else ((d.year, d.month - 1) if d.month > 1 else (d.year - 1, 12))
    return [(year, month - k) if month - k > 0 else (year - 1, month - k + 12) for k in range(2)]


def _financial_quarter(d: date):
    """Same ROC quarter as update_database.run_financial_update, keyed on the requested date."""
    roc = d.year - 1911
    return ((roc, 3) if d.month >= 11 else (roc, 2) if d.month >= 8 else (roc, 1) if d.month >= 5 else (roc - 1, 4) if d.month >= 3
            else (roc - 1, 3))


def endpoints(name: str, day, market=None) -> list:
    """Every request the dataset's real call path sends, with the URL and parameters of the sending code:
    prices_volume = services/mcp/server._fetch_twse_snapshot, _fetch_twse_flow, _fetch_tpex_* and core/crawlers/chip_data_scraper;
    financials = core/crawlers/quarterly_scraper through MCP; monthly_revenue = core/update_monthly_revenue (both month spellings);
    corporate_actions and benchmark = core/research/sources/twse. {'restricted': source} is outside public research."""
    d, market = _as_date(day), {'TWSE_ONLY': 'TWSE'}.get(market or _market(), market or _market())
    ymd, roc_day = d.strftime('%Y%m%d'), f'{d.year - 1911}/{d.month:02d}/{d.day:02d}'
    twse, tpex = market in ('TWSE', 'ALL'), market in ('TPEx', 'ALL')
    if name == 'prices_volume':
        out = [{'method': 'GET', 'url': f'{TWSE}/afterTrading/MI_INDEX?date={ymd}&type=ALL&response=json'},
               {'method': 'GET', 'url': f'{TWSE}/fund/T86?date={ymd}&selectType=ALL&response=json'}] if twse else []
        out += [{'restricted': 'tpex', 'url': f'{TPEX}/www/zh-tw/afterTrading/dailyQuotes'},
                {'restricted': 'tpex', 'url': f'{TPEX}/web/stock/3insti/daily_trade/'}] if tpex else []
        out += [{'method': 'GET', 'url': f'{TWSE}/marginTrading/MI_MARGN?date={ymd}&selectType=ALL&response=json'}] if twse else []
        return out + ([{'restricted': 'tpex', 'url': f'{TPEX}/web/stock/margin_trading/margin_balance/margin_bal_result.php?l=zh-tw&d={roc_day}&o=json'}]
                      if tpex else [])
    if name == 'financials':
        year, quarter = _financial_quarter(d)
        return [{'method': 'POST', 'url': 'https://mopsov.twse.com.tw/mops/web/ajax_t163sb04',
                 'data': {'encodeURIComponent': '1', 'step': '1', 'firstin': '1', 'off': '1', 'TYPEK': kind, 'year': str(year), 'season': f'{quarter:02d}'}}
                for kind in (['sii'] if twse else []) + (['otc'] if tpex else [])]
    if name == 'monthly_revenue':
        return [{'method': 'GET', 'url': f'https://mopsov.twse.com.tw/nas/t21/{code}/t21sc03_{y - 1911}_{text}_0.html'}
                for y, m in _revenue_months(d) for code in (['sii'] if twse else []) + (['otc'] if tpex else [])
                for text in dict.fromkeys((str(m), f'{m:02d}'))]
    if name == 'corporate_actions':
        return [{'method': 'GET', 'url': f'{TWSE}/exRight/TWT49U', 'params': {'startDate': ymd, 'endDate': ymd, 'response': 'json'}}]
    if name == 'benchmark':
        return [{'method': 'GET', 'url': f'{TWSE}/afterTrading/MI_INDEX', 'params': {'date': ymd, 'type': 'ALL', 'response': 'json'}}]
    return []  # margin is covered by prices_volume; benchmark_tri reads local files


def _reason_code(exc) -> str:
    import re
    found = re.search(r'[A-Z][A-Z0-9]*_[A-Z0-9_]+', str(exc))
    return found.group(0) if found else ('PRODUCTION_GRANT_REQUIRED' if 'grant' in str(exc) else 'DENIED')


def precheck(name: str, day, gate=None, market=None) -> list:
    """Build the exact context of every public endpoint and pass it to the ingestion authority (read-only, design D3) before anything is
    fetched. Restricted endpoints are not pre-checked, because their authorization consumes a grant use."""
    from core.runtime import public_data
    if gate is None:
        from core.runtime.operation_policy import Operation, gate as policy_gate
        scope = resolve_market_scope('TWSE_ONLY' if market in ('TWSE', 'TWSE_ONLY') else 'ALL' if market else None)
        gate = lambda ctx: policy_gate(Operation.INGEST_MARKET_DATA, request_context=ctx, dataset=name, market_scope=scope)
    contexts, reasons = [], []
    for spec in endpoints(name, day, market):
        if 'restricted' in spec:
            reasons.append(f"RESTRICTED_SOURCE_REQUIRES_SEPARATE_AUTHORIZATION:{spec['restricted']}:{spec['url']}")
            continue
        try:
            ctx = public_data.context(spec['url'], method=spec['method'], params=spec.get('params'), data=spec.get('data'))
            gate(ctx)
            contexts.append(ctx)
        except PermissionError as exc:
            reasons.append(f"{_reason_code(exc)}:{spec['url']}")
    if reasons:
        raise EndpointBlocked(reasons)
    return contexts


def _audit_keys() -> set:
    """(attempt, request_key) of every reserved ingestion HTTP attempt (public_data.ingestion_audit_path)."""
    from core.runtime import public_data
    path = public_data.ingestion_audit_path()
    rows = [json.loads(line) for line in path.read_text(encoding='utf-8').splitlines() if line.strip()] if path.is_file() else []
    return {(row['attempt'], row['request_key']) for row in rows if row.get('result') == 'RESERVED'}


def _research_rows():
    """Research budget attempt count; an unreadable budget is its own value (no research request can be sent through it either)."""
    from core.runtime import research_budget
    try:
        return len(research_budget.audit_rows())
    except Exception as exc:
        return f'UNREADABLE:{type(exc).__name__}'


def guarded(name, fetch, check=precheck, audit=_audit_keys, scope=None):
    """Pre-check every endpoint, fetch inside the ingestion scope, then require every new audited HTTP attempt to be one of the
    pre-checked requests and the research budget to be untouched (daily maintenance never spends research attempts)."""
    def run(day):
        from core.runtime import public_data
        market = resolve_market_scope(scope)
        allowed = {ctx.key for ctx in check(name, day)}
        before, research_before = audit(), _research_rows()
        with public_data.ingestion_scope(name, market):
            payload = fetch(day)
        unexpected = sorted({key for _, key in audit() - before} - allowed)
        if _research_rows() != research_before:
            unexpected.append('RESEARCH_BUDGET_TOUCHED')
        if unexpected:  # ponytail: a concurrent job's attempt also lands here; fail closed and rerun when the source is idle
            return {'status': 'SOURCE_SCOPE_MISMATCH', 'unexpected_request_keys': unexpected}
        return payload
    return run


def default_adapters(root=DEFAULT_ROOT, market_scope=None) -> dict:
    """dataset -> callable(date 'YYYY-MM-DD') -> payload. Network adapters are pre-checked per endpoint and never write the database:
    {'frame': DataFrame, rows, latest_trade_date, has_announcement_date, required_empty} or {status}."""
    cache = Path(root) / '_raw' / 'twse_rwd'
    market = resolve_market_scope(market_scope)

    def prices_volume(day):
        if market == 'TWSE_ONLY':
            return twse_only_prices(day, closures=Path(root) / 'calendar' / 'closures.json')
        from core.db_helper import MIN_VALID_MARKET_ROWS
        from jobs import update_database as ud
        frame, warnings = ud.fetch_market_data_with_mcp(ud.MCPClient(), day)
        if frame.empty:
            if warnings:
                raise RuntimeError('; '.join(warnings))
            return {'rows': 0, 'latest_trade_date': None, 'required_empty': [], 'note': 'empty snapshot: the source reports no session'}
        frame, margin_warning = ud.enrich_with_margin_balance(frame, day)
        frame = ud.clean_market_frame(frame, day)
        return {'frame': frame, 'rows': len(frame), 'min_rows': MIN_VALID_MARKET_ROWS, 'latest_trade_date': day, 'required_empty': [],
                'warnings': [w for w in (*warnings, margin_warning) if w], 'note': 'MCP snapshot + foreign flow, margin enrichment, cleaned'}

    def margin(day):
        return {'status': 'COVERED_BY', 'covered_by': 'prices_volume',
                'note': 'update_market_date merges core.crawlers.chip_data_scraper.fetch_margin_balance into daily_market_data'}

    def monthly_revenue(day):
        import pandas as pd
        from core.update_monthly_revenue import fetch_mops_static_revenue
        from core.update_monthly_revenue import clean_revenue_frame
        markets = ('sii',) if market == 'TWSE_ONLY' else ('sii', 'otc')
        frames = [clean for y, m in _revenue_months(_as_date(day))
                  if (clean := clean_revenue_frame(fetch_mops_static_revenue(y, m, markets=markets))) is not None and not clean.empty]
        if not frames:
            raise RuntimeError('MOPS monthly revenue returned no rows')
        frame = pd.concat(frames, ignore_index=True)
        return {'frame': frame, 'rows': len(frame), 'latest_trade_date': None, 'has_announcement_date': False, 'required_empty': [],
                'note': 'monthly_revenue table has no announcement-date column'}

    def financials(day):
        if market == 'TWSE_ONLY':
            return twse_only_financials(day)
        from core.update_financials_mops import load_quarter_financial_dataframe
        year, quarter = _financial_quarter(_as_date(day))
        frame, correlation = load_quarter_financial_dataframe(year, quarter)
        return {'frame': frame, 'rows': len(frame), 'latest_trade_date': None, 'has_announcement_date': False, 'required_empty': [],
                'note': f'ROC {year} Q{quarter} ({correlation}); financial_statements has no announcement-date column'}

    def corporate_actions(day):
        from core.research.sources import twse
        d = _as_date(day)
        try:
            raw = twse.fetch_corporate_actions(d, d, cache)
        except twse.SchemaDriftError:
            cached = cache / f'TWT49U_{d:%Y%m%d}_{d:%Y%m%d}.json'
            if cached.exists() and _read_json(cached, {}).get('stat') == twse.NON_TRADING_STAT:
                return {'rows': 0, 'latest_trade_date': None, 'required_empty': [], 'note': 'no TWT49U event on this date'}
            raise
        return {'rows': len(raw.payload.get('data') or []), 'latest_trade_date': None, 'required_empty': [],
                'note': 'TWT49U ex-rights/ex-dividend, cached response only; splits and capital reductions NOT_COVERED'}

    def benchmark(day):
        from core.research.sources import twse
        raw = twse.fetch_daily_quotes(_as_date(day), cache, max_attempts=1)
        if raw.error:
            raise RuntimeError(raw.error)
        kind = twse.classify(raw).kind
        if kind is twse.ResponseKind.NON_TRADING_DAY and _as_date(day) >= date.today():
            (cache / f'MI_INDEX_{_as_date(day):%Y%m%d}.json').unlink(missing_ok=True)  # not published yet: never a calendar closure
            return {'status': 'NOT_YET_PUBLISHED', 'note': 'TWSE has not published this session yet'}
        if kind is twse.ResponseKind.NON_TRADING_DAY:
            return {'rows': 0, 'latest_trade_date': None, 'required_empty': [], 'note': 'TWSE reports no trading on this date'}
        if kind is not twse.ResponseKind.TRADING_DAY:
            raise RuntimeError(f'MI_INDEX {kind.value}')
        close = _taiex_close(raw.payload)
        return {'rows': int(close is not None), 'latest_trade_date': day if close is not None else None,
                'required_empty': [] if close is not None else ['taiex_close'], 'taiex_close': close,
                'note': 'TAIEX price index from MI_INDEX (cached response)'}

    def benchmark_tri(day):
        from core.research import benchmark_tri as tri  # no download: report the local provider status only
        if not tri.files():
            return {'status': 'IMPLEMENTED_BUT_AUTH_BLOCKED', 'reason': tri.AUTH_REASON}
        return {'status': tri.CROSS_STATUS, 'files': [p.name for p in tri.files()], 'note': 'local total-return series; not cross-checked against MFI94U'}

    network = {'prices_volume': prices_volume, 'monthly_revenue': monthly_revenue, 'financials': financials,
               'corporate_actions': corporate_actions, 'benchmark': benchmark}
    return {**{name: guarded(name, fetch, check=lambda n, d: precheck(n, d, market=market), scope=market) for name, fetch in network.items()},
            'margin': margin, 'benchmark_tri': benchmark_tri}


FINANCIAL_OPTIONAL_COLUMNS = ('operating_margin',)  # what core.db_helper.ensure_financial_columns would add with ALTER TABLE


class TableCommitter:
    """One designated market table (design D4): read the rows of the staged keys, replace them in one transaction, export the whole table
    for the backup receipt, and restore a pre-image. MySQL in production, SQLite in tests and rehearsal (engine factory injected)."""

    def __init__(self, table, keys, engine=None, date_of=None, required_columns=()):
        self.table, self.keys, self._engine, self.date_of = table, tuple(keys), engine, date_of
        self.required_columns = tuple(required_columns)  # columns the table must already have: this service never runs ALTER TABLE

    def engine(self):
        if self._engine is None:
            from core.db_helper import get_db_engine
            return get_db_engine()
        return self._engine()

    def key_dates(self, frame, day) -> list:
        """ISO dates of the staged keys, compared with the commit floor."""
        return [str(d)[:10] for d in self.date_of(frame)] if self.date_of else [str(day)]

    def columns(self) -> list:
        from sqlalchemy import inspect
        return [column['name'] for column in inspect(self.engine()).get_columns(self.table)]

    def single_precision(self) -> set:
        """FLOAT (not DOUBLE) columns: MySQL sends them with 6 significant digits, so their reread matches at rtol 1e-5."""
        from sqlalchemy import inspect
        return {c['name'] for c in inspect(self.engine()).get_columns(self.table) if type(c['type']).__name__ == 'FLOAT'}

    def _groups(self, frame):
        other = [k for k in self.keys if k != 'stock_id']
        for values, part in frame.groupby(other):
            values = values if isinstance(values, tuple) else (values,)
            where = ' AND '.join(f'{key} = :k{i}' for i, key in enumerate(other))
            params = {f'k{i}': (v.item() if hasattr(v, 'item') else v) for i, v in enumerate(values)}
            yield where, {**params, 'ids': part['stock_id'].astype(str).tolist()}

    def _statement(self, verb, where):
        from sqlalchemy import bindparam, text
        return text(f'{verb} FROM {self.table} WHERE {where} AND stock_id IN :ids').bindparams(bindparam('ids', expanding=True))

    def read(self, frame, day=None):
        import pandas as pd
        with self.engine().connect() as conn:
            parts = [pd.read_sql(self._statement('SELECT *', where), conn, params=params) for where, params in self._groups(frame)]
        return pd.concat(parts, ignore_index=True) if parts else frame.iloc[0:0]

    def _replace(self, conn, staged, rows):
        for where, params in self._groups(staged):
            conn.execute(self._statement('DELETE', where), params)
        if len(rows):
            columns = set(self.columns())
            rows[[c for c in rows.columns if c in columns]].to_sql(self.table, conn, if_exists='append', index=False)

    def _with_kept_columns(self, conn, frame):
        """Staged rows plus, for keys that already exist, the stored values of columns the frame does not carry (run_daily writes the
        indicators back into daily_market_data), so a refetch never wipes them."""
        import pandas as pd
        other = [c for c in self.columns() if c not in frame.columns]
        if not other:
            return frame
        current = pd.concat([pd.read_sql(self._statement('SELECT *', where), conn, params=params) for where, params in self._groups(frame)],
                            ignore_index=True)
        if current.empty:
            return frame
        norm = lambda f: f.assign(**{f'_k_{k}': f[k].astype(str).str.strip().str[:10] for k in self.keys})
        joined = norm(frame).merge(norm(current)[[f'_k_{k}' for k in self.keys] + other], on=[f'_k_{k}' for k in self.keys], how='left')
        return joined.drop(columns=[f'_k_{k}' for k in self.keys])

    def write(self, frame, day=None) -> int:
        if missing := sorted(set(self.required_columns) - set(self.columns())):
            raise RuntimeError(f'SCHEMA_NOT_READY: {self.table} lacks {missing}; the existing update adds them, this service never alters a table')
        with self.engine().begin() as conn:  # one transaction: delete the staged keys, insert the staged rows (other columns kept)
            self._replace(conn, frame, self._with_kept_columns(conn, frame))
        return len(frame)

    def restore(self, staged, preimage) -> None:
        with self.engine().begin() as conn:  # removes inserted keys and rewrites the replaced rows exactly
            self._replace(conn, staged, preimage)

    def export(self):
        import pandas as pd
        from sqlalchemy import text
        with self.engine().connect() as conn:
            return pd.read_sql(text(f'SELECT * FROM {self.table}'), conn)


class _PairCommitter:
    """ponytail: adapts a (commit, reread-count) pair (older tests and financials until task 2.3 gives them a TableCommitter);
    it has no pre-image and no export, so its backup receipt records zero rows."""

    def __init__(self, name, pair):
        self.table, (self._commit, self._reread), self._written, self.keys = name, pair, False, ('stock_id',)

    def key_dates(self, frame, day):
        return [str(day)]

    def read(self, frame, day=None):
        return frame.head(self._reread(frame, day)) if self._written else frame.iloc[0:0]

    def write(self, frame, day=None):
        self._written = True
        return self._commit(frame, day)

    def restore(self, staged, preimage):
        raise RuntimeError(f'{self.table}: no restore for a pair committer')

    def export(self):
        import pandas as pd
        return pd.DataFrame()


def _as_committer(name, committer):
    return _PairCommitter(name, committer) if isinstance(committer, tuple) else committer


def _loose(committer) -> set:
    return getattr(committer, 'single_precision', set)()


def same_rows(a, b, keys, loose=()) -> bool:
    """Same keys and, per key, the same value in every column of b present in a (numbers within rtol 1e-9, or 1e-5 for the
    single-precision columns in `loose`; text exactly)."""
    import numpy as np
    import pandas as pd
    if len(a) != len(b):
        return False
    if len(b) == 0:
        return True
    shared = [k for k in keys if k in a.columns and k in b.columns]
    left = a.assign(**{k: a[k].astype(str) for k in shared}).set_index(shared).sort_index()
    right = b.assign(**{k: b[k].astype(str) for k in shared}).set_index(shared).sort_index()
    if not left.index.equals(right.index):
        return False
    for column in (c for c in right.columns if c in left.columns):
        x, y = left[column], right[column]
        if pd.api.types.is_numeric_dtype(x) or pd.api.types.is_numeric_dtype(y):
            rtol = 1e-5 if column in loose else 1e-9
            if not np.isclose(pd.to_numeric(x, errors='coerce'), pd.to_numeric(y, errors='coerce'), rtol=rtol, atol=1e-12, equal_nan=True).all():
                return False
        elif not (x.fillna('<NA>').astype(str).to_numpy() == y.fillna('<NA>').astype(str).to_numpy()).all():
            return False
    return True


COMMITTERS = {'prices_volume': TableCommitter('daily_market_data', ('stock_id', 'trade_date'), date_of=lambda f: f['trade_date']),
              'monthly_revenue': TableCommitter('monthly_revenue', ('stock_id', 'year', 'month'),
                                                date_of=lambda f: [f'{int(y)}-{int(m):02d}-28' for y, m in zip(f['year'], f['month'])]),
              'financials': TableCommitter('financial_statements', ('stock_id', 'year', 'quarter'), required_columns=FINANCIAL_OPTIONAL_COLUMNS,
                                           date_of=lambda f: [f'{int(y)}-{3 * int(q):02d}-28' for y, q in zip(f['year'], f['quarter'])])}


def validate_frame(name: str, day: str, frame) -> list:
    """Required columns, target date or period, non-empty and unique primary keys. An empty list means valid."""
    problems = [] if len(frame) else ['EMPTY_FRAME']
    if missing := [c for c in REQUIRED_COLUMNS.get(name, ()) if c not in frame]:
        problems.append(f'MISSING_COLUMNS:{missing}')
    keys = list(PRIMARY_KEYS.get(name, ()))
    keys = [k for k in keys if k in frame]
    if keys and frame[keys].isna().any().any():
        problems.append('PRIMARY_KEY_NULL')
    if keys and frame.duplicated(keys).any():
        problems.append(f'PRIMARY_KEY_DUPLICATES:{int(frame.duplicated(keys).sum())}')
    if name == 'prices_volume' and 'trade_date' in frame and (frame.trade_date.astype(str).str[:10] != day).any():
        problems.append('TRADE_DATE_MISMATCH')
    if name == 'financials' and {'year', 'quarter'} <= set(frame.columns):
        year, quarter = _financial_quarter(_as_date(day))
        if ((frame.year != year + 1911) | (frame.quarter != quarter)).any():
            problems.append('PERIOD_MISMATCH')
    return problems


def _file_sha(path) -> str:
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


# ---------------------------------------------------------------- service

class MarketDataUpdateService:
    def __init__(self, root=DEFAULT_ROOT, adapters=None, sleep=time.sleep, today=None, committers=None, calendar=None, enforce=None,
                 market_scope=None, commit_floor='DEV_END', latest_market_date=None, now=None):
        """Injected adapters (tests) get no committer unless one is passed; the default adapters use COMMITTERS."""
        self.root = Path(root)
        self.market_scope = resolve_market_scope(market_scope)
        self.uses_default_adapters = adapters is None
        self.adapters = default_adapters(self.root, self.market_scope) if adapters is None else dict(adapters)
        self.committers = (COMMITTERS if adapters is None else {}) if committers is None else dict(committers)
        self.sleep = sleep
        self._today = today
        self.calendar = calendar or self._calendar
        self._enforce = enforce
        self._latest_market_date = latest_market_date
        self._now = now or (lambda: datetime.now(ZoneInfo('Asia/Taipei')).replace(tzinfo=None))
        self.commit_floor = DEV_END if commit_floor == 'DEV_END' else commit_floor  # G1: keys on or before it are never written

    def _calendar(self, day) -> str:
        from core.research import trading_calendar
        from scripts import research_workflow as rw
        return trading_calendar.status(day, folders=(self.root / '_raw' / 'twse_rwd', rw.MI_INDEX))

    def expected(self, day):
        """(latest date <= day not known to be closed, its calendar status)."""
        from core.research import trading_calendar
        found, state = trading_calendar.expected_trade_date(_as_date(day), status_fn=self.calendar)
        return (found or _as_date(day)).isoformat(), state

    @property
    def today(self) -> date:
        return _as_date(self._today) if self._today is not None else date.today()

    def latest_market_date(self):
        if self._latest_market_date is not None:
            return self._latest_market_date()
        if not self.uses_default_adapters:
            return None  # injected adapters (tests, rehearsal) never read the production database
        from core.db_helper import get_latest_valid_market_date
        return get_latest_valid_market_date()

    def _unpublished(self, day) -> bool:
        now = self._now()
        return _as_date(day) == now.date() and now.hour < 15  # TWSE publishes the session after 15:00 Taipei

    def _publication_day(self):
        return self.today - timedelta(days=1) if self._unpublished(self.today) else self.today

    def catch_up_dates(self, latest=None) -> list:
        """Sessions after the database's latest valid date up to today, oldest first, at most MAX_BACKFILL_DATES: weekends and observed or
        scheduled closures skipped; today only after the 15:00 Taipei publication cutoff."""
        latest, today, now = latest or self.latest_market_date(), self.today, self._now()
        if latest is None:
            return [self.expected(self._publication_day())[0]]
        closures = set(_read_json(self.root / 'calendar' / 'closures.json', {}))
        day, out = _as_date(latest) + timedelta(days=1), []
        while day <= today and len(out) < MAX_BACKFILL_DATES:
            unpublished = day == now.date() and now.hour < 15
            if day.weekday() < 5 and day.isoformat() not in closures and self.calendar(day) not in CLOSED and not unpublished:
                out.append(day.isoformat())
            day += timedelta(days=1)
        return out

    def run(self, mode: str, dates=None, datasets=None, force: bool = False) -> dict:
        if mode not in MODES:
            raise ValueError(f'UNKNOWN_MODE: {mode}')
        names = list(DATASETS) if not datasets else [str(n).strip() for n in (datasets.split(',') if isinstance(datasets, str) else datasets)]
        unknown = sorted(set(names) - set(DATASETS))
        if unknown:
            raise ValueError(f'UNKNOWN_DATASETS: {unknown}')
        today = self.today
        if mode == 'daily':  # the ingestion authority is the switch (trusted grant or the D7 built-in policy); daily mode catches up every missing session
            if self.uses_default_adapters:
                from core.runtime import ingestion_authority
                reason = ingestion_authority.describe().get('reason')
                if reason:  # nothing read, fetched or written while disabled or refused
                    return {'status': 'SKIPPED_INGESTION_DISABLED' if reason == 'INGESTION_DISABLED' else 'SKIPPED_NO_GRANT', 'mode': mode, 'reason': reason}
                ensure_calendar_year(today.year, self.root)
            latest = self.latest_market_date()
            if latest is None:  # no database anchor (injected adapters): today only, as before
                if self._unpublished(today):
                    return {'status': 'SKIPPED_NOT_YET_PUBLISHED', 'mode': mode, 'dates': [today.isoformat()]}
                state = self.calendar(today)
                if state in CLOSED or today.weekday() >= 5:
                    return {'status': 'SKIPPED_NON_BUSINESS_DAY', 'mode': mode, 'dates': [today.isoformat()], 'calendar': state}
                if state == 'CONFLICT':
                    return {'status': 'SKIPPED_CALENDAR_CONFLICT', 'mode': mode, 'dates': [today.isoformat()], 'calendar': state}
                dates = [today.isoformat()]
            else:
                dates = self.catch_up_dates(latest)
            if not dates:
                return {'status': 'UP_TO_DATE', 'mode': mode, 'dates': []}
        elif mode == 'manual':
            dates = validate_dates(dates, today) if dates else [self.expected(self._publication_day())[0]]
        else:
            dates = validate_dates(dates, today)
        try:
            with acquire_lock(self.root):
                return self._run_locked(mode, dates, names, force)
        except UpdateInProgress:
            return {'status': 'UPDATE_IN_PROGRESS', 'mode': mode, 'dates': dates}

    def _run_locked(self, mode, dates, names, force):
        update_id = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ') + '_' + secrets.token_hex(3)
        started, started_at = time.monotonic(), datetime.now(timezone.utc).isoformat()
        self.resolve_intents()
        blocked = self.unreconciled()
        state_path = self.root / 'state.json'
        state = _read_json(state_path, {})
        results = []
        for day in dates:
            for name in names:
                key = f'{name}|{day}'
                if (name, str(day)) in blocked:  # rows equal neither image of an earlier write: never overwrite them, the Owner decides
                    results.append({'dataset': name, 'date': day, 'status': 'RECONCILIATION_REQUIRED', 'attempts': 0,
                                    'errors': [f'unreconciled write intent {blocked[(name, str(day))]}']})
                    continue
                if key in state and not force:
                    results.append({'dataset': name, 'date': day, 'status': 'SKIPPED_ALREADY_SUCCESS', 'attempts': 0,
                                    'errors': [], 'previous_update_id': state[key].get('update_id')})
                    continue
                result = self._run_one(name, day, update_id)
                results.append(result)
                if result['status'] in STORED:
                    state[key] = {**result, 'update_id': update_id}
                    _write_json(state_path, state)
        record = {
            'update_id': update_id, 'mode': mode, 'trigger': os.environ.get('STOCK_LINBOT_TRIGGER', 'manual'), 'dates': dates,
            'datasets': names, 'force': bool(force), 'market_scope': self.market_scope,
            'universe': 'TWSE_LISTED_ONLY' if self.market_scope == 'TWSE_ONLY' else 'TWSE_AND_TPEX',
            'status': 'COMPLETED_WITH_ERRORS' if any(r['status'] in FAILING for r in results) else 'COMPLETED',
            'results': results, 'coverage_gaps': list(COVERAGE_GAPS),
            'started_at': started_at,
            'duration_seconds': round(time.monotonic() - started, 3),
        }
        _write_json(self.root / f'{update_id}.json', record)
        return record

    def _run_one(self, name, day, update_id='manual'):
        base = {'dataset': name, 'date': day, 'split_mode': SPLIT_MODE.get(name)}
        adapter = self.adapters.get(name)
        if adapter is None:
            return {**base, 'status': 'NOT_COVERED', 'attempts': 0, 'errors': ['no adapter configured']}
        errors = []
        started = time.monotonic()
        for attempt in range(1, MAX_ATTEMPTS + 1):
            try:
                payload = dict(adapter(day) or {})
                break
            except PermissionError as exc:  # operation_policy.AuthorizationDenied / EndpointBlocked: a retry cannot change the decision
                return {**base, 'status': 'IMPLEMENTED_BUT_AUTH_BLOCKED', 'attempts': attempt, 'errors': errors + [f'{type(exc).__name__}: {exc}'],
                        'blocked_reason': getattr(exc, 'reasons', [_reason_code(exc)])}
            except Exception as exc:
                errors.append(f'attempt {attempt}: {type(exc).__name__}: {exc}')
                if type(exc).__name__ == 'SchemaDriftError':  # a changed source schema does not heal on retry
                    return {**base, 'status': 'SCHEMA_DRIFT', 'attempts': attempt, 'errors': errors}
                if attempt < MAX_ATTEMPTS:
                    self.sleep(BACKOFF[attempt - 1])
        else:
            return {**base, 'status': 'FAILED', 'attempts': MAX_ATTEMPTS, 'errors': errors,
                    'duration_seconds': round(time.monotonic() - started, 3)}
        frame = payload.pop('frame', None)
        expected, calendar_state = self.expected(day)
        result = {**base, **check_result(name, day, payload, expected, calendar_state), 'attempts': attempt, 'errors': errors,
                  'adapter_result': payload, 'duration_seconds': round(time.monotonic() - started, 3)}
        if frame is not None:
            try:
                result.update(self._stage_and_commit(name, day, update_id, frame, result['status']))
            except Exception as exc:  # staging or validation broke: report this dataset, keep the run going
                result.update(status='STAGE_FAILED', errors=errors + [f'{type(exc).__name__}: {exc}'[:300]])
        return result

    def _stage_and_commit(self, name, day, update_id, frame, status):
        """stage (parquet + SHA256) -> validate -> commit only the staged bytes after WRITE_PRODUCTION_DB -> re-read. Never a SUCCESS
        without a verified commit for a dataset that has a database target."""
        import pandas as pd
        path = self.root / 'staging' / update_id / f'{name}_{day}.parquet'
        path.parent.mkdir(parents=True, exist_ok=True)
        frame.to_parquet(path, index=False)
        digest = _file_sha(path)
        out = {'staging': {'path': str(path), 'sha256': digest, 'rows': len(frame)}}
        if problems := validate_frame(name, day, frame):
            return {**out, 'status': 'INTEGRITY_INVALID', 'validation': problems}
        if status not in STORED:
            return out
        if name not in self.committers:
            return {**out, 'validation': []}
        committer = _as_committer(name, self.committers[name])
        if self.commit_floor and (early := sorted(d for d in committer.key_dates(frame, day) if d <= self.commit_floor)):
            return {**out, 'status': 'COMMIT_FLOOR_VIOLATION', 'validation': [], 'floor': self.commit_floor, 'keys_on_or_before': early[:5]}
        try:
            receipt = self._ensure_backup(committer)
        except Exception as exc:  # no restorable backup, no commit (G8)
            return {**out, 'status': 'BACKUP_REQUIRED', 'validation': [], 'errors': [f'{type(exc).__name__}: {exc}'[:300]]}
        try:
            self._authorize_commit(committer.table)
            raw = path.read_bytes()  # hash and parse the same bytes
            if hashlib.sha256(raw).hexdigest() != digest:
                return {**out, 'status': 'STAGING_HASH_MISMATCH', 'validation': []}
            staged = pd.read_parquet(io.BytesIO(raw))
            preimage = committer.read(staged, day)
            intent = self._write_intent(update_id, name, day, committer, path, digest, staged, preimage)
            written = committer.write(staged, day)
            current = committer.read(staged, day)
            match = written == len(staged) and same_rows(current, staged, committer.keys, _loose(committer))
            self._finish_intent(intent, 'COMMITTED' if match else 'COMMIT_VERIFY_FAILED')
        except PermissionError as exc:  # the commit authorization, or a write function's own enforcing gate
            return {**out, 'status': 'COMMIT_AUTH_BLOCKED', 'validation': [], 'blocked_reason': [_reason_code(exc)], 'errors': [str(exc)[:300]]}
        except Exception as exc:  # the write is one transaction: a failure leaves the database as it was; the staging file stays
            return {**out, 'status': 'COMMIT_FAILED', 'validation': [], 'errors': [f'{type(exc).__name__}: {exc}'[:300]]}
        verified = {'committed_rows': written, 'reread_rows': len(current), 'reread': {'match': match}, 'backup': receipt}
        if not match:  # every staged key written by this commit and its values present on re-read
            return {**out, 'status': 'COMMIT_VERIFY_FAILED', 'validation': [], **verified}
        return {**out, 'validation': [], **verified}

    def _authorize_commit(self, table):
        """One COMMIT_MARKET_DATA authorization (one grant use) per commit, by the trusted ingestion grant (decision.md D1)."""
        if self._enforce is not None:
            return self._enforce(table)
        from core.runtime.operation_policy import Operation, enforce
        enforce(Operation.COMMIT_MARKET_DATA, task_id='market_data_ingestion', table=table, market_scope=self.market_scope)

    def _ensure_backup(self, committer) -> dict:
        """G8: before the first commit to a table, export the whole table with SHA256 and row count and write a receipt."""
        folder = self.root / 'backups' / committer.table
        receipt_path = folder / 'RECEIPT.json'
        if receipt_path.is_file():
            return _read_json(receipt_path, {})
        folder.mkdir(parents=True, exist_ok=True)
        export = folder / 'export.parquet'
        frame = committer.export()
        frame.to_parquet(export, index=False)
        receipt = {'table': committer.table, 'rows': len(frame), 'path': str(export), 'sha256': _file_sha(export),
                   'created_at': datetime.now(timezone.utc).isoformat(timespec='seconds')}
        _write_json(receipt_path, receipt)
        return receipt

    def _write_intent(self, update_id, name, day, committer, staged_path, digest, staged, preimage) -> Path:
        """G7: what is about to change, written before the transaction so a crash can be resolved by reread alone."""
        folder = self.root / 'intents'
        folder.mkdir(parents=True, exist_ok=True)
        pre_path = staged_path.with_name(staged_path.stem + '.preimage.parquet')
        preimage.to_parquet(pre_path, index=False)

        def keys(frame):
            return {tuple(map(str, row)) for row in frame[[k for k in committer.keys if k in frame.columns]].itertuples(index=False)}
        intent = {'update_id': update_id, 'dataset': name, 'table': committer.table, 'date': str(day), 'status': 'PENDING',
                  'staged': str(staged_path), 'staged_sha256': digest, 'preimage': str(pre_path), 'preimage_sha256': _file_sha(pre_path),
                  'keys': len(staged), 'inserted_keys': sorted(map(list, keys(staged) - keys(preimage))),
                  'at': datetime.now(timezone.utc).isoformat(timespec='seconds')}
        path = folder / f'{update_id}_{committer.table}_{day}.json'
        _write_json(path, intent)
        return path

    def _finish_intent(self, path, status):
        _write_json(path, {**_read_json(path, {}), 'status': status, 'finished_at': datetime.now(timezone.utc).isoformat(timespec='seconds')})

    def _intents(self):
        return sorted((self.root / 'intents').glob('*.json'))

    def resolve_intents(self) -> list:
        """Unfinished intents are decided by reread only: database == staged -> COMMITTED; == pre-image -> NOT_COMMITTED; otherwise
        RECONCILIATION_REQUIRED (that unit stops and is reported). Nothing is refetched or rewritten here."""
        import pandas as pd
        out = []
        for path in self._intents():
            intent = _read_json(path, {})
            if intent.get('status') != 'PENDING' or intent.get('dataset') not in self.committers:
                continue
            committer = _as_committer(intent['dataset'], self.committers[intent['dataset']])
            staged, preimage = pd.read_parquet(intent['staged']), pd.read_parquet(intent['preimage'])
            current = committer.read(staged, intent['date'])
            loose = _loose(committer)
            status = ('COMMITTED' if same_rows(current, staged, committer.keys, loose) else
                      'NOT_COMMITTED' if same_rows(current, preimage, committer.keys, loose) else 'RECONCILIATION_REQUIRED')
            self._finish_intent(path, status)
            out.append({'intent': path.name, 'table': intent['table'], 'date': intent['date'], 'status': status})
        return out

    def unreconciled(self) -> dict:
        """(dataset, date) -> intent file for every intent left RECONCILIATION_REQUIRED; that unit is skipped until the Owner resolves it
        (restore the pre-image, or mark the intent RECONCILED after checking the rows)."""
        return {(i.get('dataset'), str(i.get('date'))): p.name for p in self._intents()
                if (i := _read_json(p, {})).get('status') == 'RECONCILIATION_REQUIRED'}

    def restore(self, update_id) -> list:
        """Undo one update under the update lock: for each committed intent, after the same commit authorization as a write, delete the
        staged keys (inserted ones included) and rewrite the pre-image."""
        try:
            with acquire_lock(self.root):
                return self._restore_locked(update_id)
        except UpdateInProgress:
            return [{'status': 'UPDATE_IN_PROGRESS'}]

    def _restore_locked(self, update_id) -> list:
        import pandas as pd
        out = []
        for path in self._intents():
            intent = _read_json(path, {})
            if intent.get('update_id') != update_id or intent.get('status') not in ('COMMITTED', 'COMMIT_VERIFY_FAILED'):
                continue
            committer = _as_committer(intent['dataset'], self.committers[intent['dataset']])
            staged = pd.read_parquet(intent['staged'])
            if not same_rows(committer.read(staged, intent['date']), staged, committer.keys, _loose(committer)):  # a newer commit owns them
                out.append({'table': intent['table'], 'date': intent['date'], 'status': 'RECONCILIATION_REQUIRED'})
                continue
            try:
                self._authorize_commit(intent['table'])
            except PermissionError as exc:
                out.append({'table': intent['table'], 'date': intent['date'], 'status': 'COMMIT_AUTH_BLOCKED', 'reason': str(exc)[:200]})
                continue
            committer.restore(staged, pd.read_parquet(intent['preimage']))
            self._finish_intent(path, 'RESTORED')
            out.append({'table': intent['table'], 'date': intent['date'], 'status': 'RESTORED'})
        return out

    def history(self, limit: int = 20) -> list:
        # newest write first: two runs in the same second get random suffixes, so the file name alone does not order them
        paths = sorted(self.root.glob('[0-9]*.json'), key=lambda p: (p.stat().st_mtime_ns, p.name), reverse=True)[:max(int(limit), 0)]
        return [_read_json(path, {}) for path in paths]

    def verify_scheduled(self) -> dict:
        """Next-session acceptance: the newest record came from the OS task (trigger scheduled) for the latest open session, every dataset
        was reported, and every REQUIRED_DAILY dataset was stored. Anything less is FAILED with the reasons."""
        last = self.history(1)
        if not last:
            return {'status': 'FAILED', 'problems': ['NO_UPDATE_RECORD']}
        record, problems = last[0], []
        if record.get('trigger') != 'scheduled':
            problems.append('TRIGGER_NOT_SCHEDULED')
        session, state = self.expected(self.today)
        if record.get('dates') != [session]:
            problems.append(f'DATE_NOT_LATEST_SESSION:{record.get("dates")}!={session}')
        if missing := sorted(set(DATASETS) - {r['dataset'] for r in record.get('results', [])}):
            problems.append(f'DATASETS_MISSING:{missing}')
        # a scheduled run that updated nothing is not an accepted daily update
        problems += [f"DATASET_NOT_UPDATED:{r['dataset']}:{r['status']}" for r in record.get('results', [])
                     if r['dataset'] in REQUIRED_DAILY and r['status'] not in STORED + ('SKIPPED_ALREADY_SUCCESS',)]
        return {'status': 'FAILED' if problems else 'VERIFIED', 'problems': problems, 'update_id': record.get('update_id'),
                'session': session, 'calendar': state, 'per_dataset': {r['dataset']: r['status'] for r in record.get('results', [])}}

    def status(self) -> dict:
        state = _read_json(self.root / 'state.json', {})
        datasets = {}
        for key, record in state.items():
            name, _, day = key.partition('|')
            if day >= datasets.get(name, {}).get('date', ''):
                datasets[name] = {'date': day, 'status': record.get('status'), 'update_id': record.get('update_id'),
                                  'latest_trade_date': (record.get('checks') or {}).get('latest_trade_date')}
        last = self.history(1)
        last = last[0] if last else None
        schedule = load_schedule(self.root)
        session, calendar_state = self.expected(self.today)
        latest = {r['dataset']: r for r in (last or {}).get('results', [])}
        sources = {name: {'split_mode': SPLIT_MODE.get(name), 'last_status': (latest.get(name) or {}).get('status'),
                          'blocked_reason': (latest.get(name) or {}).get('blocked_reason'),
                          'endpoints': [('restricted:' + spec['restricted'] if 'restricted' in spec else 'public') + ' ' + spec['url']
                                        for spec in endpoints(name, session)]}
                   for name in DATASETS}
        try:
            from core.research import benchmark_tri as tri
            benchmark = {'status': tri.CROSS_STATUS if tri.files() else 'IMPLEMENTED_BUT_AUTH_BLOCKED', 'files': [p.name for p in tri.files()]}
        except Exception as exc:
            benchmark = {'status': 'UNKNOWN', 'reason': f'{type(exc).__name__}: {exc}'}
        from core.runtime import ingestion_authority, public_data
        return {
            'latest_trade_date': datasets.get('prices_volume', {}).get('latest_trade_date'),
            'last_update': {k: last.get(k) for k in ('update_id', 'mode', 'trigger', 'dates', 'status', 'started_at')} if last else None,
            'datasets': datasets,
            'attention': [r for r in (last or {}).get('results', [])
                          if r['status'] not in ('SUCCESS', 'SKIPPED_ALREADY_SUCCESS', 'COVERED_BY')],
            'schedule': schedule,
            'scheduler_state': 'OS_TASK_VERIFIED' if schedule.get('os_task_verified') is True else 'SCHEDULER_SETUP_REQUIRED',
            'update_in_progress': (self.root / '.lock').exists(),
            'coverage_gaps': list(COVERAGE_GAPS),
            'calendar': {'expected_session': session, 'state': calendar_state},
            'benchmark': benchmark,
            'sources': sources,
            'market_scope': self.market_scope,
            'coverage': {'twse': 'COVERED', 'tpex': 'NOT_COVERED' if self.market_scope == 'TWSE_ONLY' else 'RESTRICTED'},
            'authorization': _safe(ingestion_authority.describe),
            'ingestion_audit_tail': _safe(lambda: _tail(public_data.ingestion_audit_path(), 20)),
            'closures': _read_json(self.root / 'calendar' / 'closures.json', {}),
            'scheduler_tasks': _read_json(ROOT / 'outputs/market_data_autonomy/scheduler/tasks.json', None),
        }


def _safe(fn):
    try:
        return fn()
    except Exception as exc:  # a status page never fails on one panel
        return {'reason': f'{type(exc).__name__}: {exc}'[:200]}


def _tail(path, n) -> list:
    path = Path(path)
    return [json.loads(line) for line in path.read_text(encoding='utf-8').splitlines()[-n:] if line.strip()] if path.is_file() else []


REHEARSAL_COLUMNS = ('stock_id TEXT, trade_date TEXT, stock_name TEXT, open_price REAL, high_price REAL, low_price REAL, close_price REAL, '
                     'volume REAL, pe_ratio REAL, foreign_buy REAL, trust_buy REAL, dealer_buy REAL, margin_balance REAL, short_balance REAL')


def _sentinels() -> dict:
    """Files a rehearsal must never touch: the authorization decisions, the grant usage log and the research budget."""
    from core.runtime import operation_policy, research_budget
    from scripts import task_controller as tc
    paths = [Path(operation_policy.LOG), Path(tc.STATE) / tc.USAGE, *sorted(Path(research_budget.STATE).glob('*.sqlite3'))]
    return {str(path): _file_sha(path) if path.is_file() else None for path in paths}


def rehearse(cache_dir, session, root=None) -> dict:
    """D5: the full TWSE_ONLY path (probe, reconcile, stage, validate, backup, intent, commit, value reread) on cached official replies and a
    temporary SQLite target. Zero HTTP: the cache is the only source; production database, grant use and research budget are untouched."""
    import tempfile
    import pandas as pd
    from sqlalchemy import create_engine, text
    root = Path(root or tempfile.mkdtemp(prefix='linbot_rehearsal_'))
    root.mkdir(parents=True, exist_ok=True)
    cache, sent = Path(cache_dir), []
    before = _sentinels()

    class Reply:
        def __init__(self, path):
            self.path = path

        def json(self):
            return json.loads(self.path.read_text(encoding='utf-8'))

    def request(method, url, **kwargs):  # cached replies only; a missing file is a missing source, never a network call
        name, ymd = url.split('/')[-1].split('?')[0], url.split('date=')[1][:8]
        path = cache / f'{name}_{ymd}.json'
        sent.append(path.name)
        if not path.is_file():
            raise FileNotFoundError(path.name)
        return Reply(path)

    engine = create_engine(f"sqlite:///{root / 'rehearsal_market.db'}")
    with engine.begin() as conn:
        conn.execute(text(f'CREATE TABLE IF NOT EXISTS daily_market_data ({REHEARSAL_COLUMNS})'))
    adapters = {'prices_volume': lambda day: twse_only_prices(day, request=request, closures=root / 'calendar' / 'closures.json',
                                                              margin=lambda d: pd.DataFrame())}
    committer = TableCommitter('daily_market_data', ('stock_id', 'trade_date'), engine=lambda: engine, date_of=lambda f: f['trade_date'])
    service = MarketDataUpdateService(root=root / 'updates', adapters=adapters, committers={'prices_volume': committer},
                                      enforce=lambda table: None, calendar=lambda day: 'OPEN_OBSERVED', market_scope='TWSE_ONLY')
    record = service.run('backfill', dates=[str(session)], datasets=['prices_volume'])
    with engine.connect() as conn:
        stored = conn.execute(text('SELECT COUNT(*) FROM daily_market_data WHERE trade_date = :d'), {'d': str(session)}).scalar()
    after = _sentinels()
    return {'label': 'REHEARSAL', 'session': str(session), 'root': str(root), 'cache_files_read': sent, 'http_attempts': 0,
            'rows_in_rehearsal_db': int(stored or 0), 'sentinels_unchanged': before == after, 'sentinels': after, 'record': record}


def main(argv=None) -> int:
    for stream in (sys.stdout, sys.stderr):  # cleaning prints emoji; a cp950 console must not turn that into a FAILED dataset
        stream.reconfigure(errors='backslashreplace')
    parser = argparse.ArgumentParser(description='Market data update service (market database only).')
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument('--daily', action='store_true')
    group.add_argument('--manual', action='store_true')
    group.add_argument('--backfill', metavar='YYYY-MM-DD,...')
    group.add_argument('--verify-scheduled', action='store_true', help='next-session acceptance of the OS task (read-only)')
    group.add_argument('--rehearse', metavar='YYYY-MM-DD', help='full path on cached official replies into a temporary SQLite target')
    group.add_argument('--restore', metavar='UPDATE_ID', help='undo one update from its intents and pre-images')
    parser.add_argument('--datasets', help=f"comma list of {','.join(DATASETS)}")
    parser.add_argument('--force', action='store_true')
    parser.add_argument('--cache', default=str(ROOT / 'artifacts' / 'official_data_recovery_cache' / 'mi_index'))
    args = parser.parse_args(argv)
    if args.rehearse:
        import contextlib
        with contextlib.redirect_stdout(sys.stderr):  # progress prints go to stderr; stdout is the evidence JSON only
            result = rehearse(args.cache, args.rehearse)
        print(json.dumps(result, ensure_ascii=True, indent=2, default=str))
        return 0 if result['sentinels_unchanged'] else 1
    service = MarketDataUpdateService(root=os.environ.get('STOCK_LINBOT_UPDATE_ROOT') or DEFAULT_ROOT)
    if args.restore:
        print(json.dumps(service.restore(args.restore), ensure_ascii=False, indent=2))
        return 0
    if args.verify_scheduled:
        result = service.verify_scheduled()
        print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
        return 0 if result['status'] == 'VERIFIED' else 1
    mode = 'daily' if args.daily else 'manual' if args.manual else 'backfill'
    try:
        result = service.run(mode, dates=args.backfill, datasets=args.datasets, force=args.force)
    except ValueError as exc:
        result = {'status': 'INVALID_REQUEST', 'error': str(exc)}
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    return 0 if result['status'] in ('COMPLETED', 'SKIPPED_INGESTION_DISABLED', 'SKIPPED_NO_GRANT', 'UP_TO_DATE', 'SKIPPED_NON_BUSINESS_DAY', 'SKIPPED_NOT_YET_PUBLISHED') else 1


if __name__ == '__main__':
    raise SystemExit(main())
