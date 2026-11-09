import json

import pandas as pd
import pytest

from core.backtest import run_store


def _record(sid='hybrid_trend_rank', status='RESEARCH', reference=False, group='LAB:abc'):
    return {'strategy_id': sid, 'strategy_ids': [sid], 'parameter_hash': 'p', 'source_hash': 's', 'data_version': 'd',
            'comparison_group': group, 'certification_status': 'NOT_CERTIFIED', 'status_class': status, 'reference': reference}


def _frames(last='2025-12-31'):
    nav = pd.DataFrame({'date': ['2025-12-30', last], 'nav': [100.0, 101.0]})
    empty = pd.DataFrame(columns=['stock_id'])
    return {'nav': nav, 'holdings': empty, 'trades': empty, 'picks': empty, 'metrics': {'total_return': {'value': .01, 'reason': None}}}


def test_runs_are_unique_and_immutable(tmp_path):
    a = run_store.save_run(_record(), _frames(), root=tmp_path)
    b = run_store.save_run(_record(), _frames(), root=tmp_path)
    assert a != b and run_store.RUN_ID.match(a)
    with pytest.raises(FileExistsError):
        run_store.save_run(_record(), _frames(), root=tmp_path, run_id=a)
    assert run_store.load_run(a, root=tmp_path)['run']['status'] == 'SUCCESS'


def test_tampering_detected(tmp_path):
    rid = run_store.save_run(_record(), _frames(), root=tmp_path)
    (tmp_path / rid / 'nav.csv').write_text('date,nav\n2025-12-31,999\n', encoding='utf-8')
    with pytest.raises(run_store.ManifestMismatch):
        run_store.load_run(rid, root=tmp_path)
    assert run_store.latest('hybrid_trend_rank', root=tmp_path) is None


def test_pointers_and_failures(tmp_path):
    base = run_store.save_run(_record(reference=True), _frames(), root=tmp_path)
    exp = run_store.save_run(_record(status='EXPERIMENTAL'), _frames(), root=tmp_path)
    failed = run_store.save_failure(_record(), RuntimeError('engine down'), root=tmp_path)
    assert run_store.latest('hybrid_trend_rank', root=tmp_path)['run_id'] == exp
    assert run_store.latest('hybrid_trend_rank', 'reference', root=tmp_path)['run_id'] == base
    rows = run_store.history(root=tmp_path, strategy_id='hybrid_trend_rank')
    assert [r['run_id'] for r in rows][0] == failed and rows[0]['status'] == 'FAILED'
    assert json.loads((tmp_path / failed / 'error.json').read_text(encoding='utf-8'))['error_type'] == 'RuntimeError'


def test_experimental_cannot_be_reference(tmp_path):
    with pytest.raises(ValueError, match='REFERENCE_REQUIRES'):
        run_store.save_run(_record(status='EXPERIMENTAL', reference=True), _frames(), root=tmp_path)


def test_portfolio_run_moves_no_pointer(tmp_path):
    record = {**_record(), 'strategy_id': 'PORTFOLIO:a+b', 'strategy_ids': ['a', 'b']}
    rid = run_store.save_run(record, _frames(), root=tmp_path)
    assert run_store.latest('a', root=tmp_path) is None and run_store.history(root=tmp_path, strategy_id='a')[0]['run_id'] == rid


def test_identity_and_sealed_window_checks(tmp_path):
    record = _record()
    del record['parameter_hash']
    with pytest.raises(ValueError, match='RUN_IDENTITY_MISSING'):
        run_store.save_run(record, _frames(), root=tmp_path)
    with pytest.raises(ValueError, match='OOS_OR_INPUT_WINDOW_BLOCKED'):
        run_store.save_run(_record(), _frames('2026-01-02'), root=tmp_path)
    web = run_store.save_run(_record(status='EXPERIMENTAL', group='WEB_ADHOC:x'), _frames('2026-01-02'), root=tmp_path)
    assert run_store.load_run(web, root=tmp_path)
    assert not list(tmp_path.glob('.*.tmp'))


def test_data_blocked_has_no_performance(tmp_path):
    record = {**_record(status='DATA_BLOCKED'), 'reason': 'MONTHLY_REVENUE_NO_ANNOUNCEMENT_TIME'}
    with pytest.raises(ValueError):
        run_store.save_run(record, _frames(), root=tmp_path)
    assert run_store.save_run(record, {'metrics': {}}, root=tmp_path)


def test_run_id_format_guard(tmp_path):
    for bad in ('../latest', '..%2Flatest', 'x', None):
        with pytest.raises(FileNotFoundError):
            run_store.load_run(bad, root=tmp_path)


class _Engine:
    fail = None

    def __init__(self, **kwargs):
        pass

    def run_portfolio_backtest(self):
        if _Engine.fail:
            raise _Engine.fail
        return {'equity_curve': pd.DataFrame({'date': ['2025-01-02'], 'asset_value': [1.0], 'roi': [0.0]}),
                'trades': [{'strategy': 'v31_hybrid', 'stock_id': '2330', 'buy_date': '2025-01-02', 'sell_date': '2025-01-03'}],
                'metrics': {}, 'strategy_performance': {}}


def test_web_wrapper_saves_failures_and_reports_save_failed(monkeypatch):
    import app as app_module
    monkeypatch.setattr('core.backtest.runner.PortfolioBacktestEngine', _Engine)
    _Engine.fail = None
    result, *_ = app_module._run_portfolio_backtest(['v31'], '2025-01-01', '2026-02-01')
    run = run_store.load_run(result['run_id'])['run']
    assert result['save_status'] == 'SAVED' and run['status_class'] == 'EXPERIMENTAL'
    assert 'SEALED_WINDOW_NOT_FOR_VALIDATION' in run['flags'] and run['comparison_group'].startswith('WEB_ADHOC')
    assert run_store.latest('hybrid_trend_rank')['run_id'] == result['run_id']

    _Engine.fail = RuntimeError('boom')
    with pytest.raises(RuntimeError):
        app_module._run_portfolio_backtest(['v31'], '2025-01-01', '2025-02-01')
    assert run_store.history()[0]['status'] == 'FAILED'
    assert run_store.latest('hybrid_trend_rank')['run_id'] == result['run_id']

    _Engine.fail = None
    monkeypatch.setattr(run_store, 'save_run', lambda *a, **k: (_ for _ in ()).throw(OSError('disk full')))
    unsaved, *_ = app_module._run_portfolio_backtest(['v31'], '2025-01-01', '2025-02-01')
    assert unsaved['save_status'] == 'SAVE_FAILED' and 'run_id' not in unsaved
    assert run_store.latest('hybrid_trend_rank')['run_id'] == result['run_id']
