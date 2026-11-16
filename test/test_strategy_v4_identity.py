"""Rerun prerequisites must bind source, external inputs and preregistered conditions."""
import copy

import pytest
import pandas as pd
from types import SimpleNamespace

from scripts import strategy_v4 as research


def test_identity_binds_original_external_inputs(monkeypatch):
    monkeypatch.setattr(research, 'code_hashes', lambda: {'source.py': 'same'})
    monkeypatch.setattr(research.v2, 'identity', lambda p, s: 'original-settings-A')
    a = research.variant_identity({'strategy_id':'mad_distance'}, 'snapshot')
    monkeypatch.setattr(research.v2, 'identity', lambda p, s: 'original-settings-B')
    assert a != research.variant_identity({'strategy_id':'mad_distance'}, 'snapshot')


def test_stale_prerequisites_are_rejected():
    binding = {'snapshot_id':'s','parameters':{'capital':1},'original_identity':'a','code_hashes':{'a':'hash'}}
    assert research.require_binding({'binding':binding}, binding) is None
    for key in binding:
        changed = copy.deepcopy(binding); changed[key] = 'changed'
        with pytest.raises(ValueError,match='STALE_RESEARCH_PREREQUISITE'):
            research.require_binding({'binding':binding}, changed)
    with pytest.raises(ValueError,match='STALE_RESEARCH_PREREQUISITE'):
        research.require_binding({'status':'PASS'}, binding)


def test_feature_cache_is_read_only_and_mismatch_is_isolated(tmp_path, monkeypatch):
    from core.research.platform import feature_cache as cache
    monkeypatch.setattr(cache, 'ROOT', tmp_path/'v2')
    monkeypatch.setattr(research, 'OUT', tmp_path/'v4')
    path = cache._path(cache.ROOT, 'key', '2025-01-02'); path.parent.mkdir(parents=True)
    pd.DataFrame({'x':[1]}).to_pickle(path); before = path.read_bytes()
    data = SimpleNamespace(_cache={}, cross_section=lambda day:pd.DataFrame({'x':[2]}))
    assert research.readonly_attach(data, 'key')
    assert data.cross_section(pd.Timestamp('2025-01-02')).x.iloc[0] == 1
    assert data.cross_section(pd.Timestamp('2025-01-03')).x.iloc[0] == 2
    assert research.readonly_verify(data, 'key', [pd.Timestamp('2025-01-02')])
    assert path.read_bytes()==before and not (cache.ROOT/'key'/'DISABLED').exists()
    assert (research.OUT/'cache_verification/key/DISABLED').exists()
    assert not research.readonly_attach(SimpleNamespace(_cache={}, cross_section=lambda day:pd.DataFrame({'x':[2]})), 'key')


def test_invalid_success_run_is_preserved_and_recomputed(monkeypatch):
    from scripts import platform_v2_engine, history_engine
    params = {'strategy_id':'mad_distance','start':'2023-01-03','end':'2023-01-10','capital':1,'costs':{},'rule':'LOT_ENGINE_V1'}
    monkeypatch.setattr(research,'code_hashes',lambda:{})
    monkeypatch.setattr(research.v2,'identity',lambda *a:'original')
    binding = research.execution_binding(params,'s')
    monkeypatch.setattr(research.v2,'lookup',lambda *a:'invalid-saved')
    monkeypatch.setattr(research.run_store,'load_run',lambda *a,**kw:{'run':{'binding':binding,'publishable':False,'feature_cache':{'mismatches':['bad']}}})
    monkeypatch.setattr(research.run_store,'save_failure',lambda *a,**kw:None)
    monkeypatch.setattr(history_engine._prepared,'cache_clear',lambda:None)
    def reached(*args,**kwargs):
        raise RuntimeError('NEW_EXECUTION_REACHED')
    monkeypatch.setattr(platform_v2_engine,'execute',reached)
    with pytest.raises(RuntimeError,match='NEW_EXECUTION_REACHED'):
        research.run_variant(params,'s',binding)
