from __future__ import annotations

import random

from core.backtest.metrics import calculate_metrics
from core.backtest.result import BootstrapResult


def bootstrap_metrics(returns: list[float], samples: int = 1000, seed: int | None = None,
                      confidence: float = 0.95, method: str = "daily", block_size: int = 5) -> BootstrapResult:
    if not returns or samples <= 0:
        raise ValueError("returns and samples are required")
    if method not in {"trade", "daily", "block"} or block_size <= 0:
        raise ValueError("method must be trade, daily, or block; block_size must be positive")
    rng, observations = random.Random(seed), []
    for _ in range(samples):
        curve = [1.0]
        sample = _resample(returns, rng, method, block_size)
        for value in sample:
            curve.append(curve[-1] * (1 + value))
        metrics = calculate_metrics(curve).values
        observations.append({name: metric.value for name, metric in metrics.items() if metric.value is not None})
    tail = (1 - confidence) / 2
    distributions = {name: [row[name] for row in observations if name in row]
                     for name in {key for row in observations for key in row}}
    intervals = {name: (sorted(values)[int(tail * len(values))],
                        sorted(values)[max(0, int((1 - tail) * len(values)) - 1)])
                 for name, values in distributions.items() if values}
    losses = {name: sum(value < 0 for value in values) / len(values)
              for name, values in distributions.items() if values}
    return BootstrapResult(seed, intervals, distributions, losses)


def _resample(values, rng, method, block_size):
    if method != "block":
        return [rng.choice(values) for _ in values]
    sample = []
    while len(sample) < len(values):
        start = rng.randrange(len(values))
        sample.extend(values[(start + offset) % len(values)] for offset in range(block_size))
    return sample[:len(values)]
