import json

from core.strategy_manager import StrategyManager
from core.backtest.runner import get_registered_strategy_names


def test_canonical_registry_resolves_existing_settings_keys(manager):
    expected = {
        'v31_hybrid', 'v33_low_vol', 'v34_turbo', 'v35_innovation',
        'v36_chip_momentum', 'v37_mean_reversion', 'v38_value_dividend',
    }
    settings = json.loads(manager.settings_file.read_text(encoding='utf-8'))
    settings['active_strategies'] = ['v38_value_dividend']
    manager.settings_file.write_text(json.dumps(settings), encoding='utf-8')

    assert {key for key in StrategyManager.CANONICAL_REGISTRY
            if StrategyManager.STRATEGY_METADATA[key].source == 'native'} == {
        'hybrid_trend_rank', 'defensive_low_volatility', 'growth_momentum_breakout',
        'quality_growth', 'institutional_flow_confirmation', 'mean_reversion',
        'quality_value_low_volatility',
    }
    assert expected <= set(StrategyManager.STRATEGY_REGISTRY)
    supported = expected | set(StrategyManager.CANONICAL_REGISTRY)
    assert set(settings['active_strategies']) <= supported
    assert set(settings['random_strategy_pool']) <= supported
    assert manager.list_strategies() == list(StrategyManager.CANONICAL_REGISTRY)
    assert get_registered_strategy_names(manager) == manager.list_strategies()
    assert manager.resolve('v31_hybrid') == 'hybrid_trend_rank'
    assert manager.get_active_strategy_names() == ['quality_value_low_volatility']
