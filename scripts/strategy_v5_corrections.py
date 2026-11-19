"""V5: measure the execution-defect corrections on the pinned pre-2026 snapshot. Research only, not a formal trial.

python -m scripts.strategy_v5_corrections facts | register | smoke | run [strategy ...] | report
Writes only under outputs/strategy_v5. Production strategy files, the engine, config and V2/V4 evidence are read, never written.
"""
from __future__ import annotations

import hashlib
import inspect
import json
import os
import subprocess
import sys
import time
import uuid
from contextlib import ExitStack, contextmanager
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'outputs/strategy_v5'
RUNS = OUT / 'runs'
REGISTER = OUT / 'register.json'
SCOPE = ('aroon_trend', 'bias_reversion', 'bollinger_reversion', 'contrarian_pullback', 'defensive_low_volatility', 'kd_oversold',
         'long_term_trend', 'macd_cross', 'mad_distance', 'mean_reversion', 'regression_momentum', 'trend_following',
         'volatility_adjusted_momentum', 'volume_surge')
REPLAY = 'mad_distance'  # one original rerun from scratch: proves the stored originals still replay under today's DB-side inputs
LABELS = ['RESEARCH_ONLY', 'NOT_CERTIFIED', 'NOT_A_TRIAL', 'NOT_OOS_PREVIOUSLY_VIEWED_STRATEGIES']
EFFECTS = [('budget', ('original', 'LOT_ENGINE_V1'), ('original', 'LOT_ODD')),
           ('rule', ('original', 'LOT_ENGINE_V1'), ('corrected', 'LOT_ENGINE_V1')),
           ('joint', ('original', 'LOT_ENGINE_V1'), ('corrected', 'LOT_ODD'))]
MIN_TRADES = 30
VERDICT_RULE = ('Common VERIFIED window of all cells of the strategy. CORRECTED_BETTER when the treated cell has a higher double-cost '
                'annualised return than the comparison cell and that return is above zero; CORRECTED_NOT_BETTER otherwise; NOT_EVALUABLE '
                f'when either cell has fewer than {MIN_TRADES} closed trades, is not publishable, is missing or has no defined value.')
ODD_LOT_SUBWINDOW = (2021, 2023)
ODD_LOT_NOTE = 'DIAGNOSTIC_ONLY: intraday odd-lot trading started 2020-10; the engine fills odd lots at board-lot price and volume'
BOUND_FILES = ('scripts/strategy_v5_rules.py', 'scripts/strategy_v4_ranking.py', 'core/research/strategy_v4.py')
KNOWN_GOVERNANCE_DIFFERENCE = 'intelligence_identity_files changed or missing: core/backtest/run_store.py'


def write(path, data):
    from services.web_dashboard import clean
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(clean(data), ensure_ascii=False, indent=1, allow_nan=False, default=str), encoding='utf-8')


def _sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def matrix(strategies, corrected):
    lots = ('LOT_ENGINE_V1', 'LOT_ODD')
    return [{'strategy_id': s, 'rules': r, 'lot_rule': lot} for s in strategies for r in ('original', 'corrected')
            if r == 'original' or s in corrected for lot in lots]


def write_register(path, cells, snapshot_id, verdict_rule):
    body = {'registered_at': datetime.now(timezone.utc).isoformat(), 'snapshot_id': snapshot_id, 'labels': LABELS,
            'verdict_rule': verdict_rule, 'verdict_code_sha256': hashlib.sha256(inspect.getsource(verdict).encode()).hexdigest(),
            'min_closed_trades': MIN_TRADES, 'cells': cells}
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(body, ensure_ascii=False, indent=1), encoding='utf-8')
    path.with_suffix('.sha256').write_text(_sha(path) + '\n', encoding='ascii')
    return body


def key(cell):
    return cell['strategy_id'], cell['rules'], cell['lot_rule']


def require_registered(path, cell):
    path = Path(path)
    if _sha(path) != path.with_suffix('.sha256').read_text(encoding='ascii').strip():
        raise ValueError('REGISTER_CHANGED')
    for registered in json.loads(path.read_text(encoding='utf-8'))['cells']:
        if key(registered) == key(cell):
            return registered
    raise ValueError(f'UNREGISTERED_CELL {key(cell)}')


