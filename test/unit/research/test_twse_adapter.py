"""Offline tests for the TWSE daily-quotes adapter."""

import json
from datetime import date, datetime
from pathlib import Path

import pytest

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
    return _payload_response(_fixture(name))


def _fixture(name):
    return json.loads((FIXTURE_DIR / name).read_text(encoding="utf-8"))


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
    table = _fixture("normal.json")["tables"][0] | {"data": []}
    response = _payload_response({"stat": "OK", "tables": [table]})
    classification = twse.classify(response)

    assert (classification.kind, classification.diagnostic_code) == (
        twse.ResponseKind.EMPTY_RESULT,
        "W010_source_empty",
    )
    output = write_source_coverage(tmp_path, [classification.coverage_row("2023-01-03")])
    assert output.read_text(encoding="utf-8") == (
        "trade_date,classification,bound,code,severity,detail\n"
        "2023-01-03,EMPTY_RESULT,,W010_source_empty,WARN,successful response has no rows\n"
    )


def test_find_closing_table_by_title_and_fields_not_position():
    payload = _fixture("ten_tables.json")

    assert twse.find_closing_table(payload) == payload["tables"][8]
    assert "tables[" not in Path(twse.__file__).read_text(encoding="utf-8")


def test_missing_or_ambiguous_closing_table_is_f009_schema_drift():
    target = _fixture("normal.json")["tables"][0]
    for payload in ({"tables": []}, {"tables": [target, target]}):
        with pytest.raises(twse.SchemaDriftError) as error:
            twse.find_closing_table(payload)
        assert error.value.code == "F009_schema_drift"


def test_request_gate_honours_minimum_interval_without_real_sleep():
    now = [0.0]
    sleeps = []

    def sleep(seconds):
        sleeps.append(seconds)
        now[0] += seconds

    gate = twse.RequestGate(2.0, clock=lambda: now[0], sleep=sleep)
    gate.wait()
    now[0] += 0.5
    gate.wait()

    assert sleeps == [1.5]


def test_fetch_daily_quotes_exhausts_retries_into_error_response(tmp_path, monkeypatch):
    monkeypatch.setattr(twse.requests, "get", lambda *args, **kwargs: (_ for _ in ()).throw(twse.requests.Timeout("offline")))

    response = twse.fetch_daily_quotes(date(2023, 1, 3), tmp_path, max_attempts=2, sleep=lambda _: None)

    assert response.payload is None
    assert response.error == "offline"


@pytest.mark.parametrize(
    ("value", "expected"),
    [("112年01月04日", date(2023, 1, 4)), ("100年01月01日", date(2011, 1, 1)), ("115年12月31日", date(2026, 12, 31))],
)
def test_parse_roc_date(value, expected):
    assert twse.parse_roc_date(value) == expected


def test_fetch_corporate_actions_caches_official_response(tmp_path, monkeypatch):
    payload = {"stat": "OK", "fields": ["資料日期", "股票代號", "除權息前收盤價", "除權息參考價", "權/息"], "data": []}

    class Response:
        def raise_for_status(self):
            return None

        def json(self):
            return payload

    monkeypatch.setattr(twse.requests, "get", lambda *args, **kwargs: Response())

    response = twse.fetch_corporate_actions(date(2023, 1, 1), date(2023, 12, 31), tmp_path)

    assert response.payload == payload
    assert (tmp_path / "TWT49U_20230101_20231231.json").exists()


def test_missing_corporate_action_field_is_schema_drift():
    with pytest.raises(twse.SchemaDriftError):
        twse.validate_corporate_action_fields({"fields": []})
