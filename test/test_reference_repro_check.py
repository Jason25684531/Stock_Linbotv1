"""Batch-to-batch reproducibility check: identical runs pass, a changed share count or a NAV off by more than rtol 1e-9 fails."""
import json

import pandas as pd

from scripts import reference_repro_check as rc


def _run(root, run_id, shares=100, nav=1.0):
    folder = root / 'runs' / run_id
    folder.mkdir(parents=True)
    pd.DataFrame({'stock_id': ['2330'], 'buy_date': ['2023-01-04'], 'buy_price': [500.0], 'sell_date': ['2023-01-30'],
                  'sell_price': [510.0], 'shares': [shares], 'cost': [10.0], 'profit_pct': [2.0], 'reason': ['x']}).to_csv(folder / 'trades.csv', index=False)
    pd.DataFrame({'date': ['2023-01-03', '2023-01-04'], 'nav_base': [1e6, 1e6 * nav], 'nav_stress': [1e6, 1e6]}).to_csv(folder / 'nav.csv', index=False)


def _batch(root, batch, run_id):
    (root / 'batches' / batch).mkdir(parents=True)
    (root / 'batches' / batch / 'report.json').write_text(json.dumps({'rows': [{'strategy_id': 's', 'state': 'RUN', 'run_id': run_id,
                                                                                  'run_identity': 'RID1:x'}]}), encoding='utf-8')


def test_same_runs_pass_and_any_real_difference_fails(tmp_path, monkeypatch):
    monkeypatch.setattr(rc, 'OUT', tmp_path)
    _run(tmp_path, 'a'), _run(tmp_path, 'b'), _run(tmp_path, 'c', shares=101), _run(tmp_path, 'd', nav=1.000001)
    assert rc.compare_runs('a', 'b')['pass']
    assert not rc.compare_runs('a', 'c')['trades_exact']
    assert not rc.compare_runs('a', 'd')['nav_close']
    _batch(tmp_path, 'B1', 'a'), _batch(tmp_path, 'B2', 'd')
    assert rc.compare('B1', 'B2')['reproducible'] == 0
