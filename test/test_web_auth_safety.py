from app import app as flask_app


def test_old_login_and_logout_urls_redirect_to_dashboard():
    client = flask_app.test_client()
    assert client.get('/login').headers['Location'] == '/dashboard'
    assert client.post('/login', data={'password': 'anything'}).headers['Location'] == '/dashboard'
    assert client.get('/logout').headers['Location'] == '/dashboard'
