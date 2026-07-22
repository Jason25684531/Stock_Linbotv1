import random

from jobs.run_backtest import _seed_backtest_rng


def test_backtest_seed_makes_slippage_rng_reproducible(monkeypatch):
    monkeypatch.setenv('BACKTEST_RANDOM_SEED', '20260722')
    _seed_backtest_rng()
    first = random.uniform(0, 0.001)

    _seed_backtest_rng()
    second = random.uniform(0, 0.001)

    assert first == second
