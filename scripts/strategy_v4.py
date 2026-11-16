"""Single-worker, non-formal V4 research on the pinned pre-2026 snapshot.

Run: python -m scripts.strategy_v4. Never writes V1/V2/V3 evidence or formal ledgers.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from core.backtest import run_store
from scripts.strategy_v4_ranking import STRATEGIES, fixed_selection
from services.web_dashboard import clean
from scripts import platform_v2 as v2

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'outputs/strategy_v4'


def write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(clean(data), ensure_ascii=False, indent=1, allow_nan=False, default=str), encoding='utf-8')


def code_hashes():
    from scripts import strategy_intelligence as si
    files = set(si.identity_files()) | set(si.code_hashes()) | set(v2.ENGINE_FILES)
    files |= {'core/research/strategy_v4.py', 'scripts/strategy_v4_ranking.py', 'scripts/strategy_v4.py', 'scripts/platform_v2.py',
              'scripts/platform_v2_ledger_audit.py', 'core/research/platform/feature_cache.py', 'services/web_dashboard.py'}
    return {name: run_store.file_hash(ROOT / name) for name in sorted(files)}


def execution_binding(params, snapshot_id):
    return {'parameters': params, 'snapshot_id': snapshot_id, 'original_identity': v2.identity(params, snapshot_id), 'code_hashes': code_hashes()}


def variant_identity(params, snapshot_id):
    return 'RID4-' + run_store.canonical_hash({'binding': execution_binding(params, snapshot_id), 'variant': 'ranking-only-v1'})[:24]


def require_binding(saved, expected):
    if saved.get('binding') != expected:
        raise ValueError('STALE_RESEARCH_PREREQUISITE')


def readonly_attach(data, key):
    """Read existing V2 features; compute misses in memory without changing its cache."""
    from core.research.platform import feature_cache
    if not feature_cache.enabled(key) or not feature_cache.enabled(key, root=OUT / 'cache_verification'):
        return False
    original = data.cross_section

    def cached(day):
        if day not in data._cache:
            path = feature_cache._path(feature_cache.ROOT, key, day)
            data._cache[day] = pd.read_pickle(path) if path.exists() else original(day)
        return data._cache[day]

    data.cross_section, data.feature_cache_original = cached, original
    return True


def readonly_verify(data, key, dates):
    """Reuse V2's exact verifier on private copies of its sampled files."""
    import shutil
    from core.research.platform import feature_cache
    root = OUT / 'cache_verification'
    for day in dates:
        source = feature_cache._path(feature_cache.ROOT, key, day)
        if source.exists():
            target = feature_cache._path(root, key, day)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, target)
    return feature_cache.verify(data, key, dates, root=root)


def run_variant(params, snapshot_id, binding):
    from scripts.platform_v2_engine import execute
    from core.research.platform import feature_cache
    from unittest.mock import patch
    from scripts import history_engine
    require_binding({'binding': binding}, execution_binding(params, snapshot_id))
    identity = variant_identity(params, snapshot_id)
    if existing := v2.lookup(identity, OUT / 'runs'):
        loaded = run_store.load_run(existing, root=OUT / 'runs')
        require_binding(loaded['run'], binding)
        if loaded['run'].get('publishable') and not loaded['run'].get('feature_cache', {}).get('mismatches'):
            return loaded
    rid = run_store.new_run_id()
    try:
        # A previous failed run may retain an attached cache wrapper in this LRU.
        history_engine._prepared.cache_clear()
        original_verify = feature_cache.verify
        # Keep the verifier callable outside its patch, so it cannot recurse.
        def verify(data, key, dates):
            with patch.object(feature_cache, 'verify', original_verify):
                return readonly_verify(data, key, dates)
        with fixed_selection(params['strategy_id']), patch.object(feature_cache, 'attach', readonly_attach), patch.object(feature_cache, 'verify', verify):
            result = execute(params['strategy_id'], snapshot_id, params['start'], params['end'], params['capital'], params['costs'], params['rule'], use_cache=True)
        require_binding({'binding': binding}, execution_binding(params, snapshot_id))
        record, frames = v2.build_record(params, identity, rid, snapshot_id, result)
        record.update(strategy_type='strategy_v4_ranking', research_version='V4', variant_id='ranking-only-v1',
                      comparison_group='STRATEGY_V4', code_hashes=binding['code_hashes'], binding=binding, original_identity=binding['original_identity'])
        record['evidence'].update(identity=identity)
        run_store.save_run(record, frames, root=OUT / 'runs', run_id=rid)
        return run_store.load_run(rid, root=OUT / 'runs')
    except Exception as error:
        run_store.save_failure({'strategy_id': params['strategy_id'], 'run_identity': identity, 'parameters': params, 'snapshot_id': snapshot_id, 'binding': binding}, error, root=OUT / 'runs')
        raise


