from __future__ import annotations

from flask import render_template


def test_strategy_catalog_contains_registered_strategies_and_one_fundamental():
    from app.web_server import FUNDAMENTAL_STRATEGY_ID, _build_strategy_catalog
    from core.strategy_manager import StrategyManager

    catalog = _build_strategy_catalog()
    assert len(catalog) == len(StrategyManager.CANONICAL_REGISTRY) + 1
    assert [item['strategy_id'] for item in catalog[:-1]] == list(StrategyManager.CANONICAL_REGISTRY)
    assert [item['strategy_id'] for item in catalog[:-1] if item['source'] == 'native'] == [
        'hybrid_trend_rank',
        'defensive_low_volatility',
        'growth_momentum_breakout',
        'quality_growth',
        'institutional_flow_confirmation',
        'mean_reversion',
        'quality_value_low_volatility',
    ]
    fundamental = next(item for item in catalog if item['strategy_id'] == FUNDAMENTAL_STRATEGY_ID)
    assert fundamental['aliases'] == []
    assert fundamental['strategy_id'] != 'v32'
    assert fundamental['available_for_daily'] is False
    assert fundamental['available_for_backtest'] is False
    assert fundamental['lifecycle'] in {'SHADOW', 'UNKNOWN'}
    registry = StrategyManager().get_platform_registry()
    assert {
        item['strategy_id']: item['lifecycle'] for item in catalog[:-1]
    } == {
        strategy_id: registry.get_spec(strategy_id).lifecycle.value
        for strategy_id in registry.canonical_ids()
    }
    assert all(item['lifecycle'] == item['eligibility'] for item in catalog[:-1])


def test_strategy_api_adds_capability_fields_without_dropping_legacy_fields(monkeypatch):
    import app.web_server as web_server
    from app import app as flask_app

    catalog = [
        {
            'strategy_id': 'hybrid_trend_rank',
            'name': 'hybrid_trend_rank',
            'display_name': 'Hybrid Trend Rank',
            'available_for_daily': True,
            'available_for_backtest': True,
            'active': True,
        },
        {
            'strategy_id': 'fundamental_g2g3_top5_reb60_score_weighted_v1',
            'name': 'fundamental_g2g3_top5_reb60_score_weighted_v1',
            'display_name': 'Fundamental G2/G3',
            'aliases': [],
            'available_for_daily': False,
            'available_for_backtest': False,
            'active': False,
        },
    ]
    monkeypatch.setattr(web_server, '_build_strategy_catalog', lambda: catalog)

    response = flask_app.test_client().get('/api/strategies')
    payload = response.get_json()

    assert response.status_code == 200
    assert payload['count'] == 2
    assert payload['strategies'][0]['name'] == 'hybrid_trend_rank'
    assert payload['strategies'][1]['available_for_backtest'] is False
    assert payload['active'] == ['hybrid_trend_rank']


def test_strategy_center_template_exposes_canonical_actions_and_mobile_layout():
    from app import app as flask_app

    catalog = [
        {
            'strategy_id': 'hybrid_trend_rank',
            'display_name_en': 'Hybrid Trend Rank',
            'aliases': ['V31'],
            'category': 'Technical',
            'lifecycle': 'PRODUCTION',
            'eligibility': 'PRODUCTION',
            'eligibility_reason': None,
            'available_for_daily': True,
            'daily_reason': None,
            'available_for_backtest': True,
            'backtest_reason': None,
        },
        {
            'strategy_id': 'fundamental_g2g3_top5_reb60_score_weighted_v1',
            'display_name_en': 'Fundamental G2/G3',
            'aliases': [],
            'category': 'Fundamental',
            'lifecycle': 'SHADOW',
            'eligibility': 'BLOCKED',
            'eligibility_reason': 'PRODUCTION_DISABLED',
            'available_for_daily': False,
            'daily_reason': 'legacy-only',
            'available_for_backtest': False,
            'backtest_reason': 'legacy-only',
        },
    ]
    with flask_app.test_request_context('/strategies'):
        html = render_template('strategy_center.html', strategy_catalog=catalog)

    assert 'data-testid="strategy-center"' in html
    assert 'data-testid="strategy-catalog-cards"' in html
    assert 'hybrid_trend_rank' in html
    assert 'fundamental_g2g3_top5_reb60_score_weighted_v1' in html
    assert 'V32' not in html
    assert 'available_for_daily' in html
    assert 'available_for_backtest' in html
    assert "url_for('update_strategy')" not in html
