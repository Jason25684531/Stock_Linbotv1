"""Official data recovery: budgeted official download, MySQL reconciliation, retraining specification and readiness.

Read-only against MySQL and models; writes only into one output directory and the git-ignored official caches.
At most BUDGET official requests (retries included, >= MIN_INTERVAL seconds apart); no model is trained.
Order: baseline -> download -> reconcile -> spec -> readiness -> tests -> manifest -> verify.
Download is resumable: cached files are never fetched again, so a rerun only asks for what is still missing.
"""
import argparse
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from core.research.sources.twse import RequestGate, ResponseKind, classify, fetch_daily_quotes
from scripts.audit_strategy_history import digest, read, write
from scripts.daily_selection_recovery import CHIP_CACHE, CHIP_URLS, fetch_raw, parse_chip
from scripts.validate_model_data import MI_INDEX_CACHE, official_sessions, models

OUT = ROOT / 'outputs/official_data_recovery/2026-10-23'
PREV = ROOT / 'outputs/model_data_validation/2026-10-23'
PREV_CHIPS = ROOT / 'outputs/daily_selection_recovery/2026-10-22/official_chip_manifest.json'
INDEX_CACHE = ROOT / 'artifacts/official_data_recovery_cache/mi_index'  # new files only; the archived D3 raw directory is not touched
INDEX_URL = 'https://www.twse.com.tw/rwd/zh/afterTrading/MI_INDEX?date={d}&type=ALL&response=json'
BUDGET = 197  # owner limit for the whole change, retries included
MIN_INTERVAL = 3.2  # seconds between requests (owner minimum 3)
MAX_CONSECUTIVE_FAILURES = 5
STAGE3 = ('2026-07-29', '2026-10-23')  # first session after the cached MI_INDEX calendar .. latest date of this round
SESSION_FIELD_ERRORS = ('FAILED', 'REJECTED', 'HASH_MISMATCH')


class BudgetExceeded(RuntimeError):
    pass


# ---- request ledger: SENT is written before the request, so a crash can never under-count ----
def ledger_rows(path):
    return [json.loads(line) for line in Path(path).read_text(encoding='utf-8').splitlines()] if Path(path).exists() else []


def sent_count(path):
    return sum(1 for r in ledger_rows(path) if r['event'] == 'SENT')


def log(path, **entry):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with Path(path).open('a', encoding='utf-8') as handle:
        handle.write(json.dumps({'ts': datetime.now(timezone.utc).isoformat(), **entry}, ensure_ascii=False) + '\n')


def reserve(path, dataset, day, url, budget=BUDGET):
    if sent_count(path) >= budget:
        raise BudgetExceeded(f'{budget} requests already sent; {dataset} {day} not requested')
    log(path, event='SENT', dataset=dataset, date=day, url=url)


def quarantine(path):
    """Move a response that failed validation out of the cache (never parsed again, never deleted)."""
    target = path.parent / '_rejected'
    target.mkdir(exist_ok=True)
    path.replace(target / path.name)
    meta = path.with_suffix('.meta.json')
    if meta.exists():
        meta.replace(target / meta.name)


# ---- acquisition: one session, at most one request ----
def acquire_chip(dataset, day, gate, ledger, trusted, trading=True):
    """T86 / MI_MARGN for one session -> (status, frame). Cached files are verified, not fetched again."""
    path = CHIP_CACHE / f'{dataset}_{day.replace("-", "")}.json'
    fresh = not path.exists()
    if fresh:
        url = CHIP_URLS[dataset].format(d=day.replace('-', ''))
        reserve(ledger, dataset, day, url)
        try:
            fetch_raw(dataset, day, gate)
        except Exception as exc:  # network / HTTP errors: the day stays missing, nothing is invented
            log(ledger, event='RESULT', dataset=dataset, date=day, outcome=f'FAILED:{type(exc).__name__}')
            return f'FAILED:{type(exc).__name__}', None
        log(ledger, event='RESULT', dataset=dataset, date=day, outcome='RECEIVED')
    elif path.name in trusted and digest(path) != trusted[path.name]['sha256']:
        return 'HASH_MISMATCH', None
    try:
        frame, stat = parse_chip(dataset, path, day)
    except Exception as exc:
        if fresh:
            quarantine(path)
        return f'REJECTED:{type(exc).__name__}', None
    if frame is None:  # the source says there is no data for this date
        if trading and fresh:  # a trading day without data may simply be unpublished: do not cache it as a holiday
            quarantine(path)
        return ('MISSING_ON_TRADING_DAY' if trading else 'NON_TRADING'), None
    return 'OK', frame


def acquire_index(day, gate, ledger, cache=INDEX_CACHE):
    """MI_INDEX for one date -> response kind (TRADING_DAY / NON_TRADING_DAY / SOURCE_ERROR ...)."""
    path = Path(cache) / f'MI_INDEX_{day.replace("-", "")}.json'
    fresh = not path.exists()
    if fresh:
        reserve(ledger, 'MI_INDEX', day, INDEX_URL.format(d=day.replace('-', '')))
    response = fetch_daily_quotes(pd.Timestamp(day).date(), cache, max_attempts=1, gate=gate)
    kind = classify(response).kind
    if kind == ResponseKind.TRADING_DAY and str(response.payload.get('date')) != day.replace('-', ''):
        kind = ResponseKind.SOURCE_ERROR  # payload date differs from the requested date
    if fresh:
        log(ledger, event='RESULT', dataset='MI_INDEX', date=day, outcome=kind.value)
    return kind.value


def stage1_sessions():
    """The 44 sample sessions approved with the previous plan (parsed from that plan, not recomputed)."""
    line = next(l for l in (PREV / 'optimized_download_plan.md').read_text(encoding='utf-8').splitlines() if l.startswith('Stage 1 sessions'))
    days = [d.strip() for d in line.split(':', 1)[1].split(',')]
    assert len(days) == 44, len(days)
    return days


def stage3_weekdays():
    return [d.strftime('%Y-%m-%d') for d in pd.bdate_range(*STAGE3)]


class Aborted(RuntimeError):
    pass


def download():
    OUT.mkdir(parents=True, exist_ok=True)
    ledger = OUT / 'request_ledger.jsonl'
    gate = RequestGate(minimum_interval=MIN_INTERVAL)
    trusted = read(PREV_CHIPS)['files']
    failures, status = 0, {'T86_sample': {}, 'MI_INDEX': {}, 'T86': {}, 'MI_MARGN': {}}

    def tally(ok):
        nonlocal failures
        failures = 0 if ok else failures + 1
        if failures >= MAX_CONSECUTIVE_FAILURES:
            raise Aborted(f'{failures} consecutive failures; rerun resumes from the cache')

    try:
        for day in stage1_sessions():
            status['T86_sample'][day] = (state := acquire_chip('T86', day, gate, ledger, trusted)[0])
            tally(state == 'OK')
        for day in stage3_weekdays():
            status['MI_INDEX'][day] = (kind := acquire_index(day, gate, ledger))
            tally(kind in (ResponseKind.TRADING_DAY.value, ResponseKind.NON_TRADING_DAY.value))
        sessions = [d for d, k in status['MI_INDEX'].items() if k == ResponseKind.TRADING_DAY.value]
        for day in sessions:
            for dataset in ('T86', 'MI_MARGN'):
                status[dataset][day] = (state := acquire_chip(dataset, day, gate, ledger, trusted)[0])
                tally(state == 'OK')
        stopped = None
    except (Aborted, BudgetExceeded) as exc:
        stopped = str(exc)
    write(OUT / 'official_download_manifest.json', download_manifest(ledger, trusted, stopped))
    print(json.dumps(read(OUT / 'official_download_manifest.json')['summary'], ensure_ascii=False))


