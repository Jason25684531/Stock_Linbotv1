"""Characterize formal trial counting before the separate research ledger exists."""
from scripts import research_workflow


def test_formal_trial_count_ignores_neighboring_research_ledger(tmp_path):
    formal = tmp_path / 'formal.jsonl'
    research = tmp_path / 'research_mode_ledger.jsonl'
    assert research_workflow.effective_trials(formal) == 743
    research.write_text('{"kind":"RESEARCH_DIAGNOSTIC"}\n', encoding='utf-8')
    assert research_workflow.effective_trials(formal) == 743
