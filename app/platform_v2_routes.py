"""Research platform V2 pages (change 2026-11-12-integrated-research-platform-v2, design D8).

GET pages read stored results under outputs/platform_v2 (and, for the selection view, the stored daily recommendations read-only);
no GET starts a computation. Admin POSTs validate the input, write a request file and start one job through the shared single-slot
batch_job, behind the existing admin session and CSRF check. Guests are read-only and get 403 on every POST.
"""
from __future__ import annotations

import json
import hashlib
import re
from datetime import datetime, timezone
from pathlib import Path

from flask import abort, flash, jsonify, redirect, render_template, request, send_file, url_for

from app import app
from app import platform_routes

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'outputs/platform_v2'
RUNS, PORTFOLIOS, ANALYSIS, MODELS, REQUESTS = OUT / 'runs', OUT / 'portfolios', OUT / 'analysis', OUT / 'models', OUT / 'requests'
RUN_ID = re.compile(r'^\d{8}T\d{6}Z-[0-9a-f]{8}$')
MODEL_ID = re.compile(r'^PRED1-\d+d-[0-9a-f]{10}$')
PLATFORM_KINDS = ('platform_backtest', 'platform_portfolio', 'platform_baseline', 'platform_pipeline')
REPORTS = ('PLATFORM_V2_FINAL_ACCEPTANCE_REPORT.md', 'FEATURE_COMPLETION_MATRIX.md', 'STRATEGY_READINESS_21.json', 'MODEL_VALIDATION_REPORT.md',
           'BACKTEST_RECONCILIATION_REPORT.md', 'PORTFOLIO_VALIDATION_REPORT.md', 'DAILY_PIPELINE_HEALTH_REPORT.md', 'BLOCKERS_AND_DEFERRED.md',
           'OPERATION_GUIDE.md')


def _read(path: Path):
    try:
        return json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return None


def _validated(kind, identity, artifacts):
    """A publication review must bind the current artifacts and code, not just a saved status."""
    if not isinstance(identity, str) or not re.fullmatch(r'[A-Za-z0-9_-]+', identity):
        return False
    marker = _read(OUT / 'acceptance' / 'publication' / kind / f'{identity}.json')
    if not isinstance(marker, dict) or marker.get('status') != 'PASS' or marker.get('identity') != identity:
        return False
    try:
        return all(marker['sha256'].get(name) == hashlib.sha256(path.read_bytes()).hexdigest()
                   for name, path in artifacts.items())
    except (KeyError, TypeError, OSError):
        return False


def _run_reviewed(root, run_id):
    if not RUN_ID.fullmatch(run_id or ''):
        return False
    kind = 'portfolio' if root == PORTFOLIOS else 'backtest'
    artifacts = {'run.json': root / run_id / 'run.json', 'manifest.json': root / run_id / 'manifest.json',
                 'engine_v2.py': ROOT / 'core/research/platform/engine_v2.py',
                 'ledger_v2.py': ROOT / 'core/research/platform/ledger_v2.py',
                 'platform_v2_engine.py': ROOT / 'scripts/platform_v2_engine.py',
                 'platform_v2.py': ROOT / 'scripts/platform_v2.py'}
    if kind == 'portfolio':
        artifacts['portfolio_v2.py'] = ROOT / 'core/research/platform/portfolio_v2.py'
    return _validated(kind, run_id, artifacts)


def _safe_run(record, root):
    if record.get('publishable') and not _run_reviewed(root, record.get('run_id', '')):
        return record | {'publishable': False, 'not_publishable_reasons': ['VALIDATION_EVIDENCE_MISSING'],
                         'reconciliation': {}}
    return record


def _runs(root: Path) -> list:
    from core.backtest import run_store
    return [_safe_run(r, root) for r in run_store.history(root=root)] if root.is_dir() else []


def _loaded(root: Path, run_id: str):
    from core.backtest import run_store
    if not RUN_ID.fullmatch(run_id or ''):
        abort(404)
    try:
        loaded = run_store.load_run(run_id, root=root)
        loaded['run'] = _safe_run(loaded['run'], root)
        return loaded
    except (FileNotFoundError, OSError, run_store.ManifestMismatch):
        abort(404)


def analysis(run_id: str):
    """The newest stored analysis of a run (any analysis code version), or None."""
    found = sorted(ANALYSIS.glob(f'{run_id}__*.json'), key=lambda p: p.stat().st_mtime, reverse=True) if ANALYSIS.is_dir() else []
    return _read(found[0]) if found else None


