import json
from pathlib import Path

import pytest
import pandas as pd
from pandas.testing import assert_frame_equal

from core.research import repair_runner
from core.research.evidence_ledger import append as append_evidence
from core.research.repair_accounting import DataBlocked
from scripts import research_workflow as rw


def certify(monkeypatch, digest, requests):
    """A fixture dataset whose verified requests (stock, event_date) may certify every field; never the real audit."""
    monkeypatch.setitem(repair_runner.MATCHERS, 'FIXTURE', lambda p, r: (p['stock'], p['date']) == (r['stock_id'], r['event_date']))
    monkeypatch.setattr(repair_runner, 'CERTIFYING', {f: ('FIXTURE',) for f in (*repair_runner.REQUIRED_TERMS, 'event_list_complete')})
    sources = {digest: [{'dataset': 'FIXTURE', 'parameters': {'stock': s, 'date': d}} for s, d in requests]}
    monkeypatch.setattr(repair_runner, 'verified_sources', lambda: sources)


def test_daily_close_never_available_before_market_close():
    day, next_day = '2025-01-02', '2025-01-03'
    rows = pd.DataFrame([dict(asof_date=pd.Timestamp(day), execution_date=next_day, asset_id='A', target_weight=1,
                              signal_at=day + 'T10:00:00+08:00', score_available_at=day + 'T09:00:00+08:00',
                              membership_available_at=day + 'T09:00:00+08:00', calendar_available_at=day + 'T09:00:00+08:00')])
    bars = {day: {'A': {'close': 10, 'close_available_at': day + 'T09:30:00+08:00'}}, next_day: {}}
    state = {'date': day, 'nav': 10000, 'positions': {}, 'stock_receivable': [], 'stale_marks': {}}
    with pytest.raises(DataBlocked, match='SIGNAL_CLOSE_UNAVAILABLE'):
        repair_runner._hook(rows, bars)(state, [])


