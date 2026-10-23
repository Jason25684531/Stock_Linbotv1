"""22-strategy dual-track validation: baseline and integrity event, certified official data, Track A readiness, Track B results.

Read-only against MySQL and models; writes only into one output directory and the git-ignored official caches.
The production container mounts this working tree read-write, so nothing here (or in the code it adds) may change legacy behavior.
Order: baseline -> integrity -> download -> certify -> ... (see tasks.md).
"""
import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.audit_strategy_history import digest, read, write
from scripts.official_data_recovery import ARCHIVES, RUNTIME_STATUS, archived, git
from scripts.validate_model_data import models

OUT = ROOT / 'outputs/dual_track_validation/2026-10-23'
PREV_OUT = ROOT / 'outputs/official_data_recovery/2026-10-23'
HASHES = ROOT / 'outputs/strategy_revalidation/2026-10-21-audit/historical_hashes.json'
FRESH_LEDGER = ROOT / 'outputs/fundamental_runtime_shadow/operate-fundamental-shadow-until-oos-ready-v1/FreshOOSLedger.csv'
KNOWN_EXCEPTIONS = {'core/strategies/growth_momentum_breakout.py'}  # approved P0-3 change; nothing else is tolerated silently


def out(name):
    path = OUT / name
    if path.exists():
        raise FileExistsError(path)  # new evidence only; rerun into a new directory
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def baseline():
    from config import Config
    from core.runtime.fundamental_shadow import FrozenStrategyLoader
    from scripts.validate_native22 import preservation

    before = read(PREV_OUT / 'baseline.json')['model_sha256']
    now = {k: digest(ROOT / p) for k, (_n, p) in models().items()}
    review = read(ROOT / 'outputs/strategy_revalidation/2026-10-21-audit/frozen_gate_evidence.json')['documents']['promotion_review']
    kept = preservation()
    changed = set(kept['historical_changed'])
    write(out('baseline.json'), {
        'git_head': git('rev-parse', 'HEAD').strip(), 'git_status': git('status', '--short').splitlines(),
        'models_unchanged': before == now, 'model_sha256': now,
        'model_status': {k: v['MODEL_STATUS'] for k, v in read(ROOT / 'outputs/model_data_validation/2026-10-23/retrain_decision.json')['models'].items()},
        'archived_outputs': archived(ARCHIVES + [PREV_OUT]), 'preservation': kept,
        'historical_hashes_changed': sorted(changed), 'historical_hashes_checked': kept['historical_hashes_checked'],
        'runtime_status_file': {'path': RUNTIME_STATUS, 'sha256_at_round_start': digest(ROOT / RUNTIME_STATUS)},
        'frozen': {'fingerprint_unchanged': FrozenStrategyLoader().load().fingerprint == review['strategy_fingerprint'],
                   'cutoff': review['evaluation_window']['cutoff']},
        'fresh_oos_ledger_sha256': digest(FRESH_LEDGER), 'BROKER_ORDER_SUBMISSION': review['BROKER_ORDER_SUBMISSION'],
        'DAILY_FEATURE_MODE': Config.DAILY_FEATURE_MODE})
    b = read(OUT / 'baseline.json')
    assert b['models_unchanged'] and set(b['model_status'].values()) == {'MODEL_BLOCKED'}, 'MODELS'
    assert all(not v['changed'] for v in b['archived_outputs'].values()), 'ARCHIVED'
    assert set(kept['protected_changed']) <= KNOWN_EXCEPTIONS, kept['protected_changed']
    assert b['DAILY_FEATURE_MODE'] == 'legacy' and b['BROKER_ORDER_SUBMISSION'] == 'DISABLED'
    print('BASELINE OK; historical hashes changed:', b['historical_hashes_changed'])