def verdict(base, treated, min_trades=MIN_TRADES):
    def stress(s):
        return ((s or {}).get('cagr_stress') or {}).get('value')
    if base is None or treated is None:
        return {'status': 'NOT_EVALUABLE', 'reason': 'MISSING_OR_NOT_PUBLISHABLE_OR_NO_COMMON_VERIFIED_SPAN'}
    if min(base['closed_trades'], treated['closed_trades']) < min_trades:
        return {'status': 'NOT_EVALUABLE', 'reason': f'FEWER_THAN_{min_trades}_CLOSED_TRADES'}
    a, b = stress(base), stress(treated)
    if a is None or b is None:
        return {'status': 'NOT_EVALUABLE', 'reason': 'UNDEFINED_DOUBLE_COST_RETURN'}
    return {'status': 'CORRECTED_BETTER' if b > a and b > 0 else 'CORRECTED_NOT_BETTER', 'reason': None}


def effect_verdicts(numbers, keys):
    """One verdict per effect the strategy has; a registered cell without numbers yields NOT_EVALUABLE, never a skipped effect."""
    return {name: verdict(numbers.get(a), numbers.get(b)) for name, a, b in EFFECTS if b in keys}


def common_span(trust, registered, years):
    """VERIFIED only in years every registered cell of the strategy calls VERIFIED; a missing cell leaves no span."""
    from core.research.intelligence import performance as perf
    if len(trust) != registered or not years:
        return None
    common = {y: 'VERIFIED' if all(t.get(y) == 'VERIFIED' for t in trust) else 'PARTIAL' for y in years}
    return perf.verified_span(common, years)


def cell_name(cell):
    return '__'.join(key(cell))


def bound_code():
    return {name: _sha(ROOT / name) for name in BOUND_FILES}


def expected_identity(cell, snapshot_id):
    from core.backtest.run_store import canonical_hash
    from scripts import platform_v2 as v2
    original = v2.identity(cell['parameters'], snapshot_id)
    if cell['rules'] == 'original':
        return original
    return 'RID5-' + canonical_hash({'original_identity': original, 'rules': cell['rules'], 'bound_code': bound_code()})[:24]


def is_done(cell):
    path = OUT / 'cells' / f'{cell_name(cell)}.json'
    return path.exists() and json.loads(path.read_text(encoding='utf-8')).get('run_identity') == cell['expected_identity']


@contextmanager
def _readonly_feature_cache():
    """Read V2's feature cache, compute misses in memory, verify on private copies: V5 never writes outputs/platform_v2 (V4 pattern)."""
    import shutil
    from unittest.mock import patch
    from core.research.platform import feature_cache
    private = OUT / 'cache_verification'
    original_verify = feature_cache.verify

    def attach(data, cache_key):
        if not feature_cache.enabled(cache_key) or not feature_cache.enabled(cache_key, root=private):
            return False
        source = data.cross_section

        def cached(day):
            if day not in data._cache:
                path = feature_cache._path(feature_cache.ROOT, cache_key, day)
                data._cache[day] = pd.read_pickle(path) if path.exists() else source(day)
            return data._cache[day]
        data.cross_section, data.feature_cache_original = cached, source
        return True

    def verify(data, cache_key, dates):
        for day in dates:
            source = feature_cache._path(feature_cache.ROOT, cache_key, day)
            if source.exists():
                target = feature_cache._path(private, cache_key, day)
                target.parent.mkdir(parents=True, exist_ok=True)
                temp = target.with_name(f'{target.name}.{uuid.uuid4().hex}.tmp')
                shutil.copyfile(source, temp)
                os.replace(temp, target)  # parallel cells copy the same sample; a reader never sees a half-written file
        return original_verify(data, cache_key, dates, root=private)

    with patch.object(feature_cache, 'attach', attach), patch.object(feature_cache, 'verify', verify):
        yield


def _reusable(rid, root):
    from scripts import platform_v2 as v2
    found = v2.lookup(rid, root)
    if found and json.loads((Path(root) / found / 'run.json').read_text(encoding='utf-8')).get('publishable'):
        return found
    return None


