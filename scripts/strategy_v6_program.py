"""V6 research program: fixed targets and budget, an append-only hash-chained hypothesis queue, development runs, a single-use
holdout for at most two finalists, and a Traditional Chinese report. Research only.

python -m scripts.strategy_v6_program charter | admit <spec.json> | run <hypothesis> <variant> [holdout] | finalize | report | status
Writes only under outputs/strategy_v6. Strategy files, the engine, config and V2/V4/V5 evidence are read, never written.
"""
from __future__ import annotations

import hashlib
import inspect
import json
import sys
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'outputs/strategy_v6'
LABELS = ['RESEARCH_ONLY', 'NOT_CERTIFIED', 'NOT_A_FORMAL_TRIAL', 'NOT_OOS_PREVIOUSLY_VIEWED_DATA']
THRESHOLDS = {'base_cagr': 0.15, 'stress_cagr': 0.10, 'max_drawdown': -0.20, 'min_closed_trades': 30, 'min_years_without_trades': 5}
BUDGET = {'hypotheses': 8, 'variants': 2, 'finalists': 2, 'sourced_per_failure': 1}
WINDOWS = {'development': (2017, 2023), 'holdout': (2024, 2025)}
ETF_COSTS = {'buy': 0.001425 + 0.001, 'sell': 0.001425 + 0.001 + 0.001}  # fee + slippage; the sell side adds the 0.1% ETF tax
WEB_ALLOW = ['finlab.finance', 'ssrn.com', 'arxiv.org', 'twse.com.tw', 'tpex.org.tw', 'jstor.org', 'sciencedirect.com', 'wiley.com', 'nber.org']
ENGINE_BOUND_FILES = ('scripts/strategy_v5_rules.py', 'scripts/strategy_v4_ranking.py', 'core/research/strategy_v4.py')
KIND_FUNCTIONS = {'index_switch': ('until', 'index_switch'), 'mix': ('until', 'mix'),
                  'engine': ('until', 'first_sessions', 'entry_only_on', 'risk_adjusted_momentum', 'market_above', 'engine_rule')}
PROXY_KINDS = ('index_switch',)
SOURCE_KEY = 'FC1-b5ce30f75a5d9664'  # the 2015-06-15..2025-12-31 feature cache; development cells read it after outputs/strategy_v6/cache_equivalence.json


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def write(path, data):
    from services.web_dashboard import clean
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(clean(data), ensure_ascii=False, indent=1, allow_nan=False, default=str), encoding='utf-8')


def write_charter(snapshot_id):
    path = OUT / 'charter.json'
    if path.exists():
        raise RuntimeError('CHARTER_EXISTS: targets, budget and windows are fixed once')
    body = {'written_at': datetime.now(timezone.utc).isoformat(), 'snapshot_id': snapshot_id, 'labels': LABELS, 'thresholds': THRESHOLDS,
            'budget': BUDGET, 'windows': WINDOWS, 'etf_costs': ETF_COSTS, 'web_allow_list': WEB_ALLOW, 'etf_ingestion_allowed': False,
            'classify_sha256': _sha(inspect.getsource(classify).encode())}
    write(path, body)
    path.with_suffix('.sha256').write_text(_sha(path.read_bytes()) + '\n', encoding='ascii')
    return body


def charter():
    path = OUT / 'charter.json'
    if _sha(path.read_bytes()) != path.with_suffix('.sha256').read_text(encoding='ascii').strip():
        raise ValueError('CHARTER_CHANGED')
    body = json.loads(path.read_text(encoding='utf-8'))
    if body['classify_sha256'] != _sha(inspect.getsource(classify).encode()):
        raise ValueError('CLASSIFY_CHANGED')
    return body


def queue():
    path, rows, prev = OUT / 'queue.jsonl', [], ''
    for line in path.read_text(encoding='utf-8').splitlines() if path.exists() else []:
        row = json.loads(line)
        body = {k: v for k, v in row.items() if k != 'sha'}
        if row['prev'] != prev or row['sha'] != _sha((prev + json.dumps(body, sort_keys=True, ensure_ascii=False)).encode()):
            raise ValueError('QUEUE_CHAIN_BROKEN')
        rows.append(row)
        prev = row['sha']
    return rows


@contextmanager
def _queue_lock():
    """Cross-process lock so parallel cells cannot fork the chain; a lock older than 60 s is from a crashed writer."""
    import os
    import time
    lock = OUT / 'queue.lock'
    for _ in range(600):
        try:
            fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            break
        except FileExistsError:
            try:
                if time.time() - lock.stat().st_mtime > 60:
                    lock.unlink(missing_ok=True)
            except OSError:  # the holder released it between our open and stat
                pass
            time.sleep(0.1)
    else:
        raise TimeoutError('QUEUE_LOCK')
    try:
        yield
    finally:
        os.close(fd)
        lock.unlink(missing_ok=True)


