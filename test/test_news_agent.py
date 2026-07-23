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


def _patch_gemini_client(monkeypatch, news_agent, client_cls):
    monkeypatch.setattr(news_agent, 'genai', SimpleNamespace(Client=client_cls))
    monkeypatch.setattr(
        news_agent,
        'types',
        SimpleNamespace(
            GenerateContentConfig=lambda **kwargs: SimpleNamespace(**kwargs),
            HttpOptions=lambda **kwargs: SimpleNamespace(**kwargs),
        ),
    )


def test_news_agent_defaults_to_supported_gemini_model():
    from config import Config
    from core import news_agent

    assert Config.GEMINI_MODEL == "gemini-2.5-flash-lite"
    assert news_agent.GEMINI_MODEL == Config.GEMINI_MODEL


def test_summarize_with_gemini_falls_back_when_model_returns_only_intro(monkeypatch):
    from core import news_agent

    class FakeModels:
        def generate_content(self, **kwargs):
            return SimpleNamespace(text="身為資深台股分析師，針對 2026-05-28 盤勢，篩選出以下")

    class FakeClient:
        def __init__(self, *args, **kwargs):
            self.models = FakeModels()

    news_text = (
        "【盤前/盤後重點】\n"
        "- 台股盤前要聞 黃仁勳旋風提前來台、AI供應鏈受矚目\n"
        "  COMPTEX題材延燒，台股AI族群聚焦。\n"
        "- 台股盤後 創史上第五大漲點1376點\n"
        "  台股短線漲幅過大，需留意拉回風險。\n"
        "【美股】\n"
        "- 美光HBM4產能倍增，記憶體供應鏈受惠\n"
        "  AI伺服器需求推升高階記憶體展望。\n"
    )

    monkeypatch.setattr(news_agent.Config, "GEMINI_API_KEY", "token")
    monkeypatch.setattr(news_agent, "build_mcp_prompt_context", lambda: "market context")
    _patch_gemini_client(monkeypatch, news_agent, FakeClient)

    result = news_agent._summarize_with_gemini(news_text)

    assert "身為資深台股分析師" not in result
    assert result.count("📌") >= 3
    assert "台股盤前要聞" in result
    assert "📊 綜合研判" in result


def test_summarize_with_gemini_falls_back_when_sdk_unavailable(monkeypatch):
    from core import news_agent

    news_text = (
        "【台股】\n"
        "- 台塑股東會釋出下半年展望\n"
        "  石化庫存成本降低，營運有望轉盈。\n"
    )

    monkeypatch.setattr(news_agent.Config, "GEMINI_API_KEY", "token")
    monkeypatch.setattr(news_agent, "genai", None)
    monkeypatch.setattr(news_agent, "types", None)

    result = news_agent._summarize_with_gemini(news_text)

    assert "google-genai SDK" not in result
    assert "台塑股東會釋出下半年展望" in result
    assert "📊 綜合研判" in result


def test_fetch_anue_news_uses_cnyes_api_before_google_rss(monkeypatch):
    from core import news_agent

    payload = {
        "items": {
            "data": [
                {
                    "title": "台股盤前要聞 AI供應鏈續強",
                    "summary": "輝達供應鏈題材延燒，台股AI族群受矚目。",
                    "categoryName": "台股新聞",
                },
                {
                    "title": "美股科技股反彈",
                    "summary": "費半走強有助台股電子權值股情緒。",
                    "categoryName": "美股新聞",
                },
            ]
        }
    }

    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self):
            import json

            return json.dumps(payload).encode("utf-8")

    monkeypatch.setattr(news_agent, "urlopen", lambda *args, **kwargs: FakeResponse(), raising=False)
    monkeypatch.setattr(news_agent.feedparser, "parse", lambda url: SimpleNamespace(entries=[]))

    news_text, priority_titles = news_agent.fetch_anue_news(max_per_source=2)

    assert "台股盤前要聞 AI供應鏈續強" in news_text
    assert "輝達供應鏈題材延燒" in news_text
    assert "美股科技股反彈" in news_text
    assert priority_titles == ["台股盤前要聞 AI供應鏈續強"]


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
    _patch_gemini_client(monkeypatch, news_agent, FakeClient)

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
    _patch_gemini_client(monkeypatch, news_agent, FakeClient)

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
    _patch_gemini_client(monkeypatch, news_agent, FakeClient)

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
    _patch_gemini_client(monkeypatch, news_agent, FakeClient)

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
    _patch_gemini_client(monkeypatch, news_agent, FakeClient)

    assert news_agent.get_stock_news_mentions(['2330']) == {}
    assert news_agent.get_stock_news_mentions(['2317']) == {}
    assert news_agent.get_stock_news_mentions(['2303']) == {}

    snapshot = news_agent.get_stock_news_guard_snapshot()
    assert snapshot['breaker_open'] is True
    assert call_count['value'] == 2
