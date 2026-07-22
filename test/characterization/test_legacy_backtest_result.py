import json
from pathlib import Path


FIXTURE = Path(__file__).parents[1] / 'fixtures' / 'baseline' / 'v31_2026-04-01_2026-04-10.json'


def test_backtest_baseline_declares_the_legacy_result_contract():
    baseline = json.loads(FIXTURE.read_text(encoding='utf-8'))

    assert baseline['command'][:2] == ['--strategy', 'v31_hybrid']
    assert baseline['environment']['BACKTEST_RANDOM_SEED']
    assert baseline['order_events']
    assert set(baseline['metrics']) == {
        'final_value', 'roi_percent', 'trade_count', 'max_drawdown_percent', 'sharpe_ratio',
    }
