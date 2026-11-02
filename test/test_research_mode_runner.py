import json
from core.research import repair_runner
from core.research.evidence_ledger import append as append_evidence
from core.research.repair_runner import _research_tracks
from core.research.research_mode import RESEARCH_ASSUMPTIONS_V3, assumptions_registration, assumptions_sha, screen_registration
from scripts import research_workflow as workflow


def test_unresolved_held_configuration_has_no_partial_performance(tmp_path, monkeypatch):
    days = ['2025-01-02', '2025-01-03', '2025-01-06', '2025-01-07']
    bars = {day: {'2330': {'open': 10, 'high': 11, 'low': 9, 'close': 10, 'volume': 10000,
                           'amount': 100000, 'prior_amount': 100000, 'limit_up': 11, 'limit_down': 9,
                           'close_available_at': day + 'T13:30:00+08:00'}} for day in days}
    signal = days[0] + 'T15:00:00+08:00'
    scores = [{'asof_date': days[0], 'asset_id': '2330', 'composite_score': 1, 'signal_at': signal,
               'score_available_at': days[0] + 'T14:00:00+08:00', 'score_source_sha256': 'a' * 64}]
    universe = [{'asof_date': days[0], 'asset_id': '2330', 'member': True, 'execution_date': days[1],
                 'membership_available_at': days[0] + 'T14:00:00+08:00', 'membership_source_sha256': 'b' * 64,
                 'calendar_available_at': None, 'calendar_source_sha256': None}]
    parameters = {'config_id': 'fixture', 'top_n': 1, 'stock_weighting': 'equal', 'initial_cash': 10000,
                  'fee_rate': .001, 'tax_rate': .003, 'minimum_fee': 1, 'slippage': 0}
    event = {'ex_date': days[2], 'stock_id': '2330', 'missing_terms': ['rights_terms'], 'source_sha256': 'd' * 64}
    args = {'bars': bars, 'scores': scores, 'universe': universe, 'actions': [], 'coverage': {'2330': days},
            'parameters': parameters}
    result = _research_tracks(**args, unresolved_events=[event])
    assert result['status'] == 'NOT_COMPUTABLE' and result['reason'] == 'UNRESOLVED_CORPORATE_ACTION_HELD'
    assert result['date'] == days[2] and result['stock_id'] == '2330' and result['holdings'] > 0
    assert 'tracks' not in result and 'targets' not in result and result['research_only'] is True
    completed = _research_tracks(**args, unresolved_events=[])
    assert completed['status'] == 'COMPUTABLE'
    assert all(track['reconciliation']['status'] == 'PASS' for track in completed['tracks'].values())
    closed_bars = {day: value for day, value in bars.items() if day != days[1]}
    delayed_universe = [{**universe[0], 'execution_date': days[2]}]
    delayed = _research_tracks(**{**args, 'bars': closed_bars, 'universe': delayed_universe,
                                  'coverage': {'2330': list(closed_bars)}}, unresolved_events=[])
    assert delayed['targets']['asset_id'].tolist() == completed['targets']['asset_id'].tolist()
    assert delayed['targets']['target_weight'].tolist() == completed['targets']['target_weight'].tolist()
    assert delayed['tracks']['base']['result']['generated_orders'][0]['date'] == days[2]
    no_members = [{**universe[0], 'member': False}]
    empty = _research_tracks(**{**args, 'universe': no_members}, unresolved_events=[])
    assert empty['status'] == 'NOT_COMPUTABLE' and empty['reason'] == 'NO_ELIGIBLE_REBALANCE'
    assert empty['skipped_rebalance_dates'] == [days[0]] and 'tracks' not in empty
    second_score = {**scores[0], 'asof_date': days[1], 'signal_at': days[1] + 'T15:00:00+08:00',
                    'score_available_at': days[1] + 'T14:00:00+08:00'}
    second_universe = {**universe[0], 'asof_date': days[1], 'execution_date': days[2],
                       'membership_available_at': days[1] + 'T14:00:00+08:00'}
    resumed = _research_tracks(**{**args, 'scores': [scores[0], second_score],
                                  'universe': [no_members[0], second_universe]}, unresolved_events=[])
    assert resumed['status'] == 'COMPUTABLE' and resumed['skipped_rebalance_dates'] == [days[0]]
    assert resumed['tracks']['base']['result']['generated_orders'][0]['date'] == days[2]
    paths = {}
    for name, data in {'bars': {'price_basis': 'RAW', 'source': 'twse_rwd', 'bars': bars},
                       'actions': {'actions': [], 'coverage': {'2330': days}, 'unresolved_events': [event]},
                       'scores': scores, 'universe': universe}.items():
        path = tmp_path / f'{name}.json'
        path.write_text(json.dumps(data), encoding='utf-8')
        paths[name] = str(path)
    evidence = tmp_path / 'evidence.json'
    evidence.write_text(json.dumps({'producer_sha256': workflow.sha256(workflow.ROOT / 'scripts/research_mode_inputs.py'),
                                    'input_sha256': {name: workflow.sha256(path) for name, path in paths.items()}}), encoding='utf-8')
    paths['evidence'] = str(evidence)
    registry, ledger = tmp_path / 'registry.csv', tmp_path / 'research.jsonl'
    workflow.register(screen_registration(), path=registry)
    workflow.register(assumptions_registration('V3'), path=registry)
    monkeypatch.setattr(repair_runner, 'RESEARCH_OUTPUT_ROOT', tmp_path / 'outputs')
    config = {**parameters, 'strategy': 'fixture', 'data_label': 'RESEARCH_UNCERTIFIED', 'data_end': days[-1],
              'assumptions_sha256': assumptions_sha(RESEARCH_ASSUMPTIONS_V3), 'inputs': paths,
              'output': str(tmp_path / 'outputs' / 'blocked')}
    import pytest
    from core.research.repair_accounting import DataBlocked
    with pytest.raises(DataBlocked, match='SCORES_NOT_FROM_RAW_CONVERSION'):
        repair_runner.run(config, registry=registry, research_ledger=ledger)
    assert workflow.research_trials(ledger) == 0
    monkeypatch.setattr(repair_runner, '_verify_research_provenance', lambda evidence, paths, parameters: None)
    blocked = repair_runner.run(config, registry=registry, research_ledger=ledger)
    assert blocked['manifest']['status'] == 'NOT_COMPUTABLE' and 'tracks' not in blocked
    assert workflow.research_trials(ledger) == 1
    replay = repair_runner.run(config, registry=registry, research_ledger=ledger)
    assert replay['reused'] is True and workflow.research_trials(ledger) == 1


