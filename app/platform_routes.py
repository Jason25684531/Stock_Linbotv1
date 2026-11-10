"""Backtest run history, Factor Combination Lab, data management (admin) and latest research selection routes.

Read paths only list files named by run manifests or the frozen lab queue; every write needs an admin session and a CSRF token.
"""
from __future__ import annotations

from datetime import date
import hmac
import json
import os
from pathlib import Path
import re
import secrets
import threading
import time

import pandas as pd
from flask import abort, flash, jsonify, redirect, render_template, request, send_file, session, url_for

from core.backtest import run_store
from core.research import latest_signal
from . import app, research_payloads

ROOT = Path(__file__).resolve().parents[1]
LAB = ROOT / 'outputs/research_mode/combination_lab_v1'
REPORT = ROOT / 'docs/research/2026-11-06-multi-strategy-validation-and-composite-v1/report.html'
DEFAULT_SECRET = 'dev-secret-key-change-in-production'
ADMIN_ENV = 'STOCK_LINBOT_ADMIN_TOKEN'
ADMIN_TTL = 30 * 60
LOGIN_WINDOW, LOGIN_LIMIT = 15 * 60, 5
LAB_KINDS = ('single', 'weighted', 'intersection')
_failures: dict[str, list[float]] = {}  # ponytail: per-process login counter, resets on restart; shared store if multi-process

app.config.update(SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE='Lax',
                  SESSION_COOKIE_SECURE=os.environ.get('STOCK_LINBOT_HTTPS') == '1')


# ---------------------------------------------------------------- admin session

def admin_state():
    """(enabled, reason): writes are blocked without a token or with the default secret key."""
    if not os.environ.get(ADMIN_ENV):
        return False, f'AUTH_BLOCKED: {ADMIN_ENV} is not set'
    if app.secret_key in (None, '', DEFAULT_SECRET):
        return False, 'AUTH_BLOCKED: FLASK_SECRET_KEY is the default value'
    return True, ''


def is_admin():
    return admin_state()[0] and session.get('admin') is True and time.time() - float(session.get('admin_at', 0)) < ADMIN_TTL


def csrf_token():
    if 'csrf' not in session:
        session['csrf'] = secrets.token_urlsafe(32)
    return session['csrf']


def _csrf_ok():
    body = request.get_json(silent=True) if request.is_json else None
    sent = request.form.get('csrf') or request.headers.get('X-CSRF-Token') or (body or {}).get('csrf') or ''
    return bool(session.get('csrf')) and hmac.compare_digest(sent, session['csrf'])


def _require_admin_write():
    """None when allowed, else the (body, status) refusal."""
    enabled, reason = admin_state()
    if not enabled:
        return {'status': 'AUTH_BLOCKED', 'reason': reason}, 403
    if not is_admin():
        return {'status': 'AUTH_REQUIRED', 'reason': 'admin login required'}, 403
    if not _csrf_ok():
        return {'status': 'CSRF_FAILED', 'reason': 'missing or invalid CSRF token'}, 403
    return None


@app.context_processor
def _platform_context():
    return {'csrf_token': csrf_token, 'is_admin': is_admin}


@app.route('/admin/login', methods=['GET', 'POST'])
def admin_login():
    enabled, reason = admin_state()
    if request.method == 'GET':
        return render_template('admin_data.html', view='login', admin_enabled=enabled, admin_reason=reason, status=None, history=[])
    address = request.remote_addr or 'unknown'
    recent = [t for t in _failures.get(address, []) if time.time() - t < LOGIN_WINDOW]
    _failures[address] = recent
    if len(recent) >= LOGIN_LIMIT:
        return jsonify({'status': 'RATE_LIMITED', 'reason': f'{LOGIN_LIMIT} failed logins in {LOGIN_WINDOW // 60} minutes'}), 429
    if not enabled:
        return jsonify({'status': 'AUTH_BLOCKED', 'reason': reason}), 403
    if not _csrf_ok() or not hmac.compare_digest((request.form.get('token') or '').encode(), os.environ[ADMIN_ENV].encode()):
        recent.append(time.time())
        flash('登入失敗', 'error')
        return redirect(url_for('admin_login'))
    session.clear()
    session.update(admin=True, admin_at=time.time(), csrf=secrets.token_urlsafe(32))
    return redirect(url_for('admin_data'))


@app.route('/admin/logout', methods=['POST'])
def admin_logout():
    session.clear()
    return redirect(url_for('admin_login'))