def run_cell(cell, snapshot_id):
    """One registered cell in this process. A stored publishable run with the registered identity is reused."""
    from core.backtest import run_store
    from scripts import platform_v2 as v2
    from scripts import strategy_v5_rules as rules
    from scripts.platform_v2_engine import execute
    cell = require_registered(REGISTER, cell)
    params, started = cell['parameters'], datetime.now(timezone.utc)
    rid = expected_identity(cell, snapshot_id)
    if rid != cell['expected_identity']:
        raise RuntimeError(f"IDENTITY_DRIFT registered {cell['expected_identity']} now {rid}: code changed after registration")
    reuse_roots = [v2.RUNS, RUNS] if cell['rules'] == 'original' and params['rule'] == 'LOT_ENGINE_V1' else [RUNS]
    root, run_id = next(((r, f) for r in reuse_roots if (f := _reusable(rid, r))), (RUNS, None))
    if not run_id:
        run_id = run_store.new_run_id()
        try:
            with ExitStack() as stack:
                stack.enter_context(_readonly_feature_cache())
                entry = stack.enter_context(rules.applied(params['strategy_id'])) if cell['rules'] == 'corrected' else None
                result = execute(params['strategy_id'], snapshot_id, params['start'], params['end'], params['capital'], params['costs'], params['rule'])
            record, frames = v2.build_record(params, rid, run_id, snapshot_id, result)
            record.update(strategy_type=f"strategy_v5_{cell['rules']}", research_version='V5', labels=LABELS, cell=cell_name(cell),
                          original_identity=v2.identity(params, snapshot_id), register_sha256=_sha(REGISTER), bound_code=bound_code(),
                          correction={'defect': entry['defect'], 'hook': entry['hook']} if entry else None)
            run_store.save_run(record, frames, root=RUNS, run_id=run_id)
        except Exception as error:
            run_store.save_failure({'strategy_id': params['strategy_id'], 'run_identity': rid, 'parameters': params, 'snapshot_id': snapshot_id}, error, root=RUNS)
            raise
    timing = {'cell': cell_name(cell), 'run_id': run_id, 'root': Path(root).relative_to(ROOT).as_posix(), 'run_identity': rid,
              'started_at': started.isoformat(), 'finished_at': datetime.now(timezone.utc).isoformat(),
              'seconds': round((datetime.now(timezone.utc) - started).total_seconds(), 1)}
    write(OUT / 'cells' / f'{cell_name(cell)}.json', timing)
    return timing


def _worker(cell, snapshot_id):
    try:
        return {'state': 'DONE'} | run_cell(cell, snapshot_id)
    except Exception as error:
        return _failed(cell, error)


def _failed(cell, error):
    import traceback
    detail = {'error': f'{type(error).__name__}: {error}', 'traceback': traceback.format_exc(), 'at': datetime.now(timezone.utc).isoformat()}
    write(OUT / 'failures' / f'{cell_name(cell)}.json', detail)
    return {'state': 'FAILED', 'cell': cell_name(cell), 'reason': detail['error'][:300]}


def run(cells, snapshot_id, workers=3):
    """Each cell in a fresh spawned process, so a correction's patch never outlives its cell; one crash fails only its cell."""
    import multiprocessing
    from concurrent.futures import ProcessPoolExecutor
    from scripts import historical_foundation as hf
    rows = []
    with ProcessPoolExecutor(max_workers=workers, mp_context=multiprocessing.get_context('spawn'), max_tasks_per_child=1) as pool:
        futures = []
        for cell in cells:
            hf._wait_for_memory()
            futures.append((cell, pool.submit(_worker, cell, snapshot_id)))
        for cell, future in futures:
            try:
                rows.append(future.result())
            except Exception as error:  # BrokenProcessPool and the like: record this cell, keep the rest
                rows.append(_failed(cell, error))
            print(json.dumps(rows[-1], ensure_ascii=False), flush=True)
    return rows


def _locked(fn):
    from core.research.reference import batch_job
    from core.research.sources.twse import _exclusive
    with _exclusive(batch_job.runner.OUT / 'jobs/start.lock', timeout=30):
        if batch_job.runner_pids():
            raise RuntimeError('OTHER_RESEARCH_SESSION_ACTIVE')
        return fn()


