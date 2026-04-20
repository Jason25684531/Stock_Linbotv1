from __future__ import annotations

import pytest
from types import SimpleNamespace


class FakeGeminiError(Exception):
    def __init__(self, status_code: int, message: str) -> None:
        super().__init__(message)
        self.status_code = status_code


class FakeTimeoutError(TimeoutError):
    pass


def _fake_feed():
    return SimpleNamespace(entries=[{'title': '台股利多新聞標題'}])


@pytest.fixture(autouse=True)
def reset_stock_news_guard():
    from core import news_agent

    news_agent.reset_stock_news_guard_state()
    yield
    news_agent.reset_stock_news_guard_state()


def test_get_stock_news_mentions_limits_requests_to_eight(monkeypatch):
    from core import news_agent

    call_count = {'value': 0}

    class FakeModels:
        def generate_content(self, **kwargs):
            call_count['value'] += 1
            return SimpleNamespace(text='{"score": 1, "reason": "訂單回溫", "confidence": 0.91}')

    class FakeClient:
        def __init__(self, *args, **kwargs):
            self.models = FakeModels()

    monkeypatch.setattr(news_agent.Config, 'GEMINI_API_KEY', 'token')
    monkeypatch.setattr(news_agent, 'build_mcp_prompt_context', lambda: 'market context')
    monkeypatch.setattr(news_agent.feedparser, 'parse', lambda url: _fake_feed())
    monkeypatch.setattr(news_agent.genai, 'Client', FakeClient)

    stock_ids = [str(2300 + idx) for idx in range(10)]
    result = news_agent.get_stock_news_mentions(stock_ids)

    assert call_count['value'] == 8
    assert len(result) == 8


def test_get_stock_news_mentions_breaks_on_quota_error(monkeypatch):
    from core import news_agent

    call_count = {'value': 0}

    class FakeModels:
        def generate_content(self, **kwargs):
            call_count['value'] += 1
            raise FakeGeminiError(429, 'quota exceeded')

    class FakeClient:
        def __init__(self, *args, **kwargs):
            self.models = FakeModels()

    monkeypatch.setattr(news_agent.Config, 'GEMINI_API_KEY', 'token')
    monkeypatch.setattr(news_agent, 'build_mcp_prompt_context', lambda: 'market context')
    monkeypatch.setattr(news_agent.feedparser, 'parse', lambda url: _fake_feed())
    monkeypatch.setattr(news_agent.genai, 'Client', FakeClient)

    result = news_agent.get_stock_news_mentions(['2330', '2317'])

    assert result == {}
    assert call_count['value'] == 1


def test_get_stock_news_mentions_returns_partial_results_after_503(monkeypatch):
    from core import news_agent

    responses = iter([
        SimpleNamespace(text='{"score": 1, "reason": "法說利多", "confidence": 0.88}'),
        FakeGeminiError(503, 'service unavailable'),
    ])
    call_count = {'value': 0}

    class FakeModels:
        def generate_content(self, **kwargs):
            call_count['value'] += 1
            response = next(responses)
            if isinstance(response, Exception):
                raise response
            return response

    class FakeClient:
        def __init__(self, *args, **kwargs):
            self.models = FakeModels()

    monkeypatch.setattr(news_agent.Config, 'GEMINI_API_KEY', 'token')
    monkeypatch.setattr(news_agent, 'build_mcp_prompt_context', lambda: 'market context')
    monkeypatch.setattr(news_agent.feedparser, 'parse', lambda url: _fake_feed())
    monkeypatch.setattr(news_agent.genai, 'Client', FakeClient)

    result = news_agent.get_stock_news_mentions(['2330', '2317', '2303'])

    assert result == {'2330': {'score': 1, 'reason': '法說利多'}}
    assert call_count['value'] == 2


def test_get_stock_news_mentions_breaks_immediately_on_timeout(monkeypatch):
    from core import news_agent

    responses = iter([
        SimpleNamespace(text='{"score": 1, "reason": "法說利多", "confidence": 0.88}'),
        FakeTimeoutError('request timed out'),
    ])
    call_count = {'value': 0}

    class FakeModels:
        def generate_content(self, **kwargs):
            call_count['value'] += 1
            response = next(responses)
            if isinstance(response, Exception):
                raise response
            return response

    class FakeClient:
        def __init__(self, *args, **kwargs):
            self.models = FakeModels()

    monkeypatch.setattr(news_agent.Config, 'GEMINI_API_KEY', 'token')
    monkeypatch.setattr(news_agent, 'build_mcp_prompt_context', lambda: 'market context')
    monkeypatch.setattr(news_agent.feedparser, 'parse', lambda url: _fake_feed())
    monkeypatch.setattr(news_agent.genai, 'Client', FakeClient)

    result = news_agent.get_stock_news_mentions(['2330', '2317', '2303'])

    assert result == {'2330': {'score': 1, 'reason': '法說利多'}}
    assert call_count['value'] == 2
    snapshot = news_agent.get_stock_news_guard_snapshot()
    assert snapshot['consecutive_failures'] == 1
    assert snapshot['breaker_open'] is False


def test_get_stock_news_mentions_bypasses_calls_when_breaker_is_open(monkeypatch):
    from core import news_agent

    call_count = {'value': 0}

    class FakeModels:
        def generate_content(self, **kwargs):
            call_count['value'] += 1
            raise FakeGeminiError(503, 'service unavailable')

    class FakeClient:
        def __init__(self, *args, **kwargs):
            self.models = FakeModels()

    monkeypatch.setattr(news_agent.Config, 'GEMINI_API_KEY', 'token')
    monkeypatch.setattr(news_agent.Config, 'DASHBOARD_NEWS_FAILURE_THRESHOLD', 2)
    monkeypatch.setattr(news_agent.Config, 'DASHBOARD_NEWS_BREAKER_COOLDOWN_SECONDS', 120.0)
    monkeypatch.setattr(news_agent, 'build_mcp_prompt_context', lambda: 'market context')
    monkeypatch.setattr(news_agent.feedparser, 'parse', lambda url: _fake_feed())
    monkeypatch.setattr(news_agent.genai, 'Client', FakeClient)

    assert news_agent.get_stock_news_mentions(['2330']) == {}
    assert news_agent.get_stock_news_mentions(['2317']) == {}
    assert news_agent.get_stock_news_mentions(['2303']) == {}

    snapshot = news_agent.get_stock_news_guard_snapshot()
    assert snapshot['breaker_open'] is True
    assert call_count['value'] == 2