# ---------------------------------------------------------------- data management

def _service():
    from services.market_data_update import MarketDataUpdateService
    return MarketDataUpdateService()


@app.route('/admin/data', methods=['GET', 'POST'])
def admin_data():
    enabled, reason = admin_state()
    service = _service()
    if request.method == 'GET':
        return render_template('admin_data.html', view='data', admin_enabled=enabled, admin_reason=reason, status=service.status(),
                               history=service.history(20), admin=is_admin())
    refused = _require_admin_write()
    if refused:
        return jsonify(refused[0]), refused[1]
    action = request.form.get('action')
    if action == 'toggle':
        from services.market_data_update import load_schedule, save_schedule
        current = load_schedule(service.root)
        save_schedule(service.root, request.form.get('enabled') == '1', request.form.get('time') or current.get('time', '18:30'))
        return redirect(url_for('admin_data'))
    if action not in ('manual', 'backfill'):
        return jsonify({'status': 'INVALID_REQUEST', 'reason': 'unknown action'}), 400
    if service.status().get('update_in_progress'):
        return jsonify({'status': 'UPDATE_IN_PROGRESS'}), 409
    dates = None
    if action == 'backfill':
        from services.market_data_update import validate_dates
        dates = [d.strip() for d in (request.form.get('dates') or '').split(',') if d.strip()]
        try:
            validate_dates(dates, date.today())
        except ValueError as exc:
            return jsonify({'status': 'INVALID_REQUEST', 'reason': str(exc)}), 400
    force = request.form.get('force') == '1'
    threading.Thread(target=service.run, args=(action,), kwargs={'dates': dates, 'force': force}, daemon=True).start()
    flash('市場資料更新已開始；完成後重新整理本頁查看紀錄', 'success')
    return redirect(url_for('admin_data'))


# ---------------------------------------------------------------- backtest runs

def _run_or_404(run_id):
    try:
        return run_store.load_run(run_id)
    except run_store.ManifestMismatch:
        abort(503)
    except (FileNotFoundError, OSError):
        abort(404)


V2_EVIDENCE_FILES = ('trades',) + run_store.REQUIRED_FILES_V2[len(run_store.REQUIRED_FILES):]
# legacy runs pin these admitted inputs (scripts/legacy_baseline_run.PINNED_INPUTS); a version 2 run names its own in admission_record
LEGACY_PINNED = {'benchmark': 'tri_source', 'calendar': 'tri_calendar'}


def _ledger():
    """(verified, {experiment_id: row}) of the trusted trial ledger; core.backtest never imports scripts, the web layer reads it."""
    from scripts import research_workflow as rw
    rows = {}
    for row in rw._trial_rows():
        rows.setdefault(row['experiment_id'], row)  # the admission (TRIAL) row, not a later REPLAY of it
    return rw.verify_trials() == 'PASS', rows


def evidence_for(loaded, hash_inputs=True):
    """Evidence status of one verified run, plus which version 2 files exist and whether benchmark and calendar are pinned."""
    from scripts import research_workflow as rw
    try:
        verified, rows = _ledger()
    except Exception as exc:  # an unreadable ledger is never an admission
        verified, rows = False, {}
        status = {'status': 'POST_RUN_NOT_ADMISSION_VERIFIED', 'reasons': [f'LEDGER_UNREADABLE:{type(exc).__name__}'], 'reproducible_offline': False}
    else:
        status = run_store.evidence_status(loaded, rows.get(loaded['run'].get('trial_id')), verified, rw._tree_sha if hash_inputs else None)
    row = rows.get(loaded['run'].get('trial_id')) or {}
    names = {**LEGACY_PINNED, **((loaded['run'].get('admission_record') or {}).get('pinned') or {})}
    pinned = {role: 'PINNED_AT_ADMISSION' if names[role] in (row.get('inputs_sha256') or {}) else 'NOT_PINNED' for role in LEGACY_PINNED}
    files = loaded.get('files') or []
    return {**status, 'pinned': pinned,
            'files': {name: f'{name}.csv' in files or f'{name}.json' in files for name in V2_EVIDENCE_FILES}}


def _metric_rows(metrics):
    rows = []
    for name, value in (metrics or {}).items():
        if isinstance(value, dict) and 'value' in value:
            rows.append({'name': name, 'value': value.get('value'), 'reason': value.get('reason')})
    return rows


