"""CLI of change 2026-11-10-historical-data-foundation-v1: the parts that need strategies, the engine or MySQL (core.research may not import them).

    python -m scripts.historical_foundation extract-mysql   # read-only SELECT 2022-2025 -> write-once reconcile extract
    python -m scripts.historical_foundation build           # snapshot (BUILT | REUSE) from local raw files, no HTTP
    python -m scripts.historical_foundation evaluate        # coverage + requirements + trust -> evaluation file and reports
    python -m scripts.historical_foundation gaps            # bounded gap recovery (no HTTP; research budget checked only)
    python -m scripts.historical_foundation run [--plan-only] [--workers N]   # historical reference runs + cross-regime report
"""
from __future__ import annotations

import argparse
import functools
import json
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from core.research.history import snapshot as hs

OUT = hs.OUT
EXTRACTS = OUT / 'extracts'
MYSQL_COLUMNS = 'trade_date, stock_id, open_price, high_price, low_price, close_price, volume, foreign_buy, trust_buy, dealer_buy, margin_balance'


def _write_once(frame: pd.DataFrame, stem: str) -> Path:
    """Content-named parquet: an identical extract reuses the existing file, a different one gets a new name."""
    EXTRACTS.mkdir(parents=True, exist_ok=True)
    tmp = EXTRACTS / f'.{stem}.tmp.parquet'
    frame.to_parquet(tmp, index=False)
    target = EXTRACTS / f'{stem}_{hs.sha256(tmp)[:12]}.parquet'
    if target.exists():
        tmp.unlink()
    else:
        tmp.replace(target)
    return target


def extract_mysql() -> Path:
    from scripts import native7_rules_only as n7
    frame, duplicates, before = n7.odr.mysql_rows(start='2022-01-01', end=hs.DEV_END, columns=MYSQL_COLUMNS)
    path = _write_once(frame.sort_values(['trade_date', 'stock_id']).reset_index(drop=True), 'mysql_daily_market_data')
    print(json.dumps({'extract': hs._key(path), 'rows': len(frame), 'duplicates_dropped': duplicates, 'rows_before': before,
                      'first': str(frame.trade_date.min().date()), 'last': str(frame.trade_date.max().date()), 'statement': 'SELECT only'}))
    return path


def report_folder(evaluation_id) -> Path:
    """Write-once evidence folder: the evaluation plus the code that writes gaps and reports. Changing that code opens a new folder."""
    from core.runtime import research_budget
    code = hs.hashlib.sha256()
    for path in (Path(__file__), ROOT / 'scripts/historical_report.py', ROOT / 'core/research/history/regime.py'):
        code.update(path.read_bytes())
    code.update(json.dumps(sorted(research_budget.CAPS.items())).encode())  # gap routes depend on the budget plan the Owner approved
    return REPORTS / evaluation_id / f'C-{code.hexdigest()[:12]}'


def snapshot(snapshot_id=None):
    """The snapshot under this module's OUT (the latest one when no id is given)."""
    return hs.load(snapshot_id, OUT / 'snapshots')


def latest_extract() -> Path | None:
    found = sorted(EXTRACTS.glob('mysql_daily_market_data_*.parquet'), key=lambda p: p.stat().st_mtime)
    return found[-1] if found else None


def sources() -> hs.Sources:
    extract = latest_extract()
    return hs.default_sources({'mysql_daily_market_data': extract} if extract else None)


def build():
    snap, state = hs.build(sources())
    print(json.dumps({'snapshot_id': snap.snapshot_id, 'state': state, 'parts': {k: v['rows'] for k, v in snap.manifest['parts'].items()}}))
    return snap


EVALUATIONS = OUT / 'evaluations'
EVALUATION_LABELS = ['REFERENCE_ONLY', 'RESEARCH_ONLY', 'NOT_CERTIFIED', 'NOT_A_TRIAL']
SEARCH_ROOTS = ('artifacts', 'outputs', 'data')  # local caches probed by gap recovery round 1