def chip_status(dataset, day, trusted, trading=True):
    """Re-derive the state of a cached session (no request): OK / NON_TRADING / MISSING / HASH_MISMATCH / REJECTED."""
    path = CHIP_CACHE / f'{dataset}_{day.replace("-", "")}.json'
    if not path.exists():
        return 'MISSING', None
    if path.name in trusted and digest(path) != trusted[path.name]['sha256']:
        return 'HASH_MISMATCH', None
    try:
        frame, _ = parse_chip(dataset, path, day)
    except Exception as exc:
        return f'REJECTED:{type(exc).__name__}', None
    return ('OK', frame) if frame is not None else ('MISSING_ON_TRADING_DAY' if trading else 'NON_TRADING', None)


def index_kind(day):
    path = INDEX_CACHE / f'MI_INDEX_{day.replace("-", "")}.json'
    if not path.exists():
        return 'MISSING'
    payload = json.loads(path.read_text(encoding='utf-8'))
    from core.research.sources import RawResponse
    return classify(RawResponse('twse', 'MI_INDEX', {}, datetime.now(), None, payload, None)).kind.value


def download_manifest(ledger, trusted, stopped):
    rows = ledger_rows(ledger)
    result_time = {(r['dataset'], r['date']): r['ts'] for r in rows if r['event'] == 'RESULT'}
    sent = {(r['dataset'], r['date']): r for r in rows if r['event'] == 'SENT'}
    files, summary = [], {}

    def add(dataset, day, path, state, scope):
        meta = path.with_suffix('.meta.json')
        recorded = json.loads(meta.read_text(encoding='utf-8')) if meta.exists() else {}
        files.append({'dataset': dataset, 'date': day, 'scope': scope, 'file': path.relative_to(ROOT).as_posix() if path.exists() else None,
                      'sha256': digest(path) if path.exists() else None, 'bytes': path.stat().st_size if path.exists() else None,
                      'status': state, 'url': recorded.get('url') or (sent.get((dataset, day)) or {}).get('url'),
                      'retrieved_at': recorded.get('retrieved_at') or result_time.get((dataset, day)),
                      'requested_this_round': (dataset, day) in sent})

    sample = stage1_sessions()
    for day in sample:
        add('T86', day, CHIP_CACHE / f'T86_{day.replace("-", "")}.json', chip_status('T86', day, trusted)[0], 'STAGE1_SAMPLE')
    weekdays = stage3_weekdays()
    kinds = {d: index_kind(d) for d in weekdays}
    for day in weekdays:
        add('MI_INDEX', day, INDEX_CACHE / f'MI_INDEX_{day.replace("-", "")}.json', kinds[day], 'STAGE3')
    sessions = [d for d in weekdays if kinds[d] == ResponseKind.TRADING_DAY.value]
    for day in sessions:
        for dataset in ('T86', 'MI_MARGN'):
            add(dataset, day, CHIP_CACHE / f'{dataset}_{day.replace("-", "")}.json', chip_status(dataset, day, trusted)[0], 'STAGE3')
    states = pd.DataFrame(files)
    ok = lambda scope, dataset: int(((states.scope == scope) & (states.dataset == dataset) & states.status.isin(('OK', 'TRADING_DAY'))).sum())
    cached_before = states[(states.requested_this_round == False) & states.sha256.notna() & states.file.notna()]  # noqa: E712
    verified = [f for f in files if f['file'] and Path(f['file']).name in trusted]
    summary = {'requests_sent': sent_count(ledger), 'budget': BUDGET, 'budget_remaining': BUDGET - sent_count(ledger),
               'stage1_t86_ok': ok('STAGE1_SAMPLE', 'T86'), 'stage1_t86_expected': len(sample),
               'stage3_weekdays': len(weekdays), 'stage3_trading_sessions': len(sessions),
               'stage3_non_trading': [d for d in weekdays if kinds[d] == ResponseKind.NON_TRADING_DAY.value],
               'stage3_index_unresolved': [d for d in weekdays if kinds[d] not in (ResponseKind.TRADING_DAY.value, ResponseKind.NON_TRADING_DAY.value)],
               'stage3_t86_ok': ok('STAGE3', 'T86'), 'stage3_mi_margn_ok': ok('STAGE3', 'MI_MARGN'),
               'stage3_mi_index_trading_ok': ok('STAGE3', 'MI_INDEX'),
               'missing_or_failed': [{'dataset': f['dataset'], 'date': f['date'], 'status': f['status']} for f in files
                                     if f['status'] not in ('OK', 'TRADING_DAY', 'NON_TRADING_DAY', 'NON_TRADING')],
               'prior_cache_hash_check': {'files_in_scope_with_recorded_hash': len(verified),
                                          'mismatches': [f['file'] for f in files if f['status'] == 'HASH_MISMATCH']},
               'latest_date_unconfirmed': STAGE3[1] if kinds[STAGE3[1]] == ResponseKind.NON_TRADING_DAY.value else None,
               'files_already_cached_before_round': int(len(cached_before)), 'stopped': stopped}
    return {'budget': BUDGET, 'min_interval_seconds': MIN_INTERVAL, 'summary': summary, 'files': files,
            'ledger': OUT.joinpath('request_ledger.jsonl').relative_to(ROOT).as_posix()}


# ---- baseline ----
def archived(folders):
    result = {}
    for folder in folders:
        recorded = read(folder / 'reproduction_manifest.json')['outputs']
        result[folder.relative_to(ROOT).as_posix()] = {'files': len(recorded), 'changed': [n for n, h in recorded.items() if digest(folder / n) != h]}
    return result


ARCHIVES = [ROOT / 'outputs/daily_selection_recovery/2026-10-22', ROOT / 'outputs/native7_recovery/2026-10-22',
            ROOT / 'outputs/native22_validation/2026-10-22', PREV]


def git(*args):
    return subprocess.run(['git', *args], cwd=ROOT, capture_output=True, text=True, encoding='utf-8', check=True).stdout


def baseline():
    from config import Config
    from core.runtime.fundamental_shadow import FrozenStrategyLoader
    from scripts.validate_native22 import preservation

    before = {k: v['sha256'] for k, v in read(PREV / 'baseline.json')['models'].items()}
    now = {k: digest(ROOT / p) for k, (_n, p) in models().items()}
    decisions = read(PREV / 'retrain_decision.json')['models']
    review = read(ROOT / 'outputs/strategy_revalidation/2026-10-21-audit/frozen_gate_evidence.json')['documents']['promotion_review']
    kept = preservation()
    OUT.mkdir(parents=True, exist_ok=True)
    write(OUT / 'baseline.json', {
        'git_head': git('rev-parse', 'HEAD').strip(), 'git_status': git('status', '--short').splitlines(),
        'models_unchanged': before == now, 'model_sha256': now,
        'model_status': {k: v['MODEL_STATUS'] for k, v in decisions.items()}, 'retrain_decision': {k: v['decision'] for k, v in decisions.items()},
        'archived_outputs': archived(ARCHIVES), 'preservation': kept,
        'frozen_fingerprint_unchanged': FrozenStrategyLoader().load().fingerprint == review['strategy_fingerprint'],
        'BROKER_ORDER_SUBMISSION': review['BROKER_ORDER_SUBMISSION'], 'DAILY_FEATURE_MODE': Config.DAILY_FEATURE_MODE})
    b = read(OUT / 'baseline.json')
    assert b['models_unchanged'] and set(b['model_status'].values()) == {'MODEL_BLOCKED'}, 'MODELS'
    assert all(not v['changed'] for v in b['archived_outputs'].values()), 'ARCHIVED'
    assert not kept['historical_changed'] and set(kept['protected_changed']) <= {'core/strategies/growth_momentum_breakout.py'}, 'PRESERVATION'
    assert b['DAILY_FEATURE_MODE'] == 'legacy' and b['BROKER_ORDER_SUBMISSION'] == 'DISABLED'
    print('BASELINE OK', {k: v['files'] for k, v in b['archived_outputs'].items()})


