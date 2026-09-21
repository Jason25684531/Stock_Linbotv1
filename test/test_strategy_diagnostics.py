import hashlib
import json
from pathlib import Path
import subprocess
import sys

from core.diagnostics import DiagnosticsService
from core.diagnostics.service import CONFLICT, FUNDAMENTAL_ID, MISSING


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_data_source_inventory_and_quality_are_read_only():
    service = DiagnosticsService()
    manifest = service.runtime_root / "runtime_validation_manifest.json"
    before = _digest(manifest)
    sources = service.data_sources()
    assert {item["source_id"] for item in sources["items"]} >= {"twse_ohlcv", "mops_financial", "pit_snapshot"}
    assert service.data_quality()["PIT_INTEGRITY"] == "PASS"
    assert _digest(manifest) == before


def test_factor_catalog_preserves_authoritative_verdicts():
    factors = {item["factor_id"]: item for item in DiagnosticsService().factors()["items"]}
    assert factors["G2_OPERATING_INCOME_YOY"]["validation_status"] == "ACCEPT"
    assert factors["Q1_OPERATING_MARGIN"]["validation_status"] == "REVIEW"
    assert factors["G1_REVENUE_YOY"]["validation_status"] == "REJECT"
    assert FUNDAMENTAL_ID in factors["G3_EPS_YOY"]["used_by_strategies"]


def test_strategy_and_oos_use_frozen_and_current_runtime_evidence():
    service = DiagnosticsService()
    strategy = service.strategy(FUNDAMENTAL_ID)
    assert strategy["factors"] == ["G2_OPERATING_INCOME_YOY", "G3_EPS_YOY"]
    assert strategy["factor_weights"] == {"G2_OPERATING_INCOME_YOY": 0.5, "G3_EPS_YOY": 0.5}
    assert strategy["top_n"] == 5 and strategy["rebalance"] == "60D"
    assert strategy["stage"] == "SHADOW_APPROVED"
    assert strategy["production_status"]["eligibility"] == "BLOCKED"
    assert service.oos()["items"][0]["status"] == "INSUFFICIENT_DATA"
    assert service.strategy("v31_hybrid")["strategy_id"] == "hybrid_trend_rank"
    assert len([item for item in service.strategies()["items"] if item["strategy_id"] in {"hybrid_trend_rank", "defensive_low_volatility", "growth_momentum_breakout", "quality_growth", "institutional_flow_confirmation", "mean_reversion", "quality_value_low_volatility"}]) == 7


def test_source_priority_conflict_and_missing_contracts():
    service = DiagnosticsService()
    assert service.resolve_sources([])["status"] == MISSING
    assert service.resolve_sources([{"priority": 1, "value": "frozen", "source": "frozen"}, {"priority": 2, "value": "runtime", "source": "runtime"}])["value"] == "frozen"
    assert service.resolve_sources([{"priority": 1, "value": "a", "source": "one"}, {"priority": 1, "value": "b", "source": "two"}])["status"] == CONFLICT
    assert service.factor("unknown")["status"] == MISSING


def test_pipeline_validation_and_health_are_aggregations_not_execution():
    service = DiagnosticsService()
    pipelines = {item["pipeline"]: item for item in service.pipelines()["items"]}
    assert pipelines["pit_builder"]["entrypoint"] == "jobs/run_fundamental_pit_v3.py"
    assert service.validation()["FUTURE_LEAKAGE"] == 0
    assert service.health()["BROKER"] == "DISABLED"


def test_json_is_deterministic_and_report_only_writes_snapshot():
    service = DiagnosticsService()
    status = service.status()
    assert json.dumps(status, sort_keys=True) == json.dumps(status, sort_keys=True)
    frozen = service.strategy_root / "FrozenStrategySpec.json"
    before = _digest(frozen)
    report = service.report()
    assert report["status"] == "PASS"
    assert (service.root / report["json"]).exists()
    assert _digest(frozen) == before


def test_cli_json_contract_is_machine_readable():
    root = Path(__file__).resolve().parents[1]
    result = subprocess.run([sys.executable, "jobs/strategy_ops.py", "status", "--json"], cwd=root, check=True, capture_output=True, text=True)
    assert json.loads(result.stdout)["fundamental_g2_g3"]["production"] == "BLOCKED"
