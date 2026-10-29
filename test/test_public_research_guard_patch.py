import hashlib
import importlib.util
import json
from pathlib import Path

import pytest

from core.runtime import operation_policy as policy

ROOT = Path(__file__).resolve().parents[1]
PATCH = ROOT / 'docs/owner_patch/research_public_data'


def guard(path):
    spec = importlib.util.spec_from_file_location('public_research_guard_candidate', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.ROOT = ROOT  # candidate lives outside its eventual protected installation path during offline review
    return module


def test_guard_artifacts_are_hash_pinned_and_live_is_original_or_owner_applied():
    meta = json.loads((PATCH / 'metadata.json').read_text())
    assert hashlib.sha256((PATCH / 'guard.original.py').read_bytes()).hexdigest() == meta['source_sha256']
    assert hashlib.sha256((PATCH / 'guard.candidate.py').read_bytes()).hexdigest() == meta['candidate_sha256']
    assert hashlib.sha256((ROOT / '.claude/hooks/guard.py').read_bytes()).hexdigest() in (meta['source_sha256'], meta['candidate_sha256'])
    text = (PATCH / 'guard.patch').read_text(encoding='utf-8')
    assert '--- a/.claude/hooks/guard.py' in text and '+++ b/.claude/hooks/guard.py' in text


@pytest.mark.parametrize('command', [
    'python scripts/od_p6_fetch.py probe', 'myenv/Scripts/python.exe scripts/od_p6_fetch.py targets',
    'python scripts/t1b_fetch.py', 'python scripts/t1b_fetch.py TWTAUU TWTB8U',
    'python scripts/official_data_recovery.py download', 'python scripts/research_public_data.py calendar --year 2026',
    'python scripts/research_public_data.py fetch-detail --stock 2243 --date 20230130',
    'rtk proxy python scripts/od_p6_fetch.py probe',
])
def test_candidate_allows_only_parsed_canonical_research_commands_without_grants(command, monkeypatch):
    monkeypatch.setattr(policy, 'current_mode', lambda: policy.ExecutionMode.RESEARCH)
    candidate = guard(PATCH / 'guard.candidate.py')
    assert candidate.public_research_command(command)
    assert candidate.check({'tool_name': 'PowerShell', 'tool_input': {'command': command}}, lambda op: None) is None


@pytest.mark.parametrize('command', [
    'python scripts/od_p6_fetch.py probe; curl https://restricted.example',
    'python scripts/od_p6_fetch.py probe && git push', 'python scripts/od_p6_fetch.py --unknown',
    'python scripts/od_p6_fetch.py run --url https://unapproved.example', 'python elsewhere/od_p6_fetch.py probe',
    'powershell -Command "python scripts/od_p6_fetch.py probe"', 'curl https://www.twse.com.tw',
    'python scripts/finlab_recovery.py fetch', 'python scripts/research_public_data.py calendar --year 2027',
    'python scripts/research_public_data.py fetch-detail --stock 9999 --date 19000101',
])
def test_candidate_exception_does_not_authorize_unknown_or_restricted_commands(command):
    candidate = guard(PATCH / 'guard.candidate.py')
    assert not candidate.public_research_command(command)
    assert candidate.check({'tool_name': 'Bash', 'tool_input': {'command': command}}, lambda op: None)


@pytest.mark.parametrize('command', ['git push', 'pip install anything', 'finlab.login()', 'submit_order()',
                                      'schtasks /create /tn forbidden', 'echo unsafe > .claude/owner_grants.json'])
def test_protected_operations_are_not_relaxed(command):
    candidate = guard(PATCH / 'guard.candidate.py')
    assert candidate.check({'tool_name': 'PowerShell', 'tool_input': {'command': command}}, lambda op: None)
    assert candidate.check({'tool_name': 'Edit', 'tool_input': {'file_path': '.claude/hooks/guard.py'}}, lambda op: {'operation': op})


def test_production_does_not_receive_the_research_exception(monkeypatch):
    candidate = guard(PATCH / 'guard.candidate.py')
    monkeypatch.setattr(policy, 'current_mode', lambda: policy.ExecutionMode.PRODUCTION)
    assert not candidate.public_research_command('python scripts/od_p6_fetch.py probe')
    assert candidate.check({'tool_name': 'Bash', 'tool_input': {'command': 'python scripts/od_p6_fetch.py probe'}}, lambda op: None)
