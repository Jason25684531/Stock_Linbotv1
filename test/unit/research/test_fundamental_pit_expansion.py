from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import pytest

from core.research.fundamental_pit import (
    ExpansionError,
    PublicationExpansionRunner,
    build_metric_matrices,
    build_target_universe,
    coverage_report,
    dependency_metadata,
    fetch_publication_request,
    make_request_key,
    missingness_report,
    normalize_pit_records,
    plan_requests,
    publication_request_parameters,
    readiness,
    rebuild_publication_mapping,
    validate_pit_order,
    validate_complete_ledger,
    validate_missingness_reconciliation,
    write_authoritative_artifacts,
    run_full_expansion,
)


def test_target_uses_full_eligible_frame_not_cache_subset():
    universe = pd.DataFrame({"stock_id": ["2330", "2317", "912000", "0001"], "is_eligible": [True, True, True, True]})
    target = build_target_universe(universe, ["2330.TW"])

    assert target.tickers == ("0001", "2317", "2330", "912000")
    assert target.count == 4
    assert target.sha256 == build_target_universe(universe, ["2330.TW"]).sha256


def test_target_baseline_status_is_explicit():
    universe = pd.DataFrame({"ticker": ["2330"], "is_eligible": [True]})
    target = build_target_universe(universe, baseline={"target_ticker_count": 2, "target_ticker_sha256": "wrong"})
    blocked = build_target_universe(universe, baseline={"target_ticker_count": 1})

    assert target.status == "CHANGED"
    assert blocked.status == "BLOCKED"
    assert build_target_universe("missing-research-universe.parquet").status == "BLOCKED"


def test_full_entrypoint_blocks_without_canonical_input(tmp_path: Path):
    result = run_full_expansion("missing-research-universe.parquet", ["2330"], tmp_path / "cache", tmp_path / "v2")
    assert result["status"] == "BLOCKED"
    assert json.loads((tmp_path / "v2" / "run_manifest.json").read_text())["target_universe_status"] == "BLOCKED"


def test_full_entrypoint_snapshots_before_network_candidate(tmp_path: Path):
    universe = tmp_path / "research_universe.parquet"
    pd.DataFrame({"ticker": ["2330"], "is_eligible": [True]}).to_parquet(universe, index=False)
    result = run_full_expansion(
        universe,
        [],
        tmp_path / "cache",
        tmp_path / "v2",
        fetcher=lambda _: {"payload": {"publication_date": "2024-01-01"}, "http_status": 200},
        max_attempts=1,
    )
    assert result["manifest"]["PRE_RUN_SNAPSHOT"] == "PASS"
    assert result["manifest"]["pre_run_snapshot"]["network_fetch_count"] == 0
    assert (tmp_path / "v2" / "pre_run_snapshot" / "pre_expansion_manifest.json").exists()


def test_request_space_is_dynamic_and_deterministic():
    requests = plan_requests(["2330", "2317"])

    assert len(requests) == 2 * 13 * 4
    assert requests[0].request_key == make_request_key("2317", 102, 1)
    assert requests[-1].request_key == make_request_key("2330", 114, 4)
    assert publication_request_parameters(requests[0]) == {"co_id": "2317", "year": "102", "season": "1"}


def test_default_publication_fetcher_uses_parameterized_endpoint(monkeypatch):
    seen = {}

    class Response:
        status_code = 200
        def raise_for_status(self):
            return None
        def json(self):
            return {"publication_date": "2024-01-01"}

    def get(url, *, params, timeout):
        seen.update(url=url, params=params, timeout=timeout)
        return Response()

    import requests
    monkeypatch.setattr(requests, "get", get)
    result = fetch_publication_request(plan_requests(["2330"])[0], timeout_seconds=7)

    assert result["payload"]["publication_date"] == "2024-01-01"
    assert seen["url"].endswith("/server-java/t57sb01") and seen["timeout"] == 7


def test_review_baseline_shape_enumerates_all_878_targets():
    universe = pd.DataFrame({"stock_id": [f"{i:04d}" for i in range(1000, 1878)], "is_eligible": True})
    target = build_target_universe(universe)

    assert target.count == 878
    assert len(plan_requests(target.tickers)) == target.count * 13 * 4