def test_same_complete_inputs_research_computes_but_real_data_is_blocked(tmp_path, monkeypatch):
    days = ['2025-01-02', '2025-01-03', '2025-01-06']
    bars = {day: {'2330': {'open': 10, 'high': 11, 'low': 9, 'close': 10, 'volume': 10000,
                           'amount': 100000, 'prior_amount': 100000, 'limit_up': 11, 'limit_down': 9,
                           'close_available_at': day + 'T13:30:00+08:00'}} for day in days}
    scores = [{'asof_date': days[0], 'asset_id': '2330', 'composite_score': 1, 'signal_at': days[0] + 'T15:00:00+08:00',
               'score_available_at': days[0] + 'T14:00:00+08:00', 'score_source_sha256': 'a' * 64}]
    universe = [{'asof_date': days[0], 'asset_id': '2330', 'member': True, 'execution_date': days[1],
                 'membership_available_at': days[0] + 'T14:00:00+08:00', 'membership_source_sha256': 'b' * 64,
                 'calendar_available_at': None, 'calendar_source_sha256': None}]
    paths = {}
    for name, data in {'bars': {'price_basis': 'RAW', 'source': 'twse_rwd', 'bars': bars},
                       'actions': {'actions': [], 'coverage': {'2330': days}, 'unresolved_events': []},
                       'scores': scores, 'universe': universe}.items():
        path = tmp_path / f'{name}.json'
        path.write_text(json.dumps(data), encoding='utf-8')
        paths[name] = str(path)
    evidence = tmp_path / 'evidence.jsonl'
    append_evidence(evidence, [dict(stock_id='2330', event_date='', field='event_list_complete', value_or_UNKNOWN='UNKNOWN',
                                     source='fixture', sha256='a' * 64, fetched_at=None, grant_scope='test', tier='UNKNOWN')])
    paths['evidence'] = str(evidence)
    registry, ledger, formal = tmp_path / 'registry.csv', tmp_path / 'research.jsonl', tmp_path / 'formal.jsonl'
    workflow.register(screen_registration(), path=registry)
    workflow.register(assumptions_registration('V3'), path=registry)
    monkeypatch.setattr(repair_runner, '_verify_research_provenance', lambda evidence, paths, parameters: None)
    monkeypatch.setattr(repair_runner, 'verified_sources', lambda: {})
    monkeypatch.setattr(repair_runner, 'RESEARCH_OUTPUT_ROOT', tmp_path / 'research')
    monkeypatch.setattr(repair_runner, 'OUTPUT_ROOT', tmp_path / 'formal')
    config = {'strategy': 'fixture', 'config_id': 'fixture', 'top_n': 1, 'stock_weighting': 'equal',
              'initial_cash': 10000, 'fee_rate': .001, 'tax_rate': .003, 'minimum_fee': 1, 'slippage': 0,
              'data_label': 'RESEARCH_UNCERTIFIED', 'data_end': days[-1],
              'assumptions_sha256': assumptions_sha(RESEARCH_ASSUMPTIONS_V3), 'inputs': paths,
              'output': str(tmp_path / 'research' / 'result')}
    research = repair_runner.run(config, registry=registry, research_ledger=ledger)
    assert research['manifest']['status'] == 'COMPUTABLE' and research['manifest']['label'] == 'DIAGNOSTIC_ONLY'
    real = repair_runner.run({**config, 'data_label': 'REAL_DATA', 'output': str(tmp_path / 'formal' / 'result')},
                             registry=registry, ledger=formal)
    assert real['admission'] == 'NOT_ADMITTED' and 'BLOCKED_UNVERIFIED_OFFICIAL_TERMS' in real['reason']
    assert workflow.effective_trials(formal) == 743 and workflow.research_trials(ledger) == 1
