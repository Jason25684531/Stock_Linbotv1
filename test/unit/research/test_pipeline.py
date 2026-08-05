from datetime import datetime
import json

import pandas as pd

from core.research.pipeline import RunConfig, run
from core.research.sources import RawResponse


def _quotes(days=260):
    dates = pd.date_range("2025-01-01", periods=days, freq="B")
    close = [float(20 + day) for day in range(days)]
    return pd.DataFrame(
        {
            "trade_date": dates, "stock_id": "2330", "market": "TWSE", "currency": "TWD",
            "raw_open": [value - 0.5 for value in close], "raw_high": [value + 1 for value in close],
            "raw_low": [value - 1 for value in close], "raw_close": close,
            "volume": 1_000_000, "amount": [value * 1_000_000 for value in close],
            "transaction_count": 1, "liquidity_basis": "official_amount",
        }
    )


def _config(tmp_path, quotes):
    return RunConfig(
        run_id="fixture_run", generated_at="2026-07-31T00:00:00Z", adjustment_as_of=datetime(2026, 7, 31),
        requested_start="2025-01-01", requested_end="2025-12-31", output_dir=tmp_path, quotes=quotes,
        actions=pd.DataFrame(columns=["ex_date", "stock_id", "pre_ex_close", "event_factor"]),
    )


def test_pipeline_writes_partitioned_artifacts_and_manifest_from_in_memory_inputs(tmp_path):
    result = run(_config(tmp_path, _quotes()))

    assert result.status == "success"
    assert (tmp_path / "validation_report.csv").exists()
    assert (tmp_path / "universe_counts.csv").exists()
    assert (tmp_path / "values" / "momentum_20d" / "2025.csv").exists()
    assert (tmp_path / "qa" / "values_raw" / "momentum_20d" / "2025.csv").exists()
    assert json.loads((tmp_path / "run_manifest.json").read_text(encoding="utf-8"))["market_scope"] == "TWSE"


def test_fatal_contract_result_writes_report_but_no_factor_artifacts(tmp_path):
    quotes = _quotes().drop(columns="raw_close")

    result = run(_config(tmp_path, quotes))

    assert result.status == "failed"
    assert (tmp_path / "validation_report.csv").exists()
    assert not (tmp_path / "values").exists()
    assert json.loads((tmp_path / "run_manifest.json").read_text(encoding="utf-8"))["status"] == "failed"


def test_pipeline_loads_twse_raw_response_when_quotes_are_not_supplied(tmp_path, monkeypatch):
    from core.research import pipeline

    table = {"title": "每日收盤行情", "fields": list(pipeline.twse.REQUIRED_CLOSING_FIELDS), "data": [["2330", "100", "2", "3000", "10", "12", "9", "11"]]}
    response = RawResponse("twse_rwd", "MI_INDEX", {}, datetime(2026, 7, 31), None, {"stat": "OK", "tables": [table]}, None)
    monkeypatch.setattr(pipeline.twse, "fetch_daily_quotes", lambda *_args, **_kwargs: response)
    config = _config(tmp_path, None)

    result = run(config)

    assert result.status == "success"


def test_same_inputs_and_run_id_produce_equal_artifact_schema(tmp_path):
    first, second = tmp_path / "first", tmp_path / "second"
    config = _config(first, _quotes())

    run(config)
    run(RunConfig(**{**config.__dict__, "output_dir": second}))

    assert pd.read_csv(first / "values" / "momentum_20d" / "2025.csv").columns.tolist() == pd.read_csv(second / "values" / "momentum_20d" / "2025.csv").columns.tolist()


def test_factor_values_carry_canonical_columns_alongside_legacy_columns(tmp_path):
    run(_config(tmp_path, _quotes()))

    written = pd.read_csv(tmp_path / "values" / "momentum_20d" / "2025.csv")

    assert (written["asof_date"] == written["trade_date"]).all()
    assert (written["asset_id"] == written["stock_id"]).all()
    assert (written["factor_id"] == written["factor_name"]).all()
    assert written["raw_value"].equals(written["value"])
    assert not written.duplicated(["asof_date", "asset_id", "factor_id", "factor_version"]).any()
    assert not written.isin([float("inf"), float("-inf")]).any().any()


def test_fallback_is_observable_in_coverage_quote_lineage_and_manifest(tmp_path):
    quotes = _quotes()
    quotes["is_fallback"] = True
    quotes["fallback_reason"] = "official source unavailable"
    config = RunConfig(**{**_config(tmp_path, quotes).__dict__, "source_coverage": ({"trade_date": "2025-01-01", "classification": "SOURCE_ERROR", "code": "W011_source_error", "severity": "WARN", "detail": "timeout"},)})

    run(config)

    assert "SOURCE_ERROR" in (tmp_path / "source_coverage.csv").read_text(encoding="utf-8")
    assert quotes["is_fallback"].all()
    assert json.loads((tmp_path / "run_manifest.json").read_text(encoding="utf-8"))["warning_counts"]["W008_fallback_used"] == 1
