"""確保執行環境的 pandas/SQLAlchemy 版本與 requirements.runtime.txt 一致。

2026-07-24: 一次互動式驗證誤用系統 Python（pandas 3.0.4）而非專案的 myenv
（pandas 2.2.3，與需求檔一致），導致多個測試誤判為既有失敗。此測試在
環境不一致時給出明確訊息，而非讓下游測試以難以理解的方式失敗。
"""

import re
from importlib.metadata import version
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
REQUIREMENTS_FILE = REPO_ROOT / 'requirements.runtime.txt'
PINNED_PACKAGES = ('pandas', 'SQLAlchemy')


def _pinned_versions() -> dict[str, str]:
    text = REQUIREMENTS_FILE.read_text(encoding='utf-8')
    pins = {}
    for name in PINNED_PACKAGES:
        match = re.search(rf'^{re.escape(name)}==([\w.]+)$', text, flags=re.IGNORECASE | re.MULTILINE)
        assert match, f'{name} 未在 {REQUIREMENTS_FILE.name} 中固定版本'
        pins[name] = match.group(1)
    return pins


@pytest.mark.parametrize('package_name', PINNED_PACKAGES)
def test_installed_version_matches_pinned_requirement(package_name):
    pinned = _pinned_versions()[package_name]
    installed = version(package_name)
    assert installed == pinned, (
        f'{package_name} 版本不符：requirements.runtime.txt 固定 {pinned}，'
        f'目前偵測到 {installed}。請改用專案的 myenv 執行'
        f'（myenv\\Scripts\\python.exe / myenv/bin/python），不要使用系統 Python。'
    )
