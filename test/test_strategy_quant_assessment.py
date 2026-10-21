"""Synthetic boundary checks; these are not real-data strategy parity evidence."""
import pandas as pd

from scripts.assess_strategy_quant import episodes, regime_labels
from scripts.replay_strategy_research import summarize, closed_trades
import pytest


def test_missing_order_ledger_is_not_zero_turnover_or_trade_count():
    values = pd.Series([100., 100., 100.], index=pd.date_range('2026-01-01', periods=3))
    result = summarize({'value': values})
    assert result['metrics']['cagr'] == {'value': 0.0, 'reason': None}
    assert result['metrics']['turnover']['value'] is None
    assert result['metrics']['trade_count']['value'] is None
    assert result['metrics']['turnover']['reason']


def test_observed_no_orders_preserves_valid_zeros():
    values = pd.Series([100., 100., 100.], index=pd.date_range('2026-01-01', periods=3))
    result = summarize({'value': values, 'trades': []})
    assert result['metrics']['turnover'] == {'value': 0.0, 'reason': None}
    assert result['metrics']['trade_count'] == {'value': 0, 'reason': None}
    assert result['metrics']['win_rate']['value'] is None


def test_regime_labels_do_not_use_future_market_prices():
    prices = pd.Series([100 + i / 10 for i in range(300)], index=pd.bdate_range('2025-01-01', periods=300))
    before = regime_labels(prices)
    prices.iloc[-1] = 1
    after = regime_labels(prices)
    pd.testing.assert_series_equal(before.iloc[:-1], after.iloc[:-1])


def test_disjoint_wealth_levels_are_not_concatenated():
    dates = pd.date_range('2026-01-01', periods=6)
    labels = pd.Series(['BULL', 'BULL', 'BEAR', 'BULL', 'BEAR', 'BULL'], index=dates)
    result = episodes([100, 110, 90, 100, 90, 120], [str(d) for d in dates], labels, minimum=1)
    assert len(result['BULL']['episodes']) == 3
    assert result['BULL']['return_observations'] == 3
    missing = episodes([100, 110, 90], [str(d) for d in dates[:3]], labels, minimum=20)
    assert missing['BULL']['calculation_status'] == 'BLOCKED'


def test_partial_fills_form_one_net_cash_cycle_without_changing_execution_order():
    fills = [{'asset': '2330', 'side': side, 'quantity': quantity, 'price': price, 'fee': fee, 'date': date}
             for side, quantity, price, fee, date in [('buy', 10, 10, 1, '2026-01-01'),
                 ('sell', 5, 12, .5, '2026-01-02'), ('buy', 5, 11, .5, '2026-01-03'), ('sell', 10, 11, 1, '2026-01-04')]]
    before = [r.copy() for r in fills]
    closed = closed_trades(fills)
    assert len(closed) == 1 and closed[0]['profit'] == 12
    assert closed[0]['buy_date'] == '2026-01-01' and closed[0]['sell_date'] == '2026-01-04'
    assert fills == before
    with pytest.raises(ValueError, match='orphan sell'):
        closed_trades([fills[1]])


def test_configured_stock_proxy_cannot_certify_market_regimes(tmp_path):
    import json
    from scripts.assess_strategy_quant import assess
    from scripts.validate_strategy_inventory import write_json
    dates = pd.date_range('2026-01-01', periods=3)
    pd.DataFrame({'trade_date': dates, 'close_price': [100., 101., 102.]}).to_parquet(tmp_path / 'snapshot_benchmark.parquet')
    write_json(tmp_path / 'benchmark_manifest.json', {'symbol': '2330'})
    captured = summarize({'value': pd.Series([100., 101., 102.], index=dates), 'trades': []})
    write_json(tmp_path / 'grid_diagnostics.json', {'one': {'BASE_COST': captured}})
    assess(tmp_path)
    result = json.loads((tmp_path / 'quant_assessments.json').read_text(encoding='utf-8'))['records']['grid:one']
    assert result['benchmark_symbol'] == '2330'
    assert all(r['status'] == 'BLOCKED' for r in result['regimes'].values())
    assert result['benchmark_proxy_diagnostic']['validation_status'] == 'BLOCKED'
