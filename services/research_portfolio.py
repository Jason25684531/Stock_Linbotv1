"""Read-only view of the V7 research program: stored results against the registered targets. Never computes, never imports the runner."""
import hashlib
import json
from pathlib import Path

import pandas as pd

from services.web_dashboard import segments

ROOT = Path(__file__).resolve().parent.parent / 'outputs' / 'strategy_v7'
EXTRA_ROOTS = (ROOT.parent / 'strategy_v8',)
MET_VERDICTS = ('PROMISING', 'HOLDOUT_PASS')


def _sha(data):
    return hashlib.sha256(data).hexdigest()


def _json(path):
    return json.loads(path.read_text(encoding='utf-8'))


def _chain(path):
    """Queue rows when every row hashes to its sha and links to the previous one; None when any row was touched."""
    rows, prev = [], ''
    for line in path.read_text(encoding='utf-8').splitlines() if path.exists() else []:
        row = json.loads(line)
        sha = row.pop('sha', None)
        if row.get('prev') != prev or sha != _sha((prev + json.dumps(row, sort_keys=True, ensure_ascii=False)).encode()):
            return None
        rows.append(row | {'sha': sha})
        prev = sha
    return rows


def _gaps(cell, th):
    rows = (('cagr', cell['cagr']['value'], th['base_cagr'] - (cell['cagr']['value'] or 0)),
            ('stress', cell['cagr_stress']['value'], th['stress_cagr'] - (cell['cagr_stress']['value'] or 0)),
            ('drawdown', cell['max_drawdown']['value'], th['max_drawdown'] - (cell['max_drawdown']['value'] or 0)))
    return [{'name': name, 'gap': gap} for name, value, gap in rows if value is not None and gap > 0]


def _diagnostic_drawdown(root, result, window):
    """Worst drawdown of the same stored run over the whole registered window, for a cell whose verified span is shorter; None without a stored run."""
    path = root / 'runs' / str(result.get('run_id')) / 'nav.csv'
    if not path.is_file():
        return None
    nav = pd.read_csv(path, parse_dates=['date']).set_index('date').nav_base
    inside = nav[(nav.index.year >= window[0]) & (nav.index.year <= window[1])]
    return float((inside / inside.cummax() - 1).min()) if len(inside) else None


def _cell(result, th, windows, root):
    nav = (result.get('series') or {}).get('nav_base') or []
    peak, drawdown = 0.0, []
    for value in nav:
        peak = max(peak, value)
        drawdown.append(value / peak - 1)
    dates = (result.get('series') or {}).get('date') or []
    covered = {'start': dates[0], 'end': dates[-1], 'years': (pd.Timestamp(dates[-1]) - pd.Timestamp(dates[0])).days / 365.25} if dates else None
    first, last = result['span']
    window = windows.get(result['window'], result['span'])
    gaps = _gaps(result, th)
    excluded = [y for y in range(window[0], window[1] + 1) if not first <= y <= last]
    defined = all(result[k]['value'] is not None for k in ('cagr', 'cagr_stress', 'max_drawdown'))
    return result | {'covered': covered, 'span_mismatch': bool(covered) and covered['years'] < last - first + 1 - 0.5,
                     'excluded_years': excluded, 'window_span': list(window),
                     'diagnostic_drawdown': _diagnostic_drawdown(root, result, window) if excluded else None,
                     'gaps': gaps, 'targets_met': result['verdict'] in MET_VERDICTS and defined and not gaps,
                     'spark': segments(nav, 240, 48) if nav else [],
                     'drawdown_spark': segments(drawdown, 240, 48, lo=min(drawdown), hi=0.0) if drawdown else []}


def view(root=None, data_manifest=None):
    """One program's stored results. A later program that reuses the first program's data passes that program's manifest as the data gate."""
    root = Path(root or ROOT)
    shut = {'cells': [], 'invalid': [], 'thresholds': {}, 'labels': [], 'outcome': None}
    if not (root / 'charter.json').exists():
        return shut | {'state': 'NO_CHARTER'}
    charter = _json(root / 'charter.json')
    seal = root / 'charter.sha256'
    if not seal.exists() or seal.read_text(encoding='ascii').strip() != _sha((root / 'charter.json').read_bytes()):
        return shut | {'state': 'CHAIN_BROKEN'}
    shut |= {'thresholds': charter['thresholds'], 'labels': charter.get('labels', [])}
    if not Path(data_manifest or root / 'data' / 'manifest.json').exists():
        return shut | {'state': 'NO_DATA'}
    rows = _chain(root / 'queue.jsonl')
    if rows is None:
        return shut | {'state': 'CHAIN_BROKEN'}
    latest = {(r['hypothesis'], r['variant'], r['window']): r for r in rows if r.get('event') in ('RESULT', 'HOLDOUT')}
    cells, invalid = [], []
    for (hid, vid, window), row in sorted(latest.items()):
        name = f'{hid}__{vid}__{window}'
        path = root / 'results' / f'{name}.json'
        if not path.exists() or _sha(path.read_bytes()) != row['result_sha256']:
            invalid.append(name)
        else:
            cells.append(_cell(json.loads(path.read_bytes()), charter['thresholds'], charter.get('windows', {}), root))
    report = _json(root / 'report.json') if (root / 'report.json').exists() else {}
    current = report.get('final') and rows and report.get('queue_head') == rows[-1]['sha'] and report.get('charter_sha256') == _sha((root / 'charter.json').read_bytes())
    return shut | {'state': 'OK' if cells or invalid else 'NO_RESULTS', 'cells': cells, 'invalid': invalid, 'outcome': report.get('outcome') if current else None}


def programs():
    """(name, view) for the V7 program and every later program whose folder holds a charter, in order. Each view verifies its own queue and charter."""
    roots = [('V7', Path(ROOT))] + [(Path(r).name.removeprefix('strategy_').upper(), Path(r)) for r in EXTRA_ROOTS if (Path(r) / 'charter.json').exists()]
    shared = Path(ROOT) / 'data' / 'manifest.json'
    return [(name, view(root, None if name == 'V7' else shared)) for name, root in roots]