def append(row):
    with _queue_lock():
        rows = queue()
        prev = rows[-1]['sha'] if rows else ''
        body = row | {'seq': len(rows), 'at': datetime.now(timezone.utc).isoformat(), 'prev': prev}
        body['sha'] = _sha((prev + json.dumps(body, sort_keys=True, ensure_ascii=False)).encode())
        with (OUT / 'queue.jsonl').open('a', encoding='utf-8') as handle:
            handle.write(json.dumps(body, ensure_ascii=False, sort_keys=True) + '\n')
        return body


def _rows(hid, event=None):
    return [r for r in queue() if r['hypothesis'] == hid and (event is None or r['event'] == event)]


def state(hid):
    rows = _rows(hid)
    return rows[-1]['event'] if rows else None


def registration(hid):
    rows = _rows(hid, 'REGISTERED')
    if not rows:
        raise ValueError(f'NOT_REGISTERED {hid}')
    return rows[0]


def registered_count():
    return len({r['hypothesis'] for r in queue() if r['event'] == 'REGISTERED'})


def admit(spec):
    rules = charter()
    if _rows(spec['id']):
        raise ValueError(f"ALREADY_IN_QUEUE {spec['id']}")
    if len(spec['variants']) > rules['budget']['variants']:
        raise ValueError('TOO_MANY_VARIANTS')
    if spec.get('blocked'):
        return append({'hypothesis': spec['id'], 'event': 'DATA_BLOCKED', 'spec': spec, 'reason': spec['blocked']})
    if registered_count() >= rules['budget']['hypotheses']:
        raise ValueError('BUDGET_SPENT')
    if spec.get('parent') or any(r['event'] == 'RESULT' for r in queue()):
        # once any result exists, every new hypothesis answers a recorded failure with a retrieved source
        results = [r for r in _rows(spec.get('parent', ''), 'RESULT') if r['window'] == 'development']
        if not any(r['verdict'] in ('FAILED', 'NEAR') for r in results):
            raise ValueError('PARENT_NOT_FAILED')
        card = spec.get('source_card', '')
        text = (OUT / card).read_text(encoding='utf-8') if card.startswith('sources/') and (OUT / card).is_file() else ''
        if 'URL:' not in text or 'Retrieved:' not in text:
            raise ValueError('SOURCE_CARD_MISSING')
        siblings = [r for r in queue() if r['event'] == 'REGISTERED' and r['spec'].get('parent') == spec['parent']]
        if len(siblings) >= rules['budget']['sourced_per_failure']:
            raise ValueError('ONE_PER_FAILURE')
    return append({'hypothesis': spec['id'], 'event': 'REGISTERED', 'spec': spec, 'bound_code': bound_code(spec['kind'])})


def binding(hid):
    return [r for r in queue() if r['hypothesis'] == hid and r['event'] in ('REGISTERED', 'REBIND')][-1]['bound_code']


def rebind(hid, reason):
    """Bind a hypothesis to today's code; only before it has any result, so a rule can never change after it was judged."""
    if _rows(hid, 'RESULT') or _rows(hid, 'HOLDOUT_STARTED'):
        raise ValueError(f'REBIND_AFTER_RESULT {hid}')
    kind = registration(hid)['spec']['kind']
    deviation(f'{hid} rebound to current code before its first run: {reason}')
    return append({'hypothesis': hid, 'event': 'REBIND', 'bound_code': bound_code(kind), 'reason': reason})


def require_runnable(hid, vid, window):
    charter()
    spec = registration(hid)['spec']
    if vid not in spec['variants']:
        raise ValueError(f'UNREGISTERED_VARIANT {hid}:{vid}')
    if binding(hid) != bound_code(spec['kind']):
        raise ValueError(f'CODE_CHANGED_SINCE_REGISTRATION {hid}')
    if window == 'holdout':
        if spec['kind'] in PROXY_KINDS:
            raise ValueError(f'PROXY_HAS_NO_HOLDOUT {hid}')
        if not [r for r in _rows(hid, 'FINALIST') if r['variant'] == vid]:
            raise ValueError(f'NOT_A_FINALIST {hid}:{vid}')
        if [r for r in _rows(hid, 'HOLDOUT_STARTED') if r['variant'] == vid]:
            raise ValueError(f'HOLDOUT_ALREADY_USED {hid}:{vid}')
        if stored_result(hid, vid, 'development').get('measurement_code') != measurement_code():
            raise ValueError(f'MEASUREMENT_CHANGED_SINCE_DEVELOPMENT {hid}:{vid}')
        for member in spec['variants'][vid].get('weights', {}) if spec['kind'] == 'mix' else ():
            member_hid, member_vid = member.split(':')
            if not [r for r in _rows(member_hid, 'HOLDOUT') if r['variant'] == member_vid]:
                raise ValueError(f'MIX_MEMBER_HOLDOUT_MISSING {member}')
    return spec