def bundle(loaded, ctx):
    from scripts import strategy_intelligence as si
    record = loaded['run']; params = record['parameters']
    trades = run_store.read_frame(loaded, 'trades', )
    if len(trades):
        trades['stock_id'] = trades.stock_id.astype(str)
        for name in ['profit_pct', 'shares', 'buy_price', 'sell_price']:
            if name in trades:
                trades[name] = pd.to_numeric(trades[name])
    actions = run_store.read_frame(loaded, 'corporate_actions')
    run = {'nav': run_store.read_frame(loaded, 'nav'), 'trades': trades, 'actions': actions if len(actions) else None,
           'record': record | {'cost_model': params['costs'] | {'stress_multiple': 2}}}
    status, adjustments = si.trust_for(ctx, params['strategy_id'], run, 'CURRENT')
    return run, status, adjustments


def compare_pair(original, fixed, ctx):
    from core.research.intelligence import performance as perf, feasibility, evaluate
    from scripts.platform_v2_ledger_audit import audit_run
    if (original['run']['parameters'] != fixed['run']['parameters'] or original['run']['snapshot_id'] != fixed['run']['snapshot_id']
            or fixed['run']['original_identity'] != original['run']['run_identity']):
        raise ValueError('INCOMPARABLE_EXECUTION_CONDITIONS')
    a, sa, aa = bundle(original, ctx); b, sb, ab = bundle(fixed, ctx)
    common = {year: 'VERIFIED' if sa.get(year) == sb.get(year) == 'VERIFIED' else 'PARTIAL' for year in set(sa) | set(sb)}
    span = perf.verified_span(common, sorted(common))
    output = {'strategy_id': fixed['run']['strategy_id'], 'variant_id': 'ranking-only-v1', 'research_version': 'V4',
              'labels': ['RESEARCH_ONLY', 'NOT_CERTIFIED', 'NOT_A_TRIAL', 'NOT_OOS_PREVIOUSLY_VIEWED_STRATEGIES'],
              'common_verified_span': span, 'original': {}, 'fixed': {}}
    for label, loaded, run, status, adjustments in [('original', original, a, sa, aa), ('fixed', fixed, b, sb, ab)]:
        rec = loaded['run']; nav = run['nav']; trades = run['trades']
        audit = audit_run(loaded['dir'])
        good = rec['publishable'] and all(t['status'] == 'PUBLISHABLE' for t in audit['tracks'].values())
        write(OUT / 'acceptance' / f"ledger_{rec['run_id']}.json", audit)
        track = perf.slice_track(nav, *span) if span else None
        full = perf.summarize(nav, trades, ctx['bench'], run['record']['cost_model']) if good else None
        summary = perf.summarize(track, trades, ctx['bench'], run['record']['cost_model']) if good and span else None
        liq = feasibility.liquidity(trades, ctx['bars']) if good else {'reason': 'NOT_PUBLISHABLE'}
        events = run_store.read_frame(loaded, 'execution_events')
        unfilled = pd.Series([e.get('reason', 'UNSPECIFIED') for e in events.get('base', []) if e.get('status') == 'UNFILLED']).value_counts().to_dict()
        cost = run_store.read_frame(loaded, 'costs')
        dates = pd.to_datetime(nav.date)
        years = max((dates.iloc[-1] - dates.iloc[0]).days / 365.25, 1 / 365.25)
        fills = run_store.read_frame(loaded, 'fills')
        turnover = float(pd.to_numeric(fills.notional).sum() / nav.nav_base.mean() / years) if len(fills) else 0.
        primary = ctx['profile']['strategies'][rec['strategy_id']]['primary_type']
        adequate = bool(summary and summary['closed_trades'] >= ctx['profile']['sample_rules']['min_closed_trades_for_trade_statistics'])
        edges = evaluate.edge_checks(ctx['profile']['types'][primary], summary, None, liq, adequate)
        checks = []
        for name, metric, threshold, operator in [('BASIC_RETURN', 'cagr', .05, '>='), ('ADVANCED_RETURN', 'cagr', .1, '>='), ('COST_STRESS', 'cagr_stress', 0., '>')]:
            value = (summary or {}).get(metric, {}).get('value')
            passed = value is not None and (value >= threshold if operator == '>=' else value > threshold)
            checks.append({'name': name, 'threshold': f'{operator} {threshold}', 'value': value,
                           'status': ('PASS' if passed else 'FAIL') if adequate and value is not None else 'INSUFFICIENT',
                           'reason': 'Frozen V2 profile; common VERIFIED window' if adequate else 'NO_ADEQUATE_COMMON_VERIFIED_SAMPLE'})
        checks += [{'name': 'STRATEGY_EDGE', 'threshold': ctx['profile']['types'][primary]['edge_checks'], 'value': edges,
                    'status': 'FAIL' if any(e['status'] == 'FAIL' for e in edges) else 'PASS' if edges and all(e['status'] == 'PASS' for e in edges) else 'INSUFFICIENT', 'reason': 'Frozen type-specific checks'},
                   {'name': 'DATA_TRUST', 'threshold': 'common VERIFIED years', 'value': span, 'status': 'PASS' if span else 'INSUFFICIENT', 'reason': 'Coverage plus exposure-specific corporate-action downgrades'},
                   {'name': 'EXECUTION', 'threshold': 'both cost ledgers PUBLISHABLE', 'value': good, 'status': 'PASS' if good else 'FAIL', 'reason': 'Saved fills / cash / holdings / NAV audit'},
                   {'name': 'CERTIFICATION', 'threshold': 'formal certification', 'value': None, 'status': 'NOT_CERTIFIED', 'reason': 'Research only; no formal trial'}]
        series = None
        if summary:
            bench = ctx['bench'].assign(date=pd.to_datetime(ctx['bench'].date)).set_index('date').reindex(pd.to_datetime(track.date))
            tri = bench.taiex_total_return
            series = {'date': track.date.astype(str).tolist(), 'base': (track.nav_base / track.nav_base.iloc[0]).tolist(),
                      'stress': (track.nav_stress / track.nav_stress.iloc[0]).tolist(), 'benchmark': (tri / tri.iloc[0]).tolist() if tri.notna().all() else None}
        output[label] = {'run_id': rec['run_id'], 'run_identity': rec['run_identity'], 'snapshot_id': rec['snapshot_id'], 'rule_version': rec['rule_version'],
                         'evaluation_id': ctx['evaluation'].get('evaluation_id'), 'variant_id': rec.get('variant_id', 'original'),
                         'parameters': rec['parameters'], 'source': loaded['dir'].relative_to(ROOT).as_posix(),
                         'manifest_sha256': run_store.file_hash(loaded['dir'] / 'manifest.json'), 'publishable': good,
                         'data_trust': status, 'trust_adjustments': adjustments, 'summary': summary, 'full_window': full,
                         'summary_reason': None if summary else 'NO_COMMON_VERIFIED_SPAN_OR_NOT_PUBLISHABLE', 'series': series,
                         'liquidity': liq, 'unfilled_reasons': unfilled, 'costs': cost, 'annual_turnover_full_window': perf.v(turnover), 'checks': checks}
    valid = all(output[k]['publishable'] for k in ('original', 'fixed'))
    av = (output['original']['summary'] or {}).get('cagr', {}).get('value'); bv = (output['fixed']['summary'] or {}).get('cagr', {}).get('value')
    output['status'] = 'COMPARABLE' if valid and span else 'BLOCKED'
    output['conclusion'] = '排序修正有效；績效改善' if av is not None and bv is not None and bv > av else '排序修正有效；績效未改善，停止本輪方案' if av is not None and bv is not None else '缺少可信公平比較；保留阻擋'
    output['cagr_delta'] = perf.v(bv - av) if None not in (av, bv) else perf.v(None, 'MISSING_COMPARABLE_CAGR')
    output['gap_5'] = perf.v(.05 - bv) if bv is not None else perf.v(None, 'MISSING_COMPARABLE_CAGR')
    output['gap_10'] = perf.v(.1 - bv) if bv is not None else perf.v(None, 'MISSING_COMPARABLE_CAGR')
    return output