def test_runner_pit_rebalance_reconciliation_and_reproducibility(tmp_path, monkeypatch):
    days = ['2025-01-02', '2025-01-03', '2025-01-06', '2025-01-07']
    def bar(day):
        return {'open': 10, 'high': 11, 'low': 9, 'close': 10, 'volume': 10000, 'amount': 1000,
                'prior_amount': 1000, 'limit_up': 11, 'limit_down': 9,
                'close_available_at': day + 'T13:30:00+08:00'}
    bars = {day: {'A': bar(day), 'B': bar(day)} for day in days}
    del bars[days[2]]['A']
    scores, universe = [], []
    for date, execution, ranks in ((days[0], days[1], {'A': 2, 'B': 1}), (days[1], days[2], {'A': 1, 'B': 2})):
        for asset, score in ranks.items():
            scores.append(dict(asof_date=date, asset_id=asset, composite_score=score, signal_at=date + 'T15:00:00+08:00',
                               score_available_at=date + 'T14:00:00+08:00', score_source_sha256='a' * 64))
            universe.append(dict(asof_date=date, asset_id=asset, member=True, execution_date=execution,
                                 membership_available_at=date + 'T14:00:00+08:00', membership_source_sha256='b' * 64,
                                 calendar_available_at=date + 'T14:00:00+08:00', calendar_source_sha256='c' * 64))
    values = {'bars': {'price_basis': 'RAW', 'source': 'synthetic', 'bars': bars},
              'actions': {'actions': [], 'coverage': {'A': days, 'B': days}}, 'evidence': {'label': 'synthetic'},
              'scores': scores, 'universe': universe}
    paths = {}
    for name, value in values.items():
        path = tmp_path / (name + '.json')
        path.write_text(json.dumps(value), encoding='utf-8')
        paths[name] = str(path)
    registry, ledger = tmp_path / 'registry.csv', tmp_path / 'ledger.jsonl'
    rw.register({'hypothesis_id': rw.PIT_RAW_HYPOTHESIS, 'strategy': 'A191_132', 'hypothesis': 'h',
                 'falsification_test': 't', 'data_window': 'dev', 'trial_budget': '12', 'source': 's'},
                path=registry, today='2026-10-28')
    monkeypatch.setattr(rw, 'ARCHIVED_MANIFESTS', {})
    monkeypatch.setattr(repair_runner, 'OUTPUT_ROOT', tmp_path / 'outputs')
    config = dict(config_id='fixture', top_n=1, stock_weighting='equal', initial_cash=10000,
                  fee_rate=.001, tax_rate=.003, minimum_fee=1, slippage=0,
                  data_label='SYNTHETIC', data_end=days[-1], inputs=paths, output=str(tmp_path / 'outputs' / 'first'))
    first = repair_runner.run(config, registry=registry, ledger=ledger)
    second = repair_runner.run({**config, 'output': str(tmp_path / 'outputs' / 'second')}, registry=registry, ledger=ledger)
    assert first['manifest'] == second['manifest']
    assert first['manifest']['terms_status'] == 'NOT_APPLICABLE_SYNTHETIC'
    assert_frame_equal(first['targets'], second['targets'])
    for track in ('base', 'stress'):
        assert first['tracks'][track]['result'] == second['tracks'][track]['result']
        assert first['tracks'][track]['reconciliation']['status'] == 'PASS'
    base = first['tracks']['base']['result']
    stress = first['tracks']['stress']['result']
    assert base['daily'][1]['cash'] > stress['daily'][1]['cash']
    assert first['tracks']['base']['metrics']['turnover']['value'] is None
    assert base['cancellations'] == [{'date': days[1], 'order_id': days[0] + ':A'}]
    assert base['daily'][2]['stale_marks'] == {'A': days[1]}
    assert any(e['kind'] == 'ORDER_STATUS' and e['date'] == days[2] and e['stock_id'] == 'A' and e['status'] == 'UNFILLED' for e in base['ledger'])
    assert first['manifest']['effective_trials'] == 744
    values['bars']['bars'][days[2]]['A'] = bar(days[2])
    values['actions']['actions'] = [dict(stock_id='A', ex_date=days[2], type='cash_stock', cash_per_share=1,
                                          stock_ratio=.2, cash_pay_date=days[3], stock_credit_date=days[3],
                                          source='synthetic', source_sha256='a' * 64)]
    Path(paths['bars']).write_text(json.dumps(values['bars']), encoding='utf-8')
    Path(paths['actions']).write_text(json.dumps(values['actions']), encoding='utf-8')
    dividend = repair_runner.run({**config, 'output': str(tmp_path / 'outputs' / 'dividend')}, registry=registry, ledger=ledger)
    for track in dividend['tracks'].values():
        assert track['reconciliation']['status'] == 'PASS'
        assert track['result']['daily'][2]['cash_receivable'] == 5
        assert track['result']['daily'][2]['stock_receivable'][0]['shares'] == 1
        assert track['result']['daily'][3]['cash_receivable'] == 0
    del values['bars']['bars'][days[2]]['A']
    values['actions']['actions'] = []
    Path(paths['actions']).write_text(json.dumps(values['actions']), encoding='utf-8')
    values['bars']['bars'][days[2]]['A'] = {**bar(days[2]), 'open': 9, 'high': 9, 'low': 9, 'close': 9, 'limit_down': 9}
    Path(paths['bars']).write_text(json.dumps(values['bars']), encoding='utf-8')
    limit_down = repair_runner.run({**config, 'output': str(tmp_path / 'outputs' / 'limit_down')}, registry=registry, ledger=ledger)
    assert_frame_equal(first['targets'], limit_down['targets'])
    assert any(e.get('reason') == 'REJECTED_LOCKED_LIMIT_DOWN' for e in limit_down['tracks']['base']['result']['ledger'])
    del values['bars']['bars'][days[2]]['A']
    values['bars']['bars'][days[1]]['A'].update(open=11, high=11, low=11, close=11, limit_up=11)
    Path(paths['bars']).write_text(json.dumps(values['bars']), encoding='utf-8')
    locked = repair_runner.run({**config, 'output': str(tmp_path / 'outputs' / 'locked')}, registry=registry, ledger=ledger)
    assert_frame_equal(first['targets'], locked['targets'])
    assert any(e.get('reason') == 'REJECTED_LOCKED_LIMIT_UP' for e in locked['tracks']['base']['result']['ledger'])
    values['bars']['bars'][days[1]]['A'] = bar(days[1])
    values['bars']['bars'][days[1]]['A']['volume'] = 0
    Path(paths['bars']).write_text(json.dumps(values['bars']), encoding='utf-8')
    changed = repair_runner.run({**config, 'output': str(tmp_path / 'outputs' / 'changed')}, registry=registry, ledger=ledger)
    assert_frame_equal(first['targets'], changed['targets'])
    assert changed['tracks']['base']['result']['daily'][1]['positions'] == {}
    evidence = tmp_path / 'evidence.jsonl'
    append_evidence(evidence, [dict(stock_id='A', event_date='2025-01-02', field='cash_dividend',
                                   value_or_UNKNOWN='UNKNOWN', source='fixture', sha256='a' * 64,
                                   fetched_at=None, grant_scope='fixture', tier='LOCAL_CACHE_UNCERTIFIED')])
    real_inputs = {**paths, 'evidence': str(evidence)}
    trials_before = rw.effective_trials(ledger)
    monkeypatch.setattr(repair_runner, 'verified_sources', lambda: {})  # never read the real budget audit in tests
    real = repair_runner.run({**config, 'inputs': real_inputs, 'data_label': 'REAL_DATA', 'output': str(tmp_path / 'outputs' / 'real')}, registry=registry, ledger=ledger)
    assert real['status'] == 'BLOCKED' and real['admission'] == 'NOT_ADMITTED' and 'manifest' not in real
    assert real['held_events']['stocks_event_list_unverified'] == ['A', 'B']
    assert rw.effective_trials(ledger) == trials_before and (tmp_path / 'outputs' / 'real' / 'blockers.json').is_file()
    certified = tmp_path / 'certified.jsonl'
    official = dict(source='fixture', sha256='b' * 64, fetched_at='2026-10-30', grant_scope='fixture', tier='OFFICIAL_VERIFIED_FIELD')
    append_evidence(certified, [dict(official, stock_id=s, event_date='', field='event_list_complete', value_or_UNKNOWN=True) for s in 'AB'])
    certify(monkeypatch, 'b' * 64, [(s, '') for s in 'AB'])
    verified = repair_runner.run({**config, 'inputs': {**paths, 'evidence': str(certified)}, 'data_label': 'REAL_DATA',
                                  'output': str(tmp_path / 'outputs' / 'verified')}, registry=registry, ledger=ledger)
    assert verified['manifest']['terms_status'] == 'VERIFIED' and verified['manifest']['held_events']['total'] == 0
    assert verified['tracks']['base']['metrics']['total_return']['value'] is not None
    assert rw.effective_trials(ledger) == trials_before + 1
    values['bars']['bars'][days[0]]['A']['close_available_at'] = days[0] + 'T16:00:00+08:00'
    Path(paths['bars']).write_text(json.dumps(values['bars']), encoding='utf-8')
    with pytest.raises(DataBlocked, match='SIGNAL_CLOSE_UNAVAILABLE'):
        repair_runner.run({**config, 'output': str(tmp_path / 'outputs' / 'late')}, registry=registry, ledger=ledger)
    values['bars']['price_basis'] = 'ADJUSTED'
    Path(paths['bars']).write_text(json.dumps(values['bars']), encoding='utf-8')
    with pytest.raises(DataBlocked, match='ADJUSTED_OR_UNVERIFIED_PRICE_BASIS'):
        repair_runner.run({**config, 'output': str(tmp_path / 'outputs' / 'adjusted')}, registry=registry, ledger=ledger)