# ---- reconciliation: official (left) vs MySQL (right), read-only ----
KEYS = ['stock_id', 'trade_date']
CHIP_FIELDS = ('foreign_buy', 'trust_buy', 'dealer_buy', 'margin_balance')
FIELD_COLUMNS = {**{c: [c] for c in CHIP_FIELDS}, 'ohlc': ['open_price', 'high_price', 'low_price', 'close_price'], 'volume': ['volume']}
WHOLE_DAY_MIN_KEYS = 100  # a day is "wholly wrong" only when at least this many stocks are compared and under half agree
_STAMP = datetime(2026, 7, 28)


def compare(official, mysql, fields, listed, scope):
    """Per date and field: agreement of MySQL with the official frame on shared keys, plus gaps in either direction.

    official / mysql: frames with stock_id, trade_date and the field columns (NaN = missing).
    listed: MultiIndex of (stock_id, trade_date) of the official listed universe (MI_INDEX), to tell
    a MySQL stock the official chip file lacks from a non-TWSE stock.
    """
    merged = official.merge(mysql, on=KEYS, how='outer', suffixes=('_off', '_db'), indicator=True)
    in_listed = pd.MultiIndex.from_frame(merged[KEYS]).isin(listed)
    rows = []
    for name in fields:
        cols = FIELD_COLUMNS[name]
        off = merged[[f'{c}_off' for c in cols]]
        db = merged[[f'{c}_db' for c in cols]]
        off_has, db_has = off.notna().all(axis=1), db.notna().all(axis=1)
        shared = off_has & db_has
        same = pd.Series((off.to_numpy() == db.to_numpy()).all(axis=1), index=merged.index)
        right_only = merged._merge == 'right_only'
        frame = pd.DataFrame({
            'date': merged.trade_date, 'official_present': off_has, 'shared': shared, 'equal': shared & same,
            'mysql_null_official_present': off_has & ~db_has & (merged._merge == 'both'),
            'mysql_zero_official_nonzero': shared & db.eq(0).all(axis=1) & off.ne(0).any(axis=1),
            'official_only_stock_days': merged._merge == 'left_only',
            'mysql_only_not_listed': right_only & ~in_listed,
            'mysql_listed_absent_from_official_field': in_listed & db_has & ~off_has & (right_only | (merged._merge == 'both'))})
        for day, r in frame.groupby('date').sum().iterrows():
            rate = None if r.shared == 0 else round(float(r.equal / r.shared), 6)
            rows.append({'scope': scope, 'date': day.strftime('%Y-%m-%d'), 'field': name, 'official_present': int(r.official_present),
                         'shared_keys': int(r.shared), 'equal': int(r.equal), 'mismatch': int(r.shared - r.equal), 'match_rate': rate,
                         'mysql_null_official_present': int(r.mysql_null_official_present),
                         'mysql_null_ratio': None if r.official_present == 0 else round(float(r.mysql_null_official_present / r.official_present), 4),
                         'mysql_zero_official_nonzero': int(r.mysql_zero_official_nonzero), 'official_only_stock_days': int(r.official_only_stock_days),
                         'mysql_only_not_listed': int(r.mysql_only_not_listed),
                         'mysql_listed_absent_from_official_field': int(r.mysql_listed_absent_from_official_field),
                         'whole_day_error': bool(r.shared >= WHOLE_DAY_MIN_KEYS and rate is not None and rate < 0.5)})
    return rows


def official_bars(days, cache_dirs):
    """OHLC + volume of the TWSE listed stocks from cached MI_INDEX files; returns (bars, sessions, non_trading_weekdays)."""
    from core.research.adjustment.normalization import normalize_twse_closing_table
    from core.research.sources import RawResponse
    from core.research.sources.twse import find_closing_table

    frames, sessions, holidays = [], [], []
    for day in days:
        name = f'MI_INDEX_{day.replace("-", "")}.json'
        path = next((Path(d) / name for d in cache_dirs if (Path(d) / name).exists()), None)
        if path is None:
            continue
        payload = json.loads(path.read_text(encoding='utf-8'))
        kind = classify(RawResponse('twse', 'MI_INDEX', {}, _STAMP, None, payload, None)).kind
        if kind == ResponseKind.NON_TRADING_DAY:
            holidays.append(day)
        elif kind == ResponseKind.TRADING_DAY:
            sessions.append(day)
            frames.append(normalize_twse_closing_table(find_closing_table(payload), day, _STAMP))
    bars = pd.concat(frames, ignore_index=True).rename(columns={'raw_open': 'open_price', 'raw_high': 'high_price', 'raw_low': 'low_price',
                                                               'raw_close': 'close_price'})
    return bars[['stock_id', 'trade_date', 'open_price', 'high_price', 'low_price', 'close_price', 'volume']], sessions, holidays


def mysql_rows(days=None, start=None, end=None, columns='*'):
    """SELECT only: four-digit codes, deduplicated like Daily (keep last); returns (frame, duplicate_keys, rows_before)."""
    from sqlalchemy import text

    from core.db_helper import get_db_engine

    where = "stock_id REGEXP '^[1-9][0-9]{3}$' AND " + (
        f"trade_date IN ({', '.join(repr(pd.Timestamp(d).strftime('%Y-%m-%d')) for d in days)})" if days is not None
        else f"trade_date BETWEEN '{start}' AND '{end}'")
    frame = pd.read_sql(text(f'SELECT {columns} FROM daily_market_data WHERE {where}'), get_db_engine())
    frame = frame.assign(stock_id=frame.stock_id.astype(str).str.strip(), trade_date=pd.to_datetime(frame.trade_date))
    duplicates = int(frame.duplicated(KEYS).sum())
    return frame.drop_duplicates(KEYS, keep='last').reset_index(drop=True), duplicates, len(frame)


def official_chips(days, datasets, trusted):
    """Official T86 / MI_MARGN frames for the sessions whose cached file is OK, one merged frame keyed by stock and date."""
    merged = None
    for dataset in datasets:
        frames = [f for d in days for state, f in [chip_status(dataset, d, trusted)] if state == 'OK']
        frame = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame(columns=KEYS)
        frame = frame.assign(trade_date=pd.to_datetime(frame.trade_date))
        frame = frame[frame.stock_id.str.fullmatch(r'[1-9]\d{3}')]
        frame = frame[[c for c in frame.columns if c in KEYS + list(CHIP_FIELDS)]].drop_duplicates(KEYS)
        merged = frame if merged is None else merged.merge(frame, on=KEYS, how='outer')
    return merged


def listed_index(bars):
    return pd.MultiIndex.from_frame(bars[KEYS])


