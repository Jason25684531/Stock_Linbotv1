from pathlib import Path

from core.strategy_manager import StrategyManager


PRODUCTION_SETTINGS = Path(__file__).resolve().parents[1] / 'strategy_settings.json'


def test_activation_writes_only_temporary_settings(manager):
    before = PRODUCTION_SETTINGS.read_bytes()
    assert manager.settings_file != PRODUCTION_SETTINGS
    assert manager.set_active_strategy('v38_value_dividend')
    assert manager.get_active_strategy_name() == 'quality_value_low_volatility'
    assert PRODUCTION_SETTINGS.read_bytes() == before


def test_collection_time_app_manager_is_isolated():
    import app

    before = PRODUCTION_SETTINGS.read_bytes()
    assert app.strategy_manager.settings_file != PRODUCTION_SETTINGS
    assert app.strategy_manager.set_active_strategy('v37_mean_reversion')
    assert StrategyManager.SETTINGS_FILE != PRODUCTION_SETTINGS
    assert PRODUCTION_SETTINGS.read_bytes() == before