def rule_inputs() -> dict[str, frozenset]:
    """native_frame.rule_inputs of every native and TQuant strategy, built like legacy.check_injectable (no DB read)."""
    from unittest.mock import patch
    from core.research.native_frame import rule_inputs as scan
    from core.research.reference.catalog import capabilities
    from core.strategy.adapters.legacy import _load_factory
    from core.strategy_manager import StrategyManager
    out = {}
    with patch('core.db_helper.get_setting', lambda key, default=None: default):
        for cap in capabilities():
            if cap.family in ('native', 'tquant'):
                out[cap.strategy_id] = frozenset(scan(_load_factory(StrategyManager.CANONICAL_REGISTRY[cap.strategy_id])()))
    return out


def _strategy_shas() -> dict[str, str]:
    from scripts import legacy_baseline_run as legacy
    from core.research.reference.catalog import capabilities
    return {c.strategy_id: hs.sha256(legacy._strategy_file(c.strategy_id)) for c in capabilities() if c.family in ('native', 'tquant')}


def strategy_summary(req, cells, snap) -> dict:
    from core.research.history import trust
    span = trust.verified_span(cells)
    first_year = trust.first_backtestable_year(cells)
    sessions = snap.frame('benchmark').date
    earliest = None
    if first_year is not None:
        floor = max(pd.Timestamp(first_year, 1, 1), sessions.min() + pd.Timedelta(days=req['warmup_calendar_days']))
        earliest = str(sessions[sessions >= floor].min().date())
    return {'strategy_id': req['strategy_id'], 'verified_years': [c['year'] for c in cells if c['status'] == 'VERIFIED'],
            'partial_years': [c['year'] for c in cells if c['status'] == 'PARTIAL'],
            'blocked_years': [c['year'] for c in cells if c['status'] in ('ACCESS_BLOCKED', 'SOURCE_UNAVAILABLE', 'FAILED_VALIDATION')],
            'verified_span': span, 'first_backtestable_year': first_year, 'earliest_usable_date': earliest,
            'data_trust_full_2015_2025': all(c['status'] == 'VERIFIED' for c in cells),
            'data_trust_verified': f'{span[0]}-{span[1]}' if span else None, 'certified': False}


def inventory(snap, cells) -> list[dict]:
    """historical_source_inventory: one row per local source group (aggregate SHA256 over its files) plus one row per absent span."""
    from core.research.history import categories as cats
    groups = {}
    for group, files in snap.manifest['sources'].items():
        for name, entry in files.items():
            folder = str(Path(entry['path']).parent.as_posix())
            dataset = name.split('_2')[0] if group in ('mi_index', 'twt49u', 'capital', 'chips') else name
            groups.setdefault((group, dataset, folder), []).append((name, entry['sha256']))
    meta = {'mi_index': ('TWSE', 'https://www.twse.com.tw/rwd/zh/afterTrading/MI_INDEX', 'calendar,prices,universe,benchmark'),
            'twt49u': ('TWSE', 'https://www.twse.com.tw/rwd/zh/exRight/TWT49U', 'dividends'),
            'capital': ('TWSE', 'TWTAUU / TWTB8U', 'capital_changes'), 'delisting': ('TWSE', 'suspendListing', 'delistings,universe'),
            'chips': ('TWSE', 'T86 / MI_MARGN', 't86_flows,margin'), 'pinned': ('ENGINE_INPUT', 'pinned engine input file', 'financials,revenue'),
            'reconcile': ('RECONCILE_ONLY', 'independent second source', 'reconciliation'), 'fetch_log': ('AUDIT', 'h4_hist fetch log', 'integrity')}
    rows = []
    for (group, dataset, folder), files in sorted(groups.items()):
        stamps = sorted(n[-8:] for n, _ in files if n[-8:].isdigit())
        publisher, endpoint, category = meta[group]
        rows.append({'source_id': f'{publisher}:{dataset}:{folder}', 'publisher': publisher, 'endpoint': endpoint, 'category': category,
                     'period': [stamps[0], stamps[-1]] if stamps else None, 'files': len(files), 'location': folder,
                     'aggregate_sha256': hs.hashlib.sha256(''.join(sorted(h for _, h in files)).encode()).hexdigest(),
                     'version': snap.snapshot_id, 'access': 'LOCAL_RAW_READ_ONLY' if publisher == 'TWSE' else publisher,
                     'refetchable': 'YES_WITH_OWNER_RESEARCH_BUDGET' if publisher == 'TWSE' else 'NO_LOCAL_COPY_ONLY',
                     'status': 'LOCAL', 'independent_reconciliation': group in ('mi_index', 'twt49u', 'capital', 'delisting')})
    absent = {}
    for c in cells:
        if c['status'] in ('ACCESS_BLOCKED', 'SOURCE_UNAVAILABLE'):
            absent.setdefault((c['category'], c['status'], c['reasons'][0]), []).append(c['year'])
        for missing in c['metrics'].get('missing_sessions', []) if c['category'] == 'calendar' else []:
            absent.setdefault(('calendar:missing_session', 'ACCESS_BLOCKED', c['route'] or 'BUDGET_UNASSIGNED:hist_foundation_quotes'), []).append(missing)
    for (category, status, reason), years in sorted(absent.items(), key=str):
        route = cats.BY_ID.get(category.split(':')[0]).route
        rows.append({'source_id': f'GAP:{category}', 'publisher': route.source if route else None, 'endpoint': route.endpoint if route else None,
                     'category': category, 'period': [str(min(years)), str(max(years))], 'files': 0, 'location': None, 'aggregate_sha256': None,
                     'version': None, 'access': reason, 'refetchable': 'OWNER_BUDGET_REQUIRED' if route and route.budget_job else 'NO_KNOWN_ROUTE',
                     'status': status, 'independent_reconciliation': False, 'gap_dates_or_years': sorted(map(str, set(years))),
                     'estimated_requests': (len(set(years)) if 'missing_session' in category else
                                            (route.requests_per_year * len(set(years)) if route and route.requests_per_year else None))})
    return rows


