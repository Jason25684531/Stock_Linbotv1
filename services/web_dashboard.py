"""Read-only Web V3 composition. No backtest, evaluation, or result writes."""
from __future__ import annotations

import json
import math
import os
import threading
import time
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
# The saved V2 analysis identity contract; no import of executable research scripts.
ANALYSIS_FILES = ('scripts/platform_v2.py', 'core/research/platform/analysis.py', 'scripts/strategy_intelligence.py',
                  'core/research/intelligence/performance.py', 'core/research/intelligence/scenarios.py',
                  'core/research/intelligence/evaluate.py', 'core/research/intelligence/feasibility.py', 'scripts/historical_report.py')


def analysis_version():
    from core.backtest.run_store import canonical_hash, file_hash
    return canonical_hash({name: file_hash(ROOT / name) for name in ANALYSIS_FILES})[:12]


def linked_analysis(record, envelope):
    """Require the current saved analysis and exact run evidence before exposing metrics."""
    if not isinstance(envelope, dict) or not record.get('publishable'):
        return None
    evidence, result = envelope.get('evidence') or {}, envelope.get('result') or {}
    if evidence.get('data_class') != 'REAL_RESEARCH' or record.get('evidence', {}).get('data_class') != 'REAL_RESEARCH':
        return None
    if any(evidence.get(k) != record.get(k) for k in ('run_id', 'strategy_id', 'snapshot_id', 'engine_version', 'rule_version')):
        return None
    if (result.get('run_id') != record.get('run_id') or result.get('strategy_id') != record.get('strategy_id')
            or result.get('identity_status') != 'CURRENT' or not result.get('publishable')
            or result.get('analysis_code') != analysis_version()):
        return None
    performance = result.get('performance') or {}
    series = performance.get('series') or {}
    dates = series.get('date') or []
    if (not dates or [dates[0], dates[-1]] != record.get('period')
            or any(len(series.get(k) or []) != len(dates) for k in ('nav_base', 'nav_stress', 'drawdown'))):
        return None
    return result


