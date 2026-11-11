import numpy as np
import pandas as pd
import pytest

from core.research.history import regime


def bench(n=400, seed=1):
    rng = np.random.default_rng(seed)
    dates = pd.bdate_range('2018-01-01', periods=n)
    price = 10000 * np.exp(np.cumsum(rng.normal(0.0003, 0.01, n)))
    return pd.DataFrame({'date': dates, 'taiex_price': price, 'taiex_total_return': price * np.linspace(1, 1.05, n)})


def test_spec_id_is_pinned():
    assert regime.REGIME_SPEC_ID == 'RS1-' + regime.canonical_sha(regime.REGIME_SPEC)[:12]
    assert regime.REGIME_SPEC_ID == 'RS1-553b528c068e', 'regime spec changed: bump version and re-register before any run'


def test_labels_use_only_past_closes():
    b = bench()
    base = regime.labels(b)
    t = 250
    future = b.copy()
    future.loc[t + 1:, 'taiex_price'] *= 3
    assert regime.labels(future).iloc[: t + 1].equals(base.iloc[: t + 1])


def test_missing_benchmark_value_is_counted_not_hidden():
    b = bench(400)
    b.loc[250, 'taiex_price'] = np.nan
    nav = pd.DataFrame({'date': b.date, 'nav_base': np.linspace(1e6, 1.2e6, 400), 'nav_stress': np.linspace(1e6, 1.1e6, 400)})
    out = regime.analyze(nav, pd.DataFrame(columns=['buy_date', 'sell_date', 'profit_pct']), b, {2018: 'VERIFIED', 2019: 'VERIFIED'})
    assert out['unlabelled_sessions'] > 0


def test_short_history_has_no_label():
    first = regime.labels(bench()).iloc[0]
    assert first.trend is None and first.reason == 'INSUFFICIENT_HISTORY'


def test_bull_and_bear_thresholds():
    dates = pd.bdate_range('2020-01-01', periods=200)
    up = pd.DataFrame({'date': dates, 'taiex_price': np.linspace(100, 150, 200), 'taiex_total_return': np.linspace(100, 150, 200)})
    assert regime.labels(up).trend.iloc[-1] == 'BULL'
    down = up.assign(taiex_price=up.taiex_price[::-1].to_numpy())
    assert regime.labels(down).trend.iloc[-1] == 'BEAR'


def test_return_on_day_uses_previous_day_label():
    labels = pd.DataFrame({'date': pd.bdate_range('2020-01-01', periods=3), 'trend': ['BULL', 'BEAR', 'BULL'], 'vol': ['LOW_VOL'] * 3})
    nav = pd.DataFrame({'date': labels.date, 'nav': [100.0, 110.0, 99.0]})
    by = regime.daily_returns_with_labels(nav, 'nav', labels)
    assert by.set_index('date').loc[pd.Timestamp('2020-01-02'), 'trend'] == 'BULL'
    assert by.set_index('date').loc[pd.Timestamp('2020-01-03'), 'trend'] == 'BEAR'


def test_drawdown_counts_the_first_return():
    assert regime.metrics(pd.Series([-0.10]))['max_drawdown']['value'] == pytest.approx(-0.10)


def test_metrics_undefined_values_carry_reasons():
    m = regime.metrics(pd.Series([0.0] * 80))
    assert m['sharpe'] == {'value': None, 'reason': 'ZERO_VOLATILITY'}
    small = regime.metrics(pd.Series([0.01] * 10))
    assert small['status'] == 'INSUFFICIENT_SAMPLE' and small['cumulative_return']['value'] == pytest.approx(1.01 ** 10 - 1)


def test_analyze_excludes_untrusted_years_from_aggregates():
    b = bench(600)
    nav = pd.DataFrame({'date': b.date, 'nav_base': np.linspace(1e6, 1.5e6, 600), 'nav_stress': np.linspace(1e6, 1.4e6, 600)})
    trust = {2018: 'ACCESS_BLOCKED', 2019: 'VERIFIED', 2020: 'PARTIAL'}
    out = regime.analyze(nav, pd.DataFrame(columns=['buy_date', 'sell_date', 'profit_pct']), b, trust)
    assert out['years']['2018']['base']['cumulative_return'] == {'value': None, 'reason': 'DATA_TRUST_ACCESS_BLOCKED'}
    assert out['years']['2020']['trust'] == 'PARTIAL' and out['years']['2020']['base']['cumulative_return']['value'] is not None
    seg = out['segments']['VERIFIED_ONLY']['2019-2021']
    assert seg['years_used'] == [2019] and seg['excluded_years'] == {'2020': 'PARTIAL', '2021': 'NO_NAV'}
    assert out['labels'] == ['REFERENCE_ONLY', 'RESEARCH_ONLY', 'NOT_CERTIFIED', 'NOT_A_TRIAL', 'NOT_OOS_PREVIOUSLY_VIEWED_STRATEGIES']