def _snapshot():
    """The V2 governance check must match, except the one difference known before V5 started: run_store.py gained a file-hash cache
    on 2026-11-16. Allowed only while run_store.py keeps the bytes recorded in the V5 baseline; rebaselining is the Owner's call."""
    from scripts import historical_foundation as hf
    from scripts import platform_v2 as v2
    gov = v2.governance(write=False)
    pinned = json.loads((OUT / 'baseline/protected_sha256.json').read_text(encoding='utf-8'))['core/backtest/run_store.py']
    known = gov['differences'] == [KNOWN_GOVERNANCE_DIFFERENCE] and _sha(ROOT / 'core/backtest/run_store.py') == pinned
    if gov['status'] != 'MATCH' and not known:
        raise RuntimeError(f"GOVERNANCE_BASELINE_MISMATCH {gov['differences'][:5]}")
    write(OUT / 'governance.json', gov | {'v5_exception': None if gov['status'] == 'MATCH' else KNOWN_GOVERNANCE_DIFFERENCE})
    return hf.latest_evaluation()['snapshot_id']


def _guard():
    done = subprocess.run([sys.executable, str(OUT / 'guard.py'), 'check'], cwd=ROOT, capture_output=True, text=True)
    print(done.stdout.strip(), flush=True)
    if done.returncode:
        raise RuntimeError('GUARD_FAILED')


def register():
    from scripts import platform_v2 as v2
    from scripts import strategy_v5_rules as rules
    if REGISTER.exists():
        raise RuntimeError('REGISTER_EXISTS: a register is written once; a new matrix is a new change')
    _guard()
    snapshot_id = _snapshot()
    params = {p['strategy_id']: p for p in v2.baseline_params() if p['strategy_id'] in SCOPE}
    missing = sorted(set(SCOPE) - set(params))
    if missing:
        raise RuntimeError(f'NO_BASELINE_PARAMETERS {missing}')
    cells = matrix(SCOPE, set(rules.CORRECTIONS)) + [{'strategy_id': REPLAY, 'rules': 'replay', 'lot_rule': 'LOT_ENGINE_V1'}]
    cells = [c | {'parameters': params[c['strategy_id']] | {'rule': c['lot_rule']}, 'snapshot_id': snapshot_id} for c in cells]
    cells = [c | {'expected_identity': expected_identity(c, snapshot_id)} for c in cells]
    write_register(REGISTER, cells, snapshot_id, VERDICT_RULE)
    print(f"registered {len(cells)} cells on {snapshot_id}, sha256 {_sha(REGISTER)}, bound code {bound_code()}")


def facts():
    """Tasks 1.3, 2.2, 2.3: reusable originals, the volume distribution behind E10, and why stored originals left buys unfilled."""
    from core.backtest import run_store
    from scripts import platform_v2 as v2
    from scripts import strategy_intelligence as si
    snapshot_id = _snapshot()
    reuse, unfilled = {}, {}
    for p in v2.baseline_params():
        if p['strategy_id'] not in SCOPE:
            continue
        found = v2.lookup(v2.identity(p, snapshot_id))
        reuse[p['strategy_id']] = found
        if found:
            events = run_store.read_frame(run_store.load_run(found, root=v2.RUNS), 'execution_events').get('base', [])
            unfilled[p['strategy_id']] = pd.Series([e.get('reason', 'UNSPECIFIED') for e in events if e.get('status') == 'UNFILLED'
                                                    and e.get('side') == 'BUY'], dtype=object).value_counts().to_dict()
    bars = si.load_context()['bars']
    years = pd.to_datetime(bars.trade_date).dt.year
    vol = pd.to_numeric(bars.loc[years.between(2017, 2023) & bars.status.eq('TRADED'), 'volume'], errors='coerce').dropna()
    volume = {'unit_note': 'TWSE trading volume in shares; Config documents 5,000,000 as 500萬股', 'stock_days': int(len(vol)),
              'share_above': {str(t): float((vol > t).mean()) for t in (300, 500, 3_000_000, 5_000_000)},
              'quantiles': {str(q): float(vol.quantile(q)) for q in (0.01, 0.1, 0.5, 0.9)}}
    out = {'snapshot_id': snapshot_id, 'reusable_originals': reuse, 'buy_unfilled_reasons': unfilled, 'volume_2017_2023': volume}
    write(OUT / 'facts.json', out)
    print(json.dumps(out, ensure_ascii=False, indent=1, default=str))