def main():
    from core.research.sources.twse import _exclusive
    from core.research.reference import batch_job
    from scripts import historical_foundation as hf, strategy_intelligence as si
    # Hold the existing cross-process start lock throughout this dedicated process.
    with _exclusive(batch_job.runner.OUT / 'jobs/start.lock', timeout=30):
        if batch_job.runner_pids():
            raise RuntimeError('OTHER_RESEARCH_SESSION_ACTIVE')
        snapshot_id = hf.latest_evaluation()['snapshot_id']
        gov = v2.governance(write=False)
        if gov['status'] != 'MATCH':
            raise RuntimeError('GOVERNANCE_BASELINE_MISMATCH')
        params = {p['strategy_id']: p for p in v2.baseline_params() if p['strategy_id'] in STRATEGIES}
        register = {'registered_at': datetime.now(timezone.utc).isoformat(), 'snapshot_id': snapshot_id,
                    'authorization': 'User V4 directive: isolated non-formal research only', 'max_variants_per_strategy': 3,
                    'experiments': [{'strategy_id': sid, 'variant_id': 'ranking-only-v1', 'hypothesis': 'Preserve descending strategy score through filtering; no parameter changes',
                                     'parameters': p, 'binding': execution_binding(p, snapshot_id), 'code_hashes': code_hashes(), 'status': 'TODO', 'failure_reason': None, 'retry_count': 0} for sid, p in params.items()]}
        reg_path = OUT / 'STRATEGY_EXPERIMENT_REGISTER.json'
        if reg_path.exists():
            previous = json.loads(reg_path.read_text(encoding='utf-8'))
            try:
                old = {e['strategy_id']:e for e in previous['experiments']}
                for expected in register['experiments']:
                    require_binding(old[expected['strategy_id']], expected['binding'])
                register = previous
            except (KeyError, ValueError):
                archive = OUT / 'acceptance' / f"superseded_register_{datetime.now(timezone.utc):%Y%m%dT%H%M%S%fZ}.json"
                write(archive, previous)
                register['supersedes'] = archive.relative_to(OUT).as_posix()
                write(reg_path, register)
        else:
            write(reg_path, register)
        for sid in params:
            path = OUT / 'acceptance' / f'characterization_{sid}.json'
            char_binding = {'execution': execution_binding(params[sid], snapshot_id), 'window': ['2023-01-03', '2023-01-10']}
            if path.exists():
                previous = json.loads(path.read_text(encoding='utf-8'))
                try:
                    require_binding(previous, char_binding)
                except ValueError:
                    write(path.with_name(path.stem + f"_superseded_{datetime.now(timezone.utc):%Y%m%dT%H%M%S%fZ}.json"), previous)
                    # Retain the stale evidence; replace only this task's reusable checkpoint after rerunning.
                    previous = None
            else:
                previous = None
            if previous is None:
                # Sequential paths avoid two heavy snapshot workers in memory at once.
                a = v2._v1_window(sid, snapshot_id, '2023-01-03', '2023-01-10')
                b = v2._v2_window(sid, snapshot_id, '2023-01-03', '2023-01-10')
                tracks = b['tracks']
                frames = {'nav': pd.DataFrame({'date': [d for d, _ in tracks['base']['nav']], 'nav_base': [n for _, n in tracks['base']['nav']], 'nav_stress': [n for _, n in tracks['stress']['nav']]}),
                          'trades': pd.DataFrame(tracks['base']['trades']), 'trades_stress': pd.DataFrame(tracks['stress']['trades'])}
                diff = v2.compare(a, frames)
                write(path, {'status': 'PASS' if not diff else 'FAIL', 'differences': diff, 'snapshot_id': snapshot_id, 'window': ['2023-01-03', '2023-01-10'], 'code_hashes': code_hashes(), 'binding': char_binding})
                if diff:
                    raise RuntimeError('CHARACTERIZATION_FAILED')
            elif json.loads(path.read_text(encoding='utf-8'))['status'] != 'PASS':
                raise RuntimeError('CHARACTERIZATION_FAILED')
            print(f'CHARACTERIZATION PASS {sid}', flush=True)
        ctx = si.load_context()
        for experiment in register['experiments']:
            sid = experiment['strategy_id']; p = experiment['parameters']
            require_binding(experiment, execution_binding(p, snapshot_id))
            original_id = v2.lookup(v2.identity(p, snapshot_id))
            if not original_id:
                experiment.update(status='BLOCKED', failure_reason='NO_CURRENT_SAME_CONDITION_ORIGINAL')
                write(reg_path, register); continue
            experiment['status'] = 'RUNNING'; write(reg_path, register)
            print(f'RUNNING {sid}', flush=True)
            try:
                original = run_store.load_run(original_id, root=v2.RUNS)
                fixed = run_variant(p, snapshot_id, experiment['binding'])
                result = compare_pair(original, fixed, ctx)
                write(OUT / 'results' / f'{sid}.json', result)
                experiment.update(status='PASS' if result['status'] == 'COMPARABLE' else 'BLOCKED', run_id=fixed['run']['run_id'], original_run_id=original_id,
                                  failure_reason=None if result['status'] == 'COMPARABLE' else 'UNTRUSTED_COMPARISON')
                print(f"DONE {sid}: {result['conclusion']}", flush=True)
            except Exception as error:
                import traceback
                experiment.update(status='FAIL', failure_reason=f'{type(error).__name__}: {error}')
                write(OUT / 'acceptance' / f'failure_{sid}.json', {'traceback': traceback.format_exc()})
                print(f'FAILED {sid}: {error}', flush=True)
            write(reg_path, register)
        return 0 if all(e['status'] == 'PASS' for e in register['experiments']) else 2


if __name__ == '__main__':
    raise SystemExit(main())
