"""Offline tests for the TWSE daily-quotes adapter."""

import json
from datetime import date

from core.research.sources import twse


def test_fetch_daily_quotes_caches_raw_response(tmp_path, monkeypatch):
    payload = {"date": "20230103", "stat": "OK", "tables": []}
    calls = []

    class Response:
        def raise_for_status(self):
            return None

        def json(self):
            return payload

    def fake_get(url, *, params, timeout):
        calls.append((url, params, timeout))
        return Response()

    monkeypatch.setattr(twse.requests, "get", fake_get)

    response = twse.fetch_daily_quotes(date(2023, 1, 3), tmp_path)

    assert response.payload == payload
    assert response.source_revision == "20230103"
    assert calls[0][1] == {"date": "20230103", "type": "ALL", "response": "json"}
    assert json.loads((tmp_path / "MI_INDEX_20230103.json").read_text(encoding="utf-8")) == payload


def test_fetch_daily_quotes_uses_cache_without_http_request(tmp_path, monkeypatch):
    cached = {"date": "20230103", "stat": "OK", "tables": []}
    (tmp_path / "MI_INDEX_20230103.json").write_text(json.dumps(cached), encoding="utf-8")

    def fail_get(*args, **kwargs):
        raise AssertionError("cache hit must not make an HTTP request")

    monkeypatch.setattr(twse.requests, "get", fail_get)

    response = twse.fetch_daily_quotes(date(2023, 1, 3), tmp_path)

    assert response.payload == cached
