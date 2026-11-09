import json

import pytest

from core.research import combination_lab as lab
from core.research import condition_pool as cp
from core.research import strategy_signal as ss
from scripts import research_workflow as workflow


def _registry(tmp_path, budget=12, hid='LAB-INTERSECT-V1'):
    reg = tmp_path / 'registry.csv'
    workflow.register({'hypothesis_id': hid, 'strategy': 's', 'hypothesis': 'h', 'falsification_test': 'f',
                       'data_window': '2023-01-03..2025-12-31', 'trial_budget': str(budget), 'source': 'test'}, path=reg)
    return reg


def _inputs(tmp_path):
    code = tmp_path / 'code.py'
    code.write_text('x = 1\n', encoding='utf-8')
    data = tmp_path / 'data.json'
    data.write_text('{}', encoding='utf-8')
    return [code], {'data': data}


def test_round_cap_refuses_new_trials_but_not_replays(tmp_path, monkeypatch):
    reg, ledger = _registry(tmp_path), tmp_path / 'trials.jsonl'
    code, inputs = _inputs(tmp_path)
    monkeypatch.setattr(lab, 'ROUND_CAP', 2)
    first = lab.admit('LAB-INTERSECT-V1', {'round': lab.ROUND, 'n': 1}, code, inputs, registry=reg, ledger=ledger)
    assert first['experiment_id'] == lab._experiment_id('LAB-INTERSECT-V1', {'round': lab.ROUND, 'n': 1}, code, inputs, lab.END)
    lab.admit('LAB-INTERSECT-V1', {'round': lab.ROUND, 'n': 2}, code, inputs, registry=reg, ledger=ledger)
    assert lab.round_trials(ledger) == 2
    with pytest.raises(lab.BudgetExhausted):
        lab.admit('LAB-INTERSECT-V1', {'round': lab.ROUND, 'n': 3}, code, inputs, registry=reg, ledger=ledger)
    replay = lab.admit('LAB-INTERSECT-V1', {'round': lab.ROUND, 'n': 1}, code, inputs, registry=reg, ledger=ledger)
    assert replay['kind'] == 'REPLAY' and lab.round_trials(ledger) == 2


def test_admit_requires_round_tag_and_dev_window(tmp_path):
    reg, ledger = _registry(tmp_path), tmp_path / 'trials.jsonl'
    code, inputs = _inputs(tmp_path)
    with pytest.raises(ValueError, match='ROUND_TAG_REQUIRED'):
        lab.admit('LAB-INTERSECT-V1', {'n': 1}, code, inputs, registry=reg, ledger=ledger)
    with pytest.raises(ss.ResearchOnlyViolation):
        lab.admit('LAB-INTERSECT-V1', {'round': lab.ROUND}, code, inputs, data_end='2026-01-31', registry=reg, ledger=ledger)
    assert not ledger.exists()


def test_unregistered_hypothesis_is_refused(tmp_path):
    reg, ledger = _registry(tmp_path), tmp_path / 'trials.jsonl'
    code, inputs = _inputs(tmp_path)
    with pytest.raises(ValueError, match='not registered'):
        lab.admit('LAB-NOPE', {'round': lab.ROUND}, code, inputs, registry=reg, ledger=ledger)


def _combo(cid, size, jac, coverage=1.0):
    return {'combination_id': cid, 'size': size, 'conditions': cid.split('+'), 'coverage': coverage, 'mean_pairwise_jaccard': jac}


def test_queue_order_is_the_seven_positions():
    combos = [_combo('c1', 1, 0), _combo('c1+c2', 2, .4), _combo('c1+c3', 2, .1), _combo('c2+c3', 2, .2), _combo('c1+c2+c3', 3, .2),
              _combo('c2+c4', 2, .05, coverage=.5)]
    queue = lab.build_queue(['c1', 'c2', 'c3', 'c4'], combos, ['a', 'b'], [('a', {'X': 1})], ['vam'])
    kinds = [(q['position'], q['kind']) for q in queue]
    assert [p for p, _ in kinds] == sorted(p for p, _ in kinds)
    assert [q['item_id'] for q in queue if q['kind'] == 'intersection'] == ['LAB:c1+c3', 'LAB:c2+c3', 'LAB:c1+c2', 'LAB:c1+c2+c3']
    assert [q['position'] for q in queue if q['kind'] == 'intersection'] == [5, 5, 5, 7]
    assert next(q for q in queue if q['kind'] == 'legacy_sensitivity')['position'] == 6
    weighted = next(q for q in queue if q['kind'] == 'weighted')
    assert weighted['weights'] == lab.WEIGHTED and not weighted['skip']
    assert 'LAB:c2+c4' not in {q['item_id'] for q in queue}  # coverage below 0.8 is never queued


