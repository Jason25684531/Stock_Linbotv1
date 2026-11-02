import pytest

from core.research import repair_runner
from scripts import research_a191_synthetic, research_verify_synthetic, research_workflow


def test_twelve_synthetic_configs_use_isolated_ledger(tmp_path, monkeypatch):
    monkeypatch.setattr(repair_runner, 'OUTPUT_ROOT', tmp_path)
    actual = tmp_path / 'actual.jsonl'
    monkeypatch.setattr(research_workflow, 'TRIAL_LEDGER', actual)
    result = research_a191_synthetic.run(tmp_path / 'fixture')
    assert len(result['configurations']) == 12
    assert result['synthetic_ledger_status'] == 'PASS' and result['synthetic_effective_trials'] == 755
    assert result['actual_ledger_unchanged_observed'] and not actual.exists()
    assert all(track['status'] == 'PASS' for row in result['configurations'] for track in row['reconciliation'].values())
    audit = research_verify_synthetic.verify(tmp_path / 'fixture')
    assert audit['status'] == 'PASS' and len(audit['tracks']) == 24
    bars = tmp_path / 'fixture' / 'bars.json'
    bars.write_bytes(bars.read_bytes() + b' ')
    with pytest.raises(ValueError, match='INPUT_HASH_MISMATCH'):
        research_verify_synthetic.verify(tmp_path / 'fixture')