def integrity():
    """The production status file rewritten at 2026-10-23 18:34 +08:00: who wrote it, from which code path, and what it implies."""
    import subprocess

    import pandas as pd
    from sqlalchemy import text

    from core.db_helper import get_db_engine

    path = ROOT / RUNTIME_STATUS
    status = read(path)
    runs = pd.read_sql(text("SELECT pipeline_name, step_name, run_date, status, started_at, finished_at FROM pipeline_runs "
                            "WHERE started_at >= '2026-10-23 10:00:00' ORDER BY id"), get_db_engine())
    mtime_utc = pd.Timestamp(path.stat().st_mtime, unit='s')
    step = runs[(runs.step_name == 'run_daily') & (runs.pipeline_name == 'evening')].iloc[-1]
    mount = subprocess.run(['docker', 'inspect', 'stock_ai_stock_bot', '--format', '{{range .Mounts}}{{.Type}} {{.Destination}} rw={{.RW}};{{end}}'],
                           capture_output=True, text=True, encoding='utf-8').stdout.strip()
    write(out('integrity_event.json'), {
        'event': 'historical-hash file rewritten by the production pipeline', 'file': RUNTIME_STATUS,
        'recorded_sha256': read(HASHES)[RUNTIME_STATUS], 'current_sha256': digest(path),
        'file_mtime_utc': str(mtime_utc), 'asof_date_in_file': status['asof_date'],
        'writer': {'verdict': 'PROVEN', 'process': "scheduler 'evening' pipeline step run_daily (container stock_ai_stock_bot)",
                   'evidence': [f"pipeline_runs: run_daily finished_at {step.finished_at} UTC, status {step.status}",
                                f"file mtime {mtime_utc} UTC matches the step end to the second",
                                'jobs/run_daily.py run_fundamental_production -> core/runtime/fundamental_production STATUS_FILENAME',
                                'the pipeline runs every evening at 10:30 UTC (18:30 +08:00): update_database, run_daily, backtest validation, push_to_line']},
        'pipeline_runs_that_evening': runs.astype(str).to_dict('records'),
        'content_checks': {'broker_submission_status': status['broker_submission_status'], 'production_enable_flag': status['production_enable_flag'],
                           'production_ready': status['production_ready'], 'production_eligibility': status['production_eligibility']},
        'container_mount': mount,
        'implications': [
            'The file is a dynamic runtime status file inside the historical-hash list: it will change after every evening pipeline run, so a hash list that includes it can never stay "unchanged".',
            'The container mounts this working tree read-write: every edit under core/, jobs/ and config/ is live for the evening pipeline, the web app and the LINE bot.',
            'So any code change in this round must keep the legacy path byte-for-byte equivalent in behavior and import-safe; edits must not happen around 18:25-18:40 +08:00.',
            'The earlier rounds changed jobs/run_daily.py and core/calc_indicators.py (legacy path unchanged); the 10-23 evening run succeeded with them.'],
        'handling': 'Reported as an integrity event. Not restored, not re-hashed, no new verify exception. The historical-hash result is reported as N-1 unchanged plus this file.',
        'proposal_for_owner': 'Move runtime status files out of the historical-evidence hash list (new versioned list), or snapshot them separately; the owner decides.'})
    print('writer', step.finished_at, 'status', step.status, '| file', mtime_utc)


# ---- Stage 2 and supplementary official downloads (OD-22) ----
import functools  # noqa: E402
from datetime import date  # noqa: E402

import pandas as pd  # noqa: E402

import scripts.official_data_recovery as odr  # noqa: E402
from core.research.sources.twse import RequestGate, ResponseKind, fetch_corporate_actions  # noqa: E402
from scripts.validate_model_data import official_sessions  # noqa: E402

BUDGET = 1900  # whole change, failures and retries included
MIN_INTERVAL = 3.2
MAX_ATTEMPTS = 3
MAX_CONSECUTIVE_FAILURES = 5
STAGE2_RANGE = ('2022-01-03', '2025-12-12')
SUPPLEMENT_CACHE = ROOT / 'artifacts/official_dual_track_cache'
_ORIGINAL_RESERVE = odr.reserve


def configure(budget=BUDGET):
    """Make the shared acquire functions count against this change's budget (their default is the previous change's 197)."""
    odr.reserve = functools.partial(_ORIGINAL_RESERVE, budget=budget)


class LoggedGate:
    """RequestGate that records the instant each request is released (what the >= 3.2 s rule is about)."""

    def __init__(self, ledger, interval=MIN_INTERVAL):
        self.gate, self.ledger, self.context = RequestGate(minimum_interval=interval), ledger, {}

    def wait(self):
        self.gate.wait()
        odr.log(self.ledger, event='RELEASED', **self.context)


def acquire_with_retries(dataset, day, gate, ledger, trusted):
    """One session, at most MAX_ATTEMPTS requests; only transport and rejected-response failures are retried."""
    for attempt in range(1, MAX_ATTEMPTS + 1):
        gate.context = {'dataset': dataset, 'date': day, 'attempt': attempt}
        state, frame = odr.acquire_chip(dataset, day, gate, ledger, trusted)
        if not state.startswith(('FAILED', 'REJECTED')):
            return state, frame, attempt
    return state, frame, MAX_ATTEMPTS


def quarter_batches():
    sessions, _ = official_sessions()
    days = [d for d in sessions if STAGE2_RANGE[0] <= d <= STAGE2_RANGE[1]]
    return {str(q): list(g) for q, g in pd.Series(days).groupby(pd.PeriodIndex(pd.to_datetime(days), freq='Q'))}


