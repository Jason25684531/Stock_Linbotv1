import numpy as np
import pandas as pd
import pytest

from scripts.research_h2_a191_liquidity import factor_panels, stock_day_pnl
from scripts.research_mode_report import asset_pnl


def test_stock_day_pnl_reconciles_to_asset_pnl():
    day = lambda d, cash, pos, marks, claims=(): {'date': d, 'cash': cash, 'positions': pos, 'marks': marks,
                                                  'stock_receivable': [], 'cash_receivable_claims': list(claims)}
    track = {'result': {
        'ledger': [{'date': '2025-01-02', 'kind': 'FILL', 'stock_id': '2330', 'side': 'buy', 'shares': 10, 'notional': 1000.0, 'fee': 2, 'tax': 0},
                   {'date': '2025-01-06', 'kind': 'FILL', 'stock_id': '2330', 'side': 'sell', 'shares': 10, 'notional': 1200.0, 'fee': 2, 'tax': 4},
                   {'date': '2025-01-06', 'kind': 'CASH_CREDIT', 'stock_id': '1101', 'amount': 30.0}],
        'daily': [day('2025-01-02', 0, {'2330': 10}, {'2330': 101.0}),
                  day('2025-01-03', 0, {'2330': 10}, {'2330': 110.0}, [{'stock_id': '1101', 'amount': 30.0}]),
                  day('2025-01-06', 0, {}, {})]}}
    frame = stock_day_pnl(track)
    assert frame.groupby('stock')['pnl'].sum().to_dict() == pytest.approx(asset_pnl(track))
    assert frame.loc[frame['stock'] == '2330', 'pnl'].tolist() == pytest.approx([1010 - 1002, 90, 1194 - 1100])


def test_residual_is_orthogonal_to_amihud_each_session(monkeypatch):
    rng = np.random.default_rng(0)
    index, columns = pd.date_range('2025-01-02', periods=5), [f'{i:04d}' for i in range(40)]
    amihud = pd.DataFrame(rng.random((5, 40)), index=index, columns=columns)
    a191 = -amihud + .3 * pd.DataFrame(rng.random((5, 40)), index=index, columns=columns)
    import core.research.strategy_search as search
    import core.research.d5_research_variant as d5
    monkeypatch.setattr(search, 'compute_factor_panels', lambda frames, master: {'A191_132': a191})
    monkeypatch.setattr(d5, 'raw_factor_panels', lambda frames: {'amihud_20d': amihud})
    membership = pd.DataFrame(True, index=index, columns=columns)
    panels, _ = factor_panels({}, membership)
    x = amihud.rank(axis=1, pct=True)
    for date in index:
        assert np.corrcoef(panels['A191_132_resid_amihud'].loc[date], x.loc[date])[0, 1] == pytest.approx(0, abs=1e-9)
