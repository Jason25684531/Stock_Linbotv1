"""Offline tests for the TWSE daily-quotes adapter."""

import json
from datetime import date, datetime
from pathlib import Path

from core.research.artifacts import write_source_coverage
from core.research.sources import RawResponse
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


FIXTURE_DIR = Path(__file__).resolve().parents[2] / "fixtures" / "research" / "mi_index"


def _response(name):
    return _payload_response(json.loads((FIXTURE_DIR / name).read_text(encoding="utf-8")))


def _payload_response(payload):
    return RawResponse(
        source="twse_rwd",
        endpoint="MI_INDEX",
        request_parameters={},
        retrieved_at=datetime(2026, 7, 30),
        source_revision=None,
        payload=payload,
        error=None,
    )


def test_classify_uses_exact_real_twse_statuses_only():
    normal = twse.classify(_response("normal.json"))
    weekend = twse.classify(_response("weekend.json"))
    future = twse.classify(_response("future.json"))
    early = twse.classify(_response("early.json"))

    assert normal.kind == twse.ResponseKind.TRADING_DAY
    assert weekend.kind == twse.ResponseKind.NON_TRADING_DAY
    assert (future.kind, future.bound, future.diagnostic_code) == (
        twse.ResponseKind.OUT_OF_RANGE,
        "future",
        "W013_request_out_of_range",
    )
    assert (early.kind, early.bound, early.diagnostic_code, early.severity) == (
        twse.ResponseKind.OUT_OF_RANGE,
        "early",
        "F011_window_before_source_start",
        "FATAL",
    )


def test_classify_never_treats_unknown_or_transport_failure_as_non_trading_day():
    unknown = _payload_response({"stat": "changed by TWSE"})
    transport_failure = RawResponse(
        source="twse_rwd",
        endpoint="MI_INDEX",
        request_parameters={},
        retrieved_at=datetime(2026, 7, 30),
        source_revision=None,
        payload=None,
        error="timeout",
    )

    assert twse.classify(unknown).kind == twse.ResponseKind.SOURCE_ERROR
    assert twse.classify(transport_failure).kind == twse.ResponseKind.SOURCE_ERROR


def test_classify_empty_success_and_writes_source_coverage(tmp_path):
    response = _payload_response({"stat": "OK", "tables": []})
    classification = twse.classify(response)

    assert (classification.kind, classification.diagnostic_code) == (
        twse.ResponseKind.EMPTY_RESULT,
        "W010_source_empty",
    )
    output = write_source_coverage(tmp_path, [classification.coverage_row("2023-01-03")])
    assert output.read_text(encoding="utf-8") == (
        "trade_date,classification,bound,code,severity,detail\n"
        "2023-01-03,EMPTY_RESULT,,W010_source_empty,WARN,successful response has no tables\n"
    )