def test_weighted_skipped_when_a_component_is_not_qualified():
    queue = lab.build_queue(['c1', 'c2'], [], [], [], [])
    assert next(q for q in queue if q['kind'] == 'weighted')['skip'] == 'WEIGHTED_COMPONENT_NOT_QUALIFIED'


def test_frozen_queue_cannot_change(tmp_path):
    path = tmp_path / 'queue.json'
    queue = [{'position': 1, 'item_id': 'x', 'kind': 'single', 'conditions': ['c1']}]
    _, digest = lab.freeze_queue(queue, {}, path)
    assert lab.freeze_queue(queue, {}, path)[1] == digest
    with pytest.raises(ValueError, match='QUEUE_CHANGED_AFTER_FREEZE'):
        lab.freeze_queue(queue + [{'position': 2, 'item_id': 'y', 'kind': 'single', 'conditions': ['c2']}], {}, path)


def test_registration_binds_every_queued_config():
    items = [{'item_id': 'LAB:c1+c2', 'kind': 'intersection', 'conditions': ['c1', 'c2']}]
    row = lab.registration('intersection', items, 1)
    assert f'config_sha256={lab.item_sha(items[0])}' in row['hypothesis'] and row['trial_budget'] == '1'


def test_combination_counts():
    import itertools
    for k, expected in ((5, 31), (6, 63), (7, 127), (8, 255)):
        assert sum(1 for n in range(1, k + 1) for _ in itertools.combinations(range(k), n)) == expected


def test_trades_from_fills_pairs_position_episodes():
    ledger = [{'kind': 'FILL', 'date': '2023-01-04', 'stock_id': '2330', 'side': 'buy', 'shares': 10, 'notional': 1000.0, 'fee': 1.0, 'tax': 0.0},
              {'kind': 'FILL', 'date': '2023-02-01', 'stock_id': '2330', 'side': 'sell', 'shares': 10, 'notional': 1100.0, 'fee': 1.0, 'tax': 3.0},
              {'kind': 'ACTION', 'date': '2023-02-01', 'stock_id': '2330'}]
    trades = lab.trades_from_fills(ledger)
    assert len(trades) == 1 and trades.iloc[0]['pnl'] == pytest.approx(95.0) and trades.iloc[0]['sell_date'] == '2023-02-01'


def test_day_scores_refuse_sealed_dates():
    with pytest.raises(ss.ResearchOnlyViolation):
        cp.day_scores('c1', '2026-01-02')


def test_blocked_conditions_are_declared():
    assert cp.CONDITIONS['c4m']['blocked'] == 'DATA_BLOCKED' and cp.CONDITIONS['c5']['blocked'] == 'BLOCKED_PIT'
    assert cp.CONDITIONS['c6']['blocked'] == 'DATA_BLOCKED'


def _fake_track(start_nav=1_000_000.0):
    import pandas as pd
    days = pd.bdate_range('2023-01-03', periods=60).strftime('%Y-%m-%d')
    daily = [{'date': d, 'nav': start_nav * (1 + 0.001 * i), 'positions': {'2330': 10}} for i, d in enumerate(days)]
    ledger = [{'kind': 'FILL', 'date': days[1], 'stock_id': '2330', 'side': 'buy', 'shares': 10, 'notional': 5000.0, 'fee': 20.0, 'tax': 0.0},
              {'kind': 'FILL', 'date': days[30], 'stock_id': '2330', 'side': 'sell', 'shares': 10, 'notional': 5500.0, 'fee': 20.0, 'tax': 16.5}]
    daily[-1]['marks'], daily[-1]['stock_receivable'], daily[-1]['cash_receivable_claims'] = {'2330': 500.0}, [], []
    return {'result': {'daily': daily, 'ledger': ledger}}, days