def reconcile():
    manifest = read(OUT / 'official_download_manifest.json')
    trusted = read(PREV_CHIPS)['files']
    summary = manifest['summary']
    sample = stage1_sessions()
    recent_days = [d for d in stage3_weekdays() if index_kind(d) == ResponseKind.TRADING_DAY.value]
    cache_dirs = (MI_INDEX_CACHE, INDEX_CACHE)
    rows, calendar = [], {}

    # 1) full cached period, prices and calendar (no new request needed)
    cached_sessions, _ = official_sessions()
    cached_days = [d.strftime('%Y-%m-%d') for d in pd.date_range(cached_sessions[0], cached_sessions[-1])]
    bars, sessions, holidays = official_bars(cached_days, cache_dirs)
    db, dup, _ = mysql_rows(start=cached_sessions[0], end=cached_sessions[-1],
                            columns='stock_id, trade_date, open_price, high_price, low_price, close_price, volume')
    mysql_dates = set(db.trade_date.dt.strftime('%Y-%m-%d'))
    calendar['full_cached_period'] = {
        'official_sessions': len(sessions), 'official_weekday_holidays': len(holidays), 'mysql_dates': len(mysql_dates), 'mysql_duplicate_keys': dup,
        'mysql_rows_on_weekend_dates': int((db.trade_date.dt.weekday >= 5).sum()),
        'mysql_weekend_dates': sum(pd.Timestamp(d).weekday() >= 5 for d in mysql_dates),
        'mysql_dates_not_official_sessions_count': len(mysql_dates - set(sessions)),
        'official_sessions_missing_in_mysql': sorted(set(sessions) - mysql_dates)}
    rows += compare(bars, db, ('ohlc', 'volume'), listed_index(bars), 'FULL_CACHED_PRICES')

    # 2) the 44 sample sessions: T86 flows only, labelled SAMPLE_AUDIT
    db, dup_s, _ = mysql_rows(days=sample, columns='stock_id, trade_date, foreign_buy, trust_buy, dealer_buy')
    sample_bars, _, _ = official_bars(sample, cache_dirs)
    rows += compare(official_chips(sample, ('T86',), trusted), db, ('foreign_buy', 'trust_buy', 'dealer_buy'), listed_index(sample_bars), 'SAMPLE_AUDIT')
    calendar['sample'] = {'sessions': len(sample), 'mysql_duplicate_keys': dup_s}

    # 3) after 2026-07-28: every session of the round, prices and all four chip fields
    bars_r, sessions_r, holidays_r = official_bars(stage3_weekdays(), cache_dirs)
    db, dup_r, _ = mysql_rows(start=STAGE3[0], end=STAGE3[1], columns='stock_id, trade_date, open_price, high_price, low_price, close_price, volume, '
                                                                        'foreign_buy, trust_buy, dealer_buy, margin_balance')
    rows += compare(bars_r, db, ('ohlc', 'volume'), listed_index(bars_r), 'RECENT')
    rows += compare(official_chips(recent_days, ('T86', 'MI_MARGN'), trusted), db, CHIP_FIELDS, listed_index(bars_r), 'RECENT')
    recent_mysql = set(db.trade_date.dt.strftime('%Y-%m-%d'))
    calendar['recent'] = {'official_sessions': len(sessions_r), 'official_weekday_holidays': holidays_r, 'mysql_dates': sorted(recent_mysql),
                          'official_sessions_missing_in_mysql': sorted(set(sessions_r) - recent_mysql), 'mysql_duplicate_keys': dup_r,
                          'mysql_dates_not_official_sessions': sorted(recent_mysql - set(sessions_r))}
    frame = pd.DataFrame(rows)
    frame.to_csv(OUT / 'official_mysql_reconciliation.csv', index=False, encoding='utf-8-sig')
    write(OUT / 'reconciliation_calendar.json', calendar)
    gates(frame, calendar, summary)
    for scope, group in frame.groupby('scope'):
        print(scope, {f: [int(g.shared_keys.sum()), int(g.mismatch.sum())] for f, g in group.groupby('field')})


def scope_stats(frame, scope, field):
    g = frame[(frame.scope == scope) & (frame.field == field)]
    shared, mismatch = int(g.shared_keys.sum()), int(g.mismatch.sum())
    return {'dates': int(len(g)), 'shared_keys': shared, 'mismatch': mismatch, 'match_rate': None if shared == 0 else round(1 - mismatch / shared, 6),
            'dates_with_mismatch': int((g.mismatch > 0).sum()), 'whole_day_error_dates': g[g.whole_day_error].date.tolist(),
            'mysql_null_official_present': int(g.mysql_null_official_present.sum()), 'official_present': int(g.official_present.sum()),
            'mysql_zero_official_nonzero': int(g.mysql_zero_official_nonzero.sum()), 'official_only_stock_days': int(g.official_only_stock_days.sum()),
            'mysql_only_not_listed': int(g.mysql_only_not_listed.sum()),
            'mysql_listed_absent_from_official_field': int(g.mysql_listed_absent_from_official_field.sum())}


def grade(stats, complete=True):
    if stats['shared_keys'] == 0:
        return 'DATA_BLOCKED'
    if stats['mismatch'] or stats['mysql_null_official_present'] or not complete:
        return 'DATA_FAIL'
    return 'DATA_PASS'


def gates(frame, calendar, summary):
    previous = {g['gate']: g for g in read(ROOT / 'outputs/daily_selection_recovery/2026-10-22/data_quality_report.json')['gates']}
    sessions = summary['stage3_trading_sessions']
    results = [
        {'gate': 'OFFICIAL_CALENDAR_PRICES_RECENT', 'range': list(STAGE3), 'status': 'DATA_PASS' if not summary['stage3_index_unresolved'] else 'DATA_BLOCKED',
         'evidence': f"{sessions} sessions, {len(summary['stage3_non_trading'])} weekday non-trading responses, unresolved {summary['stage3_index_unresolved']}",
         'unconfirmed_latest_date': summary['latest_date_unconfirmed'],
         'note': (f"{summary['latest_date_unconfirmed']} answered 'no data' at 18:29 Taipei; a holiday and an unpublished day give the same answer, "
                  'so it is not counted as a session and must be re-requested on the next trading day') if summary['latest_date_unconfirmed'] else ''},
        {'gate': 'OFFICIAL_T86_RECENT', 'status': 'DATA_PASS' if summary['stage3_t86_ok'] == sessions else 'DATA_BLOCKED', 'evidence': f"{summary['stage3_t86_ok']}/{sessions} sessions"},
        {'gate': 'OFFICIAL_MI_MARGN_RECENT', 'status': 'DATA_PASS' if summary['stage3_mi_margn_ok'] == sessions else 'DATA_BLOCKED',
         'evidence': f"{summary['stage3_mi_margn_ok']}/{sessions} sessions"}]
    missing = calendar['recent']['official_sessions_missing_in_mysql']
    for field in ('ohlc', 'volume', *CHIP_FIELDS):
        stats = scope_stats(frame, 'RECENT', field)
        results.append({'gate': f'MYSQL_{field.upper()}_RECENT', 'status': grade(stats, not missing), 'stats': stats,
                        'official_sessions_without_mysql_rows': missing})
    for field in ('ohlc', 'volume'):
        stats = scope_stats(frame, 'FULL_CACHED_PRICES', field)
        results.append({'gate': f'MYSQL_{field.upper()}_FULL_CACHED_PERIOD', 'range': ['2021-12-30', '2026-07-28'], 'status': grade(stats), 'stats': stats,
                        'calendar': calendar['full_cached_period']})
    for field in ('foreign_buy', 'trust_buy', 'dealer_buy'):
        results.append({'gate': f'MYSQL_{field.upper()}_FIT_SPLIT_SAMPLE', 'status': 'SAMPLE_AUDIT', 'sample_sessions': 44,
                        'stats': scope_stats(frame, 'SAMPLE_AUDIT', field),
                        'certified_scope': 'none: 44 sampled sessions only; the other sessions of 2022-01-03..2025-12-12 stay uncertified'})
    results += [
        {'gate': 'FIT_SPLIT_FLOWS_FULL', 'status': 'DATA_BLOCKED',
         'reason': 'T86 for 914 and MI_MARGN for 958 sessions of 2022-01-03..2025-12-12 are not downloaded (Stage 2 not approved)'},
        {'gate': 'MARGIN_FIT_SPLIT_SAMPLE', 'status': 'DATA_BLOCKED', 'reason': 'Stage 1 approved T86 only; MySQL margin_balance is NULL before 2026-10-12'},
        *[{**previous[g], 'inherited_from': 'daily_selection_recovery 2026-10-22'} for g in ('FUNDAMENTALS', 'MONTHLY_REVENUE', 'UNIVERSE_TPEX')]]
    write(OUT / 'data_gate_results.json', {'grades': 'DATA_PASS / DATA_FAIL / DATA_BLOCKED / SAMPLE_AUDIT (a sample is never extrapolated)', 'gates': results})


