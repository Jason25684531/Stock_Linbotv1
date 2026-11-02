from core.research.research_mode import (D5_RESEARCH_ASSUMPTIONS_V2, D5_RESEARCH_STRATEGY, RESEARCH_ASSUMPTIONS_V1,
                                         RESEARCH_ASSUMPTIONS_V2, RESEARCH_ASSUMPTIONS_V3, assumptions_registration, assumptions_sha, screen_registration)
from scripts import research_workflow as workflow
import pytest


def test_assumptions_identity_and_screen_registration(tmp_path):
    changed = dict(RESEARCH_ASSUMPTIONS_V1, settlement='different')
    assert assumptions_sha(RESEARCH_ASSUMPTIONS_V1) != assumptions_sha(RESEARCH_ASSUMPTIONS_V2)
    assert assumptions_sha(RESEARCH_ASSUMPTIONS_V1) != assumptions_sha(changed)
    registry = tmp_path / 'registry.csv'
    workflow.register(screen_registration(), path=registry, today='2026-10-30')
    assert workflow.verify_registry(registry) == 'PASS'
    assert 'max(research trials, 743)' in registry.read_text(encoding='utf-8-sig')


def test_research_ledger_counts_configs_and_preserves_formal_count(tmp_path):
    registry, ledger, formal = (tmp_path / name for name in ('registry.csv', 'research.jsonl', 'formal.jsonl'))
    workflow.register(screen_registration(), path=registry)
    workflow.register(assumptions_registration('V3'), path=registry)
    source = tmp_path / 'input.json'
    source.write_text('{}', encoding='utf-8')
    for config in ({'id': 1}, {'id': 2}):
        workflow.admit_research_run('A', config, RESEARCH_ASSUMPTIONS_V3, [source], {'input': source},
                                    '2025-12-31', registry=registry, ledger=ledger)
    assert workflow.verify_trials(ledger) == 'PASS'
    assert workflow.research_trials(ledger) == 2
    assert workflow.effective_trials(formal) == 743
    replay = workflow.admit_research_run('A', {'id': 1}, RESEARCH_ASSUMPTIONS_V3, [source], {'input': source},
                                         '2025-12-31', registry=registry, ledger=ledger)
    assert replay['replay'] is True and workflow.research_trials(ledger) == 2
    with pytest.raises(ValueError, match='OOS'):
        workflow.admit_research_run('A', {}, RESEARCH_ASSUMPTIONS_V3, [source], {'input': source},
                                    '2026-01-01', registry=registry, ledger=ledger)


def test_d5_variant_requires_distinct_registered_assumptions(tmp_path):
    registry, ledger, source = tmp_path / 'registry.csv', tmp_path / 'ledger.jsonl', tmp_path / 'source.json'
    source.write_text('{}', encoding='utf-8')
    workflow.register(screen_registration(), path=registry)
    workflow.register(assumptions_registration('D5-V2'), path=registry)
    assert assumptions_sha(D5_RESEARCH_ASSUMPTIONS_V2) != assumptions_sha(RESEARCH_ASSUMPTIONS_V3)
    with pytest.raises(ValueError, match='BLOCKED_OOS_LEAKAGE'):
        workflow.admit_research_run('D5_PORTFOLIO_5_FACTORS', {}, RESEARCH_ASSUMPTIONS_V3, [source], {'input': source},
                                    '2025-12-31', registry=registry, ledger=ledger)
    with pytest.raises(ValueError, match='UNREGISTERED_RESEARCH_ASSUMPTIONS'):
        workflow.admit_research_run(D5_RESEARCH_STRATEGY, {}, RESEARCH_ASSUMPTIONS_V3, [source], {'input': source},
                                    '2025-12-31', registry=registry, ledger=ledger)
    row = workflow.admit_research_run(D5_RESEARCH_STRATEGY, {}, D5_RESEARCH_ASSUMPTIONS_V2, [source], {'input': source},
                                      '2025-12-31', registry=registry, ledger=ledger)
    assert row['strategy'] == D5_RESEARCH_STRATEGY and row['assumptions_sha256'] == assumptions_sha(D5_RESEARCH_ASSUMPTIONS_V2)