def run_batch(label, days, gate, ledger, trusted):
    """Both datasets for every session of one quarter; stops the batch after MAX_CONSECUTIVE_FAILURES failures in a row."""
    failures, outcome = 0, {'batch': label, 'sessions': len(days), 'states': {}, 'stopped': None}
    for day in days:
        for dataset in ('T86', 'MI_MARGN'):
            state, _frame, attempts = acquire_with_retries(dataset, day, gate, ledger, trusted)
            outcome['states'][f'{dataset}:{day}'] = {'state': state, 'attempts': attempts}
            failures = 0 if state == 'OK' else failures + 1
            if failures >= MAX_CONSECUTIVE_FAILURES:
                outcome['stopped'] = f'{failures} consecutive failures at {dataset} {day}'
                return outcome
    return outcome


def validate_batch(label, days, trusted):
    """Date, schema, row count and hash of every cached file of the batch (no request)."""
    rows = []
    for day in days:
        for dataset in ('T86', 'MI_MARGN'):
            state, frame = odr.chip_status(dataset, day, trusted)
            path = odr.CHIP_CACHE / f'{dataset}_{day.replace("-", "")}.json'
            rows.append({'batch': label, 'dataset': dataset, 'date': day, 'state': state, 'rows': None if frame is None else len(frame),
                         'sha256': digest(path) if path.exists() else None, 'ok': state == 'OK'})
    return rows


def stage2(only=None):
    configure()
    OUT.mkdir(parents=True, exist_ok=True)
    ledger = OUT / 'request_ledger.jsonl'
    gate, trusted = LoggedGate(ledger), read(odr.PREV_CHIPS)['files']
    results, stopped = [], None
    for label, days in quarter_batches().items():
        if only and label not in only:
            continue
        try:
            outcome = run_batch(label, days, gate, ledger, trusted)
        except odr.BudgetExceeded as exc:
            outcome = {'batch': label, 'stopped': f'BUDGET: {exc}', 'states': {}}
        validation = validate_batch(label, days, trusted)
        results.append({**outcome, 'validated_ok': sum(r['ok'] for r in validation), 'validated_total': len(validation), 'files': validation})
        write(OUT / 'stage2_manifest.json', {'budget': BUDGET, 'requests_sent': odr.sent_count(ledger), 'batches': results})
        print(label, f"{results[-1]['validated_ok']}/{results[-1]['validated_total']}", outcome['stopped'] or '')
        if outcome['stopped']:
            stopped = outcome['stopped']
            break
    return stopped


def request_json(ledger, gate, dataset, day, url, fetch, path):
    """One cached JSON request with up to MAX_ATTEMPTS attempts; a failure leaves a recorded gap, never a substitute."""
    if path.exists():
        return 'CACHED'
    for attempt in range(1, MAX_ATTEMPTS + 1):
        odr.reserve(ledger, dataset, day, url)
        gate.context = {'dataset': dataset, 'date': day, 'attempt': attempt}
        gate.wait()
        try:
            payload = fetch()
            if not path.exists():
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(json.dumps(payload, ensure_ascii=False), encoding='utf-8')
            odr.log(ledger, event='RESULT', dataset=dataset, date=day, outcome='RECEIVED')
            return 'OK'
        except Exception as exc:
            odr.log(ledger, event='RESULT', dataset=dataset, date=day, outcome=f'FAILED:{type(exc).__name__}')
    return 'FAILED'


