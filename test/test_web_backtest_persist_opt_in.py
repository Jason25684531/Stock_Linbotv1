"""驗證 /api/backtest/run、/backtest 的持久化開關正確傳遞給 _run_portfolio_backtest。"""


def _login(client):
    with client.session_transaction() as session:
        session['_user_id'] = 'admin'
        session['_fresh'] = True


def test_api_backtest_run_defaults_to_no_persist(monkeypatch):
    from app import app as flask_app
    import app as app_module

    captured = {}

    def fake_run_portfolio_backtest(strategies, start_date=None, end_date=None, weights=None, persist_to_db=False):
        captured['persist_to_db'] = persist_to_db
        return {'metrics': {}, 'strategy_performance': {}}, start_date, end_date

    monkeypatch.setattr(app_module, '_run_portfolio_backtest', fake_run_portfolio_backtest)

    client = flask_app.test_client()
    _login(client)
    response = client.post('/api/backtest/run', json={'strategies': ['hybrid_trend_rank']})
    payload = response.get_json()

    assert response.status_code == 200
    assert captured['persist_to_db'] is False
    assert payload['persisted'] is False


def test_api_backtest_run_can_opt_in_to_persist(monkeypatch):
    from app import app as flask_app
    import app as app_module

    captured = {}

    def fake_run_portfolio_backtest(strategies, start_date=None, end_date=None, weights=None, persist_to_db=False):
        captured['persist_to_db'] = persist_to_db
        return {'metrics': {}, 'strategy_performance': {}}, start_date, end_date

    monkeypatch.setattr(app_module, '_run_portfolio_backtest', fake_run_portfolio_backtest)

    client = flask_app.test_client()
    _login(client)
    response = client.post('/api/backtest/run', json={'strategies': ['hybrid_trend_rank'], 'persist': True})
    payload = response.get_json()

    assert response.status_code == 200
    assert captured['persist_to_db'] is True
    assert payload['persisted'] is True
