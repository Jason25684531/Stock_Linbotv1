"""Dual-CLI harness: containment verdict, guard-equivalent policy checks and canary flow with a fake CLI runner (no real Codex)."""
import json

from scripts import dual_cli_harness as h

BLOCKED = {'outside_write': False, 'tcp_3306': False, 'tcp_8080': False, 'https_twse': False, 'env_read': False}


def test_containment_needs_every_probe_blocked():
    assert h.containment_verdict(BLOCKED) == 'CONTAINMENT_VERIFIED'
    assert h.containment_verdict({**BLOCKED, 'tcp_3306': True}) == 'CONTAINMENT_UNVERIFIED'
    assert h.containment_verdict({k: v for k, v in BLOCKED.items() if k != 'env_read'}) == 'CONTAINMENT_UNVERIFIED'


def test_policy_uses_guard_rules_and_allowed_paths():
    found = h.policy_violations(['python -m pytest -q', 'git commit -m x', 'pip install requests'],
                                ['core/a.py', 'config/settings.py', 'jobs/b.py'], allowed=('core/',))
    text = ' | '.join(found)
    assert 'git commit' in text and 'pip install' in text
    assert 'config/settings.py' in text and 'jobs/b.py' in text
    assert 'core/a.py' not in text and 'pytest' not in text


def _fake_runner(escape):
    def run(argv, prompt, workspace, outside):
        if escape:
            outside.write_text('escaped', encoding='utf-8')
        (workspace / 'probe.json').write_text(json.dumps({k: v for k, v in BLOCKED.items() if k != 'outside_write'}), encoding='utf-8')
        return 0
    return run


def test_canary_detects_an_outside_write_from_outside(tmp_path):
    assert h.canary(tmp_path / 'a', run=_fake_runner(False))['verdict'] == 'CONTAINMENT_VERIFIED'
    result = h.canary(tmp_path / 'b', run=_fake_runner(True))
    assert result['verdict'] == 'CONTAINMENT_UNVERIFIED' and result['probes']['outside_write'] is True


def test_codex_command_lines_pin_model_and_sandbox(tmp_path):
    write = h.codex_argv(tmp_path, 'workspace-write', tmp_path / 'last.md')
    read = h.codex_argv(tmp_path, 'read-only', tmp_path / 'last.md')
    assert write[:2] == ['codex', 'exec'] and 'gpt-6.1-terra' in write and 'model_reasoning_effort=medium' in write
    assert 'sandbox_workspace_write.network_access=false' in write and write[write.index('-s') + 1] == 'workspace-write'
    assert read[read.index('-s') + 1] == 'read-only' and write[-1] == '-'
