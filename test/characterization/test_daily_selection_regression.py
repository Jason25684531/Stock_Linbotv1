import pytest
from sqlalchemy import text

from core.db_helper import get_db_engine
from core.strategy_manager import StrategyManager
from jobs.run_daily import run_daily_for_date


@pytest.mark.integration
@pytest.mark.slow
def test_daily_selection_live_db_smoke(capsys):
    """Exercise the mutable local DB without treating it as a parity oracle."""
    try:
        with get_db_engine().connect() as conn:
            conn.execute(text('SELECT 1'))
    except Exception as exc:
        pytest.skip(f'baseline database unavailable: {exc}')

    summary = run_daily_for_date('2026-04-10', dry_run=True)
    output = capsys.readouterr().out

    assert summary['date'] == '2026-04-10'
    assert summary['dry_run'] is True
    assert summary['skipped_persistence'] is True
    assert summary['strategy_errors'] == {}
    assert set(summary['strategy_counts']) == {
        strategy.name for strategy in StrategyManager().get_persistence_strategies()
    }
    assert '[DRY-RUN] run_daily preview mode enabled' in output