def smoke():
    """Task 4.2: one short cell per lot rule and one corrected cell, outside the register and the report."""
    from core.backtest import run_store
    from scripts import platform_v2 as v2
    from scripts import strategy_v5_rules as rules
    from scripts.platform_v2_engine import execute
    snapshot_id = _snapshot()
    base = v2.defaults() | {'strategy_id': 'mad_distance', 'start': '2023-01-03', 'end': '2023-02-24'}
    rows = []
    for rule, corrected in (('LOT_ENGINE_V1', False), ('LOT_ODD', False), ('LOT_ODD', True)):
        params, started = base | {'rule': rule}, time.time()
        with ExitStack() as stack:
            stack.enter_context(_readonly_feature_cache())
            if corrected:
                stack.enter_context(rules.applied('mad_distance'))
            result = execute(params['strategy_id'], snapshot_id, params['start'], params['end'], params['capital'], params['costs'], rule)
        record, frames = v2.build_record(params, 'SMOKE', run_store.new_run_id(), snapshot_id, result)
        rows.append({'rule': rule, 'corrected': corrected, 'publishable': record['publishable'], 'reasons': record['not_publishable_reasons'],
                     'fills': len(frames['fills']), 'seconds': round(time.time() - started, 1)})
        print(json.dumps(rows[-1]), flush=True)
    write(OUT / 'smoke.json', rows)


def _cell_numbers(loaded, ctx, window, label=None):
    from core.backtest import run_store
    from core.research.intelligence import performance as perf
    from scripts.strategy_v4 import bundle
    if not window or window[0] > window[1]:
        return None
    run, _, _ = bundle(loaded, ctx)
    track = perf.slice_track(run['nav'], *window)
    if track is None or track.empty:
        return None
    summary = perf.summarize(track, run['trades'], ctx['bench'], run['record']['cost_model'])
    fills = run_store.read_frame(loaded, 'fills')
    dates = pd.to_datetime(track.date)
    inside = fills[pd.to_datetime(fills.date).gt(dates.iloc[0]) & pd.to_datetime(fills.date).le(dates.iloc[-1])] if len(fills) else fills
    years = max((dates.iloc[-1] - dates.iloc[0]).days / 365.25, 1 / 365.25)
    bench = ctx['bench'].assign(date=pd.to_datetime(ctx['bench'].date)).set_index('date').taiex_total_return.reindex(dates)
    return {'window': list(window), 'label': label, 'cagr': summary['cagr'], 'cagr_stress': summary['cagr_stress'],
            'max_drawdown': summary['max_drawdown'], 'closed_trades': summary['closed_trades'], 'fills': int(len(inside)),
            'annual_turnover': perf.v(float(pd.to_numeric(inside.notional).sum() / track.nav_base.mean() / years) if len(inside) else 0.),
            'benchmark_total_return': perf.v(float(bench.iloc[-1] / bench.iloc[0] - 1) if bench.notna().all() else None, 'BENCHMARK_MISSING')}


def _replay_check(stored, fresh):
    import numpy as np
    from core.backtest import run_store
    a, b = (run_store.read_frame(x, 'nav') for x in (stored, fresh))

    def same(name):
        ta, tb = (run_store.read_frame(x, name) for x in (stored, fresh))
        cols = [c for c in ('stock_id', 'buy_date', 'sell_date', 'shares', 'buy_price', 'sell_price') if c in ta and c in tb]
        return len(ta) == len(tb) and ta[cols].astype(str).reset_index(drop=True).equals(tb[cols].astype(str).reset_index(drop=True))
    same_trades = same('trades') and same('trades_stress')
    same_nav = len(a) == len(b) and (a.date.astype(str).values == b.date.astype(str).values).all() and \
        np.allclose(a[['nav_base', 'nav_stress']].astype(float), b[['nav_base', 'nav_stress']].astype(float), rtol=1e-9, atol=1e-12)
    return {'status': 'MATCH' if same_trades and same_nav else 'DIFFERENT', 'trades_equal': bool(same_trades), 'nav_equal': bool(same_nav),
            'stored_run': stored['run']['run_id'], 'fresh_run': fresh['run']['run_id']}