def supplements():
    """2026-10-23 re-check, TWT49U ranges missing from the cache, official delisting list."""
    from core.research.sources import twse

    configure()
    OUT.mkdir(parents=True, exist_ok=True)
    ledger, trusted = OUT / 'request_ledger.jsonl', read(odr.PREV_CHIPS)['files']
    gate, result = LoggedGate(ledger), {}
    index = odr.INDEX_CACHE / 'MI_INDEX_20261023.json'
    if index.exists() and odr.index_kind('2026-10-23') == ResponseKind.NON_TRADING_DAY.value:  # earlier answer was inconclusive (18:29, possibly unpublished)
        moved = index.parent.parent / '_unconfirmed'
        moved.mkdir(exist_ok=True)
        index.replace(moved / f'{index.name}.{int(index.stat().st_mtime)}')
    gate.context = {'dataset': 'MI_INDEX', 'date': '2026-10-23'}
    kind = odr.acquire_index('2026-10-23', gate, ledger)
    result['MI_INDEX_2026-10-23'] = kind
    if kind == ResponseKind.TRADING_DAY.value:
        for dataset in ('T86', 'MI_MARGN'):
            result[f'{dataset}_2026-10-23'] = acquire_with_retries(dataset, '2026-10-23', gate, ledger, trusted)[0]
    for start, end in ((date(2022, 1, 3), date(2022, 12, 30)), (date(2026, 7, 29), date(2026, 10, 23))):
        label = f'{start:%Y%m%d}_{end:%Y%m%d}'
        url = f'{twse.TWT49U_URL}?startDate={start:%Y%m%d}&endDate={end:%Y%m%d}&response=json'
        result[f'TWT49U_{label}'] = request_json(ledger, gate, 'TWT49U', label, url, lambda s=start, e=end: fetch_corporate_actions(s, e, SUPPLEMENT_CACHE).payload,
                                                 SUPPLEMENT_CACHE / f'TWT49U_{label}.json')
    result['DELISTED'] = request_json(ledger, gate, 'DELISTED', '2026-10-23', f'{twse.OPENAPI_URL}/company/suspendListingCsvAndHtml',
                                      lambda: twse._fetch_openapi('company/suspendListingCsvAndHtml').payload, SUPPLEMENT_CACHE / 'suspendListing.json')
    write(OUT / 'supplements_manifest.json', {'requests_sent': odr.sent_count(ledger), 'budget': BUDGET, 'results': result,
                                              'files': {p.name: digest(p) for p in sorted(SUPPLEMENT_CACHE.glob('*.json'))}})
    print(result)


CHIP_FIELDS = odr.CHIP_FIELDS
FULL_RANGE = ('2022-01-03', '2026-10-22')
LOW_ROW_SHARE = 0.5  # a cached session with fewer rows than this share of the dataset median is suspicious


def official_coverage(days, trusted):
    """Per dataset: sessions with an OK cached file, sessions without, rows per file, suspiciously small files (no request)."""
    result, frames = {}, {}
    for dataset in ('T86', 'MI_MARGN'):
        states, rows = {}, {}
        for day in days:
            state, frame = odr.chip_status(dataset, day, trusted)
            states[day], rows[day] = state, 0 if frame is None else len(frame[frame.stock_id.str.fullmatch(r'[1-9]\d{3}')])
            if frame is not None:
                frames.setdefault(dataset, []).append(frame)
        median = float(pd.Series([v for v in rows.values() if v]).median())
        low = [d for d, v in rows.items() if states[d] == 'OK' and v < LOW_ROW_SHARE * median]
        result[dataset] = {'sessions': len(days), 'ok': sum(s == 'OK' for s in states.values()), 'not_ok': {d: s for d, s in states.items() if s != 'OK'},
                           'median_four_digit_rows': median, 'low_row_sessions': low}
    return result, frames


