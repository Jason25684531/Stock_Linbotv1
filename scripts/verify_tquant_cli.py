"""Run every port through the unchanged compatibility CLI without persistence."""
import json
import os
from pathlib import Path
import runpy
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from core.strategy_manager import StrategyManager

os.environ['BACKTEST_RANDOM_SEED'] = '2026-10-19'
settings = StrategyManager.SETTINGS_FILE.read_bytes()
results = {}
for strategy_id, metadata in StrategyManager.STRATEGY_METADATA.items():
    if metadata.source != 'tquant':
        continue
    sys.argv = ['4_run_backtest.py', '--strategy', strategy_id,
                '--start-date', '2026-10-16', '--end-date', '2026-10-16', '--no-persist']
    try:
        runpy.run_path('4_run_backtest.py', run_name='__main__')
    except SystemExit as exc:
        assert exc.code in (None, 0), (strategy_id, exc.code)
    results[strategy_id] = 'passed'
assert StrategyManager.SETTINGS_FILE.read_bytes() == settings
Path('outputs/tquant_change/cli.json').write_text(json.dumps(results, indent=2), encoding='utf-8')
print('ALL PORTED CLI CHECKS PASSED', len(results))
