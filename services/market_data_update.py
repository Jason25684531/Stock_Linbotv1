"""Market data update service (D9): one locked, idempotent, retried entry point for the market database update.

Module-level imports are stdlib only. The default adapters import jobs/update_database.py and core.* lazily, so importing
this module loads no DB, LINE, broker, run_daily or recommendation code, and the service never calls any of them.
Every default network adapter passes operation_policy.gate(Operation.DOWNLOAD_MARKET_DATA) first. Writing the database to
2026 does not open 2026 to research: research readers keep their own development-window checks.

CLI: python -m services.market_data_update --daily | --manual | --backfill 2025-01-02,2025-01-03 [--datasets a,b] [--force]
"""
from __future__ import annotations

import argparse
import json
import os
import secrets
import sys
import time
from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_ROOT = ROOT / 'outputs' / 'market_data_updates'
DATASETS = ('prices_volume', 'monthly_revenue', 'financials', 'margin', 'corporate_actions', 'benchmark', 'benchmark_tri')
MODES = ('daily', 'manual', 'backfill')
MAX_ATTEMPTS = 3
BACKOFF = (2, 4)  # seconds before attempts 2 and 3
MAX_BACKFILL_DATES = 31
STALE_LOCK_SECONDS = 2 * 3600
MIN_VALID_MARKET_ROWS_FALLBACK = 100  # mirrors core.db_helper.MIN_VALID_MARKET_ROWS when that import is unavailable
PIT_DATASETS = ('financials', 'monthly_revenue')
STORED = ('SUCCESS', 'PIT_UNVERIFIED')  # fetched with integrity ok: the idempotency key is recorded
FAILING = ('FAILED', 'INTEGRITY_INVALID')
HOLIDAY_NOTE = 'expected date is the latest Mon-Fri weekday; exchange holidays are not known, so a holiday may show STALE'
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
        return True  # ponytail: os.kill(pid, 0) terminates the target on Windows; without psutil a lock is never presumed dead
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