def test_ledger_records_all_terminal_states_and_resume(tmp_path: Path):
    calls = []

    def fetch(request):
        calls.append(request.request_key)
        if request.ticker == "2317":
            raise TimeoutError("offline")
        return {"payload": {"publication_date": "2024-01-01"}, "http_status": 200}

    # Keep the fixture small while retaining the production ROC/season plan.
    target = ["2330", "2317", "912000"]
    output = tmp_path / "run"
    cache = tmp_path / "cache"
    first = PublicationExpansionRunner(target, cache, output, fetcher=fetch, max_attempts=1).run()
    statuses = set(first["rows"]["request_status"])

    assert {"FETCHED", "FAILED", "NOT_APPLICABLE"} <= statuses
    assert len(first["rows"]) == len(target) * 13 * 4
    first_calls = len(calls)
    second = PublicationExpansionRunner(target, cache, output, fetcher=lambda _: (_ for _ in ()).throw(AssertionError("must use cache")), cache_only=True).run()

    assert second["manifest"]["network_fetch_count"] == 0
    assert "CACHED" in set(second["rows"]["request_status"])
    assert len(calls) == first_calls
    assert (output / "publication_expansion_stats.csv").exists()
    for field in ("target_ticker_count", "target_ticker_sha256", "git_head", "python_version", "git_version", "pandas_version", "artifact_sha256"):
        assert field in second["manifest"]


def test_cache_only_rerun_reproduces_ledger_and_mapping(tmp_path: Path):
    def fetch(_request):
        return {"payload": {"publication_date": "2024-01-01"}, "http_status": 200}

    cache = tmp_path / "cache"
    first = PublicationExpansionRunner(["2330"], cache, tmp_path / "network", fetcher=fetch, max_attempts=1).run()
    second = PublicationExpansionRunner(["2330"], cache, tmp_path / "cache_only", cache_only=True).run()

    assert second["manifest"]["network_fetch_count"] == 0
    assert first["rows"].drop(columns=["request_status", "cache_hit", "http_attempted"]).equals(second["rows"].drop(columns=["request_status", "cache_hit", "http_attempted"]))
    assert pd.read_csv(tmp_path / "network" / "publication_dates_expanded.csv").equals(pd.read_csv(tmp_path / "cache_only" / "publication_dates_expanded.csv"))


def test_invalid_response_is_logged_not_dropped(tmp_path: Path):
    def fetch(_request):
        return {"payload": None, "http_status": 502, "parse_status": "INVALID_RESPONSE", "failure_reason": "bad payload"}

    result = PublicationExpansionRunner(["2330"], tmp_path / "cache", tmp_path / "out", fetcher=fetch, max_attempts=1).run()
    assert set(result["rows"]["request_status"]) == {"INVALID_RESPONSE"}
    assert set(result["rows"]["failure_reason"]) == {"bad payload"}


def test_legacy_cache_metadata_is_reused_without_fetch(tmp_path: Path):
    cache = tmp_path / "legacy-cache"
    cache.mkdir()
    (cache / "legacy-2330.json").write_text(json.dumps({"ticker": "2330", "roc_year": 102, "season": 1, "payload": {"publication_date": "2024-01-01"}, "http_status": 200}))
    request_key = make_request_key("2330", 102, 1)
    result = PublicationExpansionRunner(["2330"], cache, tmp_path / "out", fetcher=lambda _: (_ for _ in ()).throw(AssertionError("legacy cache must be reused")), cache_only=True).run()
    row = result["rows"].loc[result["rows"].request_key == request_key].iloc[0]
    assert row.request_status == "CACHED" and bool(row.cache_hit)


def test_mapping_and_missingness_reconcile(tmp_path: Path):
    ledger = pd.DataFrame(
        [
            {"ticker": "2330", "roc_year": 114, "season": 1, "request_key": "a", "request_status": "FETCHED", "cache_hit": False, "http_attempted": True, "http_status": 200, "parse_status": "PASS", "publication_found": True, "failure_reason": ""},
            {"ticker": "912000", "roc_year": 114, "season": 1, "request_key": "b", "request_status": "NOT_APPLICABLE", "cache_hit": False, "http_attempted": False, "http_status": None, "parse_status": "NOT_APPLICABLE", "publication_found": False, "failure_reason": "UNSUPPORTED_IDENTIFIER"},
        ]
    )
    mapping = rebuild_publication_mapping(ledger, tmp_path / "publication_dates_expanded.csv")
    matrices = {"eps": pd.DataFrame([[1.0, None]], index=pd.to_datetime(["2024-01-01"]), columns=["2330", "912000"]), "roe": pd.DataFrame([[0.1, None]], index=pd.to_datetime(["2024-01-01"]), columns=["2330", "912000"])}
    missing = missingness_report(matrices, ["2330", "912000"], mapping)

    assert mapping.loc[mapping.ticker == "912000", "missing_reason"].item() == "UNSUPPORTED_IDENTIFIER"
    assert len(missing) == 2
    assert validate_missingness_reconciliation(matrices, missing, ["2330", "912000"])[0]


