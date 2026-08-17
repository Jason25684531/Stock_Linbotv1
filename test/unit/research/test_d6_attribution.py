import json
import importlib.util
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from core.research.attribution import (
    BenchmarkUnavailableError,
    hac_maxlags,
    load_benchmark_returns,
    run_attribution,
)


def test_missing_benchmark_cache_is_rejected(tmp_path):
    with pytest.raises(BenchmarkUnavailableError):
        load_benchmark_returns(tmp_path, symbol="2330")


def _write_mi_index(dir_path, trade_date, code_to_close):
    fields = ["證券代號", "成交股數", "成交筆數", "成交金額", "開盤價", "最高價", "最低價", "收盤價", "漲跌(+/-)", "漲跌價差"]
    data = [[code, "0", "0", "0", "0", "0", "0", str(close), "", ""] for code, close in code_to_close.items()]
    payload = {"stat": "OK", "tables": [{"title": "每日收盤行情", "fields": fields, "data": data}]}
    (dir_path / f"MI_INDEX_{trade_date}.json").write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def test_benchmark_offline_reconstruction_and_provenance(tmp_path):
    _write_mi_index(tmp_path, "20230103", {"2330": "100.0"})
    _write_mi_index(tmp_path, "20230104", {"2330": "102.0"})
    _write_mi_index(tmp_path, "20230105", {"2330": "101.0"})

    returns, provenance = load_benchmark_returns(tmp_path, symbol="2330")

    assert len(returns) == 2
    assert provenance["benchmark_id"] == "2330"
    assert provenance["frequency"] == "daily"
    assert provenance["sha256"]


def test_hac_lag_rule_is_deterministic_and_matches_formula():
    assert hac_maxlags(100) == 4
    assert hac_maxlags(300) == int(4 * (300 / 100) ** (2 / 9))


def test_hac_lag_rejects_non_positive_observations():
    with pytest.raises(ValueError):
        hac_maxlags(0)


def test_attribution_recovers_known_alpha_beta():
    rng = np.random.default_rng(3)
    n = 400
    benchmark = pd.Series(rng.normal(0.0003, 0.01, n), index=pd.date_range("2023-01-01", periods=n))
    true_alpha, true_beta = 0.0005, 1.2
    noise = rng.normal(0, 0.002, n)
    strategy = true_alpha + true_beta * benchmark + noise

    result = run_attribution("cfg", strategy, benchmark)

    assert result["n_obs"] == n
    assert abs(result["beta"] - true_beta) < 0.1
    assert abs(result["alpha"] - true_alpha) < 0.001
    assert result["hac_maxlags"] == hac_maxlags(n)
    assert 0.0 <= result["r_squared"] <= 1.0


def test_attribution_requires_minimum_observations():
    short = pd.Series([0.01, 0.02, -0.01], index=pd.date_range("2023-01-01", periods=3))
    with pytest.raises(ValueError, match="insufficient"):
        run_attribution("cfg", short, short)


def test_canonical_run_stops_when_benchmark_missing(tmp_path):
    job_path = Path(__file__).parents[3] / "jobs" / "run_portfolio_validation.py"
    spec = importlib.util.spec_from_file_location("run_portfolio_validation", job_path)
    job = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(job)
    output = job.run(
        day2_dir=tmp_path / "missing-day2",
        dataset_path=tmp_path / "missing-dataset",
        raw_cache_dir=tmp_path / "empty-cache",
        output_dir=tmp_path / "stopped-run",
    )
    manifest = json.loads((output / "run_manifest.json").read_text(encoding="utf-8"))
    assert manifest["final_status"] == "STOPPED"
    assert manifest["stop_reason"] == "BENCHMARK_UNAVAILABLE"


def test_reproducibility_comparison_counts_exact_matches(tmp_path):
    job_path = Path(__file__).parents[3] / "jobs" / "run_portfolio_validation.py"
    spec = importlib.util.spec_from_file_location("run_portfolio_validation_repro", job_path)
    job = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(job)
    reference, repro = tmp_path / "reference", tmp_path / "repro"
    reference.mkdir()
    repro.mkdir()
    (reference / "value.csv").write_text("value\n1\n", encoding="utf-8")
    (repro / "value.csv").write_text("value\n1\n", encoding="utf-8")
    (reference / "run_manifest.json").write_text(json.dumps({"artifact_sha256": job._artifact_hashes(reference)}), encoding="utf-8")
    comparison = job._compare_reproducibility(reference, repro)
    assert comparison == {"status": "PASS", "compared_artifact_count": 1, "exact_match_count": 1, "mismatch_count": 0}