# ---- retraining specification (fixed before any training) ----
SPEC_VERSION = 'retraining-spec-v1'
FEATURE_SPEC = {  # raw name -> (formula, source, null rule, in_model, reason when not in the model)
    'rsi': ('calculate_rsi(close)', 'MI_INDEX close', 'NaN until the indicator window is filled', True, ''),
    'bias': ('(close - ma20) / ma20 * 100', 'MI_INDEX close', 'NaN when ma20 is NaN or 0', True, ''),
    'macd_hist': ('calculate_macd(close)', 'MI_INDEX close', 'NaN until the window is filled', True, ''),
    'kd_k': ('calculate_kd(high, low, close)', 'MI_INDEX high/low/close', 'NaN until the window is filled', True, ''),
    'bb_width': ('calculate_bb_width(close)', 'MI_INDEX close', 'NaN until the window is filled', True, ''),
    'volume_ratio': ('volume.replace(0, 1) / mean of the last 20 volumes, clip 0..5', 'MI_INDEX volume', 'NaN below 20 volume observations (OD-1)', True, ''),
    'natr': ('calculate_natr(high, low, close)', 'MI_INDEX high/low/close', 'NaN until the window is filled', True, ''),
    'std_20': ('calculate_std_20(close)', 'MI_INDEX close', 'NaN until the window is filled', True, ''),
    'foreign_ratio': ('foreign_buy / volume.replace(0, 1), clip +-0.5', 'T86 foreign (ex foreign dealers) / MI_INDEX volume', 'NaN when the stock is absent from T86', True, ''),
    'trust_ratio': ('trust_buy / volume.replace(0, 1), clip +-0.5', 'T86 trust / MI_INDEX volume', 'NaN when absent from T86', True, ''),
    'dealer_ratio': ('dealer_buy / volume.replace(0, 1), clip +-0.5', 'T86 dealer / MI_INDEX volume', 'NaN when absent from T86', True, ''),
    'foreign_consec_days': ('consecutive sessions with foreign_buy > 0 over the stock history', 'T86 foreign', 'NaN when foreign_buy is NaN', True, ''),
    'trust_consec_days': ('consecutive sessions with trust_buy > 0 over the stock history', 'T86 trust', 'NaN when trust_buy is NaN', True, ''),
    'dealer_consec_days': ('consecutive sessions with dealer_buy > 0 over the stock history', 'T86 dealer', 'NaN when dealer_buy is NaN', True,
                           'CODE FIX REQUIRED FIRST: the shared core derives it from the one-day cross-section (always 0 or 1)'),
    'institutional_net_buy': ('foreign_buy + trust_buy + dealer_buy', 'T86', 'NaN if any component is NaN', True, ''),
    'institutional_consec_buy_days': ('foreign + trust + dealer consecutive days, 0 when net buy <= 0', 'T86', 'NaN if any component is NaN', True,
                                      'CODE FIX REQUIRED FIRST: depends on dealer_consec_days'),
    'chip_score': ('0.4 f + 0.3 t + 0.15 d + 0.15 m scores (Config.CHIP_WEIGHT_*), 0..100', 'T86 + MI_MARGN', 'NaN if any component is NaN (no neutral 50)', True, ''),
    'large_holder_ratio': ('large-holder ratio', 'none (no official or MySQL source)', 'n/a', False, 'no certified source'),
    'large_holder_ratio_change': ('change of the large-holder ratio', 'none', 'n/a', False, 'no certified source'),
    'news_sentiment_score': ('one market-wide sentiment code per session', 'news agent (no history)', 'n/a', False,
                             'constant across stocks within a session: z-score is 0, no cross-sectional information, no historical record'),
    'revenue_yoy': ('monthly revenue year-over-year %', 'MySQL monthly_revenue', 'NaN when not yet published', False,
                    'no official publication time (statutory-deadline estimate only); revisit when a timestamp source exists (OD-12)'),
    'rd_ratio': ('rd_expense / revenue', 'MOPS PIT v3', 'NaN when the latest filing has no actual publication date', False,
                 'publication dates exist only to FY2025; FY2026 filings are deadline estimates'),
    'op_profit_margin': ('operating_profit / revenue', 'MOPS PIT v3', 'same', False, 'same as rd_ratio'),
    'eps': ('quarterly EPS', 'MOPS PIT v3', 'same', False, 'same as rd_ratio'),
}
ACCEPTANCE = {
    'population': 'complete-case rows of the segment (all TWSE-listed common stocks); every metric is per session, then averaged over the segment',
    'metrics': {
        'top_decile_lift_mean': {'min': 1.20, 'definition': 'precision of label = 1 among the top 10 % by score in a session / that session base rate'},
        'monthly_lift_positive_share': {'min': 0.60, 'definition': 'share of calendar months whose mean top-decile lift is above 1.0'},
        'pooled_roc_auc': {'min': 0.55, 'definition': 'ROC AUC of the score against the label over the whole segment'},
        'mean_rank_ic': {'min': 0.02, 'definition': 'mean per-session Spearman correlation of score and realised future max return'}},
    'must_pass_on': ['validation', 'test'], 'test_evaluations_allowed': 1,
    'guards': ['label horizon of every training row ends before the next segment starts (purge check)',
               'features at session t are identical when all data after t is removed (future-independence check)',
               'AUC with permuted labels lies within 0.48..0.52', 'no session belongs to two segments',
               'model feature list equals this specification', 'data manifest SHA-256 equals the registered one'],
    'status': 'PROPOSED thresholds; frozen only by the SHA-256 of the owner-approved version',
    'on_failure': 'the model is rejected; no tuning on validation or test; a new model needs a new specification version'}
HYPERPARAMETERS = {'n_estimators': 300, 'learning_rate': 0.03, 'max_depth': 5, 'min_child_weight': 3, 'subsample': 0.7, 'colsample_bytree': 0.7,
                   'scale_pos_weight': '(negatives / positives) * 0.5 on the training segment only', 'random_state': 42, 'eval_metric': 'logloss',
                   'tuning': 'none (same values as jobs/train_model.py); validation is used for acceptance only, not early stopping'}
SPLIT = {'train': 0.6, 'validation': 0.2, 'test': 0.2, 'embargo_sessions': 5, 'purge_sessions': 'the model look_ahead_days'}


