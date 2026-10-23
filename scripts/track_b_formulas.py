"""Track B formula checks: independent reference implementations against the production factor code, windows, and the A191_132 approximation.

Synthetic panels test the formulas; official bars quantify how much the A191_132 approximation could matter.
"""
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.audit_strategy_history import write
from scripts.track_b_validation import OUT

MASTER = ROOT / 'outputs/factor_inventory/candidate_inventory_20260910_v1/candidate_factor_master.csv'
RTOL, ATOL = 1e-9, 1e-12


def panel(days=320, assets=15, seed=11):
    rng = np.random.default_rng(seed)
    index, columns = pd.bdate_range('2022-01-03', periods=days), [str(1100 + i) for i in range(assets)]
    close = pd.DataFrame(20 * np.exp(np.cumsum(rng.normal(0, 0.015, (days, assets)), axis=0)), index=index, columns=columns)
    open_ = close * (1 + rng.normal(0, 0.004, close.shape))
    high, low = np.maximum(open_, close) * 1.004, np.minimum(open_, close) * 0.996
    volume = pd.DataFrame(rng.integers(1_000, 90_000, close.shape).astype(float), index=index, columns=columns)
    return {'open': open_, 'high': high, 'low': low, 'close': close, 'volume': volume, 'amount': volume * (high + low + close) / 3, 'adjusted_close': close,
            'raw_close': close, 'adjusted_open': open_, 'adjusted_high': high, 'adjusted_low': low}


def same(a, b):
    a, b = a.to_numpy(dtype=float), b.to_numpy(dtype=float)
    mask = np.isfinite(a) & np.isfinite(b)
    return bool((np.isnan(a) == np.isnan(b)).all() and np.allclose(a[mask], b[mask], rtol=RTOL, atol=ATOL)), int(mask.sum())


def raw_a101(f):
    """-corr(rank(open), rank(volume), 10) with cross-sectional percentile ranks and a time-series correlation per asset."""
    return -f['open'].rank(axis=1, pct=True).rolling(10).corr(f['volume'].rank(axis=1, pct=True))


def day3_checks():
    from core.research.day2_candidates import candidate_specs, compute_candidate

    f = panel()
    specs = {s.candidate_id: s for s in candidate_specs(MASTER)}
    references = {
        'A101_003': lambda: raw_a101(f).replace([np.inf, -np.inf], np.nan),  # production treats a non-finite correlation as missing
        'A191_088': lambda: (f['close'] - f['close'].shift(20)) / f['close'].shift(20) * 100,
        'A191_132': lambda: f['amount'].rolling(20).mean(),
        'RS_004_NEAR_HIGH_120D': lambda: f['close'] / f['close'].rolling(120).max() - 1}
    out = {}
    for candidate_id, reference in references.items():
        equal, compared = same(compute_candidate(specs[candidate_id], f), reference())
        out[candidate_id] = {'status': 'PASS' if equal and compared > 0 else 'FAIL', 'values_compared': compared, 'tolerance': f'rtol={RTOL}, atol={ATOL}'}
    out['A101_003']['non_finite_correlations_mapped_to_missing'] = int(np.isinf(raw_a101(f).to_numpy()).sum())
    return out


def d5_checks():
    from core.research.factors import definitions as d

    f = panel(days=330)
    close, amount, volume = f['adjusted_close'], f['amount'], f['volume']
    returns = close.pct_change().abs()
    references = {
        'momentum_20d': (d._momentum_20d, close / close.shift(20) - 1, 21), 'momentum_60d': (d._momentum_60d, close / close.shift(60) - 1, 61),
        'momentum_12_1': (d._momentum_12_1, close.shift(21) / close.shift(252) - 1, 253), 'near_high_252d': (d._near_high_252d, close / close.rolling(252).max() - 1, 252),
        'amihud_20d': (d._amihud_20d, (returns / amount).where(volume > 0).rolling(20).mean(), 21)}
    out = {}
    for name, (function, reference, history) in references.items():
        got = function(f)
        equal, compared = same(got, reference)
        first = int(got.notna().all(axis=1).to_numpy().argmax()) + 1
        out[name] = {'status': 'PASS' if equal and compared > 0 and first == history else 'FAIL', 'values_compared': compared, 'first_valid_session': first, 'required_history_sessions': history}
    return out


def g2g3_checks():
    from scripts.validate_native22 import fundamental_checks

    result = fundamental_checks()
    return {k: v for k, v in result.items() if not isinstance(v, (pd.DataFrame, pd.Series))}


def a191_132_sensitivity(step=20):
    """Official 20-session mean amount against a VWAP-estimated amount (typical price x volume): how different would the factor be?"""
    bars = pd.read_parquet(OUT / 'official_bars.parquet')
    bars = bars[(bars.trade_date >= '2022-01-03') & (bars.trade_date <= '2025-12-31')]
    amount = bars.pivot(index='trade_date', columns='stock_id', values='amount').sort_index()
    typical = (bars.assign(t=(bars.high + bars.low + bars.close) / 3 * bars.volume)).pivot(index='trade_date', columns='stock_id', values='t').sort_index()
    official, proxy = amount.rolling(20).mean(), typical.rolling(20).mean()
    rows = []
    for day in official.index[40::step]:
        a, b = official.loc[day].dropna(), proxy.loc[day].dropna()
        both = a.index.intersection(b.index)
        if len(both) < 200:
            continue
        top = lambda s: set(s.loc[both].nlargest(10).index)
        rows.append({'spearman': float(a.loc[both].rank().corr(b.loc[both].rank())), 'top10_overlap': len(top(a) & top(b)) / 10, 'top20pct_overlap': len(set(a.loc[both].nlargest(len(both) // 5).index) & set(b.loc[both].nlargest(len(both) // 5).index)) / (len(both) // 5)})
    frame = pd.DataFrame(rows)
    return {'dates_sampled': len(frame), 'spearman_mean': float(frame.spearman.mean()), 'spearman_min': float(frame.spearman.min()), 'top10_overlap_mean': float(frame.top10_overlap.mean()),
            'top20pct_overlap_mean': float(frame.top20pct_overlap.mean()),
            'reading': 'A191_132 is the 20-session mean of official traded value, which equals MEAN(AMOUNT,20) when AMOUNT is the exchange traded value. It stays IMPLEMENTATION_APPROXIMATE '
                       'because the source may define AMOUNT as an estimated VWAP x volume; the figures show how much the ranking would move under that alternative.'}


def run():
    result = {'day3_reference_formulas': day3_checks(), 'd5_factors_and_windows': d5_checks(), 'g2_g3_pit_checks': g2g3_checks(), 'a191_132_approximation': a191_132_sensitivity(),
              'day3_strict_oos': 'not read: every Day3 rerun ends on 2025-12-31 (see rerun_day3.json and verification.json)'}
    write(OUT / 'track_b_formula_checks.json', json.loads(json.dumps(result, default=str)))
    print({k: ({n: v['status'] for n, v in val.items()} if k in ('day3_reference_formulas', 'd5_factors_and_windows') else '...') for k, val in result.items()})


if __name__ == '__main__':
    sys.stdout.reconfigure(encoding='utf-8')
    run()
