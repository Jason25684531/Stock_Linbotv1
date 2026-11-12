"""Cross-strategy comparison on common VERIFIED sessions and one fixed equal-weight demonstration (design D9). No weight search."""
from __future__ import annotations

import numpy as np
import pandas as pd

from core.validation.correlation import correlation_analysis

MIN_COMMON = 252
TAIL = 0.05


def _matrix(frame: pd.DataFrame) -> dict:
    return {a: {b: (None if pd.isna(frame.loc[a, b]) else float(frame.loc[a, b])) for b in frame.columns} for a in frame.index}


def analyze(members: list[dict], benchmark: pd.Series | None) -> dict:
    """members: strategy_id, snapshot_id, accounting, identity_current, verified_returns (Series by date or None), holdings (date -> ids, or None)."""
    excluded, usable = {}, [m for m in members if m.get('verified_returns') is not None and len(m['verified_returns'])]
    snapshots = pd.Series([m['snapshot_id'] for m in usable]).mode()
    reference = snapshots.iloc[0] if len(snapshots) else None
    eligible = []
    for m in members:
        if m.get('verified_returns') is None or not len(m['verified_returns']):
            excluded[m['strategy_id']] = m.get('reason') or 'NO_VERIFIED_TRACK'
        elif m['snapshot_id'] != reference:
            excluded[m['strategy_id']] = f"SNAPSHOT_DIFFERS:{m['snapshot_id']}"
        elif m['accounting'] != 'POSITION_LEDGER_CA_V1':
            excluded[m['strategy_id']] = f"ACCOUNTING_DIFFERS:{m['accounting']}"
        elif not m['identity_current']:
            excluded[m['strategy_id']] = 'IDENTITY_STALE'
        else:
            eligible.append(m)
    out = {'eligible': [m['strategy_id'] for m in eligible], 'excluded': excluded, 'snapshot_id': reference,
           'labels': ['REFERENCE_ONLY', 'RESEARCH_ONLY', 'NOT_CERTIFIED', 'NOT_A_TRIAL'], 'notice': '研究用途；示範組合不是策略，也未經認證。'}
    if len(eligible) < 2:
        return out | {'status': 'FEWER_THAN_TWO_ELIGIBLE', 'common_sessions': 0}
    returns = pd.concat({m['strategy_id']: m['verified_returns'] for m in eligible}, axis=1, join='inner').dropna()
    flat = [c for c in returns if len(returns) > 1 and returns[c].std(ddof=1) == 0]  # all cash in the common period: no co-movement to measure
    for c in flat:
        excluded[c] = 'NO_RETURN_VARIATION_IN_COMMON_PERIOD'
    returns = returns.drop(columns=flat)
    eligible = [m for m in eligible if m['strategy_id'] in returns.columns]
    out['eligible'] = [m['strategy_id'] for m in eligible]
    if len(eligible) < 2:
        return out | {'status': 'FEWER_THAN_TWO_ELIGIBLE', 'common_sessions': len(returns)}
    out |= {'common_sessions': len(returns), 'common_period': [returns.index[0].strftime('%Y-%m-%d'), returns.index[-1].strftime('%Y-%m-%d')] if len(returns) else None}
    if len(returns) < MIN_COMMON:
        return out | {'status': 'INSUFFICIENT_COMMON_VERIFIED_SESSIONS', 'minimum': MIN_COMMON}
    curves = (1 + returns).cumprod()
    under = curves < curves.cummax()
    analysis = correlation_analysis(returns, drawdowns=curves / curves.cummax() - 1)
    tails = returns.le(returns.quantile(TAIL)) & returns.lt(0)  # a loss at or below the strategy's own 5th percentile
    ids = list(returns.columns)
    out['status'] = 'OK'
    out['correlation'] = _matrix(analysis['daily_return_correlation'])
    out['drawdown_correlation'] = _matrix(analysis['drawdown_correlation'])
    out['joint_drawdown_share'] = {a: {b: float((under[a] & under[b]).mean()) for b in ids} for a in ids}
    out['joint_tail_loss'] = {a: {b: float((tails[a] & tails[b]).mean()) for b in ids} for a in ids}
    out['joint_tail_independent_reference'] = TAIL * TAIL
    out['holding_overlap'], out['holding_overlap_reason'] = _overlap(eligible, returns.index)
    if benchmark is not None:
        b = benchmark.reindex(returns.index)
        out['beta'] = {sid: (float(np.cov(returns[sid], b)[0, 1] / np.var(b, ddof=1)) if b.notna().all() and b.var() > 0 else None) for sid in ids}
    else:
        out['beta'] = {sid: None for sid in ids}
    out['industry_concentration'] = {'value': None, 'reason': 'NO_INDUSTRY_CLASSIFICATION_IN_SNAPSHOT'}
    demo = returns.mean(axis=1)
    curve = (1 + demo).cumprod()
    years = len(demo) / 252
    single_vol = returns.std(ddof=1) * np.sqrt(252)
    out['equal_weight_demo'] = {
        'label': 'RESEARCH_DEMO_NOT_A_STRATEGY', 'members': ids, 'rebalance': 'daily, equal weight',
        'cagr': float(curve.iloc[-1] ** (1 / years) - 1), 'max_drawdown': float((curve / curve.cummax() - 1).min()),
        'volatility': float(demo.std(ddof=1) * np.sqrt(252)), 'average_member_volatility': float(single_vol.mean()),
        'average_member_max_drawdown': float(((1 + returns).cumprod() / (1 + returns).cumprod().cummax() - 1).min().mean()),
        'diversification_ratio': float(single_vol.mean() / (demo.std(ddof=1) * np.sqrt(252))) if demo.std(ddof=1) > 0 else None,
        'curve': [[d.strftime('%Y-%m-%d'), float(v)] for d, v in curve.items()]}
    return out


def _overlap(eligible, sessions):
    days = [d.strftime('%Y-%m-%d') for d in sessions]
    reason = {m['strategy_id']: None if m.get('holdings') else 'POSITIONS_UNVERIFIABLE' for m in eligible}
    out = {}
    for a in eligible:
        out[a['strategy_id']] = {}
        for b in eligible:
            if not a.get('holdings') or not b.get('holdings'):
                out[a['strategy_id']][b['strategy_id']] = None
                continue
            scores = []
            for d in days:
                x, y = set(a['holdings'].get(d, ())), set(b['holdings'].get(d, ()))
                if x | y:
                    scores.append(len(x & y) / len(x | y))
            out[a['strategy_id']][b['strategy_id']] = float(np.mean(scores)) if scores else None
    return out, reason