def test_formal_gate_checks_events_actually_held_and_matches_the_dry_run(tmp_path, monkeypatch):
    days = ['2025-01-02', '2025-01-03', '2025-01-06', '2025-01-07']
    bar = {'open': 10, 'high': 11, 'low': 9, 'close': 10, 'volume': 10000, 'amount': 1000, 'prior_amount': 1000,
           'limit_up': 11, 'limit_down': 9}
    bars = {day: {s: dict(bar, close_available_at=day + 'T13:30:00+08:00') for s in 'AB'} for day in days}
    scores, universe = [], []
    for asset, score in {'A': 2, 'B': 1}.items():
        scores.append(dict(asof_date=days[0], asset_id=asset, composite_score=score, signal_at=days[0] + 'T15:00:00+08:00',
                           score_available_at=days[0] + 'T14:00:00+08:00', score_source_sha256='a' * 64))
        universe.append(dict(asof_date=days[0], asset_id=asset, member=True, execution_date=days[1],
                             membership_available_at=days[0] + 'T14:00:00+08:00', membership_source_sha256='b' * 64,
                             calendar_available_at=days[0] + 'T14:00:00+08:00', calendar_source_sha256='c' * 64))
    action = dict(stock_id='A', ex_date=days[2], type='cash', cash_per_share=1, stock_ratio=0, cash_pay_date=days[3],
                  stock_credit_date=None, source='official fixture', source_sha256='d' * 64)
    values = {'bars': {'price_basis': 'RAW', 'source': 'fixture', 'bars': bars},
              'actions': {'actions': [action], 'coverage': {'A': days, 'B': days}}, 'scores': scores, 'universe': universe}
    paths = {}
    for name, value in values.items():
        paths[name] = tmp_path / (name + '.json')
        paths[name].write_text(json.dumps(value), encoding='utf-8')
    official = dict(source='fixture', sha256='d' * 64, fetched_at='2026-10-30', grant_scope='fixture', tier='OFFICIAL_VERIFIED_FIELD')
    terms = {'ex_date': days[2], 'cash_dividend': 1, 'stock_dividend': 'NOT_APPLICABLE', 'cash_pay_date': days[3],
             'stock_credit_date': 'NOT_APPLICABLE', 'rights_terms': 'NOT_APPLICABLE', 'publication_time': '2024-12-20T17:00:00+08:00'}
    paths['evidence'] = tmp_path / 'evidence.jsonl'
    append_evidence(paths['evidence'], [dict(official, stock_id=s, event_date='', field='event_list_complete', value_or_UNKNOWN=True) for s in 'AB']
                    + [dict(official, stock_id='A', event_date=days[2], field=f, value_or_UNKNOWN=v) for f, v in terms.items()])
    registry, ledger = tmp_path / 'registry.csv', tmp_path / 'ledger.jsonl'
    rw.register({'hypothesis_id': rw.PIT_RAW_HYPOTHESIS, 'strategy': 'A191_132', 'hypothesis': 'h', 'falsification_test': 't',
                 'data_window': 'dev', 'trial_budget': '12', 'source': 's'}, path=registry, today='2026-10-28')
    monkeypatch.setattr(rw, 'ARCHIVED_MANIFESTS', {})
    monkeypatch.setattr(repair_runner, 'OUTPUT_ROOT', tmp_path / 'outputs')
    config = dict(config_id='fixture', top_n=1, stock_weighting='equal', initial_cash=10000, fee_rate=.001, tax_rate=.003,
                  minimum_fee=1, slippage=0, data_label='REAL_DATA', data_end=days[-1],
                  inputs={k: str(v) for k, v in paths.items()}, output=str(tmp_path / 'outputs' / 'unbound'))
    certify(monkeypatch, 'd' * 64, [('A', ''), ('B', ''), ('B', days[2])])  # the same hash, verified for another stock's request
    unbound = repair_runner.run(config, registry=registry, ledger=ledger)
    assert unbound['admission'] == 'NOT_ADMITTED' and rw.effective_trials(ledger) == 743
    assert unbound['held_events']['events'][0]['blocked_fields']['cash_pay_date'] == 'UNBOUND_OFFICIAL_ROW'
    certify(monkeypatch, 'd' * 64, [('A', ''), ('B', ''), ('A', days[2])])
    run = repair_runner.run({**config, 'output': str(tmp_path / 'outputs' / 'held')}, registry=registry, ledger=ledger)
    held = run['manifest']['held_events']
    assert held['status'] == 'VERIFIED' and held['total'] == held['verified'] == 1 and held['blocked_events'] == []
    assert run['manifest']['terms_status'] == 'VERIFIED' and run['tracks']['base']['metrics']['total_return']['value'] is not None
    assert run['tracks']['base']['result']['daily'][2]['cash_receivable'] == 5 and rw.effective_trials(ledger) == 744
    dry = repair_runner.held_event_terms(repair_runner.read_evidence(paths['evidence']), {'A', 'B'}, {('A', days[2])},
                                         repair_runner.verified_sources())
    assert dry['status'] == held['status']  # the dry run and the formal run use one function on the same evidence
    # Inputs edited after the dry run are refused before admission, so no trial is counted.
    original = repair_runner.held_event_terms
    def edit_after_dry_run(*args):
        result = original(*args)
        append_evidence(paths['evidence'], [dict(official, stock_id='B', event_date='', field='note', value_or_UNKNOWN='x')])
        return result
    monkeypatch.setattr(repair_runner, 'held_event_terms', edit_after_dry_run)
    with pytest.raises(DataBlocked, match='INPUT_CHANGED_DURING_DRY_RUN'):
        repair_runner.run({**config, 'output': str(tmp_path / 'outputs' / 'edited')}, registry=registry, ledger=ledger)
    assert rw.effective_trials(ledger) == 744
    monkeypatch.setattr(repair_runner, 'held_event_terms', original)
    # The window end is checked before the dry run: nothing is written, not even a blockers report.
    with pytest.raises(DataBlocked, match='OOS_OR_INPUT_WINDOW_BLOCKED'):
        repair_runner.run({**config, 'data_end': '2026-01-02', 'output': str(tmp_path / 'outputs' / 'window')}, registry=registry, ledger=ledger)
    assert not (tmp_path / 'outputs' / 'window').exists()
    def note(stock):
        append_evidence(paths['evidence'], [dict(official, stock_id=stock, event_date='', field='note', value_or_UNKNOWN='x')])
    loaded = rw._loaded_local_code
    def edit_before_admission():  # runs after the dry run and before admission
        note('C')
        return loaded()
    monkeypatch.setattr(rw, '_loaded_local_code', edit_before_admission)
    with pytest.raises(DataBlocked, match='INPUT_CHANGED_BETWEEN_DRY_RUN_AND_ADMISSION'):
        repair_runner.run({**config, 'output': str(tmp_path / 'outputs' / 'between')}, registry=registry, ledger=ledger)
    assert rw.effective_trials(ledger) == 744
    monkeypatch.setattr(rw, '_loaded_local_code', loaded)
    admit = rw.admit_experiment
    def edit_during_admission(*args, **kwargs):  # admission hashes the edited file; the trial is counted, the run stops
        note('D')
        return admit(*args, **kwargs)
    monkeypatch.setattr(rw, 'admit_experiment', edit_during_admission)
    with pytest.raises(DataBlocked, match='INPUT_CHANGED_DURING_ADMISSION'):
        repair_runner.run({**config, 'output': str(tmp_path / 'outputs' / 'during')}, registry=registry, ledger=ledger)
    assert rw.effective_trials(ledger) == 745