@app.route('/backtest/runs')
def backtest_runs():
    strategy = (request.args.get('strategy') or '').strip() or None
    runs = run_store.history(strategy_id=strategy)[:200]
    try:
        ledger = _ledger()
        evidence = {r['run_id']: run_store.evidence_status({'run': r}, ledger[1].get(r.get('trial_id')), ledger[0]) for r in runs if r['verified']}
    except Exception:  # the list still renders; each row shows '-'
        evidence = {}
    return render_template('backtest_runs.html', runs=runs, strategy=strategy, evidence=evidence)


@app.route('/api/backtest/runs')
def api_backtest_runs():
    strategy = (request.args.get('strategy') or '').strip() or None
    rows = run_store.history(strategy_id=strategy)[:200]
    keep = ('run_id', 'status', 'status_class', 'strategy_id', 'strategy_type', 'period', 'created_at', 'comparison_group', 'certification_status', 'verified', 'flags')
    return jsonify({'runs': [{k: r.get(k) for k in keep} for r in rows],
                    'latest_successful_run': (run_store.latest(strategy) or {}).get('run_id') if strategy else None,
                    'latest_reference_run': (run_store.latest(strategy, 'reference') or {}).get('run_id') if strategy else None})


@app.route('/backtest/runs/<run_id>')
def backtest_run_detail(run_id):
    loaded = _run_or_404(run_id)
    run = loaded['run']
    others, refused = [], None
    for other_id in [x for x in (request.args.get('compare') or '').split(',') if x][:4]:
        other = _run_or_404(other_id)
        if other['run']['comparison_group'] != run['comparison_group'] or run['status'] != 'SUCCESS' or other['run']['status'] != 'SUCCESS':
            fields = [k for k in ('comparison_group', 'period', 'cost_model', 'benchmark', 'data_version') if other['run'].get(k) != run.get(k)]
            refused = f'不同比較群組，拒絕並列比較；差異欄位：{", ".join(fields) or "status"}'
            others = []
            break
        others.append(other)
    curves, metrics = [], {}
    for item in [loaded] + others:
        if item['run']['status'] != 'SUCCESS' or 'nav.csv' not in item['files']:
            continue
        nav = run_store.read_frame(item, 'nav')
        column = 'nav_base' if 'nav_base' in nav.columns else next((c for c in nav.columns if c not in ('date', 'roi')), None)
        if column:
            curves.append({'x': nav.iloc[:, 0].astype(str).tolist(), 'y': nav[column].astype(float).tolist(), 'name': item['run']['run_id'], 'type': 'scatter'})
        metrics[item['run']['run_id']] = run_store.read_frame(item, 'metrics') if 'metrics.json' in item['files'] else {}
    error = run_store.read_frame(loaded, 'error') if 'error.json' in loaded['files'] else None
    trades = run_store.read_frame(loaded, 'trades').head(500).to_dict('records') if 'trades.csv' in loaded['files'] else []
    return render_template('backtest_run_detail.html', run=run, error=error, curves=json.dumps(curves), metrics=metrics,
                           metric_rows={rid: _metric_rows((m or {}).get('metrics') or {}) for rid, m in metrics.items()},
                           compare_refused=refused, trades=trades, history=run_store.history(strategy_id=run.get('strategy_id'))[:20],
                           evidence=evidence_for(loaded))


@app.route('/reports/multi-strategy')
def multi_strategy_report():
    if not REPORT.is_file():
        abort(404)
    return send_file(REPORT, mimetype='text/html', as_attachment=request.args.get('download') == '1', download_name='multi_strategy_report.html')


# ---------------------------------------------------------------- Factor Combination Lab