def evaluate(snap=None) -> dict:
    from core.research.history import coverage, trust
    from core.research.reference.catalog import capabilities
    snap = snap or snapshot()
    inputs = rule_inputs()
    caps = capabilities()
    shas = _strategy_shas()
    code = {'coverage': coverage.code_sha(), 'trust': hs.sha256(Path(trust.__file__))}
    evaluation_id = 'EV1-' + hs.hashlib.sha256(hs._canonical({'snapshot': snap.snapshot_id, 'code': code, 'strategies': shas, 'labels': EVALUATION_LABELS,
                                                              'inputs': {k: sorted(v) for k, v in inputs.items()}}).encode()).hexdigest()[:12]
    target = EVALUATIONS / snap.snapshot_id / f'{evaluation_id}.json'
    if target.exists():
        print(json.dumps({'evaluation_id': evaluation_id, 'state': 'REUSE'}))
        _point_evaluation(snap, target)
        return json.loads(target.read_text(encoding='utf-8'))
    cells = coverage.evaluate(snap)
    cov = {(c['year'], c['category']): c for c in cells}
    reqs = [trust.requirement(cap, inputs.get(cap.strategy_id, frozenset())) for cap in caps]
    matrix = trust.matrix(reqs, cov)
    result = {'evaluation_id': evaluation_id, 'snapshot_id': snap.snapshot_id, 'created_at': pd.Timestamp.now(tz='Asia/Taipei').isoformat(),
              'code_sha256': code, 'strategy_sha256': shas, 'coverage': cells, 'requirements': reqs, 'trust': matrix,
              'summaries': {r['strategy_id']: strategy_summary(r, matrix[r['strategy_id']], snap) for r in reqs},
              'inventory': inventory(snap, cells), 'labels': EVALUATION_LABELS}
    target.parent.mkdir(parents=True, exist_ok=True)
    with open(target, 'x', encoding='utf-8') as stream:
        json.dump(result, stream, ensure_ascii=False, default=str)
    _point_evaluation(snap, target)
    print(json.dumps({'evaluation_id': evaluation_id, 'state': 'WRITTEN', 'cells': pd.Series([c['status'] for c in cells]).value_counts().to_dict()}))
    return json.loads(target.read_text(encoding='utf-8'))


def _point_evaluation(snap, target):
    pointer = EVALUATIONS / 'LATEST.json'
    tmp = pointer.with_suffix('.tmp')
    tmp.write_text(json.dumps({'snapshot_id': snap.snapshot_id, 'evaluation': target.relative_to(OUT).as_posix()}), encoding='utf-8')
    tmp.replace(pointer)


def latest_evaluation() -> dict | None:
    pointer = EVALUATIONS / 'LATEST.json'
    if not pointer.exists():
        return None
    return json.loads((OUT / json.loads(pointer.read_text(encoding='utf-8'))['evaluation']).read_text(encoding='utf-8'))