def models() -> list:
    result = []
    for path in sorted(MODELS.glob('PRED1-*/model.json')) if MODELS.is_dir() else []:
        model = _read(path)
        if not model:
            continue
        if not _model_identity_current(model):
            continue
        artifacts = {'model.json': path, 'prediction.py': ROOT / 'core/research/platform/prediction.py',
                     'platform_v2.py': ROOT / 'scripts/platform_v2.py'}
        if model.get('status') == 'DEV_WINDOW_SKILL':
            artifacts['predictions.parquet'] = path.parent / 'predictions.parquet'
        if not _validated('model', model.get('model_version', ''), artifacts):
            evaluation = model.get('evaluation') or {}
            model = model | {'status': 'NOT_VALIDATED', 'evaluation': evaluation | {
                'reasons': [*(evaluation.get('reasons') or []), 'VALIDATION_EVIDENCE_MISSING']}}
        result.append(model)
    return result


def _model_identity_current(model) -> bool:
    """Hide evaluations created from a different model or runner source identity."""
    try:
        from core.backtest.run_store import canonical_hash

        code = {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest()
                for name in ('core/research/platform/prediction.py', 'scripts/platform_v2.py')}
        identity = canonical_hash({'horizon': model['horizon'], 'spec_hash': model['spec_hash'],
                                   'snapshot_id': model['snapshot_id'], 'code': code})[:10]
        return model.get('model_version') == f"PRED1-{int(model['horizon'])}d-{identity}"
    except (KeyError, OSError, TypeError, ValueError):
        return False


def _stale_model_count() -> int:
    paths = MODELS.glob('PRED1-*/model.json') if MODELS.is_dir() else ()
    return sum(1 for path in paths if (model := _read(path)) and not _model_identity_current(model))


def slot() -> dict:
    from core.research.reference import batch_job
    return batch_job.state()


def _readiness():
    from core.research.platform.params import readiness
    try:
        return readiness()
    except (OSError, ValueError, KeyError):
        return {}


@app.route('/platform')
def platform_index():
    runs, folios = _runs(RUNS), _runs(PORTFOLIOS)
    return render_template('platform_index.html', runs=runs, folios=folios, models=models(), slot=slot(), readiness=_readiness(),
                           reports=[n for n in REPORTS if (OUT / n).is_file()], active_nav='platform_index')


@app.route('/platform/selection')
def platform_selection():
    from services import smart_selection
    return render_template('platform_selection.html', view=smart_selection.load(app), active_nav='platform_index')


def _hits(model):
    """History of a DEV_WINDOW_SKILL model: per-year hit rate (p >= 0.5 vs realized up) and one stock's rows on request."""
    import pandas as pd
    path = MODELS / model['model_version'] / 'predictions.parquet'
    if model['status'] != 'DEV_WINDOW_SKILL' or not path.is_file():
        return None, None
    frame = pd.read_parquet(path, columns=['date', 'stock_id', 'year', 'p_m1', 'q10', 'q50', 'q90', 'forward', 'label'])
    frame['hit'] = ((frame.p_m1 >= 0.5).astype(int) == frame.label)
    yearly = frame.groupby('year').agg(rows=('hit', 'size'), hit_rate=('hit', 'mean'), up_rate=('label', 'mean')).reset_index().to_dict('records')
    stock = (request.args.get('stock') or '').strip()
    rows = None
    if re.fullmatch(r'[1-9]\d{3}', stock):
        rows = frame[frame.stock_id == stock].sort_values('date').tail(60).assign(date=lambda f: f.date.astype(str)).to_dict('records')
    return yearly, rows


@app.route('/platform/predictions')
def platform_predictions():
    registry = models()
    history = {m['model_version']: _hits(m) for m in registry}
    return render_template('platform_predictions.html', registry=registry, history=history, stock=request.args.get('stock', ''),
                           stale_model_count=_stale_model_count(), active_nav='platform_index')


@app.route('/platform/backtest')
def platform_backtest():
    from core.research.platform.engine_v2 import RULES
    from core.research.platform.params import BOUNDS
    rows = _readiness()
    runnable = {sid: r for sid, r in rows.items() if r.get('web_runnable')}
    return render_template('platform_backtest.html', runs=_runs(RUNS), readiness=rows, runnable=runnable, rules=list(RULES), bounds=BOUNDS,
                           slot=slot(), defaults=_defaults(), active_nav='platform_index')


