import pandas as pd
import pytest

from scripts.research_h4_attribution import drawdown


def test_drawdown_finds_peak_trough_and_recovery():
    nav = pd.Series([1.0, 1.2, 0.9, 0.6, 0.9, 1.25, 1.1], index=pd.date_range('2025-01-01', periods=7))
    info, dd = drawdown(nav)
    assert info['max_drawdown'] == pytest.approx(0.6 / 1.2 - 1)
    assert (info['peak_date'], info['trough_date'], info['recovery_date']) == ('2025-01-02', '2025-01-04', '2025-01-06')
    assert (info['peak_to_trough_sessions'], info['trough_to_recovery_sessions']) == (2, 2)
    assert drawdown(nav.iloc[:5])[0]['recovery_date'] is None
