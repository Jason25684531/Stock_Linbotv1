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


# ---------------------------------------------------------------- evidence version 2 (change 2026-11-09-autonomous-data-readiness-v1)

V2_FILES = ('positions_daily', 'trades_stress', 'open_positions', 'valuation_events', 'pit_detail')


def _admitted(tmp_path):
    """A synthetic hypothesis admitted into a temporary registry and ledger: never the formal ones."""
    from scripts import research_workflow as rw
    registry, ledger = tmp_path / 'registry.csv', tmp_path / 'trial_ledger.jsonl'
    rw.register({'hypothesis_id': 'SYN-V2', 'strategy': 'synthetic', 'hypothesis': 'x', 'falsification_test': 'x',
                 'data_window': '2025-01-02..2025-12-31', 'trial_budget': '1', 'source': 'test'}, path=registry)
    inputs = {}
    for name in ('bench', 'calendar', 'data'):
        inputs[name] = tmp_path / f'{name}.csv'
        inputs[name].write_text(f'{name},1\n', encoding='utf-8')
    code = tmp_path / 'code.py'
    code.write_text('x = 1\n', encoding='utf-8')
    config = {'fee_rate': 0.001425, 'tax_rate': 0.003, 'top_n': 3}
    row = rw.admit_experiment('SYN-V2', config, [code], inputs, '2025-12-31', registry=registry, ledger=ledger)
    admission = {'admission_id': row['experiment_id'], 'trial_id': row['experiment_id'], 'hypothesis_id': 'SYN-V2', 'code_sha256': row['code_sha256'],
                 'inputs_sha256': row['inputs_sha256'], 'input_paths': {k: str(v) for k, v in inputs.items()}, 'config': config,
                 'pinned': {'benchmark': 'bench', 'calendar': 'calendar', 'data': ['data']}, 'valuation_policy': 'finite_positive_stale',
                 'research_window': ['2025-01-02', '2025-12-31'], 'admitted_at': row['at']}
    return rw, ledger, row, admission, inputs


def _v2(record, admission):
    """parameter_hash and source_hash as a real save computes them from the executed config and code files."""
    return {**record, 'evidence_version': 2, 'trial_id': admission['trial_id'], 'hypothesis_id': admission['hypothesis_id'], 'admission': 'TRIAL',
            'admission_record': admission, 'parameter_hash': run_store.canonical_hash(admission['config']),
            'source_hash': run_store.canonical_hash(admission['code_sha256'])}


def _v2_frames():
    frames = _frames()
    empty = pd.DataFrame(columns=['stock_id'])
    return {**frames, **{name: empty for name in V2_FILES}, 'costs': {'fees_and_tax_paid': 0.0}, 'health': {'engine_error': None}}


def test_v2_is_verified_only_against_the_ledger(tmp_path):
    rw, ledger, row, admission, inputs = _admitted(tmp_path)
    rid = run_store.save_run(_v2(_record(), admission), _v2_frames(), root=tmp_path / 'runs')
    loaded = run_store.load_run(rid, root=tmp_path / 'runs')
    rows = {r['experiment_id']: r for r in rw._trial_rows(ledger)}
    verified = rw.verify_trials(ledger) == 'PASS'
    status = run_store.evidence_status(loaded, rows.get(row['experiment_id']), verified, rw._tree_sha)
    assert status == {'status': 'ADMISSION_VERIFIED', 'reasons': [], 'reproducible_offline': True, 'reproducibility_reasons': []}
    inputs['data'].write_text('data,2\n', encoding='utf-8')
    changed = run_store.evidence_status(loaded, rows[row['experiment_id']], verified, rw._tree_sha)
    assert changed['status'] == 'ADMISSION_VERIFIED' and not changed['reproducible_offline'] and changed['reproducibility_reasons'] == ['INPUT_CHANGED:data']
    assert run_store.evidence_status(loaded, None)['reasons'] == ['LEDGER_ROW_MISSING']  # an admission JSON alone is not an admission
    late = run_store.evidence_status(loaded, {**row, 'at': '2099-01-01T00:00:00+00:00'}, verified)
    assert late['status'] == 'POST_RUN_NOT_ADMISSION_VERIFIED' and 'ADMITTED_AFTER_RUN' in late['reasons']
    unpinned = run_store.evidence_status(loaded, {**row, 'inputs_sha256': {'data': row['inputs_sha256']['data']}}, verified)
    assert {'PINNED_INPUT_MISSING:benchmark', 'PINNED_INPUT_MISSING:calendar'} <= set(unpinned['reasons'])
    assert run_store.evidence_status(loaded, row, False)['reasons'] == ['LEDGER_UNVERIFIED']