@functools.lru_cache(maxsize=1)
def _local_files() -> dict[str, list[str]]:
    """Every cached MI_INDEX / TWTAUU / TWTB8U file name under the search roots (unreadable directories are skipped)."""
    import os
    found = {}
    for root in SEARCH_ROOTS:
        for folder, _, names in os.walk(ROOT / root):
            for name in names:
                if name.startswith(('MI_INDEX_', 'TWTAUU_', 'TWTB8U_')):
                    found.setdefault(name.split('.')[0], []).append(str(Path(folder, name).relative_to(ROOT)))
    return found


def _gap_probes(cell):
    """Round probes for one non-verified cell: local caches (by file name), the research-budget route (no HTTP), alternative sources."""
    from core.research.history import categories as cats
    from core.research.history.coverage import route_status

    def local(_gap):
        index = _local_files()
        if cell['category'] == 'capital_changes':  # any cached range file whose span covers the year
            found = [p for name, paths in index.items() if name.startswith(('TWTAUU_', 'TWTB8U_')) and len(name.split('_')) == 3
                     and name.split('_')[1][:4] <= str(cell['year']) <= name.split('_')[2][:4] and name.split('_')[2][4:] >= '1228' for p in paths]
        else:
            found = [p for d in cell['metrics'].get('missing_sessions', []) for p in index.get(f"MI_INDEX_{d.replace('-', '')}", [])]
        return {'found': found, 'verified': False} if found else None

    def official(_gap):
        return {'denied': route_status(cats.BY_ID[cell['category']])}

    def alternative(_gap):
        if cell['category'] in ('prices', 'calendar', 'capital_changes', 'monthly_revenue') and cell['year'] <= 2023:
            return {'found': 'FinLab recovery parquet', 'verified': False, 'role': 'RECONCILE_ONLY_NOT_PRIMARY'}
        return None
    return {'local': local, 'official': official, 'alternative': alternative}


def owner_action(cell, route, sessions_missing, delisted_missing=None) -> str:
    """The smallest Owner step that would lift this cell, named by its cause (a reason can point at another category's source)."""
    missing = sessions_missing.get(cell['year'], [])
    reasons = ' '.join(cell['reasons'])
    if missing and ('PRIOR_SESSION_MISSING_LOCALLY' in reasons or 'INCOMPLETE' in reasons) and cell['category'] in ('calendar', 'prices', 'dividends'):
        return f"budget job hist_foundation_quotes: MI_INDEX for {', '.join(missing)} ({len(missing)} requests)"
    codes = (delisted_missing or {}).get(cell['year'])
    if cell['category'] in ('universe', 'delistings') and codes:
        return (f"no request plan: confirm from TWSE delisting announcements that {', '.join(codes)} did not trade during {cell['year']} "
                '(absent from every MI_INDEX file of the year)')
    if route and route.budget_job:
        return f"budget job {route.budget_job}: {route.endpoint}" + (f' (about {route.requests_per_year} requests per year)' if route.requests_per_year else '')
    return 'no legal source known: register one before any use'