def spec_hash(spec):
    body = {k: v for k, v in spec.items() if k != 'sha256'}
    return __import__('hashlib').sha256(json.dumps(body, sort_keys=True, ensure_ascii=False).encode('utf-8')).hexdigest()


def split_sessions(sessions, purge, embargo=SPLIT['embargo_sessions'], ratios=(SPLIT['train'], SPLIT['validation'])):
    """Train / validation / test sessions with purge before and embargo after each cut (so no label horizon crosses a cut)."""
    n = len(sessions)
    a, b = int(n * ratios[0]), int(n * (ratios[0] + ratios[1]))
    return {'train': sessions[:a - purge], 'validation': sessions[a + embargo:b - purge], 'test': sessions[b + embargo:]}


def all_sessions():
    cached, _ = official_sessions()
    return cached + [d for d in stage3_weekdays() if index_kind(d) == ResponseKind.TRADING_DAY.value]


def build_spec():
    import joblib

    sessions = all_sessions()
    first_usable = sessions[30]  # complete-case rule removes rows without a filled window; 30 sessions is the shortest indicator window
    usable = sessions[sessions.index(first_usable):]
    strategies = {}
    for key, (name, path) in models().items():
        meta = joblib.load(ROOT / path)
        n, target = int(meta['look_ahead_days']), float(meta['target_return'])
        parts = split_sessions(usable, purge=n)
        strategies[key] = {
            'model_name': name, 'label': {'look_ahead_sessions': n, 'target_return': target, 'source': 'existing model metadata (strategy parameters unchanged)'},
            'illustrative_split_if_data_end_is_latest_session': {
                'data_end': usable[-1], **{k: [v[0], v[-1], len(v)] for k, v in parts.items()},
                'last_labelled_test_session': usable[-1 - n], 'note': 'boundaries are frozen with the data manifest, not now'}}
    spec = {
        'version': SPEC_VERSION, 'status': 'DRAFT_PENDING_OWNER_APPROVAL', 'created': '2026-10-23',
        'universe': {'market': 'TWSE-listed four-digit common stocks (MI_INDEX closing table)', 'tpex': 'excluded this round (no certified TPEx source); Owner decision',
                     'prices': 'raw prices (price return); ex-rights days are flagged, not adjusted'},
        'population_rule': 'complete case: a row enters training, validation, test and inference only if every model feature is non-null',
        'zscore': {'population': 'all complete-case rows of the same session (not narrowed by strategy rules)', 'formula': '(x - mean) / std over that session (ddof=0), std = 0 -> 0',
                   'nan_handling': 'rows with NaN features are not in the population and are never filled'},
        'feature_window': '150 calendar days ending at the session, computed by build_feature_history exactly as in recovered Daily',
        'features': {f'{raw}_z': dict(zip(('formula', 'source', 'null_rule', 'in_model', 'excluded_reason'), v)) for raw, v in FEATURE_SPEC.items()},
        'feature_order': [f'{raw}_z' for raw, v in FEATURE_SPEC.items() if v[3]],
        'label': {'definition': '1 if max(high over the next N official sessions) / close - 1 > target_return else 0', 'N_and_target': 'per model, see strategies',
                  'rows_without_a_full_horizon': 'dropped (the old code labelled them 0)', 'horizon_must_end_by': 'training data end'},
        'strategies': strategies, 'split': SPLIT, 'hyperparameters': HYPERPARAMETERS, 'acceptance': ACCEPTANCE,
        'training_period': {'start': 'first session with complete features (about 2022-02)', 'data_end': 'last session certified in the frozen data manifest',
                            'recorded_in_model': ['training_data_start', 'training_data_end', 'split boundaries', 'spec sha256', 'data manifest sha256', 'code commit']},
        'versioning': {'model_version': '<strategy_name>-TS2-<spec sha256[:8]>', 'old_models': 'kept unchanged beside the new files until certification'},
        'inference_contract': {
            'features': 'build_feature_history + build_multi_factor_matrix, order and names equal to the model feature list',
            'universe': 'TWSE-listed four-digit codes only', 'zscore_population': 'all complete-case rows of the session',
            'missing': 'rows with a missing model input are excluded, never filled; no valid model -> no published ranking',
            'score': 'predict_proba[:, 1]', 'model_gate': 'training_data_end and spec sha256 present, window start after training_data_end'},
        'code_changes_required_before_training': [
            'core/calc_indicators.build_feature_history: dealer_consec_days (and so institutional_consec_buy_days) over the stock history, NaN when dealer_buy is NaN',
            'z-score over all complete-case rows of the session (recovered Daily and research currently z-score over rows left after strategy rules)',
            'recovered Daily: restrict the frame to TWSE-listed codes',
            'core/research/native_models: read training_data_end and spec sha256; use training_data_end instead of training_date as the window bound',
            'jobs/run_daily recovered: drop the CHIP_SCORE_TRAINING_DEFINITION_UNPROVEN flag for models carrying a spec sha256',
            'new training script (the old jobs/train_model.py reads SELECT * from MySQL and labels incomplete horizons 0)'],
        'oos': {'start': 'the session after training_data_end', 'minimum_sessions': 252, 'counting': 'only sessions never read by any backtest, diagnostic or tuning',
                'earliest_if_data_end_is_2026-10-23': read(PREV / 'oos_feasibility.json')['scenarios']['B_retrain_latest_cutoff']['date_252_reached'],
                'rules': ['no retraining or threshold change after seeing OOS results', 'metrics and thresholds are the acceptance metrics above',
                          'the test segment is not OOS', 'the 252-session minimum is not lowered']}}
    spec['sha256'] = spec_hash(spec)
    return spec


def spec():
    OUT.mkdir(parents=True, exist_ok=True)
    document = build_spec()
    write(OUT / 'retraining_spec.json', document)
    print(document['sha256'], len(document['feature_order']), 'model features of', len(document['features']))


def stage2_batches():
    """Quarterly batches for the later, unapproved Stage 2 download: sessions without a cached T86 / MI_MARGN file."""
    cached, _ = official_sessions()
    fit = [d for d in cached if '2022-01-03' <= d <= '2025-12-12']
    rows = []
    for quarter, days in pd.Series(fit).groupby(pd.PeriodIndex(pd.to_datetime(fit), freq='Q')):
        t86 = [d for d in days if not (CHIP_CACHE / f'T86_{d.replace("-", "")}.json').exists()]
        margin = [d for d in days if not (CHIP_CACHE / f'MI_MARGN_{d.replace("-", "")}.json').exists()]
        rows.append({'batch': str(quarter), 'from': days.iloc[0], 'to': days.iloc[-1], 'sessions': len(days), 'T86_requests': len(t86),
                     'MI_MARGN_requests': len(margin), 'requests': len(t86) + len(margin), 'minutes_at_3_2s': round((len(t86) + len(margin)) * MIN_INTERVAL / 60, 1)})
    return rows


CERTIFICATION = [
    'every session of the batch has a cached file whose payload date equals the requested date and whose stat is OK, or a MI_INDEX-confirmed holiday',
    'schema check passes (T86 19 fields with the known names; MI_MARGN table with the stock-code, margin-balance and short-balance columns)',
    'file count and SHA-256 recorded in the download manifest; no HASH_MISMATCH, no rejected file left unexplained',
    'a trading session without data is MISSING_ON_TRADING_DAY and blocks DATA_PASS for the whole range',
    'MySQL reconciliation is run for the batch and reported; official gates (coverage) and MySQL gates (agreement) are graded separately',
    'the range is DATA_PASS only when every session is covered; a partial range or a sample stays SAMPLE_AUDIT / DATA_BLOCKED']