def classify(numbers, bench, thresholds):
    """PROMISING meets every threshold; NEAR misses one but beats the benchmark's return-to-drawdown at double cost; else FAILED."""
    base, stress, mdd = (numbers[k]['value'] for k in ('cagr', 'cagr_stress', 'max_drawdown'))
    if not numbers.get('publishable') or None in (base, stress, mdd):
        return {'verdict': 'FAILED', 'failure': 'DATA'}
    if numbers['kind'] == 'engine':
        enough = numbers['closed_trades'] >= thresholds['min_closed_trades']
    else:
        enough = numbers['years'] >= thresholds['min_years_without_trades']
    if not enough:
        return {'verdict': 'FAILED', 'failure': 'SAMPLE'}
    if base >= thresholds['base_cagr'] and stress >= thresholds['stress_cagr'] and mdd >= thresholds['max_drawdown']:
        return {'verdict': 'PROMISING', 'failure': None}
    failure = 'RETURN' if base < thresholds['base_cagr'] else 'COST' if stress < thresholds['stress_cagr'] else 'DRAWDOWN'
    bench_calmar = bench['cagr']['value'] / abs(bench['max_drawdown']['value'])
    near = mdd < 0 and stress / abs(mdd) > bench_calmar
    return {'verdict': 'NEAR' if near else 'FAILED', 'failure': failure}


def bound_code(kind):
    """Hashes of exactly the rule functions (and, for engine runs, the V4/V5 files) a hypothesis of this kind executes."""
    from scripts import strategy_v6_rules as rules
    code = {f'rules.{name}': _sha(inspect.getsource(getattr(rules, name)).encode()) for name in KIND_FUNCTIONS[kind]}
    if kind == 'engine':
        code |= {name: _sha((ROOT / name).read_bytes()) for name in ENGINE_BOUND_FILES}
    return code


def measurement_code():
    """Hash of the code that turns a run into numbers and a verdict; a holdout must use the code its development result used."""
    names = ('verify_against_cache', '_cached_features', '_engine_track', 'numbers_for', '_summary', '_stress_drawdown', 'bench_numbers', 'classify')
    files = ('core/research/intelligence/performance.py', 'scripts/strategy_v4.py')
    return _sha((''.join(inspect.getsource(globals()[n]) for n in names) + ''.join(_sha((ROOT / f).read_bytes()) for f in files)).encode())


def deviation(text):
    with (OUT / 'deviations.jsonl').open('a', encoding='utf-8') as handle:
        handle.write(json.dumps({'at': datetime.now(timezone.utc).isoformat(), 'text': text}, ensure_ascii=False) + '\n')


def _context():
    from scripts import strategy_intelligence as si
    return si.load_context()


def _track(nav_base, nav_stress):
    return pd.DataFrame({'date': pd.to_datetime(nav_base.date), 'nav_base': nav_base.nav.values, 'nav_stress': nav_stress.nav.values})


def _summary(track, window, ctx, trades=None, cost_model=None):
    from core.research.intelligence import performance as perf
    sliced = perf.slice_track(track, *window)
    if len(sliced) < 3:
        return None, sliced
    return perf.summarize(sliced, trades, ctx['bench'], cost_model), sliced


def _guard():
    import subprocess
    done = subprocess.run([sys.executable, str(OUT / 'guard.py'), 'check', '--fast'], cwd=ROOT, capture_output=True, text=True)
    if done.returncode:
        raise RuntimeError(f'GUARD_FAILED {done.stdout.strip()[-300:]}')


def verify_against_cache(data, sample, path_of):
    """Compare each cached day with a fresh computation. The fresh source returns its memo first, so the memo is removed for the
    comparison and restored after (as core.research.platform.feature_cache.verify does). Zero compared days is a failure."""
    mismatches, compared = [], 0
    for day in sample:
        path = path_of(day)
        if not path.exists():
            continue
        held = data._cache.pop(day, None)
        try:
            pd.testing.assert_frame_equal(pd.read_pickle(path), data.feature_cache_original(day), check_exact=True)
        except AssertionError as error:
            mismatches.append(f'{pd.Timestamp(day):%Y-%m-%d}: {str(error)[:160]}')
        finally:
            data._cache.pop(day, None)
            if held is not None:
                data._cache[day] = held
        compared += 1
    return mismatches if compared else ['NO_CACHED_DAY_COMPARED']


