import json

import pandas as pd

from jobs import run_factor_research as runner
from core.research.pipeline import RunConfig, RunResult, run


def test_cli_injects_profile_dates_and_all_loaded_trade_dates(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    raw_cache = tmp_path / "artifacts" / "factors" / "run" / "_raw" / "twse_rwd"
    raw_cache.mkdir(parents=True)
    (raw_cache / "t187ap03_L.json").write_text(json.dumps([{"公司代號": "2330", "上市日期": "19940905"}], ensure_ascii=False), encoding="utf-8")
    quotes = pd.DataFrame({"stock_id": ["2330", "2317", "2330"], "trade_date": ["2026-01-03", "2026-01-02", "2026-01-02"]})
    captured = {}
    monkeypatch.setattr(runner.pipeline, "load_twse_quotes", lambda config: quotes)
    monkeypatch.setattr(runner.pipeline, "run", lambda config: captured.setdefault("config", config) and RunResult("success", [], (), config.output_dir))

    assert runner.main(["--start", "2026-01-02", "--end", "2026-01-03", "--run-id", "run", "--no-fetch"]) == 0
    assert captured["config"].listing_dates == {"2330": pd.Timestamp("1994-09-05")}
    assert captured["config"].trading_calendar == (pd.Timestamp("2026-01-02"), pd.Timestamp("2026-01-03"))
    assert captured["config"].listing_date_provenance == {
        "listing_date_source": "twse_openapi/t187ap03_L",
        "listing_date_cache_used": True,
        "listing_date_retrieved_at": None,
        "listing_date_asset_count": 2,
        "listing_date_missing_count": 1,
    }


def test_cli_no_fetch_fails_before_any_network_when_profile_cache_is_missing(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(runner.twse, "fetch_company_profile", lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("network must not be called")))

    assert runner.main(["--start", "2026-01-02", "--end", "2026-01-03", "--run-id", "run", "--no-fetch"]) == 1
    assert "listing-date cache required for --no-fetch" in capsys.readouterr().err


def test_cli_no_fetch_uses_profile_cache_without_http_request(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    raw_cache = tmp_path / "artifacts" / "factors" / "run" / "_raw" / "twse_rwd"
    raw_cache.mkdir(parents=True)
    (raw_cache / "t187ap03_L.json").write_text(json.dumps([{"公司代號": "2330", "上市日期": "19940905"}], ensure_ascii=False), encoding="utf-8")
    quotes = pd.DataFrame({"stock_id": ["2330"], "trade_date": ["2026-01-02"]})
    monkeypatch.setattr(runner.pipeline, "load_twse_quotes", lambda config: quotes)
    monkeypatch.setattr(runner.pipeline, "run", lambda config: RunResult("success", [], (), config.output_dir))
    monkeypatch.setattr(runner.twse.requests, "get", lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("network must not be called")))

    assert runner.main(["--start", "2026-01-02", "--end", "2026-01-02", "--run-id", "run", "--no-fetch"]) == 0


def test_required_d3_without_a_dataset_writes_a_failed_manifest(tmp_path):
    quotes = pd.DataFrame({
        "trade_date": pd.bdate_range("2026-01-02", periods=3), "stock_id": "2330", "market": "TWSE", "currency": "TWD",
        "raw_open": 20.0, "raw_high": 21.0, "raw_low": 19.0, "raw_close": 20.0,
        "volume": 1_000_000.0, "amount": 25_000_000.0, "transaction_count": 1, "liquidity_basis": "official_amount",
    })
    result = run(RunConfig(
        run_id="required-d3", generated_at="2026-08-07T00:00:00Z", adjustment_as_of="2026-08-07",
        requested_start="2026-01-02", requested_end="2026-01-06", output_dir=tmp_path, quotes=quotes,
        actions=pd.DataFrame(columns=["ex_date", "stock_id", "pre_ex_close", "event_factor"]), require_research_dataset=True,
    ))

    manifest = json.loads((tmp_path / "run_manifest.json").read_text(encoding="utf-8"))
    assert result.status == "failed"
    assert manifest["status"] == "failed"
    assert "F015_research_dataset_missing" in (tmp_path / "validation_report.csv").read_text(encoding="utf-8")


def test_manifest_preserves_runner_listing_date_provenance(tmp_path):
    quotes = pd.DataFrame({
        "trade_date": pd.bdate_range("2026-01-02", periods=3), "stock_id": "2330", "market": "TWSE", "currency": "TWD",
        "raw_open": 20.0, "raw_high": 21.0, "raw_low": 19.0, "raw_close": 20.0,
        "volume": 1_000_000.0, "amount": 25_000_000.0, "transaction_count": 1, "liquidity_basis": "official_amount",
    })
    provenance = {"listing_date_source": "twse_openapi/t187ap03_L", "listing_date_cache_used": True,
                  "listing_date_retrieved_at": None, "listing_date_asset_count": 1, "listing_date_missing_count": 0}
    run(RunConfig(
        run_id="provenance", generated_at="2026-08-07T00:00:00Z", adjustment_as_of="2026-08-07",
        requested_start="2026-01-02", requested_end="2026-01-06", output_dir=tmp_path, quotes=quotes,
        actions=pd.DataFrame(columns=["ex_date", "stock_id", "pre_ex_close", "event_factor"]),
        listing_date_provenance=provenance,
    ))

    manifest = json.loads((tmp_path / "run_manifest.json").read_text(encoding="utf-8"))
    assert manifest["d3_enabled"] is False
    assert {key: manifest[key] for key in provenance} == provenance
