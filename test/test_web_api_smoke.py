def test_api_backtest_result_get_returns_backtest_summary(monkeypatch):
    from app import app as flask_app
    import app as app_module

    summary = {
        'total_roi': 12.11,
        'win_rate': 38.41,
        'max_drawdown': -7.87,
        'sharpe_ratio': 0.0,
        'trade_count': 289,
        'avg_hold_days': 7.3,
    }
    monkeypatch.setattr(app_module, '_load_backtest_summary_or_error', lambda error_message: (summary, None))

    response = flask_app.test_client().get('/api/backtest-result')
    payload = response.get_json()

    assert response.status_code == 200
    assert payload == {
        'total_roi': 12.11,
        'win_rate': 38.41,
        'mdd': -7.87,
        'sharpe': 0.0,
        'trade_count': 289,
        'avg_hold_days': 7.3,
    }