def _cached_features(sessions):
    """Read the cached 2025-end cross-sections (never write), and verify 12 sessions spread over the run against a fresh computation
    on this run's own window. Any difference marks the run FEATURE_CACHE_MISMATCH through build_record."""
    from contextlib import ExitStack
    from unittest.mock import patch
    from core.research.platform import feature_cache
    if not feature_cache.enabled(SOURCE_KEY):
        raise RuntimeError(f'SOURCE_CACHE_DISABLED {SOURCE_KEY}')
    days = list(pd.to_datetime(list(sessions)))
    sample = [days[i] for i in range(0, len(days), max(1, len(days) // 12))][:12]
    last = days[-1]

    def attach(data, _key):
        source = data.cross_section

        def cached(day):
            if pd.Timestamp(day) > last:
                raise ValueError(f'FEATURE_AFTER_WINDOW_END {day}')
            if day not in data._cache:
                path = feature_cache._path(feature_cache.ROOT, SOURCE_KEY, day)
                data._cache[day] = pd.read_pickle(path) if path.exists() else source(day)
            return data._cache[day]
        data.cross_section, data.feature_cache_original = cached, source
        return True

    def verify(data, _key, _dates):
        found = verify_against_cache(data, sample, lambda day: feature_cache._path(feature_cache.ROOT, SOURCE_KEY, day))
        stack.verified['compared'] = sum(feature_cache._path(feature_cache.ROOT, SOURCE_KEY, d).exists() for d in sample)
        return found
    stack = ExitStack()
    stack.enter_context(patch.object(feature_cache, 'attach', attach))
    stack.enter_context(patch.object(feature_cache, 'verify', verify))
    stack.verified = {'source_key': SOURCE_KEY, 'dates': [f'{d:%Y-%m-%d}' for d in sample]}
    return stack


def _engine_track(hid, vid, spec, window, bench, end=None, start=None, root=None):
    """Run the host strategy with the hypothesis rule on the snapshot up to the window end; reuse an identical stored publishable run."""
    from core.backtest import run_store
    from core.backtest.run_store import canonical_hash
    from scripts import history_engine
    from scripts import platform_v2 as v2
    from scripts import strategy_v5_corrections as v5
    from scripts import strategy_v6_rules as rules
    from scripts.platform_v2_engine import execute
    variant, rules_charter = spec['variants'][vid], charter()
    base = next(p for p in v2.baseline_params() if p['strategy_id'] == variant['host'])
    params = base | {'end': end or f"{rules_charter['windows'][window][1]}-12-31"} | ({'start': start} if start else {})
    snapshot, root = rules_charter['snapshot_id'], root or OUT / 'runs'
    rid = 'RID6-' + canonical_hash({'original_identity': v2.identity(params, snapshot), 'hypothesis': hid, 'variant': variant,
                                    'bound_code': bound_code('engine')})[:24]
    run_id = v5._reusable(rid, root)
    if not run_id:
        run_id = run_store.new_run_id()
        sessions = history_engine._prepared(snapshot, params['start'], params['end']).sessions
        cache = _cached_features(sessions)
        with cache, rules.engine_rule(variant, sessions, rules.until(bench, params['end'])):
            result = execute(params['strategy_id'], snapshot, params['start'], params['end'], params['capital'], params['costs'], params['rule'])
        record, frames = v2.build_record(params, rid, run_id, snapshot, result)
        record.update(v6_feature_cache_check=cache.verified | {'mismatches': result['feature_cache']['mismatches']})
        record.update(strategy_type='strategy_v6_hypothesis', research_version='V6', labels=LABELS, hypothesis=hid, variant=vid,
                      variant_params=variant, bound_code=bound_code('engine'), charter_sha256=_sha((OUT / 'charter.json').read_bytes()))
        run_store.save_run(record, frames, root=root, run_id=run_id)
    return run_store.load_run(run_id, root=root), rid


def _stress_drawdown(sliced):
    curve = sliced.nav_stress.astype(float)
    return {'value': float((curve / curve.cummax() - 1).min()), 'reason': None} if len(curve) > 2 else {'value': None, 'reason': 'NO_SESSIONS'}


def numbers_for(hid, vid, window, ctx):
    """Development or holdout numbers for one variant, computed only from data up to the window end."""
    from scripts import strategy_v6_rules as rules
    rules_charter = charter()
    spec = registration(hid)['spec']
    variant, years = spec['variants'][vid], tuple(rules_charter['windows'][window])
    window_end, costs = f'{years[1]}-12-31', rules_charter['etf_costs']
    out = {'hypothesis': hid, 'variant': vid, 'window': window, 'kind': spec['kind'], 'proxy': spec['kind'] in PROXY_KINDS,
           'source': spec.get('source'), 'bound_code': bound_code(spec['kind'])}
    if spec['kind'] == 'index_switch':
        bench = rules.until(ctx['bench'], window_end)
        track = _track(rules.index_switch(bench, variant['ma'], costs, 1), rules.index_switch(bench, variant['ma'], costs, 2))
        summary, sliced = _summary(track, years, ctx)
        out |= {'publishable': summary is not None, 'run_identity': 'PROXY-' + _sha(json.dumps([hid, vid, variant, out['bound_code']]).encode())[:24],
                'note': 'Proxy on the total-return index. Each switch fills at the signal session close, so the figures are a best case.'}
    elif spec['kind'] == 'engine':
        from core.research.intelligence import performance as perf
        from scripts.strategy_v4 import bundle
        loaded, rid = _engine_track(hid, vid, spec, window, ctx['bench'])
        run, trust, _ = bundle(loaded, ctx)
        verified = None
        if window == 'development':
            inside = {y: s for y, s in trust.items() if years[0] <= int(y) <= years[1]}
            verified = perf.verified_span(inside, sorted(inside))
            years = verified or years
        summary, sliced = _summary(run['nav'], years, ctx, run['trades'], run['record']['cost_model'])
        reasons = list(loaded['run'].get('not_publishable_reasons') or []) + ([] if verified or window != 'development' else ['NO_VERIFIED_YEAR'])
        out |= {'publishable': bool(loaded['run']['publishable']) and not reasons, 'not_publishable_reasons': reasons,
                'run_identity': rid, 'run_id': loaded['run']['run_id'], 'feature_cache': loaded['run'].get('feature_cache'),
                'data_trust': {str(k): v for k, v in trust.items() if years[0] <= int(k) <= years[1]}}
    elif spec['kind'] == 'mix':
        members = {}
        for key in variant['weights']:
            member_hid, member_vid = key.split(':')
            member = stored_result(member_hid, member_vid, window)  # members are never recomputed, so they keep their binding and holdout rule
            members[key] = pd.DataFrame(member['series'])
            out['proxy'] = out['proxy'] or member['proxy']
            out.setdefault('members', {})[key] = {k: member[k] for k in ('cagr', 'cagr_stress', 'max_drawdown', 'run_identity')}
        base = rules.mix({k: m.rename(columns={'nav_base': 'nav'})[['date', 'nav']] for k, m in members.items()}, variant['weights'])
        stress = rules.mix({k: m.rename(columns={'nav_stress': 'nav'})[['date', 'nav']] for k, m in members.items()}, variant['weights'])
        summary, sliced = _summary(_track(base, stress), years, ctx)
        out |= {'publishable': summary is not None, 'run_identity': 'MIX-' + _sha(json.dumps([hid, vid, variant, out['bound_code']]).encode())[:24]}
    else:
        raise ValueError(f"UNKNOWN_KIND {spec['kind']}")
    summary = summary or {}
    missing = {'value': None, 'reason': 'NO_SESSIONS_OR_NOT_PUBLISHABLE'}
    if spec['kind'] != 'engine':
        summary['invested_session_share'] = {'value': None, 'reason': 'NOT_A_STOCK_ENGINE_RUN'}
    out |= {'measurement_code': measurement_code(), 'span': list(years),
            **{k: summary.get(k, missing) for k in ('cagr', 'cagr_stress', 'max_drawdown', 'calmar', 'worst_year', 'invested_session_share')},
            'max_drawdown_stress': _stress_drawdown(sliced), 'closed_trades': summary.get('closed_trades'), 'years': summary.get('sessions', 0) / 252,
            'series': {'date': sliced.date.astype(str).tolist(), 'nav_base': sliced.nav_base.astype(float).tolist(),
                       'nav_stress': sliced.nav_stress.astype(float).tolist()} if len(sliced) else None}
    if out['closed_trades'] is None:
        out['closed_trades'] = 0 if spec['kind'] != 'engine' else None
    return out


def bench_numbers(years, ctx):
    from core.research.intelligence import performance as perf
    bench = ctx['bench'].assign(date=pd.to_datetime(ctx['bench'].date))
    track = pd.DataFrame({'date': bench.date, 'nav_base': bench.taiex_total_return, 'nav_stress': bench.taiex_total_return})
    s = perf.summarize(perf.slice_track(track, *years), None, None)
    return {'cagr': s['cagr'], 'max_drawdown': s['max_drawdown'], 'calmar': s['calmar'], 'span': list(years)}


def run(hid, vid, window='development'):
    path = OUT / 'results' / f'{hid}__{vid}__{window}.json'
    recorded = [r for r in _rows(hid, 'RESULT') if r.get('variant') == vid and r.get('window') == window]
    if window == 'development' and recorded:
        return stored_result(hid, vid, window)
    if path.exists():  # written but never recorded: a crash between the file and its queue row; it was never judged
        append({'hypothesis': hid, 'event': 'RUN_FAILED', 'variant': vid, 'window': window, 'reason': 'ORPHAN_RESULT_FILE_REPLACED'})
    require_runnable(hid, vid, window)
    _guard()
    if window == 'holdout':
        append({'hypothesis': hid, 'event': 'HOLDOUT_STARTED', 'variant': vid})  # a crash still counts as the one look
    started = datetime.now(timezone.utc)
    try:
        ctx = _context()
        numbers = numbers_for(hid, vid, window, ctx)
        bench = bench_numbers(tuple(numbers['span']), ctx)
        if numbers['closed_trades'] is None:
            numbers['closed_trades'] = 0
        judged = classify(numbers, bench, charter()['thresholds'])
    except Exception as error:
        import traceback
        write(OUT / 'failures' / f'{hid}__{vid}__{window}.json', {'error': f'{type(error).__name__}: {error}', 'traceback': traceback.format_exc(),
                                                                   'started_at': started.isoformat(), 'failed_at': datetime.now(timezone.utc).isoformat()})
        append({'hypothesis': hid, 'event': 'RUN_FAILED', 'variant': vid, 'window': window, 'reason': f'{type(error).__name__}: {str(error)[:200]}'})
        raise
    if window == 'holdout':
        judged['verdict'] = 'HOLDOUT_PASS' if judged['verdict'] == 'PROMISING' else 'HOLDOUT_FAIL'
    finished = datetime.now(timezone.utc)
    write(path, numbers | {'benchmark': bench} | judged | {'started_at': started.isoformat(), 'finished_at': finished.isoformat(),
                                                          'seconds': round((finished - started).total_seconds(), 1)})
    append({'hypothesis': hid, 'event': 'RESULT' if window == 'development' else 'HOLDOUT', 'variant': vid, 'window': window,
            'verdict': judged['verdict'], 'failure': judged['failure'], 'run_identity': numbers['run_identity'], 'result_sha256': _sha(path.read_bytes())})
    return json.loads(path.read_text(encoding='utf-8'))


def stored_result(hid, vid, window):
    """A result file is trusted only if its hash equals the one its queue row recorded."""
    path = OUT / 'results' / f'{hid}__{vid}__{window}.json'
    rows = [r for r in queue() if r['hypothesis'] == hid and r.get('variant') == vid and r.get('window') == window and r['event'] in ('RESULT', 'HOLDOUT')]
    if not rows or rows[-1]['result_sha256'] != _sha(path.read_bytes()):
        raise ValueError(f'RESULT_CHANGED {hid}:{vid}:{window}')
    return json.loads(path.read_text(encoding='utf-8'))


def smoke(hid, vid):
    """A short engine run on 2023-01-03..2023-03-31 outside the queue: timing and cache verification before a long batch."""
    import time
    require_runnable(hid, vid, 'development')
    ctx, started = _context(), time.time()
    loaded, _ = _engine_track(hid, vid, registration(hid)['spec'], 'development', ctx['bench'], end='2023-03-31', start='2023-01-03',
                              root=OUT / 'smoke_runs' / measurement_code()[:12])
    out = {'hypothesis': hid, 'variant': vid, 'seconds': round(time.time() - started, 1), 'publishable': loaded['run']['publishable'],
           'reasons': loaded['run'].get('not_publishable_reasons'), 'feature_cache': loaded['run'].get('feature_cache')}
    write(OUT / 'smoke' / f'{hid}__{vid}.json', out)
    deviation(f'smoke run {hid}:{vid} on 2023-01-03..2023-03-31 outside the queue (task 4.1); not a result')
    return out


def finalize():
    rules = charter()
    done = [r for r in queue() if r['event'] == 'FINALIST']
    if done:
        return done
    candidates = []
    for row in queue():
        if row['event'] == 'RESULT' and row['verdict'] == 'PROMISING':
            result = stored_result(row['hypothesis'], row['variant'], 'development')
            stress_mdd = (result.get('max_drawdown_stress') or {}).get('value')
            if not result['proxy'] and stress_mdd:
                candidates.append((result['cagr_stress']['value'] / abs(stress_mdd), row['hypothesis'], row['variant']))
    return [append({'hypothesis': h, 'event': 'FINALIST', 'variant': vv}) for _, h, vv in sorted(candidates, reverse=True)[:rules['budget']['finalists']]]


def outcome(rows, budget, final=True):
    if any(r['event'] == 'HOLDOUT' and r['verdict'] == 'HOLDOUT_PASS' for r in rows):
        return 'TARGET_MET_ON_HOLDOUT'
    if not final:
        return 'IN_PROGRESS'
    registered = len({r['hypothesis'] for r in rows if r['event'] == 'REGISTERED'})
    return 'BUDGET_SPENT_TARGET_NOT_MET' if registered >= budget['hypotheses'] else 'STOPPED_BY_BLOCKER'


def _pct(metric):
    value = (metric or {}).get('value')
    return '—' if value is None else f'{value * 100:+.1f}%'


def _dsr(results):
    """Deflated Sharpe of each development run against the trials before V6 plus this program's runs (None with a reason when undefined)."""
    import numpy as np
    from scripts import research_workflow as rw
    from scripts.track_b_stats import deflated_sharpe
    returns = {k: pd.Series(r['series']['nav_stress']).pct_change().dropna() for k, r in results.items() if r.get('series')}
    sharpes = [x.mean() / x.std(ddof=1) for x in returns.values() if x.std(ddof=1) > 0]
    trials = rw.effective_trials() + len(returns)
    variance = float(np.var(sharpes, ddof=1)) if len(sharpes) > 1 else 0.0
    return trials, {k: {'value': deflated_sharpe(x.values, trials, variance), 'reason': None} if variance > 0
                    else {'value': None, 'reason': 'FEWER_THAN_TWO_TRIAL_SHARPES'} for k, x in returns.items()}


ZH = {'PROMISING': '達標候選', 'NEAR': '接近目標', 'FAILED': '未過', 'HOLDOUT_PASS': '驗證區間通過', 'HOLDOUT_FAIL': '驗證區間未過',
      'RETURN': '報酬不足', 'COST': '成本吃掉報酬', 'DRAWDOWN': '回撤過大', 'SAMPLE': '樣本不足', 'DATA': '資料或帳務問題', None: ''}
OUTCOME_ZH = {'TARGET_MET_ON_HOLDOUT': '在驗證區間達標', 'BUDGET_SPENT_TARGET_NOT_MET': '預算用完，未達標',
              'STOPPED_BY_BLOCKER': '因資料或來源阻擋而停止，未達標', 'IN_PROGRESS': '進行中（尚未結束）'}


def _gap(r, t):
    parts = [('年化', r['cagr']['value'], t['base_cagr']), ('兩倍成本', r['cagr_stress']['value'], t['stress_cagr']),
             ('回撤', r['max_drawdown']['value'], t['max_drawdown'])]
    return '；'.join(f'{name} 差 {(goal - got) * 100:.1f} 點' for name, got, goal in parts if got is not None and got < goal) or '全部達標'


def write_report(owner_items=(), final=False):
    rules, rows = charter(), queue()
    results = {}
    for row in rows:
        if row['event'] in ('RESULT', 'HOLDOUT'):
            results[f"{row['hypothesis']}__{row['variant']}__{row['window']}"] = stored_result(row['hypothesis'], row['variant'], row['window'])
    dev = {k: r for k, r in results.items() if k.endswith('__development')}
    trials, dsr = _dsr(dev)
    end = outcome(rows, rules['budget'], final)
    t = rules['thresholds']
    lines = ['# V6 年報酬目標研究計畫：驗收報表', '', f"> {' / '.join(rules['labels'])}。看過歷史資料的開發研究，不是投資建議。", '',
             f"**結果：{OUTCOME_ZH[end]}**（{end}）", '',
             f"目標（登錄後不可改）：年化 ≥ {t['base_cagr']:.0%}、兩倍成本後年化 ≥ {t['stress_cagr']:.0%}、最大回撤不差於 {t['max_drawdown']:.0%}，"
             f"且引擎類至少 {t['min_closed_trades']} 筆平倉、指數與配置類至少 {t['min_years_without_trades']} 年。"
             f"開發區間 {rules['windows']['development'][0]}–{rules['windows']['development'][1]}，驗證區間 {rules['windows']['holdout'][0]}–{rules['windows']['holdout'][1]}。", '',
             '## 每個假說', '',
             '| 假說 | 變體 | 區間 | 年化 | 兩倍成本年化 | 最大回撤 | 兩倍成本回撤 | 持股天數比例 | 平倉筆數 | 同期大盤 | 判定 | 原因 | 離目標 | 代理 | Deflated Sharpe |',
             '|---|---|---|---:|---:|---:|---:|---:|---:|---:|---|---|---|---|---:|']
    for key, r in results.items():
        d = dsr.get(key, {'value': None})
        dsr_text = '—' if d['value'] is None else f"{d['value']:.2f}"
        bench = r['benchmark']
        lines.append(f"| {r['hypothesis']} | {r['variant']} | {r['window']} {r['span'][0]}–{r['span'][1]} | {_pct(r['cagr'])} | {_pct(r['cagr_stress'])} | "
                     f"{_pct(r['max_drawdown'])} | {_pct(r.get('max_drawdown_stress'))} | {_pct(r.get('invested_session_share'))} | {r.get('closed_trades')} | "
                     f"{_pct(bench['cagr'])} / {_pct(bench['max_drawdown'])} | {ZH[r['verdict']]} | {ZH[r['failure']]} | {_gap(r, t)} | "
                     f"{'是' if r['proxy'] else '否'} | {dsr_text} |")
    lines += ['', '說明：']
    lines += ['- 代理＝以含息加權指數計算，不是可交易的 ETF；換手在訊號當日收盤成交，是偏樂觀的上限。',
              '- 引擎類假說的出場由宿主策略的停損、停利與最長持有天數決定，所以「低頻進場」也代表較多時間持有現金，請看持股天數比例。',
              '- 「接近目標」使用登錄時的判定函式：以兩倍成本年化除以基本成本的最大回撤，和同期大盤比較。兩倍成本的回撤另列一欄。',
              '- 驗證區間仍要求至少 30 筆平倉；交易少的策略在兩年驗證區間可能因樣本不足而未過，這是登錄時的規則。',
              '- 驗證區間 2024–2025 在專案的資料可信度分類中是 PARTIAL。']
    blocked = [r for r in rows if r['event'] == 'DATA_BLOCKED']
    if blocked:
        lines += ['', '資料不足而未執行的假說：'] + [f"- {r['hypothesis']}（{r['spec']['family']}）：{r['reason']}" for r in blocked]
    failed = [r for r in rows if r['event'] == 'RUN_FAILED']
    finished = {(r['hypothesis'], r['variant']) for r in rows if r['event'] == 'HOLDOUT'}
    open_holdouts = [r for r in rows if r['event'] == 'HOLDOUT_STARTED' and (r['hypothesis'], r['variant']) not in finished]
    if failed or open_holdouts:
        lines += ['', '執行失敗或未完成：'] + [f'- {r["hypothesis"]}{r["variant"]} {r["window"]}：{r["reason"]}' for r in failed]
        lines += [f'- {r["hypothesis"]}{r["variant"]} 驗證區間已開始但沒有結果，仍算用掉一次' for r in open_holdouts]
    usable = {k: r for k, r in dev.items() if r['failure'] != 'DATA'}
    if usable:
        lines += ['', '## 最接近目標的結果（開發區間，不含資料問題的格子）', '']
        for name, metric in (('年化', 'cagr'), ('兩倍成本年化', 'cagr_stress'), ('最大回撤', 'max_drawdown')):
            r = max(usable.values(), key=lambda x: x[metric]['value'] if x[metric]['value'] is not None else -9)
            proxy_label = '（代理）' if r['proxy'] else ''
            lines.append(f'- 最佳{name}：{r["hypothesis"]}{r["variant"]} {_pct(r[metric])}{proxy_label}（{_gap(r, t)}）')
    lines += ['', '## 來源', ''] + [f"- {r['hypothesis']}：{r['spec'].get('source', '')}" for r in rows if r['event'] in ('REGISTERED', 'DATA_BLOCKED')]
    lines += ['', '## 試驗次數與顯著性', '', f"本計畫跑了 {len(dev)} 個開發格子。Deflated Sharpe 以專案既有的有效試驗數加上本計畫的格子，共 {trials} 次計算；"
              '數值越接近 1，越不像是試很多次碰巧挑到的。本計畫未寫入正式試驗帳本。']
    cards = sorted((OUT / 'sources').glob('*.md')) if (OUT / 'sources').is_dir() else []
    lines += ['', '## 文獻與來源卡片', ''] + ([f'- `{p.relative_to(ROOT).as_posix()}`' for p in cards] or ['- 無'])
    devs = [json.loads(x) for x in (OUT / 'deviations.jsonl').read_text(encoding='utf-8').splitlines()] if (OUT / 'deviations.jsonl').exists() else []
    lines += ['', '## 偏離紀錄（執行當下寫入）', ''] + ([f"- {d['at'][:16]}Z {d['text']}" for d in devs] or ['- 無'])
    lines += ['', '## 需要你決定的事項', ''] + ([f'{i}. {item}' for i, item in enumerate(owner_items, 1)] or ['- 無'])
    write(OUT / 'report.json', {'outcome': end, 'final': final, 'charter_sha256': _sha((OUT / 'charter.json').read_bytes()), 'queue_head': rows[-1]['sha'] if rows else None,
                                'effective_trials_for_dsr': trials, 'deflated_sharpe': dsr,
                                'results': {k: {x: r.get(x) for x in ('hypothesis', 'variant', 'window', 'span', 'cagr', 'cagr_stress', 'max_drawdown', 'max_drawdown_stress',
                                                                      'invested_session_share', 'closed_trades', 'verdict', 'failure', 'proxy', 'benchmark')}
                                            for k, r in results.items()}, 'deviations': devs, 'owner_items': list(owner_items),
                                'generated_at': datetime.now(timezone.utc).isoformat()})
    (OUT / 'V6_ACCEPTANCE_REPORT.md').write_text('\n'.join(lines) + '\n', encoding='utf-8')
    return end


def main(argv):
    command = argv[0] if argv else ''
    if command == 'charter':
        from scripts import historical_foundation as hf
        print(json.dumps(write_charter(hf.latest_evaluation()['snapshot_id']), ensure_ascii=False)[:300])
    elif command == 'admit':
        print(json.dumps(admit(json.loads(Path(argv[1]).read_text(encoding='utf-8'))), ensure_ascii=False)[:300])
    elif command == 'rebind':
        print(json.dumps(rebind(argv[1], argv[2]), ensure_ascii=False)[:300])
    elif command == 'smoke':
        print(json.dumps(smoke(argv[1], argv[2]), ensure_ascii=False, default=str)[:600])
    elif command == 'run':
        result = run(argv[1], argv[2], argv[3] if len(argv) > 3 else 'development')
        print(json.dumps({k: result.get(k) for k in ('hypothesis', 'variant', 'window', 'verdict', 'failure', 'span', 'seconds')} |
                         {k: result[k]['value'] for k in ('cagr', 'cagr_stress', 'max_drawdown')}, ensure_ascii=False))
    elif command == 'finalize':
        print(json.dumps(finalize(), ensure_ascii=False)[:500])
    elif command == 'status':
        for row in queue():
            print(row['seq'], row['hypothesis'], row['event'], row.get('variant', ''), row.get('verdict', ''), row.get('failure', ''))
    elif command == 'report':
        items = json.loads((OUT / 'owner_items.json').read_text(encoding='utf-8')) if (OUT / 'owner_items.json').exists() else []
        print(write_report(items, final='--final' in argv))
    else:
        print(__doc__)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main(sys.argv[1:]))