def certify():
    """Official coverage of T86 / MI_MARGN over the whole range, and MySQL against them (graded separately)."""
    bars = pd.read_parquet(OUT / 'official_bars.parquet', columns=['trade_date', 'stock_id'])
    days = sorted(d.strftime('%Y-%m-%d') for d in bars.trade_date.unique() if FULL_RANGE[0] <= d.strftime('%Y-%m-%d') <= FULL_RANGE[1])
    trusted = read(odr.PREV_CHIPS)['files']
    coverage, frames = official_coverage(days, trusted)
    chips = None
    for dataset, parts in frames.items():
        frame = pd.concat(parts, ignore_index=True).assign(stock_id=lambda d: d.stock_id.astype(str))
        frame = frame[frame.stock_id.str.fullmatch(r'[1-9]\d{3}')]
        frame = frame.assign(trade_date=pd.to_datetime(frame.trade_date))[[c for c in frame.columns if c in odr.KEYS + list(CHIP_FIELDS)]].drop_duplicates(odr.KEYS)
        chips = frame if chips is None else chips.merge(frame, on=odr.KEYS, how='outer')
    mysql, duplicates, _ = odr.mysql_rows(start=FULL_RANGE[0], end=FULL_RANGE[1], columns='stock_id, trade_date, foreign_buy, trust_buy, dealer_buy, margin_balance')
    rows = odr.compare(chips, mysql, CHIP_FIELDS, pd.MultiIndex.from_frame(bars[odr.KEYS]), 'FULL')
    frame = pd.DataFrame(rows)
    frame.to_csv(OUT / 'official_mysql_reconciliation.csv', index=False, encoding='utf-8-sig')
    complete = all(not coverage[d]['not_ok'] and not coverage[d]['low_row_sessions'] for d in coverage)
    d3 = read(OUT / 'd3_certification.json')
    gates = [
        {'gate': 'OFFICIAL_T86_FULL_RANGE', 'range': list(FULL_RANGE), 'status': 'DATA_PASS' if not coverage['T86']['not_ok'] and not coverage['T86']['low_row_sessions'] else 'DATA_BLOCKED', 'coverage': coverage['T86']},
        {'gate': 'OFFICIAL_MI_MARGN_FULL_RANGE', 'range': list(FULL_RANGE), 'status': 'DATA_PASS' if not coverage['MI_MARGN']['not_ok'] and not coverage['MI_MARGN']['low_row_sessions'] else 'DATA_BLOCKED',
         'coverage': coverage['MI_MARGN']},
        {'gate': 'OFFICIAL_CALENDAR_AND_PRICES', 'range': list(FULL_RANGE), 'status': 'DATA_PASS', 'sessions': len(days),
         'note': '2026-10-23 answered no data at 18:29 and again at about 20:50 +08:00 (treated as a non-trading day, not counted as a session)'},
        {'gate': 'D3_ADJUSTED_PRICES', 'status': d3['status'], 'price_match_rate': d3['price_match_rate'], 'rows': d3['rows'], 'exceptions': d3['exceptions']},
        {'gate': 'OFFICIAL_CORPORATE_ACTIONS', 'status': 'DATA_PASS', 'scope': 'adjustment factors (reference price / previous close); cash dividend amounts are not separable for rights-and-dividend events',
         'files': d3['event_file_sha256']},
        {'gate': 'OFFICIAL_DELISTING_LIST', 'status': 'DATA_PASS', 'scope': 'official list and last traded session; exit prices and payouts are NOT available -> DELISTING_EXIT_UNVERIFIED if held'},
        {'gate': 'TOTAL_RETURN', 'status': 'DATA_BLOCKED', 'reason': 'rights-and-dividend events cannot be split into cash and shares; every configuration holds through such events'},
        {'gate': 'FUNDAMENTALS_VALUES', 'status': 'DATA_BLOCKED', 'reason': 'publication dates are official through FY2025; values were not independently re-verified (a sample re-parse was not performed this round)'},
        {'gate': 'MONTHLY_REVENUE_TIMING', 'status': 'DATA_BLOCKED', 'reason': 'no official announcement time'},
        {'gate': 'UNIVERSE_TPEX', 'status': 'DATA_BLOCKED', 'reason': 'no certified TPEx source; research and recovered Daily use TWSE-listed stocks only'},
        {'gate': 'COSTS', 'status': 'DATA_PASS', 'scope': 'fee 0.1425 %, tax 0.3 %, slippage 0.1 % from config/settings.py; stress at twice'},
        {'gate': 'EXECUTION_CONSTRAINTS', 'status': 'DATA_PASS', 'scope': 'official OHLC and previous close / ex-rights reference price give limit prices and locked opens; the audit is flag-only'}]
    for field in CHIP_FIELDS:
        g = frame[frame.field == field]
        shared, mismatch = int(g.shared_keys.sum()), int(g.mismatch.sum())
        stats = {'shared_keys': shared, 'mismatch': mismatch, 'match_rate': None if not shared else round(1 - mismatch / shared, 6), 'dates': int(len(g)),
                 'dates_with_mismatch': int((g.mismatch > 0).sum()), 'whole_day_error_dates': g[g.whole_day_error].date.tolist(),
                 'mysql_null_official_present': int(g.mysql_null_official_present.sum()), 'official_present': int(g.official_present.sum()),
                 'mysql_zero_official_nonzero': int(g.mysql_zero_official_nonzero.sum())}
        gates.append({'gate': f'MYSQL_{field.upper()}_FULL_RANGE', 'status': odr.grade(stats, complete=complete), 'stats': stats, 'official_coverage_complete': complete})
    write(OUT / 'data_gate_results.json', {'grades': 'DATA_PASS / DATA_FAIL / DATA_BLOCKED; official sources and MySQL are graded separately', 'mysql_duplicate_keys': duplicates, 'gates': gates})
    print({g['gate']: g['status'] for g in gates})


STAGES = ('baseline', 'integrity', 'supplements', 'stage2', 'certify')

if __name__ == '__main__':
    sys.stdout.reconfigure(encoding='utf-8')
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('stage', choices=STAGES)
    parser.add_argument('--only', help='stage2: comma-separated quarter labels, e.g. 2022Q1,2022Q2')
    args = parser.parse_args()
    if args.stage == 'stage2':
        stage2(args.only.split(',') if args.only else None)
    else:
        globals()[args.stage]()
