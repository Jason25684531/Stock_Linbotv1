from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import date

import pandas as pd
import pytest

from core.research.fundamental_pit import (
    MOPS_EMPTY_RESULT,
    MOPS_ERROR_PAGE,
    MOPS_REAL_DATA_PAGE,
    MOPS_RATE_LIMIT_PAGE,
    PublicationParseError,
    PublicationRequest,
    available_date_for_publication,
    build_metric_matrices,
    classify_mops_html,
    fetch_mops_publication_records,
    fetch_mops_publication_response,
    fetch_publication_request,
    parse_mops_publication_html,
    publication_request_key,
    validate_publication_record,
)


TRADING_DATES = pd.date_range("2025-05-13", "2025-05-20", freq="B")


def request(ticker: str = "2330", year: int = 114, season: int = 1) -> PublicationRequest:
    return PublicationRequest(ticker, year, season, f"legacy|{ticker}|{year}|{season}")


def publication_html(
    ticker: str = "2330",
    period: str = "114 年 第一季",
    upload: str = "114/05/13 19:10:39",
) -> str:
    return f"""
    <html><body>
      <table>
        <tr><th>證券代號</th><th>資料年度</th><th>資料類型</th><th>結案類型</th>
            <th>性質</th><th>資料詳細說明</th><th>備註</th><th>電子檔案</th>
            <th>檔案大小</th><th>上傳日期</th><th>財務報告更(補)正</th></tr>
        <tr><td>{ticker}</td><td>{period}</td><td>財務報告書</td><td></td><td></td>
            <td>IFRSs合併財報</td><td></td><td>202501_{ticker}_AI1.pdf</td>
            <td>100</td><td>{upload}</td><td>無</td></tr>
      </table>
    </body></html>
    """


def test_valid_mops_html_is_real_data_not_invalid_response():
    assert classify_mops_html(publication_html()) == MOPS_REAL_DATA_PAGE


@pytest.mark.parametrize(
    ("html", "expected"),
    [
        ("<html><body>查詢過量，請稍後再查詢</body></html>", MOPS_RATE_LIMIT_PAGE),
        ("<html><body>系統錯誤</body></html>", MOPS_ERROR_PAGE),
        ("<html><body>查無所需資料</body></html>", MOPS_EMPTY_RESULT),
    ],
)
def test_block_error_and_empty_html_are_classified(html: str, expected: str):
    assert classify_mops_html(html) == expected


def test_fetch_quarantines_rate_limit_page(tmp_path):
    result = fetch_mops_publication_records(
        request(),
        tmp_path / "cache",
        TRADING_DATES,
        transport=lambda _: ("<html><body>查詢過量，請稍後再查詢</body></html>", 200, {"content_type": "text/html"}),
    )
    assert result["parse_status"] == "QUARANTINED"
    assert result["records"] == ()


def test_publication_date_and_timestamp_are_parsed():
    records = parse_mops_publication_html(publication_html(), request(), TRADING_DATES)
    record = records[0]
    assert record.publication_date == date(2025, 5, 13)
    assert record.publication_timestamp is not None
    assert record.publication_timestamp.hour == 19
    assert record.available_date == date(2025, 5, 14)
    assert record.availability_rule == "NEXT_TRADING_DATE_AFTER_CLOSE"


def test_wildcard_publication_page_maps_multiple_periods():
    wildcard = PublicationRequest("2330", 0, 0, "batch")
    html = publication_html() + publication_html(period="113 年 第四季", upload="114/03/01 12:00:00")
    records = parse_mops_publication_html(html, wildcard, TRADING_DATES)
    assert {(record.fiscal_year, record.quarter) for record in records} == {(2025, 1), (2024, 4)}
    assert {record.request_key for record in records} == {
        publication_request_key("2330", 114, 1), publication_request_key("2330", 113, 4)
    }


def test_wildcard_publication_form_uses_empty_year_and_season():
    from core.research.fundamental_pit import mops_publication_form

    assert mops_publication_form(PublicationRequest("2330", 0, 0, "batch"))["year"] == ""
    assert mops_publication_form(PublicationRequest("2330", 0, 0, "batch"))["seamon"] == ""


def test_ticker_mismatch_fails():
    with pytest.raises(PublicationParseError, match="ticker mismatch"):
        parse_mops_publication_html(publication_html(ticker="2317"), request(), TRADING_DATES)


def test_period_mismatch_fails():
    with pytest.raises(PublicationParseError, match="period mismatch"):
        parse_mops_publication_html(publication_html(period="113 年 第四季"), request(), TRADING_DATES)


def test_real_html_parse_failure_is_recorded_as_invalid_response(tmp_path):
    result = fetch_mops_publication_records(
        request(),
        tmp_path / "cache",
        TRADING_DATES,
        transport=lambda _: (publication_html(ticker="2317").encode("big5"), 200, {"content_type": "text/html"}),
    )
    assert result["parse_status"] == "INVALID_RESPONSE"
    assert result["evidence_status"] == "UNVERIFIED"
    assert "ticker mismatch" in result["failure_reason"]
    assert result["records"] == ()


def test_period_must_precede_publication_date():
    record = parse_mops_publication_html(publication_html(), request(), TRADING_DATES)[0]
    with pytest.raises(PublicationParseError, match="period_end"):
        validate_publication_record(replace(record, publication_date=record.period_end))