def clean(value):
    """JSON has no NaN; preserve unknown values as null, including chart gaps."""
    if isinstance(value, dict):
        return {k: clean(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [clean(v) for v in value]
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value


MAX_AGE_SECONDS = 600
_rows_cache = {}
_refreshing = threading.Lock()


def strategies(routes, intelligence):
    """Cached _strategies. Verifying every stored run touches ~2,400 files (4 s on the container's bind mount), so the rows are
    reused while the folders and pointers they come from are unchanged; an in-place edit is re-verified within MAX_AGE_SECONDS."""
    key = (tuple(intelligence._catalog()), *(_stamp(p) for p in (
        routes.RUNS, routes.ANALYSIS, routes.OUT / 'acceptance' / 'publication' / 'backtest', routes.OUT / 'STRATEGY_READINESS_21.json',
        intelligence.OUT / 'LATEST.json')))
    hit = _rows_cache.get(key)
    if hit is None:
        return _refresh(key, routes, intelligence)
    if time.monotonic() - hit[0] >= MAX_AGE_SECONDS and _refreshing.acquire(blocking=False):
        # serve the verified rows already held while one background pass re-verifies; a page view never waits for it
        threading.Thread(target=_refresh, args=(key, routes, intelligence, True), daemon=True).start()
    return hit[1]


def _refresh(key, routes, intelligence, release=False):
    try:
        rows = _strategies(routes, intelligence)
        _rows_cache.clear()
        _rows_cache[key] = (time.monotonic(), rows)
        return rows
    finally:
        if release:
            _refreshing.release()


def _stamp(path):
    try:
        return str(path), os.stat(path).st_mtime_ns
    except OSError:
        return str(path), None


def _strategies(routes, intelligence):
    """Join by catalog ID, retaining blocked cards and one current reviewed report per ID."""
    readiness, runs = routes._readiness(), routes._runs(routes.RUNS)
    rows = []
    for sid in intelligence._catalog():
        card = intelligence.result(sid) or {}
        ready = readiness.get(sid) or {}
        related = [r for r in runs if r.get('strategy_id') == sid]
        selected, report = None, None
        for run in related:
            if not run.get('publishable'):
                continue
            try:
                loaded = routes._loaded(routes.RUNS, run['run_id'])
                candidate = linked_analysis(loaded['run'], routes.analysis(run['run_id']))
            except Exception as error:
                from werkzeug.exceptions import HTTPException
                if not isinstance(error, (HTTPException, OSError, ValueError, KeyError)):
                    raise
                continue
            if candidate:
                selected, report = loaded['run'], candidate
                break
        state = ready.get('state') or card.get('status') or 'DATA_MISSING'
        trust = state if state in ('DATA_BLOCKED', 'GOVERNED_ONLY') else ('VERIFIED' if (report or {}).get('performance', {}).get('verified') else 'PROVISIONAL')
        dimensions = (report or {}).get('dimensions') or {}
        track = ((report or {}).get('performance') or {}).get('verified') or ((report or {}).get('performance') or {}).get('provisional') or {}
        fields = (card.get('definition') or {}).get('fields') or {}
        rows.append({'strategy_id': sid, 'name': card.get('display_name') or sid,
                     'type': (card.get('profile') or {}).get('primary_type', 'DATA_MISSING'),
                     'type_label': ((card.get('profile') or {}).get('type') or {}).get('label', 'DATA_MISSING'),
                     'card': card, 'readiness': ready, 'trust': trust, 'state': state,
                     'reason': ready.get('reason') or card.get('reason'), 'runs': related, 'selected': selected,
                     'report': report, 'track': track, 'target': dimensions.get('return_target') or {'status': 'INSUFFICIENT_DATA'},
                     'edge': dimensions.get('type_edge') or {}, 'fields_present': sum(bool(f.get('text')) for f in fields.values()),
                     'fields_total': len(fields)})
    return rows


HEALTH_QUESTIONS = (('earn', '賺多少？'), ('drawdown', '跌多深？'), ('efficiency', '划算嗎？'), ('consistency', '常贏嗎？'),
                    ('tradability', '買得到嗎？'))
REGIMES = (('BULL', '多頭'), ('SIDEWAYS', '盤整'), ('BEAR', '空頭'))
SORTS = {'cagr': lambda r: -r['cagr'], 'mdd': lambda r: -r['mdd'], 'name': lambda r: r['name']}


def segments(values, width, height, lo=None, hi=None, points=160):
    """SVG polyline point strings, one per run of known values: a missing value breaks the line instead of being drawn as zero."""
    values = list(values or [])
    known = [v for v in values if v is not None]
    if len(known) < 2:
        return []
    lo = min(known) if lo is None else lo
    span = ((max(known) if hi is None else hi) - lo) or 1
    last, step = len(values) - 1, max(1, len(values) // points)
    out, run = [], []
    for i in [*range(0, last, step), last]:
        if values[i] is None:
            out, run = out + [run] if len(run) > 1 else out, []
            continue
        run.append(f'{i / last * width:.1f},{height - 2 - (values[i] - lo) / span * (height - 4):.1f}')
    return [' '.join(r) for r in out + [run] if len(r) > 1]


def health_strip(report):
    """One status per question, read from the saved items: the most severe judged status (RISK > CAUTION > GOOD).
    A question whose items were never judged keeps its saved status, so INSUFFICIENT_DATA is never shown as good."""
    strip = []
    for key, question in HEALTH_QUESTIONS:
        items = ((report or {}).get('health') or {}).get(key) or []
        statuses = [i.get('status') for i in items]
        status = next((s for s in ('RISK', 'CAUTION', 'GOOD', 'INSUFFICIENT_DATA') if s in statuses), 'NOT_APPLICABLE' if items else None)
        strip.append({'key': key, 'question': question, 'status': status, 'lead': items[0] if items else None, 'entries': items})
    return strip


def card(row):
    """The strategy-wall card: figures exist only when a reviewed report exists, and come from that same report."""
    track, series = row['track'], (((row['report'] or {}).get('performance') or {}).get('series') or {})
    value = lambda key: (track.get(key) or {}).get('value') if row['report'] else None
    nav = [v for v in series.get('nav_base') or [] if v is not None]
    return {'strategy_id': row['strategy_id'], 'name': row['name'], 'type_label': row['type_label'],
            'trust': row['trust'], 'reason': row['reason'], 'cagr': value('cagr'), 'mdd': value('max_drawdown'),
            'mdd_reason': (track.get('max_drawdown') or {}).get('reason'),
            'track': 'VERIFIED' if (row['report'] or {}).get('performance', {}).get('verified') else 'PROVISIONAL',
            'spark': segments(series.get('nav_base'), 240, 44, points=120), 'health': health_strip(row['report']),
            'rising': len(nav) > 1 and nav[-1] >= nav[0]}  # colour by the line's own direction, not by another period's metric


def wall(rows, args):
    """Catalog order by default. A visitor sort only reorders stored values; strategies without a value go last."""
    cards = [card(r) for r in rows]
    kinds = sorted({c['type_label'] for c in cards})
    cards = [c for c in cards if (not args.get('type') or c['type_label'] == args['type'])
             and (not args.get('trust') or c['trust'] == args['trust'])]
    sort = args.get('sort') if args.get('sort') in SORTS else ''
    if sort:
        known = [c for c in cards if sort == 'name' or c[sort] is not None]
        cards = sorted(known, key=SORTS[sort]) + [c for c in cards if c not in known]
    return {'cards': cards, 'types': kinds, 'sort': sort, 'total': len(rows)}


def report_view(row):
    """Everything the visual report draws, taken from the one reviewed analysis the strategy wall also uses."""
    report = row['report'] or {}
    perf = report.get('performance') or {}
    prov, series = perf.get('provisional') or {}, perf.get('series') or {}
    nav, bench = series.get('nav_base') or [], series.get('benchmark_total_return') or []
    known = [v for v in nav + bench if v is not None]
    lo, hi = (min(known), max(known)) if known else (None, None)
    years = [{'year': y, 'value': (v or {}).get('base'), 'reason': None if (v or {}).get('base') is not None else 'NO_VALUE'}
             for y, v in (prov.get('yearly') or {}).items()]
    regimes = perf.get('regime', {}).get('regimes') or {}
    track = 'VERIFIED_ONLY' if regimes.get('VERIFIED_ONLY') else 'VERIFIED_AND_PARTIAL'
    regime_rows = [{'label': label, **(((regimes.get(track) or {}).get(code) or {}).get('base') or {}).get('annualized_return', {'value': None, 'reason': 'NO_REGIME_SESSIONS'})}
                   for code, label in REGIMES]
    return {'prov': prov, 'verified': perf.get('verified'), 'verified_span': perf.get('verified_span'), 'dates': series.get('date') or [], 'nav': nav,
            'nav_line': segments(nav, 900, 240, lo, hi, points=400), 'bench_line': segments(bench, 900, 240, lo, hi, points=400),
            'dd_line': segments(series.get('drawdown'), 900, 60, None, 0, points=400),
            'years': years, 'winning_years': sum(1 for y in years if (y['value'] or 0) > 0), 'health': health_strip(report),
            'regimes': regime_rows, 'regime_track': track, 'scenarios': report.get('scenarios') or {}, 'monthly': report.get('monthly') or {}}


def resources():
    import psutil
    return {'available_memory_gib': round(psutil.virtual_memory().available / 2**30, 2),
            'free_disk_gib': round(shutil.disk_usage(ROOT).free / 2**30, 2),
            'logical_cpus': psutil.cpu_count(),
            'cache_present': (ROOT / 'outputs/platform_v2/feature_cache').is_dir()}


def operations_status():
    """Captured acceptance evidence, never a live health or activation assertion."""
    try:
        value = json.loads((ROOT / 'outputs/final_system_acceptance/operations_summary.json').read_text(encoding='utf-8'))
        if not isinstance(value, dict) or not isinstance(value.get('captured_at'), str):
            raise ValueError('invalid acceptance summary')
        return {k: v for k, v in value.items() if k in (
            'captured_at', 'status', 'scope', 'last_success', 'last_failure', 'next_run',
            'recent_error', 'retry_count', 'recovery', 'backup', 'attention')}
    except (OSError, ValueError):
        return {'status': 'UNAVAILABLE', 'scope': 'Acceptance evidence unavailable; live operations unverified'}


def report_meta(record, envelope, card):
    return {'Definition ID (V1 card)': (card.get('definition') or {}).get('definition_id'),
            'Profile Version (V1 card)': (card.get('profile') or {}).get('profile_id'),
            'Evaluation ID (V1 card)': (card.get('versions') or {}).get('evaluation_id'),
            'V2 analysis version': ((envelope or {}).get('result') or {}).get('analysis_code'),
            'Report Generated At': ((envelope or {}).get('evidence') or {}).get('source_timestamp'),
            'Source Files': f"outputs/platform_v2/runs/{record['run_id']}/manifest.json; outputs/platform_v2/analysis/{record['run_id']}__{((envelope or {}).get('result') or {}).get('analysis_code', 'DATA_MISSING')}.json"}
