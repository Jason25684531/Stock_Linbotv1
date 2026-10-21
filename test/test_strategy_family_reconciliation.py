"""Synthetic taxonomy boundaries, never financial backtest/parity evidence."""
from copy import deepcopy
import json

import pytest

from scripts.reconcile_strategy_families import reconcile, sha


def record(identity, kind, **extra):
    return {'id': identity, 'kind': kind, 'counts_as_strategy': kind not in {'BENCHMARK', 'SIGNAL_ONLY', 'ANALYSIS_ONLY'},
        'sources': [{'path': 'source.ipynb'}], 'display_name': identity, **extra}


def inputs(tmp_path, records, decisions=None):
    pool = tmp_path / 'pool.json'
    pool.write_text('{}')
    return ({'records': records, 'notebooks': []},
        {'records': [{'id': r['id'], 'backtest': {'status': 'BLOCKED', 'reason': 'synthetic missing data'}} for r in records]},
        {'method': 'reviewed logic', 'reviewed_source_sha256': {}, 'notebook_decisions': decisions or {},
         'native_decisions': {}, 'd5_factor_ids': ['momentum', 'quality'],
         'd5_pool_evidence': {'path': 'pool.json', 'sha256': sha(pool)}})


def decision(family, role='ORIGINAL'):
    return {'family_id': family, 'name': family, 'investment_logic': 'reviewed signal',
        'evidence': [{'path': 'source.ipynb'}], 'reviewed_role': role}


def test_port_and_original_share_family_without_promoting_parity(tmp_path):
    rows = [record('original', 'STRATEGY'), record('future', 'STRATEGY', sources=[{'path': 'future.ipynb'}]),
            record('port', 'REGISTERED', original_notebook='source.ipynb')]
    future_decision = decision('signal')
    future_decision['evidence'] = [{'path': 'future.ipynb'}]
    i, v, r = inputs(tmp_path, rows, {'original': decision('signal'), 'future': future_decision})
    before = deepcopy((i, v, r))
    result = reconcile(i, v, r, tmp_path)
    assert result['stats']['strategy_families'] == 1
    assert result['stats']['ported_families'] == 1
    assert result['families'][0]['execution_contract_count'] == 3
    assert result['stats']['partially_ported_families'] == 1
    assert result['families'][0]['unported_original_versions'] == ['future']
    assert result['validation_plan']['port_parity_checks'][0]['original_ids'] == ['original']
    assert result['families'][0]['family_logic_validation'].startswith('NOT_TESTED')
    assert (i, v, r) == before


def test_same_notebook_can_contain_different_signal_families(tmp_path):
    rows = [record('technical', 'STRATEGY'), record('ml', 'STRATEGY')]
    i, v, r = inputs(tmp_path, rows, {k: decision(k) for k in ('technical', 'ml')})
    result = reconcile(i, v, r, tmp_path)
    assert result['stats']['strategy_families'] == len(r['notebook_decisions'])


def test_parameter_weighting_does_not_add_family_but_factor_set_does(tmp_path):
    rows = [record(k, 'RESEARCH_SETTING', strategy_id=k, namespace='strategy_research', parameters=p)
        for k, p in [('one', {'factor_set': 'a+b', 'top_n': 10, 'factor_weighting': 'equal'}),
                     ('two', {'factor_set': 'b+a', 'top_n': 20, 'factor_weighting': 'icir'}),
                     ('three', {'factor_set': 'a', 'top_n': 10})]]
    i, v, r = inputs(tmp_path, rows)
    result = reconcile(i, v, r, tmp_path)
    assert result['stats']['strategy_families'] == 2
    assert result['stats']['research_parameter_combinations'] == len(rows)
    assert all(f['execution_contract_count'] == 1 for f in result['families'])
    cases = result['validation_plan']['execution_cases']
    assert len({c['id'] for c in cases}) == len(rows)
    assert all(c['lane'] == 'PARAMETER_OPTIMIZATION' for c in cases)


def test_frozen_runtime_links_candidate_without_duplicate_optimization(tmp_path):
    candidate = record('fundamental_strategy_validation:candidate', 'RESEARCH_SETTING', strategy_id='candidate',
        namespace='fundamental_strategy_validation', parameters={'factor_ids': "['G2', 'G3']"})
    runtime = record('frozen', 'FROZEN_RUNTIME', research_candidate='candidate')
    i, v, r = inputs(tmp_path, [candidate, runtime])
    result = reconcile(i, v, r, tmp_path)
    assert result['stats']['strategy_families'] == 1
    assert result['stats']['research_parameter_combinations'] == 1
    assert result['families'][0]['execution_contract_count'] == 2
    assert {c['lane'] for c in result['validation_plan']['execution_cases']} == {'IMPLEMENTATION_BACKTEST', 'PARAMETER_OPTIMIZATION'}


def test_tools_and_research_model_reclassification_preserve_legacy_records(tmp_path):
    rows = [record('index', 'BENCHMARK'), record('signal', 'SIGNAL_ONLY'), record('pandas', 'ANALYSIS_ONLY'), record('plot', 'ANALYSIS_ONLY')]
    i, v, r = inputs(tmp_path, rows, {'pandas': decision('arbitrage', 'RESEARCH_MODEL'), 'plot': decision('not_a_strategy', 'ANALYSIS_ONLY')})
    result = reconcile(i, v, r, tmp_path)
    assert result['stats']['non_strategy_tools'] == 3
    assert result['stats']['current_analysis_only_tools'] == 1
    assert result['stats']['historical_validation_identities'] == 0
    assert result['stats']['historical_source_records'] == len(rows)
    model = next(n for n in result['records'] if n['id'] == 'pandas')
    assert model['historical_kind'] == 'ANALYSIS_ONLY' and not model['historical_counts_as_strategy']
    assert model['new_validation_status'] == 'NOT_TESTED'


def test_unreviewed_identity_or_changed_source_fails_closed(tmp_path):
    rows = [record('unreviewed', 'STRATEGY')]
    i, v, r = inputs(tmp_path, rows)
    with pytest.raises(ValueError, match='UNREVIEWED_NOTEBOOK_IDENTITY'):
        reconcile(i, v, r, tmp_path)
    r['reviewed_source_sha256']['pool.json'] = 'outdated'
    with pytest.raises(ValueError, match='SOURCE_REVIEW_STALE'):
        reconcile(i, v, r, tmp_path)