def _read_json(path, default=None):
    try:
        return json.loads(Path(path).read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return default


def lab_state():
    queue = _read_json(LAB / 'queue.json', {}) or {}
    summary = _read_json(LAB / 'lab_summary.json')
    items = {}
    for path in sorted((LAB / 'items').glob('*.json')) if (LAB / 'items').is_dir() else []:
        data = _read_json(path, {}) or {}
        items[data.get('item_id')] = {k: v for k, v in data.items() if k not in ('traceback', 'item')}
    conditions = pd.read_csv(LAB / 'conditions.csv').fillna('').to_dict('records') if (LAB / 'conditions.csv').is_file() else []
    combos = pd.read_csv(LAB / 'combinations.csv').to_dict('records') if (LAB / 'combinations.csv').is_file() else []
    queued = {q['item_id']: q for q in queue.get('queue', [])}
    for combo in combos:
        item_id = 'LAB:' + combo['combination_id']
        state = items.get(item_id, {})
        combo['queue_position'] = queued.get(item_id, {}).get('position')
        combo['status'] = state.get('status') or ('PENDING' if item_id in queued else 'NOT_TESTED_COVERAGE' if combo['size'] > 1 and combo['coverage'] < 0.8 else 'NOT_TESTED_BUDGET' if combo['size'] > 1 else 'PENDING')
        combo['run_id'] = state.get('run_id')
        combo['trial_id'] = state.get('experiment_id')
    return {'queue': queue, 'summary': summary, 'item_states': items, 'conditions': conditions, 'combinations': combos}


@app.route('/lab/combinations')
def lab_combinations():
    state = lab_state()
    return render_template('lab_combinations.html', state=state, heatmap=json.dumps((state['summary'] or {}).get('heatmap')),
                           admin=is_admin())


@app.route('/reference')
def reference_comparison():
    """Fixed 21-row reference comparison (decision.md D3): the latest batch report as stored, grouped by comparison_group in catalog order;
    no sort, score, ranking or JSON endpoint."""
    from core.research.reference import runner
    report = runner.latest_report()
    gaps = {}
    if report is not None:
        gaps = (_read_json(runner.OUT / 'batches' / report['batch_id'] / 'gap_report.json', {}) or {}).get('strategies', {})
    groups = {}
    for row in (report or {}).get('rows', []):
        groups.setdefault(row.get('comparison_group') or 'NOT_EXECUTED', []).append(row)
    from core.research.reference import batch_job
    return render_template('reference_mode.html', report=report, groups=groups, gaps=gaps, active='reference_comparison',
                           job=batch_job.state(), admin=is_admin(), admin_enabled=admin_state()[0])


def publish_overview(now=None) -> dict:
    """Dashboard card: latest market and recommendation dates against the expected session (the same gate as the LINE push), the
    listed-only universe and the latest LINE publish states. Read-only; a database error is shown, never hidden."""
    from datetime import datetime
    from core.db_helper import get_actual_latest_date, get_latest_valid_market_date, normalize_date_str
    from core.research.trading_calendar import publish_freshness
    now = now or datetime.now()
    out = {'universe': 'TWSE_LISTED_ONLY', 'tpex': 'NOT_COVERED', 'slot': 'evening' if now.hour >= 15 else 'morning'}
    try:
        market, recommendation = normalize_date_str(get_latest_valid_market_date()), normalize_date_str(get_actual_latest_date())
        fresh, expected, reason = publish_freshness(market, recommendation, now.date(), out['slot'])
        out.update(market_date=market, recommendation_date=recommendation, expected_session=expected, fresh=fresh, reason=reason)
    except Exception as exc:
        out['error'] = f'{type(exc).__name__}: {exc}'[:200]
    folder = ROOT / 'outputs' / 'line_publish'
    out['line'] = [{'key': p.stem, 'state': (_read_json(p, {}) or {}).get('state')} for p in sorted(folder.glob('*.json'))[-2:]] \
        if folder.is_dir() else []
    return out


@app.route('/reference/batches/<batch_id>/<name>')
def reference_download(batch_id, name):
    """The stored fixed-order report of one batch, as written (Markdown only: no JSON ranking API, decision.md D3)."""
    from core.research.reference import runner
    if name not in ('report.md', 'gap_report.md') or not re.fullmatch(r'\d{8}T\d{6}Z-[0-9a-f]{8}', batch_id):
        abort(404)
    path = runner.OUT / 'batches' / batch_id / name
    if not path.is_file():
        abort(404)
    return send_file(path, mimetype='text/markdown', as_attachment=True, download_name=f'{batch_id}_{name}')


@app.route('/admin/reference/<action>', methods=['POST'])
def admin_reference(action):
    """Start or cancel the background reference batch (admin + CSRF); a second start while any runner process lives is refused."""
    if action not in ('start', 'cancel'):
        abort(404)
    refused = _require_admin_write()
    if refused:
        return jsonify(refused[0]), refused[1]
    from core.research.reference import batch_job
    result = batch_job.start() if action == 'start' else batch_job.cancel()
    if result['status'] in ('RUN_IN_PROGRESS', 'NOT_RUNNING'):
        return jsonify(result), 409
    flash('參考批次已開始；重新整理本頁查看進度' if action == 'start' else '參考批次已取消；已完成的回測保留，下次會直接重用', 'success')
    return redirect(url_for('reference_comparison'))


@app.route('/api/lab/summary')
def api_lab_summary():
    state = lab_state()
    return jsonify({**{k: state[k] for k in ('summary', 'item_states', 'conditions', 'combinations')},
                    'queue_sha256': (_read_json(LAB / 'queue.sha256.json', {}) or {}).get('queue_sha256')})


_lab_lock = threading.Lock()


@app.route('/api/lab/runs', methods=['POST'])
def api_lab_run():
    """Run one PENDING item of the frozen queue; arbitrary condition sets or weights are refused before any engine call."""
    body = request.get_json(silent=True) or request.form.to_dict()
    if set(body) - {'item_id', 'csrf'} or not isinstance(body.get('item_id'), str):
        return jsonify({'status': 'NOT_REGISTERED', 'reason': 'only an item_id of the frozen queue is accepted'}), 403
    refused = _require_admin_write()
    if refused:
        return jsonify(refused[0]), refused[1]
    state = lab_state()
    item = next((q for q in (state['queue'] or {}).get('queue', []) if q['item_id'] == body['item_id']), None)
    if item is None or item['kind'] not in LAB_KINDS:
        return jsonify({'status': 'NOT_REGISTERED', 'reason': 'item is not a registered lab configuration'}), 403
    if body['item_id'] in state['item_states']:
        return jsonify({'status': 'ALREADY_RUN', 'run_id': state['item_states'][body['item_id']].get('run_id')}), 409
    from core.research import combination_lab as lab
    if lab.round_trials() >= lab.ROUND_CAP:
        return jsonify({'status': 'BUDGET_EXHAUSTED', 'reason': f'round cap {lab.ROUND_CAP} reached'}), 409
    if not _lab_lock.acquire(blocking=False):
        return jsonify({'status': 'RUN_IN_PROGRESS'}), 409

    def work():
        try:
            from scripts import combination_lab_run as runner
            runner.require_research()
            levels, note = runner.benchmark_levels()
            runner.run_item(item, levels, note)
        finally:
            _lab_lock.release()

    threading.Thread(target=work, daemon=True).start()
    return jsonify({'status': 'STARTED', 'item_id': item['item_id']}), 202


# ---------------------------------------------------------------- latest research selection

LATEST_FIELDS = ('stock_name', 'ticker', 'strategy_or_conditions', 'factor_values', 'composite_score', 'rank', 'reason',
                 'data_date', 'data_quality', 'research_or_certification_status', 'risk_notice')


def _known_ids():
    from core.strategy_manager import StrategyManager
    ids = set(StrategyManager.STRATEGY_METADATA) | {e['strategy_id'] for e in research_payloads.catalog_entries()}
    return ids | {q['item_id'] for q in (_read_json(LAB / 'queue.json', {}) or {}).get('queue', [])}


@app.route('/api/selection/latest')
def api_selection_latest():
    sid = (request.args.get('strategy') or '').strip()
    if not re.fullmatch(r'[A-Za-z0-9_:.+-]{1,120}', sid or '') or sid not in _known_ids():
        return jsonify({'status': 'NOT_FOUND', 'reason': 'unknown strategy or combination'}), 404
    state = latest_signal.readiness()
    missing = list(state.get('missing', [])) + ['OWNER_FORWARD_RESEARCH_REGISTRATION', 'PIT_QUALIFIED_LATEST_PROVIDER']
    return jsonify({'strategy': sid, 'status': 'LATEST_SIGNAL_DISABLED', 'reason': state.get('reason'), 'missing': missing,
                    'fields': list(LATEST_FIELDS), 'research_only': True,
                    'note': '研究模組不得讀 2026 封存資料；正式每日推薦是另一條既有流程，未受影響'}), 403


@app.route('/selection/latest')
def latest_selection():
    from core.strategy_manager import StrategyManager
    options = [(sid, meta.display_name_zh) for sid, meta in StrategyManager.STRATEGY_METADATA.items()]
    options += [(e['strategy_id'], e['display_name_zh']) for e in research_payloads.catalog_entries()]
    state = latest_signal.readiness()
    return render_template('latest_selection.html', options=options, state=state, fields=LATEST_FIELDS)
