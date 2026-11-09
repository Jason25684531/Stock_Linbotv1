"""Reproduce the 1225 odd-lot-only bar without running a research trial."""
import math

import pandas as pd
import pytest

from scripts import legacy_baseline_run as legacy
from test_valuation_policy import bars, engine, outcome, DAYS


def test_known_1225_missing_close_uses_prior_close(monkeypatch):
    path = legacy.input_paths()['bars']
    if not path.is_file():
        pytest.skip('local official bars unavailable')
    data = pd.read_parquet(path, filters=[('stock_id', '==', '1225'),
                                           ('trade_date', '>=', pd.Timestamp('2023-05-09')),
                                           ('trade_date', '<=', pd.Timestamp('2023-05-12'))]).set_index('trade_date')
    before, missing, after = (data.loc[pd.Timestamp(d)] for d in ('2023-05-10', '2023-05-11', '2023-05-12'))
    assert math.isnan(missing['close']) and missing['volume'] == 614 and before['close'] == 60.7
    rows = bars(d1={'open_price': 59.6, 'close_price': 60.0, 'volume': 19283.0},
                d2={'open_price': before['open'], 'close_price': before['close'], 'volume': before['volume']},
                d3={'open_price': missing['open'], 'close_price': missing['close'], 'volume': missing['volume']},
                d4={'open_price': after['open'], 'close_price': after['close'], 'volume': after['volume']})
    e = engine(rows, monkeypatch, 'finite_positive_stale')
    result = outcome(e)
    assert result['error'] is None and all(math.isfinite(float(x)) for x in result['daily_assets'])
    event = next(e for e in e.valuation_events if e['date'] == DAYS[3])
    assert event['price_source_date'] == DAYS[2] and event['price'] == before['close']