def test_available_date_is_not_before_publication_date():
    record = parse_mops_publication_html(publication_html(), request(), TRADING_DATES)[0]
    assert record.publication_date <= record.available_date
    validate_publication_record(record)


def test_date_only_publication_uses_next_trading_date():
    available, rule = available_date_for_publication(date(2025, 5, 13), None, TRADING_DATES)
    assert available == date(2025, 5, 14)
    assert rule == "NEXT_TRADING_DATE_AFTER_CLOSE"


def test_weekend_or_holiday_publication_uses_next_trading_date():
    calendar = [date(2025, 5, 16), date(2025, 5, 19)]
    available, rule = available_date_for_publication(date(2025, 5, 17), None, calendar)
    assert available == date(2025, 5, 19)
    assert rule == "NEXT_TRADING_DATE_NON_TRADING_DAY"


def test_future_publication_is_invisible_and_available_is_visible():
    records = pd.DataFrame([
        {"ticker": "2330", "period_end": "2025-03-31", "publication_date": "2025-05-13", "available_date": "2025-05-14", "eps": 2.0}
    ])
    matrices = build_metric_matrices(records, ["2330"], ["2025-05-13", "2025-05-14"])
    assert pd.isna(matrices["eps"].iloc[0, 0])
    assert matrices["eps"].iloc[1, 0] == 2.0


def test_backward_asof_uses_latest_available_revision():
    records = pd.DataFrame([
        {"ticker": "2330", "period_end": "2024-12-31", "publication_date": "2025-01-10", "available_date": "2025-01-13", "eps": 1.0},
        {"ticker": "2330", "period_end": "2025-03-31", "publication_date": "2025-05-13", "available_date": "2025-05-14", "eps": 2.0},
    ])
    matrices = build_metric_matrices(records, ["2330"], ["2025-01-14", "2025-05-13", "2025-05-14"])
    assert matrices["eps"].iloc[:, 0].tolist() == [1.0, 1.0, 2.0]


def test_no_forward_fill_across_unpublished_period():
    records = pd.DataFrame([
        {"ticker": "2330", "period_end": "2025-03-31", "publication_date": "2025-05-13", "available_date": "2025-05-14", "eps": 2.0}
    ])
    matrices = build_metric_matrices(records, ["2330"], ["2025-05-01", "2025-05-13"])
    assert matrices["eps"].isna().all().all()


def test_successful_publication_cache_is_not_refetched(tmp_path):
    calls = []

    def transport(req):
        calls.append(req.request_key)
        return publication_html().encode("big5"), 200, {"content_type": "text/html;charset=big5"}

    cache = tmp_path / "cache"
    first = fetch_mops_publication_records(request(), cache, TRADING_DATES, transport=transport)
    second = fetch_mops_publication_records(
        request(), cache, TRADING_DATES,
        transport=lambda _: (_ for _ in ()).throw(AssertionError("successful evidence must be cached")),
    )
    assert first["cache_hit"] is False
    assert second["cache_hit"] is True
    assert len(calls) == 1


def test_concurrent_same_destination_cache_writes_are_atomic(tmp_path):
    html = publication_html().encode("big5")

    def transport(_):
        return html, 200, {"content_type": "text/html;charset=big5"}

    cache = tmp_path / "cache"
    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(
            lambda _: fetch_mops_publication_response(request(), cache, transport=transport),
            range(2),
        ))

    response_dir = cache / "mops_publication"
    html_files = list(response_dir.glob("*.html"))
    json_files = list(response_dir.glob("*.json"))
    assert len(html_files) == len(json_files) == 1
    assert classify_mops_html(html_files[0].read_bytes()) == MOPS_REAL_DATA_PAGE
    metadata = json.loads(json_files[0].read_text(encoding="utf-8"))
    assert metadata["classification"] == MOPS_REAL_DATA_PAGE
    assert metadata["request_key"] == publication_request_key("2330", 114, 1)
    assert all(result["classification"] == MOPS_REAL_DATA_PAGE for result in results)
    assert not list(response_dir.glob("*.tmp"))


def test_publication_evidence_key_is_deterministic():
    key = publication_request_key("2330.TW", 114, 1)
    assert key == "mops.t57sb01|ticker=2330|roc_year=114|season=1|statement_type=income_statement"
    left = parse_mops_publication_html(publication_html(), request(), TRADING_DATES, retrieved_at=pd.Timestamp("2026-09-15", tz="UTC").to_pydatetime())
    right = parse_mops_publication_html(publication_html(), request(), TRADING_DATES, retrieved_at=pd.Timestamp("2026-09-15", tz="UTC").to_pydatetime())
    assert left == right


def test_legacy_json_adapter_accepts_a_real_html_page(monkeypatch):
    class Response:
        status_code = 200
        content = publication_html().encode("big5")

        def raise_for_status(self):
            return None

        def json(self):
            raise ValueError("not JSON")

    import requests
    monkeypatch.setattr(requests, "get", lambda *args, **kwargs: Response())
    result = fetch_publication_request(request())
    assert result["parse_status"] == "PASS"
    assert result["format"] == "HTML"