def _defaults():
    from core.backtest.runner import FEE_RATE, MIN_FEE, TAX_RATE
    from config import Config
    return {'capital': 1_000_000, 'fee_rate': FEE_RATE, 'tax_rate': TAX_RATE, 'minimum_fee': MIN_FEE,
            'slippage_max': float(getattr(Config, 'SLIPPAGE_MAX_PIPS', Config.SLIPPAGE_RATE))}


@app.route('/platform/backtest/<run_id>')
def platform_run(run_id):
    from core.backtest import run_store
    loaded = _loaded(RUNS, run_id)
    record = loaded['run']
    trades = None
    if record.get('publishable'):
        frame = run_store.read_frame(loaded, 'trades')
        trades = frame.where(frame.notna(), None).to_dict('records')
    ledger = run_store.read_frame(loaded, 'daily_ledger').to_dict('records') if 'daily_ledger.csv' in loaded['files'] else []
    return render_template('platform_run.html', record=record, files=loaded['files'], result=(analysis(run_id) or {}).get('result'),
                           trades=trades, ledger=ledger, active_nav='platform_index')


@app.route('/platform/<kind>/<run_id>/files/<name>')
def platform_file(kind, run_id, name):
    root = {'backtest': RUNS, 'portfolio': PORTFOLIOS}.get(kind)
    if root is None:
        abort(404)
    loaded = _loaded(root, run_id)
    if name not in loaded['files']:
        abort(404)
    return send_file(loaded['dir'] / name, as_attachment=True, download_name=f'{run_id}_{name}')


@app.route('/platform/portfolio')
def platform_portfolio():
    rows = _readiness()
    eligible = {sid: r for sid, r in rows.items() if r.get('web_runnable')}
    return render_template('platform_portfolio.html', folios=_runs(PORTFOLIOS), eligible=eligible, rules=['LOT_ENGINE_V1', 'LOT_BOARD_1000', 'LOT_ODD'],
                           slot=slot(), defaults=_defaults(), active_nav='platform_index')


@app.route('/platform/portfolio/<run_id>')
def platform_portfolio_run(run_id):
    loaded = _loaded(PORTFOLIOS, run_id)
    return render_template('platform_portfolio_run.html', record=loaded['run'], files=loaded['files'], active_nav='platform_index')


def _pipeline():
    return _read(OUT / 'pipeline' / 'LATEST.json')


@app.route('/platform/system')
def platform_system():
    from app import web_server
    try:
        production = web_server._build_system_status_payload(include_freshness=False)[0]  # the existing read-only /system summary
    except Exception as error:  # the read-only production view must never break the page
        production = {'error': f'{type(error).__name__}: {str(error)[:200]}'}
    return render_template('platform_system.html', slot=slot(), pipeline=_pipeline(), governance=_read(OUT / 'governance' / 'LATEST.json'),
                           readiness=_readiness(), production=production, reports=[n for n in REPORTS if (OUT / n).is_file()],
                           active_nav='platform_index')


@app.route('/platform/reports/<name>')
def platform_report(name):
    if name not in REPORTS or not (OUT / name).is_file():
        abort(404)
    if name == 'MODEL_VALIDATION_REPORT.md' and any(
        'VALIDATION_EVIDENCE_MISSING' in (m.get('evaluation', {}).get('reasons') or []) for m in models()
    ):
        abort(409, description='VALIDATION_EVIDENCE_MISSING: 模型報告尚未完成重新驗證')
    return send_file(OUT / name, as_attachment=True, download_name=name)


def _number(name, cast=float):
    value = request.form.get(name, '').strip()
    try:
        return cast(value)
    except ValueError:
        raise ValueError(f'INVALID_NUMBER: {name}') from None


def _request_file(kind, payload) -> Path:
    REQUESTS.mkdir(parents=True, exist_ok=True)
    path = REQUESTS / f'{kind}_{datetime.now(timezone.utc):%Y%m%dT%H%M%S%fZ}.json'
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding='utf-8')
    return path


def _start(kind, args=()):
    from core.research.reference import batch_job
    outcome = batch_job.start(workers=4 if kind in ('platform_baseline', 'platform_pipeline') else 1, kind=kind, args=args)
    if outcome['status'] == 'RUN_IN_PROGRESS':
        return jsonify(outcome | {'reason': 'another batch job is running; nothing was started'}), 409
    return None