def test_v2_refuses_incomplete_admission_and_v1_is_unchanged(tmp_path):
    _, _, _, admission, _ = _admitted(tmp_path)
    with pytest.raises(ValueError, match='RUN_ADMISSION_INCOMPLETE:pinned.calendar'):
        run_store.save_run(_v2(_record(), {**admission, 'pinned': {'benchmark': 'bench', 'data': ['data']}}), _v2_frames(), root=tmp_path / 'runs')
    with pytest.raises(ValueError, match='trades_stress'):
        run_store.save_run(_v2(_record(), admission), {k: v for k, v in _v2_frames().items() if k != 'trades_stress'}, root=tmp_path / 'runs')
    assert not (tmp_path / 'runs').exists() or not any(p.name[0].isdigit() for p in (tmp_path / 'runs').iterdir())
    old = run_store.load_run(run_store.save_run(_record(), _frames(), root=tmp_path / 'v1'), root=tmp_path / 'v1')
    assert old['files'] == ['holdings.csv', 'metrics.json', 'nav.csv', 'picks.csv', 'run.json', 'trades.csv']
    assert run_store.evidence_status(old)['status'] == 'HISTORICAL_EVIDENCE_MISSING'


def test_repair_pointer_after_interrupted_save(tmp_path):
    first = run_store.save_run(_record(reference=True), _frames(), root=tmp_path)
    second = run_store.save_run(_record(reference=True), _frames(), root=tmp_path)
    run_store._pointer(tmp_path, 'successful', 'hybrid_trend_rank', first)  # as if the save died before moving the pointer
    repairs = run_store.repair_pointers(root=tmp_path)
    assert repairs == [{'strategy_id': 'hybrid_trend_rank', 'kind': 'successful', 'run_id': second, 'previous': first}]
    assert run_store.latest('hybrid_trend_rank', root=tmp_path)['run_id'] == second and run_store.repair_pointers(root=tmp_path) == []


@pytest.mark.parametrize('change, reason', [
    (lambda run, row: run.update(parameter_hash='other'), 'RUN_PARAMETERS_NOT_ADMITTED'),
    (lambda run, row: run.update(source_hash='other'), 'RUN_CODE_NOT_ADMITTED'),
    (lambda run, row: run.update(evidence_version=1), 'NOT_EVIDENCE_VERSION_2'),
    (lambda run, row: run.update(hypothesis_id='OTHER'), 'HYPOTHESIS_MISMATCH'),
    (lambda run, row: run['admission_record'].update(admission_id='other'), 'ADMISSION_ID_MISMATCH'),
    (lambda run, row: run['admission_record'].update(config={'top_n': 99}), 'CONFIG_HASH_MISMATCH'),
    (lambda run, row: row.update(code_sha256={}), 'CODE_HASH_MISMATCH'),
])
def test_copied_or_mismatched_admission_is_not_verified(tmp_path, change, reason):
    """A real ledger row cannot vouch for a run whose own parameters, code, version or identity differ."""
    import copy
    _, _, row, admission, _ = _admitted(tmp_path)
    loaded = run_store.load_run(run_store.save_run(_v2(_record(), admission), _v2_frames(), root=tmp_path / 'runs'), root=tmp_path / 'runs')
    assert run_store.evidence_status(loaded, row, True)['status'] == 'ADMISSION_VERIFIED'
    loaded, row = copy.deepcopy(loaded), dict(row)
    change(loaded['run'], row)
    status = run_store.evidence_status(loaded, row, True)
    assert status['status'] == 'POST_RUN_NOT_ADMISSION_VERIFIED' and reason in status['reasons']