def gaps():
    sessions = all_sessions()
    batches = stage2_batches()
    t86_missing = sum(b['T86_requests'] for b in batches)
    margin_missing = sum(b['MI_MARGN_requests'] for b in batches)
    manifest = read(OUT / 'official_download_manifest.json')['summary']
    reconciliation = pd.read_csv(OUT / 'official_mysql_reconciliation.csv')
    sample = reconciliation[reconciliation.scope == 'SAMPLE_AUDIT']
    rows = [
        {'item': 'T86 2022-01-03..2025-12-12', 'class': 'NEEDS_DOWNLOAD', 'have': 'sample sessions only', 'missing_requests': t86_missing, 'needed_for': 'training features, fit-split audit',
         'note': 'Stage 2, not approved'},
        {'item': 'MI_MARGN 2022-01-03..2025-12-12', 'class': 'NEEDS_DOWNLOAD', 'have': 'none', 'missing_requests': margin_missing, 'needed_for': 'chip_score, margin_change_pct',
         'note': 'Stage 2, not approved'},
        {'item': 'T86 / MI_MARGN 2025-12-15..2026-10-23', 'class': 'AVAILABLE_NOW' if not manifest['missing_or_failed'] else 'NEEDS_DOWNLOAD', 'have': 'cached + this round',
         'missing_requests': len([m for m in manifest['missing_or_failed'] if m['dataset'] in ('T86', 'MI_MARGN')]), 'needed_for': 'training and OOS clock', 'note': ''},
        {'item': 'MI_INDEX prices and official calendar 2021-12-30..2026-10-23', 'class': 'AVAILABLE_NOW' if not manifest['stage3_index_unresolved'] else 'NEEDS_DOWNLOAD',
         'have': 'cached + this round', 'missing_requests': len(manifest['stage3_index_unresolved']), 'needed_for': 'prices, labels, universe, calendar', 'note': ''},
        {'item': 'TWT49U ex-rights events 2022 and after 2026-07-28', 'class': 'NEEDS_DOWNLOAD', 'have': '2023-01-03..2026-07-28', 'missing_requests': 2,
         'needed_for': 'flag labels whose horizon contains an ex-rights day', 'note': 'one range request each'},
        {'item': 'Cash dividend amounts (separate from the combined rights + dividend value)', 'class': 'OWNER_DECISION', 'have': 'TWT49U combined value only',
         'missing_requests': 1600, 'needed_for': 'total return in backtests (not for training labels)', 'note': 'per-event MOPS detail pages, about 1,600 events since 2023; estimate'},
        {'item': 'Quarterly filings FY2026Q1..Q2 publication times', 'class': 'NEEDS_DOWNLOAD', 'have': 'FY2013..FY2025', 'missing_requests': 1780,
         'needed_for': 'fundamental features rd_ratio, op_profit_margin, eps (excluded from v1 model)', 'note': 'only if the Owner adds fundamentals; estimate from the PIT ledger rate'},
        {'item': 'Monthly revenue publication times', 'class': 'OWNER_DECISION', 'have': 'values only (MOPS summary pages carry no announcement time)', 'missing_requests': None,
         'needed_for': 'revenue_yoy (excluded from v1 model; used by V34/V35 rules)', 'note': 'no timestamped official source found in the endpoint probes'},
        {'item': 'TPEx universe (prices, flows, margin)', 'class': 'OWNER_DECISION', 'have': 'none', 'missing_requests': 3 * (len(sessions)),
         'needed_for': 'including TPEx stocks', 'note': 'recommended: exclude this round and filter live Daily to TWSE codes'},
        {'item': 'large_holder_ratio / large_holder_ratio_change', 'class': 'OWNER_DECISION', 'have': 'none', 'missing_requests': None, 'needed_for': 'two model features',
         'note': 'no source; excluded from v1'},
        {'item': 'news_sentiment_score history', 'class': 'OWNER_DECISION', 'have': 'none', 'missing_requests': None, 'needed_for': 'one model feature',
         'note': 'constant across stocks within a session; excluded from v1'},
        {'item': 'Core code fixes (dealer_consec_days history, z-score population, TWSE universe filter, model contract fields)', 'class': 'CODE_CHANGE_REQUIRED',
         'have': 'see retraining_spec.json', 'missing_requests': 0, 'needed_for': 'train / inference parity', 'note': 'not allowed in this round (core/ unchanged)'},
        {'item': 'Owner approval of the retraining specification (thresholds, feature set, universe, hyper-parameters)', 'class': 'OWNER_DECISION', 'have': 'draft with SHA-256',
         'missing_requests': 0, 'needed_for': 'freezing before training', 'note': 'DRAFT_PENDING_OWNER_APPROVAL'}]
    pd.DataFrame(rows).to_csv(OUT / 'remaining_data_gaps.csv', index=False, encoding='utf-8-sig')
    document = read(OUT / 'retraining_spec.json')
    decisions = read(PREV / 'retrain_decision.json')['models']
    sample_mismatch = int(sample.mismatch.sum())
    blockers = ['SPEC_NOT_APPROVED', f'STAGE2_DATA_NOT_DOWNLOADED ({t86_missing} T86 + {margin_missing} MI_MARGN requests)',
                'CORE_CODE_CHANGES_REQUIRED (' + str(len(document['code_changes_required_before_training'])) + ' items)', 'OWNER_DECISIONS_OPEN']
    write(OUT / 'retraining_readiness.json', {
        'spec': {'version': document['version'], 'status': document['status'], 'sha256': document['sha256']},
        'models': {k: {'model': v['model'], 'retrain_decision': decisions[k]['decision'], 'MODEL_STATUS': 'MODEL_BLOCKED', 'label': document['strategies'][k]['label'],
                       'feature_count': len(document['feature_order']), 'ready_for_training': False, 'blockers': blockers} for k, v in decisions.items()},
        'stage2': {'approved': False, 'requests': t86_missing + margin_missing, 'hours_at_3_2s': round((t86_missing + margin_missing) * MIN_INTERVAL / 3600, 2),
                   'batches': batches, 'certification_standard': CERTIFICATION,
                   'procedure': ['reuse acquire_chip / fetch_raw (cache first, SHA-256 verified)', 'one batch per quarter, oldest first', 'stop after 5 consecutive failures; rerun resumes',
                                 'reconcile and grade after every batch', 'freeze the data manifest only when all batches are covered']},
        'fit_split_sample_audit': {'sessions': 44, 'foreign_mismatch_stock_days': sample_mismatch, 'note': 'SAMPLE_AUDIT only, not a certification'},
        'recommendation': {'approve_stage2_download': True,
                           'reason': 'the 44-session sample cannot certify the period and MySQL flows are known to contain whole-day zeros; official history is needed either way',
                           'approve_retraining_now': False,
                           'retraining_preconditions': ['Owner approves the specification hash', 'Stage 2 downloaded and certified', 'core code changes implemented and tested', 'open Owner decisions closed']}})
    print(pd.DataFrame(rows).groupby('class').size().to_dict(), 'stage2 requests', t86_missing + margin_missing)


TESTS = ['test/test_official_data_recovery.py', 'test/test_model_data_validation.py', 'test/test_daily_selection_recovery.py', 'test/test_native_recovery.py',
         'test/characterization', 'test/test_run_daily.py', 'test/test_native22_engine_contracts.py', 'test/test_next_session_execution.py']