def _costs_from_form():
    return {k: _number(k) for k in ('fee_rate', 'tax_rate', 'minimum_fee', 'slippage_max')}


@app.post('/platform/backtest')
def platform_backtest_submit():
    refused = platform_routes._require_admin_write()
    if refused:
        return jsonify(refused[0]), refused[1]
    from core.research.platform.params import check_params
    try:
        params = check_params(request.form.get('strategy_id'), request.form.get('start'), request.form.get('end'), _number('capital'),
                              _costs_from_form(), request.form.get('rule'))
    except ValueError as error:
        return jsonify({'status': 'INVALID_PARAMETERS', 'reason': str(error)}), 400
    busy = _start('platform_backtest', ['--params', str(_request_file('backtest', params))])
    if busy:
        return busy
    flash('回測已送出，背景執行中；相同參數會直接重用既有 Run。完成後出現在下方清單。', 'success')
    return redirect(url_for('platform_backtest'))


@app.post('/platform/portfolio')
def platform_portfolio_submit():
    refused = platform_routes._require_admin_write()
    if refused:
        return jsonify(refused[0]), refused[1]
    from core.research.platform.params import check_params, readiness
    from core.research.platform.portfolio_v2 import REBALANCES, check_weights
    members = request.form.getlist('members')
    try:
        raw = [w for w in request.form.get('weights', '').replace('，', ',').split(',') if w.strip()]
        weights = check_weights(members, [float(w) for w in raw] if raw else None)
        rows = readiness()
        if any(not (rows.get(sid) or {}).get('web_runnable') for sid in members):
            raise ValueError('MEMBER_NOT_WEB_RUNNABLE')
        rebalance = request.form.get('rebalance', 'REBAL_NONE')
        if rebalance not in REBALANCES:
            raise ValueError(f'UNKNOWN_REBALANCE: {rebalance}')
        windows = [rows[sid]['backtest_window'] for sid in members]
        window = [max(w[0] for w in windows), min(w[1] for w in windows)]
        check_params(members[0], request.form.get('start'), request.form.get('end'), _number('capital'), _costs_from_form(), request.form.get('rule'),
                     {members[0]: rows[members[0]] | {'backtest_window': window}})
    except (ValueError, TypeError) as error:
        return jsonify({'status': 'INVALID_PARAMETERS', 'reason': str(error)}), 400
    payload = {'members': members, 'weights': weights, 'start': request.form.get('start'), 'end': request.form.get('end'), 'capital': _number('capital'),
               'costs': _costs_from_form(), 'rule': request.form.get('rule'), 'rebalance': rebalance}
    busy = _start('platform_portfolio', ['--params', str(_request_file('portfolio', payload))])
    if busy:
        return busy
    flash('組合回測已送出，背景執行中；成員資格（VERIFIED 軌與可發布的 V2 Run）會在工作中再次檢查。', 'success')
    return redirect(url_for('platform_portfolio'))


@app.post('/platform/system/<action>')
def platform_system_action(action):
    if action not in ('pipeline', 'cancel', 'retry'):
        abort(404)
    refused = platform_routes._require_admin_write()
    if refused:
        return jsonify(refused[0]), refused[1]
    from core.research.reference import batch_job
    state = batch_job.state()
    if action == 'cancel':
        if state.get('kind') not in PLATFORM_KINDS or state.get('status') != 'RUNNING':
            return jsonify({'status': 'NOT_RUNNING', 'reason': 'no platform job is running'}), 409
        batch_job.cancel()
        flash('平台工作已取消；已完成的 Run 與步驟保留，重試時會依身份重用。', 'success')
        return redirect(url_for('platform_system'))
    if action == 'retry':
        if state.get('kind') not in PLATFORM_KINDS or state.get('status') not in ('FAILED', 'CANCELLED', 'PARTIAL'):
            return jsonify({'status': 'NOTHING_TO_RETRY', 'reason': 'the last job is not a failed or cancelled platform job'}), 409
        busy = _start(state['kind'], state.get('args') or [])
    else:
        busy = _start('platform_pipeline')
    if busy:
        return busy
    flash('研究管線已啟動；已完成且身份相同的步驟會直接重用。' if action == 'pipeline' else '已重新送出上一個平台工作。', 'success')
    return redirect(url_for('platform_system'))