def gaps(evaluation=None) -> Path:
    """Bounded recovery for every non-VERIFIED SIGNAL cell; writes gaps/<evaluation_id>/gap_report.json and .md. Never fetches."""
    from core.research.history import categories as cats
    from core.research.reference import gaps as reference_gaps
    evaluation = evaluation or latest_evaluation()
    affected = {}
    for sid, cells in evaluation['trust'].items():
        for c in cells:
            for cid in c['binding']:
                affected.setdefault((c['year'], cid), []).append(sid)
    rows = []
    sessions_missing = {c['year']: c['metrics'].get('missing_sessions', []) for c in evaluation['coverage'] if c['category'] == 'calendar'}
    delisted_missing = {c['year']: c['metrics'].get('not_found', []) for c in evaluation['coverage'] if c['category'] == 'delistings'}
    for c in evaluation['coverage']:
        if c['status'] == 'VERIFIED' or cats.BY_ID[c['category']].role != 'SIGNAL':
            continue
        result = reference_gaps.recover({'field': c['category'], 'issue': c['status']}, _gap_probes(c))
        route = cats.BY_ID[c['category']].route
        rows.append({'year': c['year'], 'category': c['category'], 'status': c['status'], 'reasons': c['reasons'],
                     'exact_range': c['metrics'].get('missing_sessions') or f"{c['year']}-01-01..{c['year']}-12-31",
                     'rounds': result['rounds'], 'attempts': result['attempts'], 'recovered': result['recovered'],
                     'legal_alternative': 'FinLab (reconcile only)' if any(a['source'] == 'alternative' and a['result'] for a in result['attempts']) else None,
                     'affected_strategies': sorted(affected.get((c['year'], c['category']), [])),
                     'owner_action': owner_action(c, route, sessions_missing, delisted_missing), 'note': route.note if route else ''})
    from scripts.historical_report import write_once
    out = report_folder(evaluation['evaluation_id'])
    write_once('gap_report.json', json.dumps({'evaluation_id': evaluation['evaluation_id'], 'snapshot_id': evaluation['snapshot_id'],
                                              'http_requests': 0, 'gaps': rows}, ensure_ascii=False, indent=1, default=str), out)
    print(json.dumps({'gaps': len(rows), 'recovered': sum(r['recovered'] for r in rows), 'path': hs._key(out)}))
    return out / 'gap_report.json'


RUNS = OUT / 'runs'
REPORTS = OUT / 'reports'
BENCHMARK = 'TWSE total return index (發行量加權股價報酬指數, MI_INDEX row); price index for regimes'


ACCOUNTING = 'POSITION_LEDGER_CA_V1'  # change 2026-11-10-engine-accounting-and-history-gaps-v1: raw prices, official events booked on positions
LEGACY_LABEL = 'LEGACY_ACCOUNTING/PROVISIONAL'  # how a stored run without the accounting key is shown; the run folder itself is never touched


def history_group(snapshot_id) -> str:
    from core.backtest.run_store import canonical_hash
    from scripts import legacy_baseline_run as legacy
    return 'REFERENCE_HISTORY:' + canonical_hash({
        'engine': 'legacy_next_session_open_v2', 'costs': legacy._costs(), 'stress_multiple': legacy.STRESS_MULTIPLE,
        'valuation': 'finite_positive_stale', 'accounting': ACCOUNTING, 'rebalance': 'daily rules', 'universe': 'snapshot TWSE listed four-digit equities',
        'benchmark': BENCHMARK, 'snapshot_id': snapshot_id, 'window_rule': 'first backtestable year (trust matrix) .. 2025-12-31'})[:12]


def _config(cap, window=None):
    from scripts import legacy_baseline_run as legacy
    config = {k: v for k, v in legacy.config_for(cap.strategy_id, accounting=ACCOUNTING).items() if k not in ('round', 'start', 'end')}
    return config | ({'window': window} if window else {})


def history_identity(cap, snapshot_id, start, end) -> str:
    from core.research.history import ledger
    from core.research.reference import runner
    from scripts import legacy_baseline_run as legacy
    from scripts import native7_rules_only as n7
    config = _config(cap, [start, end]) | {'snapshot_id': snapshot_id, 'group': history_group(snapshot_id)}
    inputs = {'snapshot_id': snapshot_id, 'user_settings': hs.sha256(legacy.input_paths()['user_settings']), 'chip_manifest': hs.sha256(n7.odr.PREV_CHIPS)}
    code = [*runner._code_files(cap), ROOT / 'scripts/history_engine.py', Path(hs.__file__), Path(ledger.__file__)]
    return runner.run_identity(strategy_id=cap.strategy_id, config=config, code_files=code, inputs=inputs)


def lookup(identity, root=None) -> str | None:
    from core.backtest import run_store
    root = Path(root or RUNS)
    for folder in sorted(root.glob('2*'), reverse=True):
        try:
            record = json.loads((folder / 'run.json').read_text(encoding='utf-8'))
            if record.get('run_identity') == identity and record.get('status') == 'SUCCESS':
                run_store.load_run(folder.name, root=root)
                return folder.name
        except (OSError, ValueError):
            continue
    return None


