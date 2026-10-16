from __future__ import annotations

import json


def _login(client):
    with client.session_transaction() as session:
        session['_user_id'] = 'admin'
        session['_fresh'] = True


def _catalog():
    return [
        {
            'strategy_id': 'hybrid_trend_rank', 'display_name_en': 'Hybrid Trend Rank',
            'aliases': ['V31'], 'available_for_backtest': True, 'backtest_reason': None,
        },
        {
            'strategy_id': 'fundamental_g2g3_top5_reb60_score_weighted_v1', 'display_name_en': 'Fundamental G2/G3',
            'aliases': [], 'available_for_backtest': False, 'backtest_reason': 'legacy-only',
        },
    ]


def test_backtest_configuration_uses_capability_catalog(monkeypatch):
    import app.web_server as web_server
    from app import app as flask_app

    monkeypatch.setattr(web_server, '_build_strategy_catalog', _catalog)
    client = flask_app.test_client()
    _login(client)

    response = client.get('/backtest?strategies=hybrid_trend_rank')
    html = response.get_data(as_text=True)

    assert response.status_code == 200
    assert 'value="hybrid_trend_rank"' in html
    assert 'value="fundamental_g2g3_top5_reb60_score_weighted_v1"' in html
    assert 'disabled' in html
    assert 'legacy-only' in html


def test_backtest_post_blocks_unsupported_catalog_selection(monkeypatch):
    import app.web_server as web_server
    import app as app_module
    from app import app as flask_app

    monkeypatch.setattr(web_server, '_build_strategy_catalog', _catalog)
    called = False

    def fail_if_called(*args, **kwargs):
        nonlocal called
        called = True
        raise AssertionError('unsupported strategy reached the engine')

    monkeypatch.setattr(app_module, '_run_portfolio_backtest', fail_if_called)
    client = flask_app.test_client()
    _login(client)

    response = client.post('/backtest', data={'strategies': ['fundamental_g2g3_top5_reb60_score_weighted_v1']})

    assert response.status_code == 302
    assert called is False


def test_backtest_result_keeps_sections_and_omits_global_trades_without_run_evidence():
    from flask import render_template
    from app import app as flask_app

    chart = json.dumps({'data': [], 'layout': {}})
    with flask_app.test_request_context('/backtest'):
        html = render_template(
            'backtest_result.html',
            metrics={
                'total_return': {'value': None, 'reason': 'equity history is unavailable'},
                'max_drawdown': -2.5,
                'sharpe_ratio': {'value': None, 'reason': 'return variance is zero'},
                'win_rate': 0,
            },
            strategy_performance={},
            equity_chart=chart,
            drawdown_chart=chart,
            monthly_chart=chart,
            trades=[],
            selected_strategies=['hybrid_trend_rank'],
            start_date='2026-01-01',
            end_date='2026-01-31',
        )

    assert 'data-testid="backtest-overview"' in html
    assert 'data-testid="equity-section"' in html
    assert 'data-testid="drawdown-section"' in html
    assert 'data-testid="monthly-section"' in html
    assert 'data-testid="trades-section"' not in html
    assert '資料不可用' in html
    assert 'equity history is unavailable' in html
    assert 'return variance is zero' in html
    assert 'Infinity' not in html
