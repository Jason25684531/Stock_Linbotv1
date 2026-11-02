"""Produce a conservative status snapshot from an existing qualification matrix."""
import argparse
import csv
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from core.research.strategy_status import unavailable_metrics


def write_snapshot(matrix_path, output):
    with Path(matrix_path).open(encoding='utf-8-sig', newline='') as stream:
        rows = list(csv.DictReader(stream))
    if len(rows) != 22 or len({r['strategy'] for r in rows}) != 22:
        raise ValueError('expected 22 distinct strategies')
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    paths = [output / name for name in ('research_status.json', 'qualified_strategies.json')]
    if any(path.exists() for path in paths):
        raise FileExistsError('snapshot already exists')
    strategies = []
    for row in rows:
        # The matrix is the single source: its RESEARCH_STATUS already carries the loaded evidence.
        if not row.get('RESEARCH_STATUS') or not row.get('RESEARCH_REASON'):
            raise ValueError('matrix lacks RESEARCH_STATUS/RESEARCH_REASON; regenerate it with goal_strategy_verdicts.py matrix')
        status, reason = row['RESEARCH_STATUS'], row['RESEARCH_REASON']
        strategies.append({'strategy': row['strategy'], 'status': status, 'reason': reason,
                           'metrics': unavailable_metrics('no independently verified research run')})
    qualified = [row['strategy'] for row in strategies if row['status'] in ('OOS_PASSED', 'PAPER_ELIGIBLE')]
    for path, data in zip(paths, ({'strategies': strategies},
                                  {'status': 'QUALIFIED' if qualified else 'NO_QUALIFIED_STRATEGY', 'strategies': qualified})):
        with path.open('x', encoding='utf-8') as stream:
            json.dump(data, stream, ensure_ascii=False, indent=2)
    return strategies


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('matrix')
    parser.add_argument('output')
    args = parser.parse_args()
    print(f"{len(write_snapshot(args.matrix, args.output))} strategies")
