"""V4 held-event policy end to end: forfeit keeps the configuration computable and is reconciled; V3 still stops."""
import json

import pandas as pd
import pytest

from core.research import repair_runner
from core.research.repair_runner import _research_tracks
from core.research.research_mode import (D5_RESEARCH_ASSUMPTIONS_V3, RESEARCH_ASSUMPTIONS_V3, RESEARCH_ASSUMPTIONS_V4,
                                         assumptions_registration, assumptions_sha, registered_version, screen_registration)
from scripts import research_workflow as workflow


def fixture(days):
    bars = {day: {'2330': {'open': 10, 'high': 11, 'low': 9, 'close': 10, 'volume': 10000, 'amount': 100000,
                           'prior_amount': 100000, 'limit_up': 11, 'limit_down': 9, 'close_available_at': day + 'T13:30:00+08:00'}}
            for day in days}
    scores = [{'asof_date': days[0], 'asset_id': '2330', 'composite_score': 1, 'signal_at': days[0] + 'T15:00:00+08:00',
               'score_available_at': days[0] + 'T14:00:00+08:00', 'score_source_sha256': 'a' * 64}]
    universe = [{'asof_date': days[0], 'asset_id': '2330', 'member': True, 'execution_date': days[1],
                 'membership_available_at': days[0] + 'T14:00:00+08:00', 'membership_source_sha256': 'b' * 64,
                 'calendar_available_at': None, 'calendar_source_sha256': None}]
    parameters = {'config_id': 'fixture', 'top_n': 1, 'stock_weighting': 'equal', 'initial_cash': 10000,
                  'fee_rate': .001, 'tax_rate': .003, 'minimum_fee': 1, 'slippage': 0}
    return bars, scores, universe, parameters


def test_v4_forfeits_unknown_dividend_but_stops_on_share_count_change():
    days = ['2025-01-02', '2025-01-03', '2025-01-06', '2025-01-07']
    bars, scores, universe, parameters = fixture(days)
    dividend = {'ex_date': days[2], 'stock_id': '2330', 'missing_terms': ['cash_dividend', 'stock_dividend'],
                'source_sha256': 'd' * 64, 'value_per_share': .5}
    args = {'bars': bars, 'scores': scores, 'universe': universe, 'actions': [], 'coverage': {'2330': days}, 'parameters': parameters}
    stopped = _research_tracks(**args, unresolved_events=[dividend], assumptions=RESEARCH_ASSUMPTIONS_V3)
    assert stopped['status'] == 'NOT_COMPUTABLE' and stopped['held_event_policy'] == 'STOP'
    done = _research_tracks(**args, unresolved_events=[dividend], assumptions=RESEARCH_ASSUMPTIONS_V4)
    assert done['status'] == 'COMPUTABLE' and done['held_event_policy'] == 'FORFEIT_UNKNOWN_ENTITLEMENTS'
    assert all(track['reconciliation']['status'] == 'PASS' for track in done['tracks'].values())
    forfeit = done['forfeits']['base']
    assert forfeit['events'] == 1 and forfeit['stocks'] == ['2330'] and forfeit['forfeited_value_total'] > 0
    assert 0 < forfeit['forfeited_share_of_final_nav'] < 1
    reduction = {**dividend, 'missing_terms': ['share_count_change', 'settlement_terms']}
    blocked = _research_tracks(**args, unresolved_events=[reduction], assumptions=RESEARCH_ASSUMPTIONS_V4)
    assert blocked['status'] == 'NOT_COMPUTABLE' and blocked['missing_terms'] == reduction['missing_terms']


