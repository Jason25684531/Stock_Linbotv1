import numpy as np
import pandas as pd
import pytest

from core.research.intelligence.performance import _yearly
from core.research.platform.analysis import monthly_table


def nav():
    days = pd.bdate_range('2020-03-02', '2021-06-30')
    values = 1_000_000 * np.cumprod(1 + np.sin(np.arange(len(days))) / 200)
    return pd.DataFrame({'date': days, 'nav_base': values, 'nav_stress': values * 0.99})


def test_months_compound_to_the_yearly_return():
    frame = nav()
    table = monthly_table(frame)
    yearly = _yearly(frame)
    for year, row in table.items():
        product = np.prod([1 + m['value'] for m in row['months'].values() if m['value'] is not None]) - 1
        assert product == pytest.approx(yearly[year]['base'], rel=1e-9, abs=1e-12)
        assert row['year'] == pytest.approx(yearly[year]['base'], rel=1e-9, abs=1e-12)


def test_month_without_sessions_is_blank_not_zero():
    table = monthly_table(nav())
    assert table['2020']['months']['1'] == {'value': None, 'reason': 'NO_SESSIONS'}
    assert table['2021']['months']['12']['value'] is None


def test_cash_month_has_zero_holding_sessions():
    frame = nav()
    ledger = pd.DataFrame({'date': frame.date, 'holdings': [0 if d.month == 4 and d.year == 2020 else 1 for d in frame.date]})
    table = monthly_table(frame, ledger)
    assert table['2020']['months']['4']['holding_sessions'] == 0
    assert table['2020']['months']['5']['holding_sessions'] > 0