def report():
    from core.backtest import run_store
    from scripts import strategy_intelligence as si
    from scripts.platform_v2_ledger_audit import audit_run
    from scripts.strategy_v4 import bundle
    reg = json.loads(REGISTER.read_text(encoding='utf-8'))
    require_registered(REGISTER, reg['cells'][0])
    ctx = si.load_context()
    if ctx['profile']['sample_rules']['min_closed_trades_for_trade_statistics'] != MIN_TRADES:
        raise RuntimeError('PROFILE_MIN_TRADES_CHANGED')
    loaded, status = {}, {}
    for cell in reg['cells']:
        path = OUT / 'cells' / f'{cell_name(cell)}.json'
        timing = json.loads(path.read_text(encoding='utf-8')) if path.exists() else None
        if not timing:
            status[key(cell)] = 'MISSING'
            continue
        if timing['run_identity'] != cell['expected_identity']:
            status[key(cell)] = 'IDENTITY_MISMATCH'
            continue
        run = run_store.load_run(timing['run_id'], root=ROOT / timing['root'])
        audit = audit_run(run['dir'])
        write(OUT / 'acceptance' / f'ledger_{cell_name(cell)}.json', audit)
        good = run['run']['publishable'] and all(t['status'] == 'PUBLISHABLE' for t in audit['tracks'].values())
        loaded[key(cell)], status[key(cell)] = run, 'PUBLISHABLE' if good else 'NOT_PUBLISHABLE'
    rows = []
    for sid in SCOPE:
        cells = [c for c in reg['cells'] if c['strategy_id'] == sid and c['rules'] != 'replay']
        keys = {(c['rules'], c['lot_rule']) for c in cells}
        trust = [bundle(loaded[(sid, *k)], ctx)[1] for k in keys if (sid, *k) in loaded]
        years = sorted(set().union(*trust)) if trust else []
        span = common_span(trust, len(cells), years)
        sub = (max(span[0], ODD_LOT_SUBWINDOW[0]), min(span[1], ODD_LOT_SUBWINDOW[1])) if span else None
        good = {k for k in keys if status.get((sid, *k)) == 'PUBLISHABLE'}
        full = {k: _cell_numbers(loaded[(sid, *k)], ctx, span, ODD_LOT_NOTE if k[1] == 'LOT_ODD' and span and span[0] < 2021 else None) for k in good}
        later = {k: _cell_numbers(loaded[(sid, *k)], ctx, sub) for k in good}
        rows.append({'strategy_id': sid, 'common_verified_span': span, 'subwindow': sub,
                     'cell_status': {'/'.join(k): status.get((sid, *k)) for k in sorted(keys)},
                     'cells': {'/'.join(k): full.get(k) for k in sorted(keys)}, 'cells_2021_2023': {'/'.join(k): later.get(k) for k in sorted(keys)},
                     'verdicts': effect_verdicts(full, keys), 'verdicts_2021_2023': effect_verdicts(later, keys)})
    replay = next(c for c in reg['cells'] if c['rules'] == 'replay')
    stored = (REPLAY, 'original', 'LOT_ENGINE_V1')
    check = _replay_check(loaded[stored], loaded[key(replay)]) if stored in loaded and key(replay) in loaded else {'status': 'NOT_RUN'}
    gov = json.loads((OUT / 'governance.json').read_text(encoding='utf-8'))
    out = {'labels': LABELS, 'register_sha256': _sha(REGISTER), 'snapshot_id': reg['snapshot_id'], 'verdict_rule': reg['verdict_rule'],
           'cells_registered': len(reg['cells']), 'cells_publishable': sum(v == 'PUBLISHABLE' for v in status.values()),
           'cell_status': {cell_name({'strategy_id': k[0], 'rules': k[1], 'lot_rule': k[2]}): v for k, v in status.items()},
           'replay_check': check, 'governance': {'status': gov['status'], 'differences': gov['differences'], 'v5_exception': gov.get('v5_exception')},
           'odd_lot_note': ODD_LOT_NOTE, 'generated_at': datetime.now(timezone.utc).isoformat(),
           'runner_sha256_at_report': _sha(Path(__file__)), 'strategies': rows}
    write(OUT / 'report.json', out)
    (OUT / 'V5_ACCEPTANCE_REPORT.md').write_text(markdown(json.loads((OUT / 'report.json').read_text(encoding='utf-8'))), encoding='utf-8')
    print(f"report: {out['cells_publishable']}/{out['cells_registered']} publishable, replay {check['status']}")
    return out


