from types import SimpleNamespace as NS

import pandas as pd
import pytest

from scripts import historical_foundation as hf


def caps():
    return [NS(strategy_id='bollinger_reversion', family='tquant', engine='TQUANT_SNAPSHOT', governance=None, static_block=None, data_needs=()),
            NS(strategy_id='growth_momentum_breakout', family='native', engine='LEGACY_V2', governance=None,
               static_block='MONTHLY_REVENUE_NO_ANNOUNCEMENT_TIME', data_needs=()),
            NS(strategy_id='H4:GROWTH-MOMENTUM-V2', family='research', engine=None, governance='H4_SEALED_RESEARCH', static_block=None, data_needs=())]


def evaluation():
    summary = lambda earliest: {'earliest_usable_date': earliest}
    trust = {c.strategy_id: [{'year': 2024, 'backtestable': c.strategy_id == 'bollinger_reversion', 'binding': ['revenue_announcement_time']}] for c in caps()}
    return {'snapshot_id': 'HS1-test', 'evaluation_id': 'EV1-test', 'trust': trust,
            'summaries': {'bollinger_reversion': summary('2024-01-02'), 'growth_momentum_breakout': summary(None), 'H4:GROWTH-MOMENTUM-V2': summary(None)}}


def fake_result(_row):
    dates = pd.bdate_range('2024-01-02', periods=3).strftime('%Y-%m-%d')
    return {'nav': pd.DataFrame({'date': dates, 'nav_base': [1e6, 1.01e6, 1.02e6], 'nav_stress': [1e6, 1.0e6, 1.01e6]}),
            'trades': pd.DataFrame(columns=['stock_id', 'buy_date', 'sell_date', 'profit_pct']), 'engine_error': None, 'accounting': hf.ACCOUNTING}


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(hf, 'OUT', tmp_path)
    from core.research.reference import catalog
    monkeypatch.setattr(catalog, 'capabilities', caps)
    return tmp_path / 'runs'


def test_plan_states_and_window(isolated):
    rows = {r['strategy_id']: r for r in hf.plan(evaluation(), caps(), isolated)}
    assert rows['H4:GROWTH-MOMENTUM-V2']['state'] == 'GOVERNED_ONLY'
    assert rows['growth_momentum_breakout']['state'] == 'DATA_BLOCKED'
    assert rows['bollinger_reversion']['state'] == 'RUN' and rows['bollinger_reversion']['window'] == ['2024-01-02', '2025-12-31']


def test_second_batch_reuses_without_engine(isolated):
    calls = []
    first = hf.run(execute=lambda row: calls.append(row) or fake_result(row), root=isolated, evaluation=evaluation())
    assert first['engine_calls'] == 1 and len(calls) == 1
    second = hf.run(execute=lambda row: calls.append(row) or fake_result(row), root=isolated, evaluation=evaluation())
    assert second['engine_calls'] == 0 and len(calls) == 1
    row = {r['strategy_id']: r for r in second['rows']}['bollinger_reversion']
    assert row['state'] == 'REUSE' and row['run_id'] == {r['strategy_id']: r for r in first['rows']}['bollinger_reversion']['run_id']


def test_regime_spec_registered_before_execution(isolated):
    seen = []
    hf.run(execute=lambda row: seen.append((isolated.parent / 'regime_specs').exists()) or fake_result(row), root=isolated, evaluation=evaluation())
    assert seen == [True]


def test_failed_batch_is_not_reported_as_done(isolated, monkeypatch, capsys):
    rows = [{'strategy_id': 'bollinger_reversion', 'state': 'FAILED', 'run_id': None, 'reason': 'MemoryError'}]
    monkeypatch.setattr(hf, 'run', lambda **kw: {'rows': rows})
    assert hf.main(['run']) == 1
    out = capsys.readouterr().out
    assert '"status": "FAILED"' in out and '"status": "DONE"' not in out


def test_identity_changes_with_window():
    cap = caps()[0]
    assert hf.history_identity(cap, 'HS1-a', '2024-01-02', '2025-12-31') != hf.history_identity(cap, 'HS1-a', '2023-01-03', '2025-12-31')
    assert hf.history_identity(cap, 'HS1-a', '2024-01-02', '2025-12-31') != hf.history_identity(cap, 'HS1-b', '2024-01-02', '2025-12-31')


def test_window_after_2025_refused():
    from scripts import history_engine
    with pytest.raises(ValueError, match='OOS_OR_INPUT_WINDOW_BLOCKED'):
        history_engine.execute_window(caps()[0], 'RID1:x', 'HS1-x', '2025-06-02', '2026-01-05')


def test_identity_carries_the_accounting_model_and_ledger_code(monkeypatch):
    cap = caps()[0]
    new = hf.history_identity(cap, 'HS1-a', '2024-01-02', '2025-12-31')
    monkeypatch.setattr(hf, 'ACCOUNTING', 'LEGACY_RAW_PRICE')
    assert hf.history_identity(cap, 'HS1-a', '2024-01-02', '2025-12-31') != new  # a legacy-accounting run can never be REUSEd as a ledger run
    assert hf._config(cap)['accounting'] == 'LEGACY_RAW_PRICE'


def test_save_refuses_a_result_from_another_accounting_model(isolated):
    row = {'strategy_id': 'bollinger_reversion', 'window': ['2024-01-02', '2024-01-04'], 'run_identity': 'RID1:abc'}
    with pytest.raises(ValueError, match='ACCOUNTING_MISMATCH'):
        hf.save(caps()[0], row, fake_result(row) | {'accounting': 'LEGACY_RAW_PRICE'}, 'HS1-test', isolated)
    undeclared = {k: v for k, v in fake_result(row).items() if k != 'accounting'}
    with pytest.raises(ValueError, match='ACCOUNTING_MISMATCH'):
        hf.save(caps()[0], row, undeclared, 'HS1-test', isolated)
    run_id = hf.save(caps()[0], row, fake_result(row) | {'accounting': hf.ACCOUNTING, 'ledger_version': 'v1'}, 'HS1-test', isolated)
    import json
    record = json.loads((isolated / run_id / 'run.json').read_text(encoding='utf-8'))
    assert record['accounting'] == hf.ACCOUNTING and record['ledger_version'] == 'v1' and (isolated / run_id / 'corporate_actions.csv').exists()
