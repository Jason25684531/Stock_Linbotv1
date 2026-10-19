"""Capture/compare real CLI trades and dry-run duration without persistence."""
import argparse
import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import numpy as np
from core.backtest import runner
from core.strategy_manager import StrategyManager
from jobs.run_daily import run_daily_for_date


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('stage', choices=['before', 'after'])
    args = parser.parse_args()
    directory = Path('outputs/tquant_change')
    directory.mkdir(parents=True, exist_ok=True)
    os.environ['BACKTEST_RANDOM_SEED'] = '2026-10-19'
    settings = StrategyManager.SETTINGS_FILE.read_bytes()
    records = {}
    original = runner.BacktestEngine

    class RecordingEngine(original):
        def buy(self, stock_id, price, date_str):
            existed = stock_id in self.positions
            super().buy(stock_id, price, date_str)
            if not existed and stock_id in self.positions:
                p = self.positions[stock_id]
                records[self.mode].append([date_str, stock_id, 'BUY', p['shares'], p['cost']])

        def sell(self, stock_id, price, date_str, reason):
            shares = self.positions[stock_id]['shares'] if stock_id in self.positions else None
            super().sell(stock_id, price, date_str, reason)
            if shares is not None and stock_id not in self.positions:
                records[self.mode].append([date_str, stock_id, 'SELL', shares, self.trades[-1]['sell_price']])

    runner.BacktestEngine = RecordingEngine
    try:
        for key, meta in StrategyManager.STRATEGY_METADATA.items():
            if meta.legacy_ids:
                records[key] = []
                assert runner.main(['--strategy', key, '--start-date', '2026-09-01',
                                    '--end-date', '2026-10-16', '--no-persist']) == 0
    finally:
        runner.BacktestEngine = original
    start = time.perf_counter()
    daily = run_daily_for_date('2026-10-16', dry_run=True)
    elapsed = time.perf_counter() - start
    assert daily is not None
    assert daily['strategy_errors'] == {}, daily['strategy_errors']
    assert StrategyManager.SETTINGS_FILE.read_bytes() == settings
    result = {'trades': records, 'daily_seconds': elapsed, 'daily_result': daily}
    (directory / f'{args.stage}.json').write_text(
        json.dumps(result, ensure_ascii=False, default=str, indent=2), encoding='utf-8')
    if args.stage == 'after':
        before = json.loads((directory / 'before.json').read_text(encoding='utf-8'))
        assert before['trades'].keys() == records.keys()
        for key, rows in records.items():
            expected = before['trades'][key]
            assert len(rows) == len(expected), key
            for row, old in zip(rows, expected):
                assert row[:4] == old[:4], (key, row, old)
                np.testing.assert_allclose(row[4], old[4], rtol=1e-9, atol=1e-12)
        print('TRADE PARITY PASSED', {k: len(v) for k, v in records.items()})
    print('DAILY_SECONDS', elapsed)


if __name__ == '__main__':
    main()
