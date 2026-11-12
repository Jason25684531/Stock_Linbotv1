"""Strategy intelligence batch (change 2026-11-11-strategy-intelligence-platform-v1).

Loads stored historical runs, the snapshot and the trust matrix, calls the pure functions in
core/research/intelligence and writes immutable results under outputs/strategy_intelligence.
REFERENCE_ONLY, RESEARCH_ONLY, NOT_CERTIFIED, NOT_A_TRIAL. Zero HTTP, no MySQL, nothing after 2025-12-31.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

OUT = ROOT / 'outputs/strategy_intelligence'
HISTORY_RUNS = ROOT / 'outputs/historical_foundation/runs'


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


def sha256(path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def write_json(path: Path, value) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + '.tmp')
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=1, sort_keys=True, default=str), encoding='utf-8')
    os.replace(tmp, path)
    return path


def read_json(path: Path, default=None):
    path = Path(path)
    return json.loads(path.read_text(encoding='utf-8')) if path.is_file() else default


def identity_files() -> dict:
    """Every file inside a historical run identity, with its SHA256 (the files this change must never edit)."""
    from core.research.history import ledger, snapshot as hs
    from core.research.reference import runner
    from core.research.reference.catalog import capabilities
    files = {ROOT / 'scripts/history_engine.py', Path(hs.__file__), Path(ledger.__file__)}
    for cap in capabilities():
        if cap.engine is not None and not cap.governance:
            files.update(runner._code_files(cap))
    return {Path(p).resolve().relative_to(ROOT).as_posix(): sha256(p) for p in sorted(files, key=str)}


def run_folder_hashes(root=HISTORY_RUNS) -> dict:
    return {f'{d.name}/{f.name}': sha256(f) for d in sorted(Path(root).glob('2*')) if d.is_dir() for f in sorted(d.iterdir()) if f.is_file()}


def baseline(out=OUT) -> dict:
    """Write baseline.json once; governance via the existing checkpoint baseline, plus identity files and stored history runs."""
    from scripts import data_readiness_checkpoint as drc
    path = Path(out) / 'baseline.json'
    if path.is_file():
        return read_json(path)
    value = drc.baseline(out) | {'identity_files': identity_files(), 'history_runs': run_folder_hashes(),
                                 'git_head': subprocess.run(['git', 'rev-parse', 'HEAD'], cwd=ROOT, capture_output=True, text=True).stdout.strip()}
    write_json(path, value)
    return value


def governance_diff(out=OUT) -> list[str]:
    """Differences against baseline.json: ledgers, effective_trials(), H4, identity files and every old run file."""
    from scripts import data_readiness_checkpoint as drc
    base = read_json(Path(out) / 'baseline.json')
    if base is None:
        return ['BASELINE_MISSING']
    diffs = drc.compare_baseline(out)
    now_ids = identity_files()
    diffs += [f'IDENTITY_FILE_CHANGED:{k}' for k, v in base['identity_files'].items() if now_ids.get(k) != v]
    now_runs = run_folder_hashes()
    diffs += [f'OLD_RUN_FILE_CHANGED:{k}' for k, v in base['history_runs'].items() if now_runs.get(k) != v]
    return diffs


DATA = ROOT / 'core/research/intelligence/data'
CARDS = DATA / 'cards'
PROFILE = DATA / 'evaluation_profile.json'


def catalog_ids() -> list[str]:
    from core.research.reference.catalog import capabilities
    return [c.strategy_id for c in capabilities()]


def card_path(strategy_id: str) -> Path:
    return CARDS / f"{strategy_id.replace(':', '__')}.json"


def cards(stamp_missing=True) -> dict:
    """Stamp hashes on new sources (authoring), then build every card; a missing card or symbol is an error."""
    from core.research.intelligence import definition
    built, errors = {}, []
    for sid in catalog_ids():
        path = card_path(sid)
        if not path.is_file():
            errors.append(f'CARD_MISSING: {sid}')
            continue
        try:
            card = read_json(path)
            if card.get('strategy_id') != sid:
                raise ValueError(f'CARD_ID_MISMATCH: {card.get("strategy_id")}')
            stamped = definition.stamp(card)
            if stamp_missing and stamped != card:
                write_json(path, stamped)
            built[sid] = definition.build(stamped)
        except (KeyError, ValueError) as exc:
            errors.append(f'{sid}: {exc}')
    return {'built': built, 'errors': errors}


RESULTS = OUT / 'results'
LABELS = ['REFERENCE_ONLY', 'RESEARCH_ONLY', 'NOT_CERTIFIED', 'NOT_A_TRIAL', 'NOT_OOS_PREVIOUSLY_VIEWED_STRATEGIES']
RANGES = {'1y': 1, '3y': 3, '5y': 5, '10y': 10, 'full': None}
INITIAL_CAPITAL = 1_000_000.0


DEPENDENCIES = ('core/backtest/metrics.py', 'core/backtest/costs.py', 'core/backtest/execution.py', 'core/backtest/run_store.py',
                'core/validation/bootstrap.py', 'core/validation/correlation.py', 'core/research/history/regime.py', 'core/research/history/coverage.py',
                'scripts/historical_report.py', 'scripts/historical_foundation.py')


def code_hashes() -> dict:
    """Every source file whose change can change a number: the analysis package, this script and the modules it computes with."""
    files = sorted((ROOT / 'core/research/intelligence').glob('*.py')) + [Path(__file__)] + [ROOT / f for f in DEPENDENCIES]
    return {p.relative_to(ROOT).as_posix(): sha256(p) for p in files}


def risk_numbers(sid: str, settings: dict) -> dict:
    """Stop loss, take profit and maximum holding days as the run read them: the strategy object under the pinned run settings."""
    import importlib
    from unittest.mock import patch
    from core.backtest import execution, runner
    from core.strategy_manager import StrategyManager
    out = {'max_holdings': runner.MAX_HOLDINGS, 'position_size': runner.POSITION_SIZE, 'fill_cap': execution.CAP}
    target = StrategyManager.CANONICAL_REGISTRY.get(sid)
    if not target:
        return out | {'risk_reason': 'NOT_RUN_BY_THE_BACKTEST_ENGINE'}
    module, cls = target.rsplit('.', 1)
    with patch('core.db_helper.get_setting', lambda key, default=None: settings.get(key, default)):
        strategy = getattr(importlib.import_module(module), cls)()
        return out | {'stop_loss': strategy.stop_loss, 'take_profit': strategy.take_profit, 'max_hold_days': strategy.max_hold_days,
                      'source': f'{target} properties under the pinned run settings'}


def _read_frame(path: Path, **kw):
    import pandas as pd
    return pd.read_csv(path, **kw) if path.is_file() and path.read_text(encoding='utf-8').strip() else None


def load_run(run_id: str) -> dict:
    import pandas as pd
    from core.backtest import run_store
    run_store.load_run(run_id, root=HISTORY_RUNS)  # manifest and file hashes verified, else it raises
    folder = HISTORY_RUNS / run_id
    record = read_json(folder / 'run.json')
    trades = _read_frame(folder / 'trades.csv', dtype={'stock_id': str})
    return {'run_id': run_id, 'record': record, 'manifest_sha256': sha256(folder / 'manifest.json'), 'nav': pd.read_csv(folder / 'nav.csv'),
            'trades': trades if trades is not None else pd.DataFrame(columns=['stock_id', 'buy_date', 'sell_date', 'profit_pct']),
            'actions': _read_frame(folder / 'corporate_actions.csv', dtype={'stock_id': str})}


def pick_run(sid: str, row: dict, snapshot_id: str | None = None) -> tuple[str | None, str]:
    """CURRENT when a stored run has the identity of the current code; else the newest ledger run on the same snapshot, IDENTITY_STALE."""
    if row['state'] == 'REUSE':
        return row['run_id'], 'CURRENT'
    if row['state'] not in ('RUN', 'FAILED'):
        return None, row['state']
    best = None
    for folder in sorted(HISTORY_RUNS.glob('2*')):
        record = read_json(folder / 'run.json', {})
        if record.get('strategy_id') == sid and record.get('accounting') == 'POSITION_LEDGER_CA_V1' and record.get('status') == 'SUCCESS' \
                and (snapshot_id is None or record.get('snapshot_id') == snapshot_id):
            best = folder.name
    return (best, 'IDENTITY_STALE') if best else (None, 'NO_STORED_RUN')


def load_context() -> dict:
    from core.research.history import regime
    from core.research.intelligence import profile
    from scripts import historical_foundation as hf
    from scripts import historical_report as hr
    from scripts import legacy_baseline_run as legacy
    evaluation = hf.latest_evaluation()
    snap = hf.snapshot(evaluation['snapshot_id'])
    marks, jumps = hr.event_marks(snap)
    bars = snap.frame('bars')
    bars['stock_id'] = bars.stock_id.astype(str)
    calendar = snap.frame('calendar')
    prof = profile.load(PROFILE)
    built = cards(stamp_missing=False)
    if built['errors']:
        raise ValueError(f"CARDS_INCOMPLETE: {built['errors'][:5]}")
    return {'evaluation': evaluation, 'bars': bars, 'bench': snap.frame('benchmark'), 'sessions': calendar[calendar.kind == 'TRADING'].date,
            'plan': {r['strategy_id']: r for r in hf.plan(evaluation)}, 'profile': prof,
            'profile_id': profile.check_frozen(prof, OUT / 'profile_freeze.json'),
            'regime_spec_id': regime.REGIME_SPEC_ID, 'settings': read_json(legacy.input_paths()['user_settings']), 'code': code_hashes(),
            'marks': marks, 'jumps': jumps,
            'engine_files': {k: v for k, v in identity_files().items() if k.startswith(('core/backtest/', 'scripts/history_engine', 'core/research/history/'))}}


def _performance(ctx, run, status) -> dict:
    import pandas as pd
    from core.research.history import regime
    from core.research.intelligence import performance as perf
    nav, trades, cost = run['nav'], run['trades'], run['record']['cost_model']
    nav_dates = pd.to_datetime(nav.date)
    if len(trades):  # holding sessions on the full run, so a trade bought before a clipped track keeps its real length
        sessions = pd.DatetimeIndex(nav_dates)
        trades = trades.assign(held_sessions=sessions.searchsorted(pd.to_datetime(trades.sell_date)) - sessions.searchsorted(pd.to_datetime(trades.buy_date)))
    span = perf.verified_span(status, sorted(set(nav_dates.dt.year)))
    verified = None
    if span:
        track = perf.slice_track(nav, *span)
        verified = perf.summarize(track, trades, ctx['bench'], cost)
    provisional = perf.summarize(nav, trades, ctx['bench'], cost)
    ranges = {}
    for name, years in RANGES.items():
        sub = nav if years is None else nav.iloc[-(years * 252 + 1):]
        if years is not None and len(nav) < years * 252 + 1:
            ranges[name] = {'reason': f'RANGE_LONGER_THAN_RUN: run has {len(nav) - 1} sessions, range needs {years * 252}'}
            continue
        yrs = sorted(set(pd.to_datetime(sub.date).dt.year))
        label = 'VERIFIED' if all(status.get(y) == 'VERIFIED' for y in yrs) else 'PROVISIONAL'
        s = perf.summarize(sub, trades, ctx['bench'], cost)
        ranges[name] = {'label': label, 'period': s['period'], **{k: s[k] for k in ('cagr', 'total_return', 'max_drawdown', 'sharpe', 'excess_cagr',
                                                                                     'benchmark_cagr_total_return', 'longest_recovery_sessions')}}
    provisional_only = {y: 'PARTIAL' if s == 'IDENTITY_STALE' else s for y, s in status.items()}  # stale years: PROVISIONAL basis only, never VERIFIED_ONLY
    regimes = regime.analyze(nav, trades, ctx['bench'], provisional_only)
    for year, row in regimes['years'].items():  # the evidence keeps the real label; PARTIAL was only the analysis basis
        row['trust'] = status.get(int(year), row['trust'])
    in_span = trades if not span else trades[pd.to_datetime(trades.sell_date).dt.year.between(*span)] if len(trades) else trades
    marks, jumps = ctx.get('marks', {}), ctx.get('jumps', {})
    events = {sid: marks.get(sid, pd.DatetimeIndex([])).union(jumps.get(sid, pd.DatetimeIndex([]))) for sid in set(marks) | set(jumps)}
    trade_stats = perf.trade_level(in_span, ctx['bars'], run['actions'], ctx['sessions'], events, cost) | {'track': 'VERIFIED' if span else 'PROVISIONAL'}
    bench = ctx['bench'].assign(date=pd.to_datetime(ctx['bench'].date)).set_index('date').reindex(nav_dates)
    base = nav.nav_base.astype(float)
    tri = bench.taiex_total_return.astype(float)
    series = {'date': nav.date.tolist(), 'nav_base': (base / base.iloc[0]).round(12).tolist(), 'nav_stress': (nav.nav_stress / nav.nav_stress.iloc[0]).round(12).tolist(),
              'benchmark_total_return': (tri / tri.iloc[0]).round(6).where(tri.notna(), None).tolist() if tri.notna().iloc[0] else None,
              'drawdown': (base / base.cummax() - 1).round(6).tolist()}
    method = ('VERIFIED track: the longest run of consecutive VERIFIED years clipped from the continuous run, starting from the previous close; '
              'positions held at that close are carried in, so it is a slice of one run, not a separate backtest started on that day.')
    return {'method': method, 'verified_span': list(span) if span else None, 'verified': verified, 'provisional': provisional, 'ranges': ranges,
            'regime': {k: regimes[k] for k in ('regime_spec_id', 'years', 'segments', 'regimes', 'rolling_252', 'regime_sessions', 'unlabelled_sessions')},
            'trades': trade_stats, 'series': series, 'cost_summary': run['record'].get('cost_summary'), 'turnover_annual': _turnover(run['record'])}


def _turnover(record) -> dict:
    value = (record.get('cost_summary') or {}).get('turnover_annual')
    return {'value': value, 'reason': None if value is not None else 'NOT_IN_RUN_RECORD',
            'definition': 'run.json cost_summary: traded value (entries, exits, open entries) / mean NAV / years, full run, PROVISIONAL'}


def _scenarios(ctx, run, status, stale=False) -> dict:
    import pandas as pd
    from core.research.intelligence import performance as perf
    from core.research.intelligence import scenarios as sc
    nav = run['nav'].assign(date=lambda x: pd.to_datetime(x.date))
    check = sc.sample_check(nav.date, ctx['sessions'], status)
    bench = ctx['bench'].assign(date=pd.to_datetime(ctx['bench'].date)).set_index('date').taiex_total_return
    floor = ctx['profile']['sample_rules']['min_closed_trades_for_trade_statistics']
    if check['status'] == 'SUFFICIENT':  # a mostly-cash track would only resample zeros: the frozen trade sample rule applies here too
        trades = run['trades']
        closed = int(pd.to_datetime(trades.sell_date).dt.year.between(*check['span']).sum()) if len(trades) else 0
        check = check | {'closed_trades_in_span': closed, 'min_closed_trades': floor}
        if closed < floor:
            check['status'] = 'INSUFFICIENT_SAMPLE'
    if check['status'] != 'SUFFICIENT':
        series = nav.set_index('date').nav_base
        episodes = sorted(perf.drawdown_episodes(series.index, series.tolist()), key=lambda e: e['depth'])[:10] if len(series) else []
        return {'status': 'IDENTITY_STALE' if stale else check['status'], 'check': check, 'notice': sc.NOTICE, 'historical': {
            'track': 'PROVISIONAL', 'drawdown_episodes': episodes, 'stress': sc.stress(series, bench),
            'period': [nav.date.min().strftime('%Y-%m-%d'), nav.date.max().strftime('%Y-%m-%d')]}}
    track = perf.slice_track(run['nav'], *check['span'])
    series = track.assign(date=pd.to_datetime(track.date)).set_index('date').nav_base.astype(float)
    returns = series.pct_change().dropna().to_numpy()
    return {'status': 'SUFFICIENT', 'check': check, 'period': [series.index[1].strftime('%Y-%m-%d'), series.index[-1].strftime('%Y-%m-%d')],
            'simulation': sc.simulate(returns), 'stress': sc.stress(series, bench),
            'historical': {'track': 'VERIFIED', 'drawdown_episodes': sorted(perf.drawdown_episodes(series.index, series.tolist()), key=lambda e: e['depth'])[:10]}}


def _feasibility(ctx, run) -> dict:
    from core.research.intelligence import feasibility as fz
    rec = run['record']
    rebuilt = fz.rebuild(run['nav'], run['trades'], run['actions'], rec.get('open_positions', []), ctx['bars'], rec['cost_model'], INITIAL_CAPITAL)
    liq = fz.liquidity(run['trades'], ctx['bars'])
    out = {'reconciliation': {k: v for k, v in rebuilt.items() if k != 'daily'}, 'positions': fz.position_summary(rebuilt), 'liquidity': liq,
           'capacity': fz.capacity(liq, INITIAL_CAPITAL, rebuilt['status'] == 'RECONCILED', min_trades=ctx['profile']['sample_rules']['min_closed_trades_for_trade_statistics']),
           'slippage_model': {'model': 'core/backtest/execution.py::apply_slippage', 'max': rec['cost_model'].get('slippage_max'),
                              'note': rec.get('cost_summary', {}).get('slippage')}, 'cost_model': rec['cost_model']}
    if rebuilt['status'] == 'RECONCILED':
        out['daily_holdings'] = {d['date']: d['holdings'] for d in rebuilt['daily']}
    return out


def _rel(path: Path) -> str:
    try:
        return path.relative_to(ROOT).as_posix()
    except ValueError:
        return str(path)


def _stage(job, sid, stage, folder, key_hashes, compute):
    """Checkpointed stage: REUSE an existing output of the same identity, never recompute a stage that hit the attempt limit."""
    path = folder / f"{sid.replace(':', '__')}.{stage}.json"
    key = f"{folder.name}|{sid}|{stage}"
    if path.is_file():
        job.start(sid, stage, key, key_hashes)
        job.done(sid, stage, _rel(path), reused=True)
        return read_json(path)
    if job.exhausted(key):
        job.blocked_by_attempts(sid, stage, key)
        return None
    job.start(sid, stage, key, key_hashes)
    try:
        try:
            value = compute()
        except MemoryError:  # RESOURCE_EXHAUSTED: release what this process holds and retry once (strategies already run one at a time)
            import gc
            gc.collect()
            job.row(sid, stage)['memory_retry'] = True
            value = compute()
    except Exception as exc:  # one stage of one strategy fails; the job continues with the rest
        job.fail(sid, stage, key, exc)
        return None
    write_json(path, value)
    job.done(sid, stage, _rel(path), reused=False)
    return value


def analyze_strategy(ctx, job, sid) -> dict | None:
    """One strategy; any error outside a stage is recorded as a failed 'load' row and the job continues."""
    from core.backtest.run_store import canonical_hash
    key = f"{sid}|load|{canonical_hash(ctx['code'])[:16]}"  # same code, same key: attempts add up across jobs
    if job.exhausted(key):
        job.blocked_by_attempts(sid, 'load', key)
        return None
    try:
        return _analyze(ctx, job, sid)
    except Exception as exc:
        job.start(sid, 'load', key, {})
        job.fail(sid, 'load', key, exc)
        return None


def trust_for(ctx, sid, run, identity_status) -> tuple[dict, dict]:
    """Per-year trust of this run: coverage matrix plus the run-level downgrades of the published report (one shared function).
    A run whose identity is not current has no VERIFIED year at all."""
    from core.research.intelligence import performance as perf
    cells = ctx['evaluation']['trust'][sid]
    if run is None:
        return perf.year_status(cells), {}
    if identity_status != 'CURRENT':
        return {int(c['year']): 'IDENTITY_STALE' for c in cells}, {'reason': 'IDENTITY_STALE'}
    from scripts import historical_report as hr
    end = ctx['plan'][sid].get('window', [None, run['record']['period'][1]])[1] or run['record']['period'][1]
    trust, exposure, impact = hr.run_trust(cells, run['nav'], run['trades'], run['actions'], end, ctx.get('marks', {}), ctx.get('jumps', {}))
    return trust, {'exposure': exposure, 'corporate_action_impact': impact}


def _analyze(ctx, job, sid) -> dict | None:
    from core.backtest.run_store import canonical_hash
    from core.research.intelligence import definition as definition_mod
    from core.research.intelligence import evaluate
    row = ctx['plan'][sid]
    run_id, identity_status = pick_run(sid, row, ctx['evaluation']['snapshot_id'])
    prof = ctx['profile']
    ptype = prof['strategies'][sid]['primary_type']
    card = read_json(card_path(sid))
    defin = definition_mod.build(card, numeric=risk_numbers(sid, ctx['settings']))
    run = load_run(run_id) if run_id else None
    status, adjustments = trust_for(ctx, sid, run, identity_status)
    ident = {'run_id': run_id, 'manifest_sha256': run['manifest_sha256'] if run else None, 'snapshot_id': ctx['evaluation']['snapshot_id'],
             'evaluation_id': ctx['evaluation']['evaluation_id'], 'regime_spec_id': ctx['regime_spec_id'], 'definition_id': defin['definition_id'],
             'profile_id': ctx['profile_id'], 'code': ctx['code'], 'state': row['state'], 'identity_status': identity_status}
    identity = 'SI1-' + canonical_hash(ident)[:16]
    folder = RESULTS / identity
    hashes = {'input_hash': canonical_hash({k: ident[k] for k in ('run_id', 'manifest_sha256', 'definition_id', 'profile_id')})[:16],
              'code_hash': canonical_hash(ctx['code'])[:16], 'data_hash': canonical_hash([ident['snapshot_id'], ident['evaluation_id']])[:16]}
    final = folder / f"{sid.replace(':', '__')}.json"
    from core.strategy_manager import StrategyManager
    meta = StrategyManager.STRATEGY_METADATA.get(sid)
    base = {'strategy_id': sid, 'display_name': meta.display_name_zh if meta else sid, 'family': row.get('family'),
            'identity': identity, 'identity_inputs': ident, 'labels': LABELS, 'certification': 'NOT_CERTIFIED',
            'definition': defin, 'profile': {'profile_id': ctx['profile_id'], 'primary_type': ptype, 'secondary_types': prof['strategies'][sid]['secondary_types'],
                                             'type_evidence': prof['strategies'][sid]['evidence'], 'type': prof['types'][ptype], 'targets': prof['targets'],
                                             'risk_budget': prof['risk_budget']},
            'year_status': {str(k): v for k, v in status.items()}, 'coverage_year_status': {str(c['year']): c['status'] for c in ctx['evaluation']['trust'][sid]},
            'trust_adjustments': adjustments}
    if run is None:
        for stage in ('performance', 'scenarios', 'feasibility'):
            job.skipped(sid, stage, f"{row['state']}: {row.get('reason')}")
        result = base | {'status': row['state'] if row['state'] in ('DATA_BLOCKED', 'GOVERNED_ONLY') else identity_status, 'reason': row.get('reason'), 'run': None}
        return _keep(final, result)
    rec = run['record']
    performance = _stage(job, sid, 'performance', folder, hashes, lambda: _performance(ctx, run, status))
    scenario = _stage(job, sid, 'scenarios', folder, hashes, lambda: _scenarios(ctx, run, status, identity_status != 'CURRENT'))
    feasible = _stage(job, sid, 'feasibility', folder, hashes, lambda: _feasibility(ctx, run))
    if performance is None or scenario is None or feasible is None:
        return None
    ectx = {'verified': performance['verified'], 'provisional': performance['provisional'],
            'trades': performance['trades'], 'liquidity': feasible['liquidity'], 'positions': feasible['positions'], 'capacity': feasible['capacity'],
            'scenarios_sample': scenario['check'], 'year_status': base['year_status'],
            'verified_span': performance['verified_span'], 'identity_status': identity_status, 'primary_type': ptype}
    result = base | {'status': 'ANALYZED' if identity_status == 'CURRENT' else 'PROVISIONAL_IDENTITY_STALE', 'reason': None,
                     'run': {'run_id': run_id, 'run_identity': rec['run_identity'], 'identity_status': identity_status, 'snapshot_id': rec['snapshot_id'],
                             'accounting': rec['accounting'], 'ledger_version': rec.get('ledger_version'), 'period': rec['period'],
                             'comparison_group': rec['comparison_group'], 'created_at': rec.get('created_at'), 'manifest_sha256': run['manifest_sha256'],
                             'engine': {'contract': rec['parameters'].get('execution_contract'), 'files': ctx['engine_files']}, 'cost_model': rec['cost_model'],
                             'benchmark': rec.get('benchmark')},
                     'performance': performance, 'scenarios': scenario, 'feasibility': {k: v for k, v in feasible.items() if k != 'daily_holdings'},
                     'dimensions': evaluate.dimensions(ectx, prof), 'health': evaluate.health(ectx, prof),
                     'versions': {'analysis_code': ctx['code'], 'regime_spec_id': ctx['regime_spec_id'], 'profile_id': ctx['profile_id'],
                                  'definition_id': defin['definition_id'], 'evaluation_id': ctx['evaluation']['evaluation_id']}}
    return _keep(final, result) | {'_holdings': feasible.get('daily_holdings')}


def _keep(path: Path, result: dict) -> dict:
    """Results are immutable: an existing file of the same identity is returned as stored, never rewritten."""
    if path.is_file():
        return read_json(path)
    write_json(path, result)
    return result


def _portfolio(ctx, job, results: dict) -> dict | None:
    import pandas as pd
    from core.backtest.run_store import canonical_hash
    from core.research.intelligence import performance as perf, portfolio
    members = []
    for sid, r in results.items():
        if r is None or r.get('run') is None:
            reason = 'ANALYSIS_FAILED' if r is None else f"{r['status']}:{r.get('reason')}"
            members.append({'strategy_id': sid, 'snapshot_id': None, 'accounting': None, 'identity_current': False, 'verified_returns': None, 'reason': reason})
            continue
        span = r['performance'].get('verified_span') if r['run']['identity_status'] == 'CURRENT' else None
        returns = None
        if span:
            s = r['performance']['series']
            nav = pd.DataFrame({'date': s['date'], 'nav_base': s['nav_base'], 'nav_stress': s['nav_stress']})
            track = perf.slice_track(nav, *span)
            returns = pd.Series(track.nav_base.astype(float).pct_change().to_numpy()[1:], index=pd.to_datetime(track.date)[1:])
        members.append({'strategy_id': sid, 'snapshot_id': r['run']['snapshot_id'], 'accounting': r['run']['accounting'],
                        'identity_current': r['run']['identity_status'] == 'CURRENT', 'verified_returns': returns, 'holdings': r.get('_holdings'),
                        'reason': None if r['run']['identity_status'] == 'CURRENT' else 'IDENTITY_STALE'})
    key = 'PF1-' + canonical_hash({'members': sorted((sid, (r or {}).get('identity')) for sid, r in results.items()), 'code': ctx['code']})[:16]
    folder = RESULTS / key
    bench = ctx['bench'].assign(date=pd.to_datetime(ctx['bench'].date)).set_index('date').taiex_total_return.astype(float).pct_change()
    return _stage(job, 'PORTFOLIO', 'portfolio', folder, {'input_hash': key}, lambda: portfolio.analyze(members, bench) | {'identity': key})


def run_job(job_id=None, only=None) -> int:
    from core.research.intelligence import jobstate
    job_id = job_id or datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ') + '-' + os.urandom(3).hex()
    job = jobstate.Job(OUT, job_id)
    try:
        ctx = load_context()
    except Exception as exc:  # nothing can be analysed without the context: the whole job fails with its class
        job.start('ALL', 'load_context', f'{job_id}|load', {})
        job.fail('ALL', 'load_context', f'{job_id}|load', exc)
        overall = job.finish()
        print(json.dumps({'command': 'run', 'status': overall, 'job_id': job_id, 'error': job.state['rows']['ALL|load_context']['last_error']}), flush=True)
        return jobstate.EXIT[overall]
    results = {}
    for sid in catalog_ids():
        if only and sid not in only:
            continue
        results[sid] = analyze_strategy(ctx, job, sid)
        print(json.dumps({'strategy_id': sid, 'strategy_status': job.per_strategy().get(sid)}), flush=True)
    folio = _portfolio(ctx, job, results) if not only else None
    overall = job.finish()
    latest = {'job_id': job_id, 'finished_at': job.state['finished_at'], 'status': overall, 'profile_id': ctx['profile_id'],
              'snapshot_id': ctx['evaluation']['snapshot_id'], 'strategies': {sid: r['identity'] for sid, r in results.items() if r},
              'failed': {sid: job.per_strategy().get(sid) for sid, r in results.items() if r is None},
              'portfolio': folio['identity'] if folio else None}
    if not only:
        write_json(OUT / 'jobs' / f'{job_id}.latest.json', latest)
        if overall in ('COMPLETE', 'PARTIAL') and not latest_problems(latest, ctx.get('code')) and ctx.get('code') in (None, code_hashes()):
            write_json(OUT / 'LATEST.json', latest)
    print(json.dumps({'command': 'run', 'status': 'DONE' if overall == 'COMPLETE' else overall, 'job_id': job_id, 'exit_code': jobstate.EXIT[overall],
                      'strategies': job.per_strategy()}, ensure_ascii=False), flush=True)
    return jobstate.EXIT[overall]


def latest_problems(latest: dict, code: dict | None = None) -> list[str]:
    """LATEST.json switches only when every result it points to exists and carries the identity it is filed under."""
    problems, code = [], code or code_hashes()  # the job's own code; tables() checks against the code on disk now
    for sid, ident in latest['strategies'].items():
        stored = read_json(RESULTS / ident / f"{sid.replace(':', '__')}.json")
        if not stored or stored.get('identity') != ident or stored.get('strategy_id') != sid:
            problems.append(f'{sid}:{ident}')
        elif stored.get('identity_inputs', {}).get('code') != code:
            problems.append(f'{sid}:{ident}:CODE_CHANGED_SINCE_RESULT')
    if latest.get('portfolio') and not (RESULTS / latest['portfolio'] / 'PORTFOLIO.portfolio.json').is_file():
        problems.append(f"PORTFOLIO:{latest['portfolio']}")
    return problems


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument('command', choices=['baseline', 'governance', 'cards', 'run'])
    parser.add_argument('--workers', type=int, default=1)  # batch_job passes it; the analysis runs strategies one at a time
    parser.add_argument('--only', nargs='*')
    parser.add_argument('--job-id')
    args = parser.parse_args(argv)
    if args.command == 'run':
        return run_job(args.job_id, args.only)
    if args.command == 'cards':
        result = cards()
        summary = {sid: {k: v['status'] for k, v in d['fields'].items() if v['status'] != 'TRACED'} for sid, d in result['built'].items()}
        print(json.dumps({'cards': len(result['built']), 'errors': result['errors'], 'not_traced': summary}, ensure_ascii=False, indent=1))
        return 0 if not result['errors'] and len(result['built']) == len(catalog_ids()) else 1
    if args.command == 'baseline':
        value = baseline()
        print(json.dumps({'effective_trials': value['effective_trials'], 'identity_files': len(value['identity_files']),
                          'history_run_files': len(value['history_runs'])}))
        return 0
    diffs = governance_diff()
    print(json.dumps({'status': 'PASS' if not diffs else 'FAIL', 'differences': diffs}, ensure_ascii=False))
    return 0 if not diffs else 1


if __name__ == '__main__':
    raise SystemExit(main())