def plan(evaluation, caps=None, root=None) -> list[dict]:
    """One row per catalog strategy: GOVERNED_ONLY > ENGINE_UNSUPPORTED > DATA_BLOCKED > REUSE > RUN, with the window and identity."""
    from core.research.reference.catalog import capabilities
    rows = []
    for cap in caps or capabilities():
        summary = evaluation['summaries'][cap.strategy_id]
        row = {'strategy_id': cap.strategy_id, 'family': cap.family, 'window': None, 'run_identity': None, 'run_id': None}
        if cap.governance:
            row |= {'state': 'GOVERNED_ONLY', 'reason': cap.governance}
        elif cap.engine is None:
            row |= {'state': 'ENGINE_UNSUPPORTED', 'reason': 'NO_ENGINE_PATH'}
        elif cap.static_block or summary['earliest_usable_date'] is None:
            blocked = [c for c in evaluation['trust'][cap.strategy_id] if not c['backtestable']]
            row |= {'state': 'DATA_BLOCKED', 'reason': cap.static_block or ';'.join(sorted({b for c in blocked for b in c['binding']}))}
        else:
            window = [summary['earliest_usable_date'], hs.DEV_END]
            identity = history_identity(cap, evaluation['snapshot_id'], *window)
            reuse = lookup(identity, root)
            row |= {'window': window, 'run_identity': identity, 'run_id': reuse, 'state': 'REUSE' if reuse else 'RUN',
                    'reason': 'IDENTICAL_RUN_IDENTITY' if reuse else 'NO_STORED_RUN'}
        rows.append(row)
    return rows


def save(cap, row, result, snapshot_id, root=None) -> str:
    from core.backtest import run_store
    from core.research.reference import runner
    from scripts import legacy_baseline_run as legacy
    config = _config(cap, row['window'])
    if result.get('accounting') != ACCOUNTING:  # a missing declaration is a mismatch: never label an undeclared result as a ledger run
        raise ValueError(f"ACCOUNTING_MISMATCH: engine ran {result.get('accounting')}, identity promises {ACCOUNTING}")
    record = {'strategy_id': cap.strategy_id, 'strategy_ids': [cap.strategy_id], 'strategy_type': f'reference_history_{cap.family}',
              'parameters': config, 'parameter_hash': run_store.canonical_hash(config), 'run_identity': row['run_identity'],
              'source_hash': row['run_identity'].split(':', 1)[1], 'data_version': snapshot_id, 'snapshot_id': snapshot_id,
              'comparison_group': history_group(snapshot_id), 'certification_status': 'NOT_CERTIFIED', 'status_class': 'EXPERIMENTAL',
              'labels': runner.LABELS + ['RESEARCH_ONLY', 'NOT_OOS_PREVIOUSLY_VIEWED_STRATEGIES'], 'period': row['window'],
              'cost_model': {**legacy._costs(), 'stress_multiple': legacy.STRESS_MULTIPLE}, 'engine_error': result.get('engine_error'),
              'benchmark': BENCHMARK, 'accounting': ACCOUNTING, 'ledger_version': result.get('ledger_version'),
              'open_positions': result.get('open_positions', []), 'cost_summary': result.get('cost_summary')}
    actions = result.get('corporate_actions')
    frames = {'nav': result['nav'], 'trades': result['trades'], 'holdings': pd.DataFrame(columns=['date', 'stock_id', 'shares']),
              'picks': pd.DataFrame(columns=['date', 'stock_id']), 'metrics': runner.metrics(result['nav']),
              'corporate_actions': actions if actions is not None else pd.DataFrame(columns=['date', 'stock_id', 'event_id', 'kind', 'status'])}
    return run_store.save_run(record, frames, root=Path(root or RUNS))


