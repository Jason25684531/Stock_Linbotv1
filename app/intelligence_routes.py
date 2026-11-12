"""Strategy intelligence pages (change 2026-11-11-strategy-intelligence-platform-v1).

GET pages read stored results under outputs/strategy_intelligence only; no request computes anything. Admin POSTs start or cancel the
intelligence job through the shared batch_job slot, behind the existing admin session and CSRF check. Guests are read-only.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from flask import abort, flash, jsonify, redirect, render_template, request, send_file, url_for

from app import app
from app import platform_routes

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'outputs/strategy_intelligence'
RESULTS = OUT / 'results'
NOTES = ROOT / 'core/research/intelligence/data/implementation_notes.json'
REPORTS = ('STRATEGY_INTELLIGENCE_FINAL_ACCEPTANCE_REPORT.md', 'REPORT_TABLES.md', 'DEFERRED_FINDINGS.md')
IDENTITY = re.compile(r'(SI1|PF1)-[0-9a-f]{16}')


def _read(path: Path):
    try:
        return json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return None


def _catalog() -> list:
    from core.research.reference.catalog import capabilities
    return [c.strategy_id for c in capabilities()]


def _file(sid: str) -> str:
    return f"{sid.replace(':', '__')}.json"


def latest() -> dict:
    return _read(OUT / 'LATEST.json') or {}


def result(sid: str, identity: str | None = None) -> dict | None:
    """The stored result of one strategy: the requested identity when it exists, else the latest job's."""
    identity = identity or latest().get('strategies', {}).get(sid)
    if not identity or not IDENTITY.fullmatch(identity):
        return None
    return _read(RESULTS / identity / _file(sid))


def identities(sid: str) -> list:
    """Every stored result identity for the strategy (newest first), for the result switcher."""
    found = [(p.stat().st_mtime, p.parent.name) for p in RESULTS.glob(f'SI1-*/{_file(sid)}')] if RESULTS.is_dir() else []
    return [name for _, name in sorted(found, reverse=True)]


def job_panel() -> dict:
    from core.research.reference import batch_job
    state = batch_job.state()
    job_id = state.get('job_id') if state.get('kind') == 'intelligence' else latest().get('job_id')
    job = _read(OUT / 'jobs' / f'{job_id}.json') if job_id else None
    if job and job.get('status') == 'RUNNING' and state.get('job_id') == job_id and state.get('status') in ('CANCELLED', 'FAILED'):
        job['status'] = state['status']  # a cancelled or killed process cannot finish its own job file
    rows = list((job or {}).get('rows', {}).values())
    counts = {}
    for r in rows:
        counts[r['status']] = counts.get(r['status'], 0) + 1
    return {'slot': state, 'job': job, 'rows': rows, 'counts': counts, 'latest': latest()}


@app.route('/intelligence')
def intelligence_index():
    rows = []
    for sid in _catalog():
        r = result(sid)
        rows.append({'strategy_id': sid, 'result': r, 'failed': latest().get('failed', {}).get(sid)})
    reports = [name for name in REPORTS if (OUT / name).is_file()]
    return render_template('intelligence_index.html', rows=rows, panel=job_panel(), reports=reports, active_nav='intelligence_index')


@app.route('/intelligence/portfolio')
def intelligence_portfolio():
    key = latest().get('portfolio')
    folio = _read(RESULTS / key / 'PORTFOLIO.portfolio.json') if key and IDENTITY.fullmatch(key) else None
    return render_template('intelligence_portfolio.html', folio=folio, active_nav='intelligence_index')


@app.route('/intelligence/forward')
def intelligence_forward():
    from core.research import latest_signal
    state = latest_signal.readiness()
    return render_template('intelligence_forward.html', state=state, active_nav='intelligence_index')


@app.route('/intelligence/reports/<name>')
def intelligence_report(name):
    if name not in REPORTS or not (OUT / name).is_file():
        abort(404)
    return send_file(OUT / name, mimetype='text/markdown', as_attachment=True, download_name=name)


@app.route('/intelligence/<sid>/evidence.json')
def intelligence_evidence(sid):
    if sid not in _catalog():
        abort(404)
    identity = request.args.get('result') or latest().get('strategies', {}).get(sid)
    if not identity or not IDENTITY.fullmatch(identity) or result(sid, identity) is None:
        abort(404)
    return send_file(RESULTS / identity / _file(sid), mimetype='application/json', as_attachment=True, download_name=f'{identity}_{_file(sid)}')


@app.route('/intelligence/<sid>')
def intelligence_strategy(sid):
    if sid not in _catalog():
        abort(404)
    chosen = request.args.get('result')
    if chosen and not IDENTITY.fullmatch(chosen):
        abort(404)
    r = result(sid, chosen)
    if chosen and r is None:
        abort(404)
    current = latest().get('strategies', {}).get(sid)
    return render_template('intelligence_strategy.html', sid=sid, r=r, chosen=chosen, versions=identities(sid), active_nav='intelligence_index',
                           superseded=bool(r and current and r['identity'] != current), failed=latest().get('failed', {}).get(sid),
                           notes=implementation_notes(sid))


def implementation_notes(sid: str) -> dict:
    notes = _read(NOTES) or {}
    return {'strategy': notes.get('strategies', {}).get(sid, []), 'engine': notes.get('engine', []), 'source': notes.get('source')}


@app.route('/admin/intelligence/<action>', methods=['POST'])
def admin_intelligence(action):
    if action not in ('start', 'cancel'):
        abort(404)
    refused = platform_routes._require_admin_write()
    if refused:
        return jsonify(refused[0]), refused[1]
    from core.research.reference import batch_job
    if action == 'cancel' and batch_job.state().get('kind') != 'intelligence':  # never cancels a history or reference batch from here
        return jsonify({'status': 'NOT_RUNNING', 'reason': 'the running job is not an intelligence job'}), 409
    outcome = batch_job.start(workers=1, kind='intelligence') if action == 'start' else batch_job.cancel()
    if outcome['status'] in ('RUN_IN_PROGRESS', 'NOT_RUNNING'):
        return jsonify(outcome), 409
    flash('策略分析工作已開始；相同身份的階段會直接重用，重新整理查看進度' if action == 'start' else '工作已取消；已完成的階段保留，下次會直接重用', 'success')
    return redirect(url_for('intelligence_index'))
