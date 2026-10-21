import pandas as pd

from core.research.vectorbt_adapter import run_vectorbt
from jobs.run_portfolio_research import build_close_matrix


def test_d5_rebalance_never_fills_missing_open_with_prior_price():
    dates = pd.to_datetime(['2026-10-16', '2026-10-19', '2026-10-20'])
    source = pd.DataFrame({'execution_date': dates, 'asset_id': ['2330'] * 3, 'entry_price': [100., float('nan'), 110.]})
    prices = build_close_matrix(source)
    targets = pd.DataFrame({'execution_date': dates[:2], 'asset_id': ['2330'] * 2, 'target_weight': [1., 0.]})
    result = run_vectorbt(prices, targets, fee_rate=0., tax_rate=0., sparse_rebalance=True)
    orders = result['orders']
    assert len(orders) == 1
    assert orders.iloc[0]['Timestamp'] == dates[0]
    assert orders.iloc[0]['Side'] == 'Buy'