def test_post_admission_path_saves_a_run(tmp_path, monkeypatch):
    """Everything after admission runs on a fake engine result, so a bug there never wastes a counted trial."""
    import pandas as pd
    from core.backtest import run_store
    from scripts import combination_lab_run as runner
    track, days = _fake_track()
    levels = pd.Series([100 + i for i in range(len(days))], index=pd.to_datetime(days))
    monkeypatch.setattr(lab, 'score_rows', lambda item, registry=None: ([], {d: 'OK' for d in days}))
    monkeypatch.setattr(lab, 'admit', lambda *a, **k: {'experiment_id': 'abc', 'kind': 'TRIAL'})
    monkeypatch.setattr(lab, 'simulate', lambda item, rows: ({'status': 'COMPUTABLE', 'tracks': {'base': track, 'stress': track},
                                                             'targets': pd.DataFrame([{'asof_date': days[0], 'asset_id': '2330'}])}, {}))
    monkeypatch.setattr(runner.workflow, 'effective_trials', lambda *a, **k: 750)
    item = {'position': 3, 'item_id': 'LAB:c1', 'kind': 'single', 'conditions': ['c1']}
    evidence = runner.run_research_engine_item(item, levels, registry=None)
    assert evidence['status'] == 'SUCCESS' and evidence['metrics']['total_return']['value'] is not None
    assert evidence['gates_comparison']['status'] == 'COMPARISON_ONLY_DATA_UNCERTIFIED'
    run_id = runner.save(item, evidence, 'TRI')
    run = run_store.load_run(run_id)['run']
    assert run['status_class'] == 'EXPERIMENTAL' and run['trial_id'] == 'abc' and run['comparison_group'].startswith('LAB_ENGINE')
    json.dumps(runner._clean({k: v for k, v in evidence.items()}), allow_nan=False)


def test_legacy_post_admission_path(tmp_path, monkeypatch):
    import pandas as pd
    from core.backtest import run_store
    from scripts import combination_lab_run as runner
    days = pd.bdate_range('2023-01-03', periods=60).strftime('%Y-%m-%d')
    nav = [[d, 1_000_000 * (1 - 0.0005 * i)] for i, d in enumerate(days)]
    trades = [{'stock_id': '2330', 'buy_date': days[1], 'buy_price': 500.0, 'sell_date': days[9], 'sell_price': 490.0, 'shares': 100, 'cost': 150.0}]
    fake = {'tracks': {n: {'nav': nav, 'trades': trades} for n in ('base', 'stress')}, 'picks': [[days[0], '2330', 1]],
            'checks': {'trade_count': 1, 'pit_status': 'NOT_APPLICABLE', 'degenerate': False}, 'data_version': 'dv'}
    monkeypatch.setattr(runner.legacy, 'run_legacy', lambda sid, overrides=None: fake)
    monkeypatch.setattr(lab, 'admit', lambda *a, **k: {'experiment_id': 'leg', 'kind': 'TRIAL'})
    monkeypatch.setattr(runner.workflow, 'effective_trials', lambda *a, **k: 750)
    levels = pd.Series([100.0 + i for i in range(len(days))], index=pd.to_datetime(days))
    item = {'position': 1, 'item_id': 'LEGACY:mean_reversion', 'kind': 'legacy_baseline', 'strategy_id': 'mean_reversion'}
    evidence = runner.run_legacy_item(item, levels, registry=None)
    run_id = runner.save(item, evidence, 'TRI')
    assert run_store.latest('mean_reversion', 'reference')['run_id'] == run_id
    assert run_store.load_run(run_id)['run']['gate_result'] == 'DATA_BLOCKED'
    summary = runner.legacy_summary([{'item_id': 'LEGACY:mean_reversion', **runner._clean(evidence)}])
    assert summary['mean_reversion']['gate_result'] == 'DATA_BLOCKED' and summary['growth_momentum_breakout']['gate_result'] == 'DATA_BLOCKED'


def test_sensitivity_runs_only_after_a_clean_baseline(tmp_path, monkeypatch):
    from scripts import combination_lab_run as runner
    monkeypatch.setattr(runner, 'ITEMS', tmp_path)
    assert runner.sensitivity_eligible('mean_reversion') == (False, 'BASELINE_NOT_COMPLETE')
    path = tmp_path / (runner._safe('LEGACY:mean_reversion') + '.json')
    for evidence, expected in (({'status': 'FAILED'}, 'BASELINE_NOT_SUCCESS'),
                               ({'status': 'SUCCESS', 'checks': {'trade_count': 12}}, 'FEWER_THAN_30_TRADES'),
                               ({'status': 'SUCCESS', 'checks': {'trade_count': 40, 'degenerate': True}}, 'DEGENERATE_FACTOR'),
                               ({'status': 'SUCCESS', 'checks': {'trade_count': 40, 'pit_status': 'PIT_VIOLATION'}}, 'PIT_VIOLATION'),
                               ({'status': 'SUCCESS', 'checks': {'trade_count': 40}}, '')):
        path.write_text(json.dumps(evidence), encoding='utf-8')
        assert runner.sensitivity_eligible('mean_reversion')[1] == expected
