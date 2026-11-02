"""A distinct D5 research variant with per-decision historical fitting."""
import hashlib
import json

import numpy as np
import pandas as pd

from core.research.composite_factor import build_factor_weights
from core.research.factors.definitions import CANONICAL_FACTOR_IDS, FACTOR_REGISTRY, compute_factor


D5_POLICY_V1 = {
    'strategy': 'D5_RESEARCH_PIT_V1', 'factor_universe': sorted(CANONICAL_FACTOR_IDS),
    'forward_horizon': 60, 'training_window': 252, 'minimum_ic_days': 120,
    'minimum_cross_section': 30, 'minimum_abs_mean_ic': .02, 'candidate_count': 5,
    'event_policy': 'Exclude historical labels crossing any corporate action, reference change, delisting or unavailable trading session.',
    'fit_cutoff': 'Use only samples whose forward horizon has completed by the signal close.',
    'weights': 'Recompute equal, ic, icir or redundancy_adjusted from the same point-in-time history.',
}


def policy_sha():
    return hashlib.sha256(json.dumps(D5_POLICY_V1, sort_keys=True, separators=(',', ':')).encode('utf-8')).hexdigest()


def registration():
    return {'hypothesis_id': 'RESEARCH-D5-POLICY:V1', 'strategy': D5_POLICY_V1['strategy'],
            'hypothesis': json.dumps(D5_POLICY_V1, sort_keys=True, separators=(',', ':')),
            'falsification_test': 'Insufficient historical candidates or missing terms make the affected decision or configuration unavailable.',
            'data_window': '2022-01-01 to 2025-12-31; fit uses only completed historical labels',
            'trial_budget': '48 archived setting combinations; research trials counted in separate ledger',
            'source': 'policy_sha256:' + policy_sha()}


def raw_factor_panels(frames):
    """Apply existing D4 factor formulas to raw, rather than retrospectively adjusted, bars."""
    if frames['close'].index.max() > pd.Timestamp('2025-12-31'):
        raise ValueError('OOS_OR_INPUT_WINDOW_BLOCKED')
    raw = dict(frames)
    raw.update({'adjusted_close': frames['close'], 'adjusted_open': frames['open'],
                'adjusted_high': frames['high'], 'adjusted_low': frames['low'], 'raw_close': frames['close']})
    close = frames['close']
    return {name: pd.DataFrame(compute_factor(FACTOR_REGISTRY[name], raw).values, index=close.index, columns=close.columns)
            .apply(pd.to_numeric, errors='coerce').replace([np.inf, -np.inf], np.nan)
            for name in D5_POLICY_V1['factor_universe']}


def historical_ic(frames, panels, membership, events):
    """Calculate historical IC labels; consumers must enforce the completed-horizon cutoff."""
    close, volume = frames['close'], frames['volume']
    if close.index.max() > pd.Timestamp('2025-12-31'):
        raise ValueError('OOS_OR_INPUT_WINDOW_BLOCKED')
    event_mask = pd.DataFrame(False, index=close.index, columns=close.columns)
    for event in events:
        day, asset = pd.Timestamp(event['ex_date']), str(event['stock_id'])
        if day in event_mask.index and asset in event_mask.columns:
            event_mask.at[day, asset] = True
    invalid = event_mask | close.isna() | volume.isna() | volume.le(0)
    horizon = D5_POLICY_V1['forward_horizon']
    future_invalid = invalid.shift(-1).iloc[::-1].rolling(horizon, min_periods=horizon).max().iloc[::-1]
    forward = (close.shift(-horizon) / close - 1).where(future_invalid.eq(0))
    eligible = membership.reindex(index=close.index, columns=close.columns, fill_value=False)
    ic, ranks = {}, {}
    for name, panel in panels.items():
        rank = panel.where(eligible).rank(axis=1, pct=True)
        labels = forward.where(rank.notna()).rank(axis=1, pct=True)
        valid = rank.notna() & labels.notna()
        ic[name] = rank.corrwith(labels, axis=1).where(valid.sum(axis=1) >= D5_POLICY_V1['minimum_cross_section'])
        ranks[name] = rank
    return pd.DataFrame(ic), ranks


def fit_at(signal_date, ic, ranks):
    """Fit five factors using only samples whose future-return horizon has completed."""
    signal_date = pd.Timestamp(signal_date)
    if signal_date > pd.Timestamp('2025-12-31') or signal_date not in ic.index:
        raise ValueError('INVALID_DEVELOPMENT_SIGNAL_DATE')
    position = ic.index.get_loc(signal_date)
    end = position - D5_POLICY_V1['forward_horizon'] + 1
    if end <= 0:
        return None
    history = ic.iloc[max(0, end - D5_POLICY_V1['training_window']):end]
    rows = []
    for name in D5_POLICY_V1['factor_universe']:
        valid = history[name].dropna()
        if len(valid) < D5_POLICY_V1['minimum_ic_days']:
            continue
        mean, std = float(valid.mean()), float(valid.std(ddof=1))
        if not np.isfinite(mean) or not np.isfinite(std) or std <= 1e-12 or abs(mean) < D5_POLICY_V1['minimum_abs_mean_ic']:
            continue
        rows.append({'factor_id': name, 'mean_ic': mean, 'icir': mean / std, 'direction': 1 if mean > 0 else -1})
    rows.sort(key=lambda row: (-abs(row['mean_ic']), row['factor_id']))
    if len(rows) < D5_POLICY_V1['candidate_count']:
        return None
    candidates = pd.DataFrame(rows[:D5_POLICY_V1['candidate_count']])
    ids = candidates['factor_id'].tolist()
    dates = ic.index[max(0, position - D5_POLICY_V1['training_window'] + 1):position + 1]
    stacked = pd.concat({name: ranks[name].loc[dates].stack(future_stack=True) for name in ids}, axis=1)
    correlation = stacked.corr(method='spearman', min_periods=30).reindex(index=ids, columns=ids)
    return {'candidates': candidates, 'correlation': correlation, 'last_label_asof': history.index[-1].isoformat()}


def weights_at(fit, method):
    return build_factor_weights(fit['candidates'], fit['correlation'], method, source_handoff_id='D5_RESEARCH_PIT_V1')
