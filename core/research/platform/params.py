"""Backtest parameter bounds shared by the web form and the batch (change 2026-11-12, design D3). Web input is untrusted."""
from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[3]
OUT = ROOT / 'outputs/platform_v2'
CUTOFF = '2025-12-31'
BOUNDS = {'capital': (100_000, 100_000_000), 'fee_rate': (0, 0.01), 'tax_rate': (0, 0.01), 'minimum_fee': (0, 1_000), 'slippage_max': (0, 0.05)}
COST_KEYS = {'fee_rate', 'tax_rate', 'minimum_fee', 'slippage_max'}


def readiness() -> dict:
    payload = json.loads((OUT / 'STRATEGY_READINESS_21.json').read_text(encoding='utf-8'))
    return {row['strategy_id']: row for row in payload['result']['strategies']}


def check_params(strategy_id, start, end, capital, costs, rule, rows=None) -> dict:
    """Normalized parameters, or ValueError with the reason."""
    from core.research.platform.engine_v2 import RULES
    row = (rows if rows is not None else readiness()).get(strategy_id)
    if not row:
        raise ValueError(f'UNKNOWN_STRATEGY: {strategy_id}')
    if not row.get('web_runnable') or not row['backtest_window']:  # needs a VERIFIED track and a publishable V2 baseline run
        raise ValueError(f"NOT_RUNNABLE: {row['state']} {row['reason']}")
    first, last = row['backtest_window'][0], min(row['backtest_window'][1], CUTOFF)
    try:
        start, end = pd.Timestamp(start).strftime('%Y-%m-%d'), pd.Timestamp(end).strftime('%Y-%m-%d')
    except (ValueError, TypeError) as error:
        raise ValueError('INVALID_DATE') from error
    if not first <= start < end <= last:
        raise ValueError(f'WINDOW_OUT_OF_RANGE: allowed {first}..{last}')
    if rule not in RULES:
        raise ValueError(f'UNKNOWN_RULE_VERSION: {rule}')
    if not isinstance(costs, dict) or set(costs) != COST_KEYS:
        raise ValueError('COSTS_KEYS')
    for name, value in {'capital': capital, **costs}.items():
        low, high = BOUNDS[name]
        if not isinstance(value, (int, float)) or isinstance(value, bool) or not low <= float(value) <= high:
            raise ValueError(f'OUT_OF_BOUNDS: {name} must be within {low}..{high}')
    return {'strategy_id': strategy_id, 'start': start, 'end': end, 'capital': float(capital),
            'costs': {k: float(v) for k, v in costs.items()}, 'rule': rule}
