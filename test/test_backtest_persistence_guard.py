"""驗證 conftest.py 的 save_backtest_results() autouse 防護。"""

import pytest

import core.db_helper
import core.backtest.runner


def test_unmarked_call_is_blocked():
    with pytest.raises(AssertionError, match='allow_real_backtest_persistence'):
        core.db_helper.save_backtest_results(trades_df=None, equity_df=None)


def test_unmarked_call_via_runner_reference_is_blocked():
    with pytest.raises(AssertionError, match='allow_real_backtest_persistence'):
        core.backtest.runner.save_backtest_results(trades_df=None, equity_df=None)


@pytest.mark.allow_real_backtest_persistence
def test_marked_test_bypasses_guard(monkeypatch):
    monkeypatch.setattr('core.db_helper.save_backtest_results', lambda **kwargs: True)
    from core.db_helper import save_backtest_results as patched

    assert patched(trades_df=None, equity_df=None) is True