def test_complete_ledger_reconciles_request_space():
    ledger = pd.DataFrame([{"request_key": request.request_key, "request_status": "NOT_APPLICABLE"} for request in plan_requests(["912000"])])
    assert validate_complete_ledger(ledger, ["912000"]) == (True, "")


def test_pit_ordering_future_exclusion_and_metric_definitions():
    records = pd.DataFrame(
        [
            {"ticker": "2330", "period_end": "2023-12-31", "publication_date": "2024-03-01", "available_date": "2024-03-02", "eps": 2.0, "net_income": 10.0, "equity": 100.0},
            {"ticker": "2330", "period_end": "2024-03-31", "publication_date": "2024-06-01", "available_date": "2024-06-02", "eps": 3.0, "net_income": 20.0, "equity": 100.0},
        ]
    )
    ok, diagnostics = validate_pit_order(records)
    matrices = build_metric_matrices(records, ["2330"], ["2024-04-01", "2024-07-01"])

    assert ok and diagnostics.empty
    assert matrices["eps"].iloc[0, 0] == 2.0
    assert matrices["roe"].iloc[0, 0] == 0.1
    assert matrices["eps"].iloc[1, 0] == 3.0


def test_pit_ordering_rejects_future_publication():
    records = pd.DataFrame([{ "ticker": "2330", "period_end": "2024-01-01", "publication_date": "2024-04-01", "available_date": "2024-04-01", "asof_date": "2024-03-01" }])
    ok, diagnostics = validate_pit_order(records)
    assert not ok and not diagnostics.empty


def test_coverage_denominators_and_layered_readiness():
    matrices = {"eps": pd.DataFrame([[1.0, None]], columns=["2330", "2317"]), "roe": pd.DataFrame([[0.1, None]], columns=["2330", "2317"])}
    coverage = coverage_report(matrices, ["2330", "2317", "2454"], ["2330"])
    ready = readiness(pipeline_integrity=True, factor_matrix_coverage=1.0, full_expansion=False)

    assert coverage.loc[coverage.metric == "eps", "factor_matrix_denominator"].item() == 1
    assert coverage.loc[coverage.metric == "eps", "target_universe_denominator"].item() == 3
    assert ready["FACTOR_MATRIX_RESEARCH_READY"] == "YES"
    assert ready["FULL_PUBLICATION_EXPANSION"] == "PARTIAL"
    assert ready["READY_FOR_RESEARCH_CYCLE_V3"] == "NO"


def test_dependency_metadata_separates_python_and_git():
    metadata = dependency_metadata()
    assert metadata["python_version"].startswith("3.")
    assert metadata["git_version"].startswith("git version ")


def test_publication_mapping_keeps_date_and_v1_is_immutable(tmp_path: Path):
    ledger = pd.DataFrame([{
        "ticker": "2330", "roc_year": 114, "season": 1, "request_key": "a", "request_status": "FETCHED", "cache_hit": False,
        "http_attempted": True, "http_status": 200, "parse_status": "PASS", "publication_found": True, "publication_date": "2024-03-01", "failure_reason": "",
    }])
    mapping = rebuild_publication_mapping(ledger, tmp_path / "mapping.csv")
    assert mapping.loc[0, "publication_date"] == "2024-03-01"
    with pytest.raises(ExpansionError):
        write_authoritative_artifacts(tmp_path / "fundamental_pit_coverage_v1", pd.DataFrame(), {}, pd.DataFrame(), pd.DataFrame(), {})


def test_normalization_preserves_revisions_and_sets_unavailable_status(tmp_path: Path):
    records = pd.DataFrame([
        {"ticker": "2330", "period_end": "2023-12-31", "publication_date": "2024-03-01", "available_date": "2024-03-02", "eps": 2.0},
        {"ticker": "2330", "period_end": "2023-12-31", "publication_date": "2024-03-03", "available_date": "2024-03-04", "eps": 2.1},
    ])
    normalized = normalize_pit_records(records)
    matrices = {"eps": pd.DataFrame([[2.1]], index=pd.to_datetime(["2024-04-01"]), columns=["2330"]), "roe": pd.DataFrame([[0.1]], index=pd.to_datetime(["2024-04-01"]), columns=["2330"])}
    coverage = coverage_report(matrices, ["2330"], ["2330"])
    paths = write_authoritative_artifacts(tmp_path / "v2", normalized, matrices, pd.DataFrame(), coverage, {"status": "review"})

    assert len(normalized) == 2
    assert set(normalized["revision_status"]) == {"SOURCE_UNAVAILABLE"}
    assert paths["records"].exists() and paths["matrix"].exists()
