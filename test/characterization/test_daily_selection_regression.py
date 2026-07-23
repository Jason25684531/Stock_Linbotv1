import json
from pathlib import Path

import pytest
from sqlalchemy import text

from core.db_helper import get_db_engine
from jobs.run_daily import run_daily_for_date


FIXTURE = Path(__file__).parents[1] / 'fixtures' / 'baseline' / 'daily_selection_2026-04-10.json'


@pytest.mark.integration
@pytest.mark.slow
def test_daily_selection_matches_the_fixed_date_baseline(capsys):
    try:
        with get_db_engine().connect() as conn:
            conn.execute(text('SELECT 1'))
    except Exception as exc:
        pytest.skip(f'baseline database unavailable: {exc}')

    baseline = json.loads(FIXTURE.read_text(encoding='utf-8'))
    summary = run_daily_for_date(baseline['date'], dry_run=True)
    output = capsys.readouterr().out

    assert summary['date'] == baseline['date']
    assert summary['dry_run'] is True
    assert summary['skipped_persistence'] is True
    assert summary['strategy_errors'] == {}
    assert summary['strategy_counts'] == baseline['strategy_counts']
    for stock_id in baseline['v38_top_five']:
        assert f'. {stock_id} (' in output