VERDICT_ZH = {'CORRECTED_BETTER': '修正後較好', 'CORRECTED_NOT_BETTER': '沒有較好', 'NOT_EVALUABLE': '無法判定'}
CELL_ZH = {'original/LOT_ENGINE_V1': '原版・整張', 'original/LOT_ODD': '原版・零股', 'corrected/LOT_ENGINE_V1': '修正・整張', 'corrected/LOT_ODD': '修正・零股'}
OWNER_ITEMS = [
    'contrarian_pullback 的分數混用新台幣與百分比，但策略寫明依原 notebook 公式，是否改寫要你決定。',
    'V36 到 V38 的成交量門檻 500 與 300 股在 2017 到 2023 年讓 100% 的交易日通過，等於沒有作用；原意的單位沒有記載。',
    'hybrid_trend_rank 沒有定義排序；institutional_flow_confirmation 的籌碼出場在回測中永遠不會觸發（需要新的引擎身份）。',
    'V2 研究治理基線與目前程式只差 core/backtest/run_store.py（11-16 加入雜湊快取）；是否重建治理基線由你決定。',
    '是否把任何修正升為正式版：這是看過歷史資料的開發研究，升級前需要另開變更與樣本外驗證。',
    'core/calc_indicators.py 有一行未提交的變更：把 11-17 清理時刪掉的未使用匯入還原成 b264e7d 的內容。這個檔案的雜湊是 V2 特徵快取的鍵值，'
    '刪掉那行會讓所有回測重算 2,577 天的特徵（實測一格超過 5 小時）。請一併提交；若捨棄，快取鍵值會再次改變。',
    '偏離登錄規則：登錄前執行過 3 次冒煙測試（2023-01-03 到 2023-02-24，未存成 Run，不在報表內）。'
    '第一版冒煙測試在唯讀快取加入前，往 outputs/platform_v2/feature_cache/FC1-2dcee7870eb5702f 寫入 31 天快取檔；這些是快取，未刪除，可由你決定是否清掉。',
]


def _pct(metric):
    value = (metric or {}).get('value')
    return '—' if value is None else f'{value * 100:+.1f}%'