def test_registered_versions_resolve_by_sha_and_runner_records_version(tmp_path, monkeypatch):
    assert registered_version(assumptions_sha(RESEARCH_ASSUMPTIONS_V4)) == 'V4'
    assert registered_version(assumptions_sha(D5_RESEARCH_ASSUMPTIONS_V3)) == 'D5-V3'
    assert registered_version('0' * 64) is None
    days = ['2025-01-02', '2025-01-03', '2025-01-06']
    bars, scores, universe, parameters = fixture(days)
    paths = {}
    for name, data in {'bars': {'price_basis': 'RAW', 'source': 'twse_rwd', 'bars': bars},
                       'actions': {'actions': [], 'coverage': {'2330': days}, 'unresolved_events': []},
                       'scores': scores, 'universe': universe}.items():
        path = tmp_path / f'{name}.json'
        path.write_text(json.dumps(data), encoding='utf-8')
        paths[name] = str(path)
    evidence = tmp_path / 'evidence.json'
    evidence.write_text(json.dumps({'kind': 'fixture'}), encoding='utf-8')
    paths['evidence'] = str(evidence)
    registry, ledger = tmp_path / 'registry.csv', tmp_path / 'research.jsonl'
    workflow.register(screen_registration(), path=registry)
    workflow.register(assumptions_registration('V4'), path=registry)
    monkeypatch.setattr(repair_runner, '_verify_research_provenance', lambda evidence, paths, parameters: None)
    monkeypatch.setattr(repair_runner, 'RESEARCH_OUTPUT_ROOT', tmp_path / 'research')
    config = {**parameters, 'strategy': 'fixture', 'data_label': 'RESEARCH_UNCERTIFIED', 'data_end': days[-1],
              'assumptions_sha256': assumptions_sha(RESEARCH_ASSUMPTIONS_V4), 'inputs': paths,
              'output': str(tmp_path / 'research' / 'result')}
    result = repair_runner.run(config, registry=registry, research_ledger=ledger)
    manifest = result['manifest']
    assert manifest['status'] == 'COMPUTABLE' and manifest['assumptions_version'] == 'V4'
    assert manifest['parameter_fit_scope'] == 'IN_SAMPLE_DEVELOPMENT_WINDOW' and manifest['held_event_policy'] == 'FORFEIT_UNKNOWN_ENTITLEMENTS'
    assert workflow.research_trials(ledger) == 1
    from core.research.repair_accounting import DataBlocked
    with pytest.raises(DataBlocked, match='UNREGISTERED_RESEARCH_ASSUMPTIONS'):
        repair_runner.run({**config, 'assumptions_sha256': 'f' * 64, 'output': str(tmp_path / 'research' / 'other')},
                          registry=registry, research_ledger=ledger)
    with pytest.raises(ValueError, match='REGISTRY_UNVERIFIED'):  # V3 identity is runnable only when V3 is registered here
        repair_runner.run({**config, 'assumptions_sha256': assumptions_sha(RESEARCH_ASSUMPTIONS_V3), 'output': str(tmp_path / 'research' / 'v3')},
                          registry=registry, research_ledger=ledger)
    assert workflow.research_trials(ledger) == 1


def test_v5_reference_exit_conversion_keeps_rows_without_prices_unresolved():
    from scripts.research_mode_inputs import convert_reference_changes, convert_reference_exits
    from core.research.research_mode import RESEARCH_ASSUMPTIONS_V5, assumptions_sha, registered_version
    assert registered_version(assumptions_sha(RESEARCH_ASSUMPTIONS_V5)) == 'V5'
    rows = [{'ex_date': '2024-10-21', 'stock_id': '8249', 'kind': 'CAPITAL_REDUCTION', 'pre_close': 48.95, 'reference_price': 53.27, 'reason': 'cash_return'},
            {'ex_date': '2024-11-11', 'stock_id': '8476', 'kind': 'PAR_VALUE_CHANGE', 'pre_close': None, 'reference_price': 29.4}]
    result = convert_reference_exits(rows, 'c' * 64, '2025-12-31')
    assert result['actions'] == [{'ex_date': '2024-10-21', 'stock_id': '8249', 'type': 'reference_change_exit', 'kind': 'CAPITAL_REDUCTION',
                                  'pre_close': 48.95, 'reference_price': 53.27, 'reason': 'cash_return',
                                  'source': 'LOCAL_CACHE_UNCERTIFIED', 'source_sha256': 'c' * 64}]
    assert result['unresolved_events'][0]['missing_terms'] == ['share_count_change', 'settlement_terms']
    assert convert_reference_changes(rows, 'c' * 64, '2025-12-31')[0]['missing_terms'] == ['share_count_change', 'settlement_terms']  # V4 path unchanged
    with pytest.raises(ValueError, match='OOS'):
        convert_reference_exits([{**rows[0], 'ex_date': '2026-01-05'}], 'c' * 64, '2025-12-31')


def test_d5_composite_uses_only_fitted_factors_and_directions():
    from scripts.prepare_d5_signals import composite
    dates = pd.to_datetime(['2025-01-02'])
    ranks = {'momentum_20d': pd.DataFrame({'A': [.9], 'B': [.1]}, index=dates),
             'realized_vol_20d': pd.DataFrame({'A': [.8], 'B': [.2]}, index=dates),
             'unused': pd.DataFrame({'A': [.0], 'B': [1.0]}, index=dates)}
    candidates = pd.DataFrame([{'factor_id': 'momentum_20d', 'mean_ic': .05, 'icir': .5, 'direction': 1},
                               {'factor_id': 'realized_vol_20d', 'mean_ic': -.05, 'icir': -.5, 'direction': -1}])
    correlation = pd.DataFrame([[1, 0], [0, 1]], index=candidates['factor_id'], columns=candidates['factor_id'])
    score, weights = composite({'candidates': candidates, 'correlation': correlation}, 'equal', ranks, dates[0])
    assert score['A'] == pytest.approx((.9 + (1 - .8)) / 2) and score['B'] == pytest.approx((.1 + (1 - .2)) / 2)
    assert set(weights['factor_id']) == {'momentum_20d', 'realized_vol_20d'}