def check_result(name: str, day: str, payload: dict) -> dict:
    """Integrity, freshness and point-in-time checks over one adapter result."""
    if payload.get('status'):  # adapter-declared: NOT_COVERED, COVERED_BY, IMPLEMENTED_BUT_AUTH_BLOCKED, DATA_BLOCKED
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
    expected = latest_weekday(_as_date(day)).isoformat()
    actual = payload.get('latest_trade_date')
    stale = actual is not None and str(actual)[:10] < expected
    pit_verified = bool(payload.get('has_announcement_date')) if name in PIT_DATASETS else None
    checks = {'rows': rows, 'integrity_ok': not problems, 'integrity_problems': problems,
              'expected_latest_trade_date': expected, 'latest_trade_date': actual, 'stale': stale,
              'pit_verified': pit_verified}
    if stale:
        checks['note'] = HOLIDAY_NOTE
    status = ('INTEGRITY_INVALID' if problems else 'STALE' if stale
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


def default_adapters(root=DEFAULT_ROOT) -> dict:
    """dataset -> callable(date 'YYYY-MM-DD') -> {rows, latest_trade_date, has_announcement_date, required_empty} or {status}."""
    cache = Path(root) / '_raw' / 'twse_rwd'

    def gate(name):
        from core.runtime.operation_policy import Operation, gate as policy_gate
        policy_gate(Operation.DOWNLOAD_MARKET_DATA, dataset=name)

    def prices_volume(day):
        gate('prices_volume')
        from core.db_helper import MIN_VALID_MARKET_ROWS, get_db_engine, get_latest_trade_date, normalize_date_str
        from jobs import update_database
        rows = update_database.update_market_date(get_db_engine(), day)  # snapshot + foreign flow via MCP, margin merged
        return {'rows': rows, 'min_rows': MIN_VALID_MARKET_ROWS, 'required_empty': [],
                'latest_trade_date': normalize_date_str(get_latest_trade_date()),
                'note': 'jobs/update_database.update_market_date: MCP snapshot + foreign flow, margin enrichment, upsert'}

    def margin(day):
        return {'status': 'COVERED_BY', 'covered_by': 'prices_volume',
                'note': 'update_market_date merges core.crawlers.chip_data_scraper.fetch_margin_balance into daily_market_data'}

    def monthly_revenue(day):
        gate('monthly_revenue')
        from core.update_monthly_revenue import fetch_mops_static_revenue, process_and_save
        d = _as_date(day)
        # Same month choice as update_database.run_monthly_revenue_update, keyed on the requested date instead of now().
        year, month = (d.year, d.month) if d.day >= 12 else ((d.year, d.month - 1) if d.month > 1 else (d.year - 1, 12))
        rows = 0
        for offset in range(2):
            y, m = (year, month - offset) if month - offset > 0 else (year - 1, month - offset + 12)
            frame = fetch_mops_static_revenue(y, m)
            if frame is not None and not frame.empty:
                process_and_save(frame)
                rows += len(frame)
        if not rows:
            raise RuntimeError('MOPS monthly revenue returned no rows')
        return {'rows': rows, 'latest_trade_date': None, 'has_announcement_date': False, 'required_empty': [],
                'note': 'monthly_revenue table has no announcement-date column; rows counts fetched rows'}

    def financials(day):
        gate('financials')
        from core.update_financials_mops import update_quarter
        d = _as_date(day)
        roc = d.year - 1911
        # Same quarter choice as update_database.run_financial_update, keyed on the requested date.
        year, quarter = ((roc, 3) if d.month >= 11 else (roc, 2) if d.month >= 8 else (roc, 1) if d.month >= 5
                         else (roc - 1, 4) if d.month >= 3 else (roc - 1, 3))
        if not update_quarter(year, quarter):
            raise RuntimeError(f'update_quarter({year}, {quarter}) returned False')
        return {'rows': None, 'latest_trade_date': None, 'has_announcement_date': False, 'required_empty': [],
                'note': f'ROC {year} Q{quarter}; financial_statements has no announcement-date column'}

    def corporate_actions(day):
        gate('corporate_actions')
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
        gate('benchmark')
        from core.research.sources import twse
        raw = twse.fetch_daily_quotes(_as_date(day), cache, max_attempts=1)
        if raw.error:
            raise RuntimeError(raw.error)
        kind = twse.classify(raw).kind
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
        report = tri.quality(tri.research_calendar())
        return {'status': report['status'], 'reason': report['reason'], 'covered_days': report['covered_days'],
                'calendar_days': report['calendar_days'], 'missing_dates': report.get('missing_dates', [])[:20]}

    return {'prices_volume': prices_volume, 'monthly_revenue': monthly_revenue, 'financials': financials,
            'margin': margin, 'corporate_actions': corporate_actions, 'benchmark': benchmark, 'benchmark_tri': benchmark_tri}


# ---------------------------------------------------------------- service

class MarketDataUpdateService:
    def __init__(self, root=DEFAULT_ROOT, adapters=None, sleep=time.sleep, today=None):
        self.root = Path(root)
        self.uses_default_adapters = adapters is None
        self.adapters = default_adapters(self.root) if adapters is None else dict(adapters)
        self.sleep = sleep
        self._today = today

    @property
    def today(self) -> date:
        return _as_date(self._today) if self._today is not None else date.today()

    def run(self, mode: str, dates=None, datasets=None, force: bool = False) -> dict:
        if mode not in MODES:
            raise ValueError(f'UNKNOWN_MODE: {mode}')
        names = list(DATASETS) if not datasets else [str(n).strip() for n in (datasets.split(',') if isinstance(datasets, str) else datasets)]
        unknown = sorted(set(names) - set(DATASETS))
        if unknown:
            raise ValueError(f'UNKNOWN_DATASETS: {unknown}')
        today = self.today
        if mode == 'daily':
            if not load_schedule(self.root)['enabled']:
                return {'status': 'SKIPPED_DISABLED', 'mode': mode}
            if today.weekday() >= 5:
                return {'status': 'SKIPPED_NON_BUSINESS_DAY', 'mode': mode, 'dates': [today.isoformat()]}
            dates = [today.isoformat()]
        elif mode == 'manual':
            dates = validate_dates(dates, today) if dates else [latest_weekday(today).isoformat()]
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
        self._pipeline_step('start')
        state_path = self.root / 'state.json'
        state = _read_json(state_path, {})
        results = []
        for day in dates:
            for name in names:
                key = f'{name}|{day}'
                if key in state and not force:
                    results.append({'dataset': name, 'date': day, 'status': 'SKIPPED_ALREADY_SUCCESS', 'attempts': 0,
                                    'errors': [], 'previous_update_id': state[key].get('update_id')})
                    continue
                result = self._run_one(name, day)
                results.append(result)
                if result['status'] in STORED:
                    state[key] = {**result, 'update_id': update_id}
                    _write_json(state_path, state)
        record = {
            'update_id': update_id, 'mode': mode, 'dates': dates, 'datasets': names, 'force': bool(force),
            'status': 'COMPLETED_WITH_ERRORS' if any(r['status'] in FAILING for r in results) else 'COMPLETED',
            'results': results, 'coverage_gaps': list(COVERAGE_GAPS),
            'started_at': started_at,
            'duration_seconds': round(time.monotonic() - started, 3),
        }
        _write_json(self.root / f'{update_id}.json', record)
        self._pipeline_step('finish', record)
        return record

    def _run_one(self, name, day):
        base = {'dataset': name, 'date': day}
        adapter = self.adapters.get(name)
        if adapter is None:
            return {**base, 'status': 'NOT_COVERED', 'attempts': 0, 'errors': ['no adapter configured']}
        errors = []
        started = time.monotonic()
        for attempt in range(1, MAX_ATTEMPTS + 1):
            try:
                payload = adapter(day) or {}
                break
            except PermissionError as exc:  # operation_policy.AuthorizationDenied: a retry cannot change the decision
                return {**base, 'status': 'IMPLEMENTED_BUT_AUTH_BLOCKED', 'attempts': attempt,
                        'errors': errors + [f'{type(exc).__name__}: {exc}']}
            except Exception as exc:
                errors.append(f'attempt {attempt}: {type(exc).__name__}: {exc}')
                if attempt < MAX_ATTEMPTS:
                    self.sleep(BACKOFF[attempt - 1])
        else:
            return {**base, 'status': 'FAILED', 'attempts': MAX_ATTEMPTS, 'errors': errors,
                    'duration_seconds': round(time.monotonic() - started, 3)}
        return {**base, **check_result(name, day, payload), 'attempts': attempt, 'errors': errors,
                'adapter_result': payload, 'duration_seconds': round(time.monotonic() - started, 3)}

    def _pipeline_step(self, phase, record=None):
        if not self.uses_default_adapters:
            return
        try:
            from core.runtime.operation_policy import Operation, enforce
            enforce(Operation.WRITE_PRODUCTION_DB, task_id='market_data_update_pipeline', dataset='pipeline_runs')
            from core.db_helper import record_pipeline_step_finish, record_pipeline_step_start
            context = {'pipeline_name': 'market_data_update', 'step_name': 'market_data_update',
                       'run_date': self.today.isoformat()}
            if phase == 'start':
                record_pipeline_step_start(**context)
                return
            failed = [f"{r['dataset']}|{r['date']}: {r['status']}" for r in record['results'] if r['status'] in FAILING]
            prices = [r for r in record['results'] if r['dataset'] == 'prices_volume' and r.get('checks')]
            latest = prices[-1]['checks'].get('latest_trade_date') if prices else None
            record_pipeline_step_finish(**context, status='failed' if failed else 'success', source_date=latest,
                                        trade_date=latest, error_summary='; '.join(failed)[:1000] or None)
        except Exception as exc:  # pipeline bookkeeping must never break the update itself
            print(f'pipeline step record skipped ({phase}): {exc}', file=sys.stderr)

    def history(self, limit: int = 20) -> list:
        paths = sorted(self.root.glob('[0-9]*.json'), reverse=True)[:max(int(limit), 0)]
        return [_read_json(path, {}) for path in paths]

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
        return {
            'latest_trade_date': datasets.get('prices_volume', {}).get('latest_trade_date'),
            'last_update': {k: last.get(k) for k in ('update_id', 'mode', 'dates', 'status', 'started_at')} if last else None,
            'datasets': datasets,
            'attention': [r for r in (last or {}).get('results', [])
                          if r['status'] not in ('SUCCESS', 'SKIPPED_ALREADY_SUCCESS', 'COVERED_BY')],
            'schedule': schedule,
            'scheduler_state': 'OS_TASK_VERIFIED' if schedule.get('os_task_verified') is True else 'SCHEDULER_SETUP_REQUIRED',
            'update_in_progress': (self.root / '.lock').exists(),
            'coverage_gaps': list(COVERAGE_GAPS),
        }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description='Market data update service (market database only).')
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument('--daily', action='store_true')
    group.add_argument('--manual', action='store_true')
    group.add_argument('--backfill', metavar='YYYY-MM-DD,...')
    parser.add_argument('--datasets', help=f"comma list of {','.join(DATASETS)}")
    parser.add_argument('--force', action='store_true')
    args = parser.parse_args(argv)
    mode = 'daily' if args.daily else 'manual' if args.manual else 'backfill'
    try:
        result = MarketDataUpdateService().run(mode, dates=args.backfill, datasets=args.datasets, force=args.force)
    except ValueError as exc:
        result = {'status': 'INVALID_REQUEST', 'error': str(exc)}
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    return 0 if result['status'] in ('COMPLETED', 'SKIPPED_DISABLED', 'SKIPPED_NON_BUSINESS_DAY') else 1


if __name__ == '__main__':
    raise SystemExit(main())
