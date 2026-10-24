"""
共用測試 Fixtures
============================================
提供 StrategyManager、空 DataFrame 等跨測試共用 fixture。
避免各測試檔重複定義相同的 setup 邏輯。
"""

import sys
from pathlib import Path

import pytest
import pandas as pd
from core.strategy_manager import StrategyManager


@pytest.fixture(autouse=True)
def _isolate_strategy_settings(monkeypatch, tmp_path):
    """Keep default and collection-time managers away from operator settings."""
    source = StrategyManager.SETTINGS_FILE
    isolated = tmp_path / 'strategy_settings.json'
    isolated.write_bytes(source.read_bytes())
    monkeypatch.setattr(StrategyManager, 'SETTINGS_FILE', isolated)
    app_manager = getattr(sys.modules.get('app'), 'strategy_manager', None)
    for instance in (StrategyManager._instance, app_manager):
        if instance is not None and instance.settings_file == source:
            monkeypatch.setattr(instance, 'settings_file', isolated)
            monkeypatch.setattr(instance, '_settings', None)
            monkeypatch.setattr(instance, '_strategy_cache', {})
    monkeypatch.setattr(StrategyManager, '_instance', None)


@pytest.fixture
def manager():
    """每次測試前重設 StrategyManager Singleton，確保測試隔離"""
    StrategyManager._instance = None
    return StrategyManager()


@pytest.fixture
def empty_df():
    """空 DataFrame，供篩選邏輯邊界測試"""
    return pd.DataFrame()


@pytest.fixture(autouse=True)
def _forbid_real_backtest_persistence(monkeypatch, request):
    """預設攔截真實 save_backtest_results()，避免測試意外覆寫正式回測資料。

    2026-07-24: 一次互動式驗證意外觸發真實回測落庫，整表覆寫了
    backtest_trades/backtest_equity_curve。需要真正落庫語意的測試請標記
    @pytest.mark.allow_real_backtest_persistence 並自行指向隔離的測試目標。
    """
    if 'allow_real_backtest_persistence' in request.keywords:
        return

    def _blocked(*args, **kwargs):
        raise AssertionError(
            'save_backtest_results() 被呼叫，但測試未標記 '
            '@pytest.mark.allow_real_backtest_persistence'
        )

    monkeypatch.setattr('core.db_helper.save_backtest_results', _blocked)
    monkeypatch.setattr('core.backtest.runner.save_backtest_results', _blocked)


def _production_tree_state():
    from core.runtime import fundamental_production as production

    root = production.OPERATION_ROOT
    return {p.relative_to(root).as_posix(): (p.stat().st_size, p.stat().st_mtime_ns) for p in sorted(root.rglob('*')) if p.is_file()}


@pytest.fixture(autouse=True)
def _redirect_production_status_writes(monkeypatch, tmp_path):
    """Any write of fundamental_production_status.json aimed at the real operation folder goes to the test's tmp_path.

    2026-10-24: test_run_daily_persistence called run_daily_for_date('2026-04-28') without stubbing run_fundamental_production, which rewrote the
    production status file (asof 2026-04-28) on every full regression. The writer resolves its default root at call time, so one guard covers every test.
    """
    from core.runtime import fundamental_production as production

    real_write = production._write_status
    real_root = production.OPERATION_ROOT.resolve()

    def guarded(root, payload):
        redirected = tmp_path / 'redirected_production_status' if Path(root).resolve() == real_root else root
        return real_write(redirected, payload)

    monkeypatch.setattr(production, '_write_status', guarded)


@pytest.fixture(scope='session', autouse=True)
def _production_folder_unchanged_after_session():
    """Fail the session if any file under the real operation folder changed (size or mtime), whatever the cause."""
    before = _production_tree_state()
    yield
    after = _production_tree_state()
    changed = sorted(k for k in before.keys() | after.keys() if before.get(k) != after.get(k))
    assert not changed, f'PRODUCTION_FOLDER_CHANGED_BY_TESTS: {changed}'