def tests():
    import xml.etree.ElementTree as ET
    from collections import Counter

    junit = OUT / 'test_junit.xml'
    proc = subprocess.run([sys.executable, '-m', 'pytest', *TESTS, '-q', '-p', 'no:cacheprovider', f'--junitxml={junit}'],
                          cwd=ROOT, capture_output=True, text=True, encoding='utf-8')
    cases = [{'test': f"{c.get('classname')}::{c.get('name')}",
              'status': 'FAIL' if c.find('failure') is not None or c.find('error') is not None else 'SKIP' if c.find('skipped') is not None else 'PASS'}
             for c in ET.parse(junit).getroot().iter('testcase')]
    write(OUT / 'test_results.json', {'command': 'python -m pytest ' + ' '.join(TESTS), 'returncode': proc.returncode, 'summary': proc.stdout.strip().splitlines()[-1],
                                      'counts': dict(Counter(c['status'] for c in cases)), 'cases': cases})
    print(proc.returncode, proc.stdout.strip().splitlines()[-1])


JSON_OUTPUTS = ('baseline.json', 'official_download_manifest.json', 'official_mysql_reconciliation.csv', 'reconciliation_calendar.json', 'data_gate_results.json',
                'retraining_spec.json', 'retraining_readiness.json', 'remaining_data_gaps.csv', 'request_ledger.jsonl', 'test_results.json')


def manifest():
    import platform

    summary = read(OUT / 'official_download_manifest.json')['summary']
    write(OUT / 'reproduction_manifest.json', {
        'change': 'openspec/changes/2026-10-23-recover-official-data-and-retraining-spec', 'output_dir': OUT.relative_to(ROOT).as_posix(),
        'git_head': git('rev-parse', 'HEAD').strip(), 'baseline_head': read(OUT / 'baseline.json')['git_head'],
        'commands': [f'python scripts/official_data_recovery.py {s}' for s in ('baseline', 'download  # run twice: the second run resumes and requests only the missing day',
                                                                                 'reconcile', 'spec', 'gaps', 'tests', 'manifest', 'verify')],
        'official_requests': {'sent': summary['requests_sent'], 'budget': BUDGET, 'min_interval_seconds': MIN_INTERVAL, 'ledger': 'request_ledger.jsonl'},
        'inputs': {'previous_chip_manifest': digest(PREV_CHIPS), 'previous_validation_manifest': digest(PREV / 'reproduction_manifest.json'),
                   'mi_index_cached_files': 'official_download_manifest.json (new files) and mi_index_cache_hashes.json of the previous run (older files)',
                   'mysql': 'SELECT only at run time; the aggregates are in official_mysql_reconciliation.csv'},
        'code': {p: digest(ROOT / p) for p in ('scripts/official_data_recovery.py', 'test/test_official_data_recovery.py')},
        'spec_sha256': read(OUT / 'retraining_spec.json')['sha256'], 'model_sha256': read(OUT / 'baseline.json')['model_sha256'],
        'environment': {'python': platform.python_version(), 'pandas': pd.__version__},
        'outputs': {p.relative_to(OUT).as_posix(): digest(p) for p in sorted(OUT.rglob('*')) if p.is_file() and p.name != 'reproduction_manifest.json'
                    and 'download_stage' not in p.name},
        'BROKER_ORDER_SUBMISSION': 'DISABLED'})
    print(len(read(OUT / 'reproduction_manifest.json')['outputs']))


def verify():
    import re

    from config import Config
    from scripts.validate_native22 import preservation

    start = read(OUT / 'baseline.json')
    assert start['model_sha256'] == {k: digest(ROOT / p) for k, (_n, p) in models().items()}, 'MODEL_CHANGED'
    assert all(not v['changed'] for v in archived(ARCHIVES).values()), 'ARCHIVED_OUTPUT_CHANGED'
    kept = preservation()
    assert not kept['historical_changed'] and set(kept['protected_changed']) <= {'core/strategies/growth_momentum_breakout.py'}, kept
    touched = git('diff', '--name-only', start['git_head'], '--', 'jobs', 'core', 'config', 'execution', 'app', 'services', 'strategy_settings.json', 'ML_Data').split()
    assert not touched, 'PRODUCTION_CODE_CHANGED: ' + str(touched)
    assert Config.DAILY_FEATURE_MODE == 'legacy'
    rows = ledger_rows(OUT / 'request_ledger.jsonl')
    times = [datetime.fromisoformat(r['ts']) for r in rows if r['event'] == 'SENT']
    assert len(times) <= BUDGET, 'BUDGET'
    # SENT is logged before the gate waits, so single gaps cannot show the spacing; the gate (MIN_INTERVAL between request starts) makes the
    # duration of one run at least (requests - 1) * MIN_INTERVAL, and that is what the ledger can prove.
    stamps = [datetime.fromisoformat(r['ts']) for r in rows]
    runs, begin = [], 0
    for i in range(1, len(rows) + 1):
        if i == len(rows) or (stamps[i] - stamps[i - 1]).total_seconds() > 120:
            chunk = rows[begin:i]
            runs.append({'requests': sum(r['event'] == 'SENT' for r in chunk), 'seconds': round((stamps[i - 1] - stamps[begin]).total_seconds(), 1)})
            begin = i
    assert all(r['seconds'] >= (r['requests'] - 1) * MIN_INTERVAL for r in runs), f'RUN_TOO_FAST {runs}'
    stage2 = [p for p in CHIP_CACHE.glob('*.json') if not p.name.endswith('.meta.json') and re.search(r'_(\d{8})\.json$', p.name)
              and '20220103' <= re.search(r'_(\d{8})\.json$', p.name).group(1) <= '20251212']
    assert len(stage2) == 44, f'STAGE2_EXECUTED? {len(stage2)} files in 2022-01-03..2025-12-12 (expected the 44-session sample)'
    spec_doc = read(OUT / 'retraining_spec.json')
    assert spec_doc['sha256'] == spec_hash(spec_doc) and spec_doc['status'] == 'DRAFT_PENDING_OWNER_APPROVAL', 'SPEC'
    readiness = read(OUT / 'retraining_readiness.json')
    assert all(m['MODEL_STATUS'] == 'MODEL_BLOCKED' and not m['ready_for_training'] for m in readiness['models'].values()), 'READINESS'
    source = Path(__file__).read_text(encoding='utf-8')
    assert not re.search(r"text\(f?['\"](?!SELECT)", source) and not re.search(r"\.(to_sql|execute)\(", source), 'MYSQL_WRITE_PATH'
    assert read(OUT / 'official_download_manifest.json')['summary']['prior_cache_hash_check']['mismatches'] == [], 'CACHE_HASH'
    write(OUT / 'verification.json', {'status': 'PASS', 'official_requests': len(times), 'budget': BUDGET, 'runs': runs, 'spacing_evidence': 'run duration >= (requests - 1) x 3.2 s; per-request send instants were not recorded',
                                      'stage2_executed': False, 'models_unchanged_and_blocked': 7, 'training_executed': False, 'mysql': 'SELECT only',
                                      'production_code_unchanged_since': start['git_head'], 'archived_outputs_unchanged': True, 'preservation': kept,
                                      'spec_sha256': spec_doc['sha256'], 'DAILY_FEATURE_MODE': 'legacy', 'BROKER_ORDER_SUBMISSION': 'DISABLED'})
    print('VERIFY PASS', len(times))


STAGES = ('baseline', 'download', 'reconcile', 'spec', 'gaps', 'tests', 'manifest', 'verify')

if __name__ == '__main__':
    sys.stdout.reconfigure(encoding='utf-8')
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('stage', choices=STAGES)
    globals()[parser.parse_args().stage]()
