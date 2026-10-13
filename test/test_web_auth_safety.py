from app import app as flask_app


def test_old_login_and_logout_urls_redirect_to_dashboard():
    client = flask_app.test_client()
    assert client.get('/login').headers['Location'] == '/dashboard'
    assert client.post('/login', data={'password': 'anything'}).headers['Location'] == '/dashboard'
    assert client.get('/logout').headers['Location'] == '/dashboard'


def test_user_trade_uses_single_local_identity_without_login(monkeypatch):
    import app.web_server as web_server

    captured = {}
    monkeypatch.setattr(
        web_server.services,
        'create_user_simulation_trade',
        lambda **kwargs: captured.update(kwargs) or True,
    )
    client = flask_app.test_client()

    accepted = client.post('/api/user/trade', json={
        'user_id': 'attacker', 'stock_id': '2330', 'buy_price': 100, 'buy_date': '2026-10-08',
    })
    assert accepted.status_code == 200
    assert captured['user_id'] == 'admin'
