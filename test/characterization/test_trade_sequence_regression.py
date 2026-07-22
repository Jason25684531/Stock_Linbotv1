import json
import math
import os
from pathlib import Path
import re

from jobs.run_backtest import BacktestEngine, _seed_backtest_rng


ATOL = 1e-12
RTOL = 1e-9
FIXTURE = Path(__file__).parents[1] / 'fixtures' / 'baseline' / 'v31_2026-04-01_2026-04-10.json'


def _metric(output: str, pattern: str) -> float:
    match = re.search(pattern, output)
    assert match, output
    return float(match.group(1).replace(',', ''))


def test_seeded_v31_trade_sequence_matches_the_characterization_baseline(monkeypatch, capsys):
    baseline = json.loads(FIXTURE.read_text(encoding='utf-8'))
    monkeypatch.setenv('BACKTEST_RANDOM_SEED', baseline['environment']['BACKTEST_RANDOM_SEED'])
    _seed_backtest_rng()
    engine = BacktestEngine(
        mode='v31_hybrid',
        start_date='2026-04-01',
        end_date='2026-04-10',
        initial_capital=1000000,
        persist_results=False,
    )
    engine.run(return_metrics=True)
    stdout = capsys.readouterr().out
    events = [
        line.strip() for line in stdout.splitlines()
        if '買入 ' in line or '停損 ' in line or '停利 ' in line or '時間到 ' in line or '趨勢轉空 ' in line
    ]
    # 日期、股票代碼、方向、順序與數量都包含在逐字相等的事件契約中。
    assert events == baseline['order_events']

    metrics = baseline['metrics']
    actual = {
        'final_value': _metric(stdout, r'最終資產: \$([\d,]+)'),
        'roi_percent': _metric(stdout, r'報酬率: ([+-]?[\d.]+)%'),
        'trade_count': _metric(stdout, r'交易次數: ([\d.]+)'),
        'max_drawdown_percent': _metric(stdout, r'最大回撤 \(MDD\): ([\d.]+)%'),
        'sharpe_ratio': _metric(stdout, r'夏普比率 \(Sharpe\): ([+-]?[\d.]+)'),
    }
    for name, expected in metrics.items():
        assert math.isclose(actual[name], expected, rel_tol=RTOL, abs_tol=ATOL), name
