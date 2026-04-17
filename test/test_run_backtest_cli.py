from datetime import date

from config import V34_MODE_PRESETS
from core.strategies.v34_turbo import V34TurboStrategy
from jobs.run_backtest import BacktestEngine, parse_cli_args, resolve_backtest_plan


class _FakeStrategyManager:
    STRATEGY_REGISTRY = {
        'v31_hybrid': 'fake.V31',
        'v33_low_vol': 'fake.V33',
        'v34_turbo': 'fake.V34',
        'v35_innovation': 'fake.V35',
        'v36_chip_momentum': 'fake.V36',
        'v37_mean_reversion': 'fake.V37',
        'v38_value_dividend': 'fake.V38',
    }

    def __init__(self, strategy=None):
        self._strategy = strategy or V34TurboStrategy()

    def list_strategies(self):
        return list(self.STRATEGY_REGISTRY.keys())

    def get_strategy(self, strategy_name):
        if strategy_name not in self.STRATEGY_REGISTRY:
            return None
        return self._strategy

    def get_strategy_overrides(self, strategy_name):
        return {}


def test_resolve_backtest_plan_expands_all_and_auto_portfolio():
    args = parse_cli_args(['--strategies', 'all', '--days', '365', '--mode', 'balanced'])

    plan = resolve_backtest_plan(
        args,
        strategy_manager=_FakeStrategyManager(),
        latest_trade_date=date(2026, 4, 15),
    )

    assert plan['run_portfolio'] is True
    assert plan['strategy_names'] == [
        'v31_hybrid',
        'v33_low_vol',
        'v34_turbo',
        'v35_innovation',
        'v36_chip_momentum',
        'v37_mean_reversion',
        'v38_value_dividend',
    ]
    assert plan['start_date'] == '2025-04-15'
    assert plan['strategy_filter_mode'] == 'balanced'


def test_resolve_backtest_plan_keeps_single_strategy_without_portfolio():
    args = parse_cli_args(['--v35', '--days', '30', '--mode', 'balanced'])

    plan = resolve_backtest_plan(
        args,
        strategy_manager=_FakeStrategyManager(),
        latest_trade_date=date(2026, 4, 15),
    )

    assert plan['run_portfolio'] is False
    assert plan['strategy_names'] == ['v35_innovation']
    assert plan['start_date'] == '2026-03-16'
    assert plan['strategy_filter_mode'] == 'balanced'


def test_load_strategy_object_applies_runtime_mode_overrides(monkeypatch):
    cached_strategy = V34TurboStrategy()

    monkeypatch.setattr(
        'jobs.run_backtest.StrategyManager',
        lambda: _FakeStrategyManager(strategy=cached_strategy),
    )

    engine = BacktestEngine.__new__(BacktestEngine)
    engine.mode = 'v34_turbo'
    engine.strategy_filter_mode = 'balanced'

    strategy = BacktestEngine._load_strategy_object(engine)

    assert strategy is not cached_strategy
    assert strategy._get_float_setting('v34_breakout_ratio', 0.0) == float(
        V34_MODE_PRESETS['balanced']['v34_breakout_ratio']
    )