from __future__ import annotations

import itertools


def cost_sensitivity(costs, evaluate):
    """Evaluate every supplied fee/tax/slippage/minimum-fee/fill-delay combination."""
    if not isinstance(costs, dict):
        return [{"cost": cost, **evaluate(cost)} for cost in costs]
    keys = tuple(costs)
    return [
        {**config, **evaluate(config)}
        for values in itertools.product(*(costs[key] for key in keys))
        for config in (dict(zip(keys, values)),)
    ]