def run(execute=None, workers=4, plan_only=False, root=None, evaluation=None) -> dict:
    """Plan, REUSE identical identities, execute the rest in worker processes, save, then write the cross-regime report."""
    from concurrent.futures import ProcessPoolExecutor
    from core.research.history import regime
    from core.research.reference.catalog import capabilities
    evaluation = evaluation or latest_evaluation()
    if execute is None:  # the engine re-reads chip and PIT files by path: refuse when any source drifted from the snapshot's hashes
        snap = snapshot(evaluation['snapshot_id'])
        problems = hs.verify(snap, deep=True) + chip_manifest_conflicts(snap)
        if problems:
            raise hs.SnapshotDrift(f'SNAPSHOT_SOURCES_CHANGED: {problems[:5]}')
    spec = regime.register(OUT)  # before any execution: the spec time precedes every run it labels
    caps = {c.strategy_id: c for c in capabilities()}
    rows = plan(evaluation, list(caps.values()), root)
    todo = sorted((r for r in rows if r['state'] == 'RUN'), key=lambda r: (r['window'], r['strategy_id']))  # earliest start = longest window first
    print(json.dumps({'plan': {s: sum(r['state'] == s for r in rows) for s in sorted({r['state'] for r in rows})}, 'regime_spec': spec['regime_spec_id']}), flush=True)
    if plan_only:
        return {'rows': rows}
    calls = 0
    if todo:
        if execute is None:
            from scripts.history_engine import execute_by_id
            from concurrent.futures import as_completed
            with ProcessPoolExecutor(max_workers=workers) as pool:
                futures = {}
                for r in todo:
                    _wait_for_memory()  # each worker's window preparation peaks at a few GB: never start them all at once
                    futures[pool.submit(execute_by_id, r['strategy_id'], r['run_identity'], evaluation['snapshot_id'], *r['window'])] = r
                for future in as_completed(futures):  # save each run as soon as it finishes
                    _finish(futures[future], future, caps, evaluation, root)
                    calls += 1
        else:
            for row in todo:
                calls += 1
                _finish(row, execute(row), caps, evaluation, root)
    return {'rows': rows, 'engine_calls': calls, 'regime_spec': spec}


def chip_manifest_conflicts(snap) -> list[str]:
    """The engine skips a chip file whose hash differs from the trusted chip manifest; coverage counted it, so such a file must stop the run."""
    from scripts import native7_rules_only as n7
    trusted = n7.read(n7.odr.PREV_CHIPS)['files']
    return [f'CHIP_MANIFEST_SHA_MISMATCH:{name}' for name, entry in snap.manifest['sources'].get('chips', {}).items()
            if f'{name}.json' in trusted and trusted[f'{name}.json']['sha256'] != entry['sha256']]


MIN_FREE_GB, STAGGER_SECONDS = 6, 45


def _wait_for_memory(limit_seconds=7200):
    import time
    import psutil
    time.sleep(STAGGER_SECONDS)
    waited = 0
    while psutil.virtual_memory().available < MIN_FREE_GB * 2 ** 30:
        if waited >= limit_seconds:
            raise MemoryError(f'LESS_THAN_{MIN_FREE_GB}_GB_FREE_FOR_{limit_seconds}_S')
        time.sleep(15)
        waited += 15


def _finish(row, outcome, caps, evaluation, root):
    try:
        result = outcome.result() if hasattr(outcome, 'result') else outcome
        row['run_id'] = save(caps[row['strategy_id']], row, result, evaluation['snapshot_id'], root)
    except Exception as exc:  # one strategy's failure is recorded, the batch continues
        row |= {'state': 'FAILED', 'reason': f'{type(exc).__name__}: {str(exc)[:300]}'}
    print(json.dumps({k: row[k] for k in ('strategy_id', 'state', 'run_id', 'reason')}), flush=True)


def refresh():
    """Web job: snapshot (REUSE when nothing changed), evaluation, bounded gap recovery and data reports. No HTTP."""
    from scripts import historical_report
    evaluation = evaluate(build())
    gaps(evaluation)
    historical_report.data_reports(evaluation)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument('command', choices=['extract-mysql', 'build', 'evaluate', 'gaps', 'refresh', 'run'])
    parser.add_argument('--plan-only', action='store_true')
    parser.add_argument('--workers', type=int, default=4)
    args = parser.parse_args(argv)
    if args.command == 'run':
        from scripts import historical_report
        result = run(workers=args.workers, plan_only=args.plan_only)
        failed = [r['strategy_id'] for r in result['rows'] if r['state'] == 'FAILED']
        if failed:  # a partial batch is not a result: no report, no DONE, non-zero exit (the web shows FAILED)
            print(json.dumps({'command': 'run', 'status': 'FAILED', 'failed': failed}), flush=True)
            return 1
        if not args.plan_only:
            historical_report.regime_reports(result['rows'])
    else:
        {'extract-mysql': extract_mysql, 'build': build, 'evaluate': evaluate, 'gaps': gaps, 'refresh': refresh}[args.command]()
    print(json.dumps({'command': args.command, 'status': 'DONE'}), flush=True)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