def markdown(rep):
    lines = ['# V5 策略執行缺陷修正：驗收報表', '', f"> {' / '.join(rep['labels'])}。已看過歷史資料的開發研究，不是樣本外證據，也不是投資建議。", '',
             f"產生時間 {rep['generated_at'][:19]}Z。快照 `{rep['snapshot_id']}`。登錄檔 SHA-256 `{rep['register_sha256'][:16]}…`。",
             f"登錄 {rep['cells_registered']} 格，可發布 {rep['cells_publishable']} 格。重播對照：{rep['replay_check']['status']}。", '']
    counts = {}
    for row in rep['strategies']:
        for effect, v in row['verdicts'].items():
            counts.setdefault(effect, {}).setdefault(v['status'], 0)
            counts[effect][v['status']] += 1
    lines += ['## 判定總表（共同可信期間）', '', '| 效果 | 修正後較好 | 沒有較好 | 無法判定 |', '|---|---:|---:|---:|']
    for effect, zh in (('budget', '預算（零股 對 整張）'), ('rule', '規則（修正 對 原版，整張）'), ('joint', '合併（修正零股 對 原版整張）')):
        c = counts.get(effect, {})
        lines.append(f"| {zh} | {c.get('CORRECTED_BETTER', 0)} | {c.get('CORRECTED_NOT_BETTER', 0)} | {c.get('NOT_EVALUABLE', 0)} |")
    lines += ['', '判定規則：兩倍成本年化報酬比對照格高，而且大於 0，才算「修正後較好」。任一格平倉少於 30 筆、不可發布或缺少數值時為「無法判定」。不以 10% 目標判定。',
              '共同可信期間是同一策略所有格子可信年度的交集，可能比策略智能平台或 V4 報表的期間短，數字不可跨報表直接比較。', '',
              '## 逐策略結果', '']
    for row in rep['strategies']:
        span = row['common_verified_span']
        lines += [f"### {row['strategy_id']}", '', f"共同可信期間：{span[0]}–{span[1]}" if span else '共同可信期間：無', '',
                  '| 格子 | 年化 | 兩倍成本年化 | 最大回撤 | 年換手 | 平倉筆數 | 同期大盤累計 | 狀態 |', '|---|---:|---:|---:|---:|---:|---:|---|']
        for name, n in row['cells'].items():
            status = row['cell_status'].get(name)
            if n:
                turnover = n['annual_turnover']['value']
                lines.append(f"| {CELL_ZH.get(name, name)} | {_pct(n['cagr'])} | {_pct(n['cagr_stress'])} | {_pct(n['max_drawdown'])} | "
                             f"{'—' if turnover is None else f'{turnover:.1f} 倍'} | {n['closed_trades']} | {_pct(n['benchmark_total_return'])} | {'診斷' if n['label'] else status} |")
            else:
                lines.append(f"| {CELL_ZH.get(name, name)} | — | — | — | — | — | — | {status} |")
        verdicts = '；'.join(f"{e}：{VERDICT_ZH[v['status']]}" + (f"（{v['reason']}）" if v['reason'] else '') for e, v in row['verdicts'].items())
        later = '；'.join(f"{e}：{VERDICT_ZH[v['status']]}" for e, v in row['verdicts_2021_2023'].items())
        odd = span and span[0] < 2021 and any(e in row['verdicts'] for e in ('budget', 'joint'))
        best = max(((n['cagr']['value'], CELL_ZH.get(k, k)) for k, n in row['cells'].items() if n and n['cagr']['value'] is not None), default=None)
        gap = f"最高年化 {best[0] * 100:+.1f}%（{best[1]}），距 5% {(0.05 - best[0]) * 100:.1f} 個百分點、距 10% {(0.10 - best[0]) * 100:.1f} 個百分點（僅供參考，不是判定）" if best else '沒有可用的年化數值'
        lines += ['', f'判定：{verdicts}' + ('。預算與合併判定含 2020-10 以前的零股，僅供診斷' if odd else ''), gap,
                  f"2021–2023 子區間（次要，未列入登錄的判定規則）：{later or '—'}", '']
    gov = rep['governance']
    lines += ['## 治理與對照', '', f"- 重播對照：重新執行 mad_distance 原版的回測引擎（每日特徵讀 V2 快取），與已存結果比較，狀態 {rep['replay_check']['status']}"
              f"（兩條成本軌的交易相同 {rep['replay_check'].get('trades_equal')}，淨值相同 {rep['replay_check'].get('nav_equal')}）。這證明資料庫端的持股上限輸入沒有改變已存原版。",
              f"- 研究治理：{gov['status']}，例外 {gov['v5_exception'] or '無'}。這個例外是本變更自行設定的，見需要你決定的第 4 項。",
              f"- 零股說明：{rep['odd_lot_note']}。整張規則本來就買 int(預算/股價) 股，兩種規則唯一的差別是整張規則會跳過「預算買不起一張」的股票。",
              '- 同期大盤是共同可信期間的累計報酬，其他欄位是年化。', '', '## 需要你決定的事項', '']
    lines += [f'{i}. {item}' for i, item in enumerate(OWNER_ITEMS, 1)]
    return '\n'.join(lines) + '\n'


def main(argv):
    command = argv[0] if argv else ''
    if command == 'register':
        _locked(register)
    elif command == 'facts':
        facts()
    elif command == 'smoke':
        _locked(smoke)
    elif command == 'run':
        reg = json.loads(REGISTER.read_text(encoding='utf-8'))
        todo = [c for c in reg['cells'] if not is_done(c) and (len(argv) < 2 or c['strategy_id'] in argv[1:])]
        _guard()
        snapshot_id = _snapshot()
        if snapshot_id != reg['snapshot_id']:
            raise RuntimeError('SNAPSHOT_CHANGED_AFTER_REGISTRATION')
        print(f'{len(todo)} cells to run', flush=True)
        rows = _locked(lambda: run(todo, snapshot_id, workers=int(os.environ.get('V5_WORKERS', '3'))))
        return 0 if all(r['state'] == 'DONE' for r in rows) else 2
    elif command == 'report':
        report()
    else:
        print(__doc__)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main(sys.argv[1:]))
