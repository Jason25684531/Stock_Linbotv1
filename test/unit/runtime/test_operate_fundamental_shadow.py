from __future__ import annotations

import json

import pandas as pd

from core.runtime import fresh_oos_promotion as oos
from core.runtime import fundamental_shadow as shadow


def test_shadow_run_id_is_deterministic_and_reusable(tmp_path, monkeypatch):
    monkeypatch.setenv("FUNDAMENTAL_STRATEGY_ENABLED", "true")
    monkeypatch.setenv("FUNDAMENTAL_STRATEGY_SHADOW_ONLY", "true")
    first = shadow.run_shadow("2026-09-17", output_root=tmp_path)
    second = shadow.run_shadow("2026-09-17", output_root=tmp_path)
    assert first["run_id"] == "2026-09-17_cb7c0d88e585"
    assert second["run_id"] == first["run_id"]
    assert second["run_timestamp"] == first["run_timestamp"]


def test_ledger_schema_has_required_oos_identity_fields():
    required = {
        "date", "strategy_id", "strategy_fingerprint", "PIT_snapshot_id", "shadow_run_id",
        "rebalance_flag", "eligible_assets", "G2_valid_assets", "G3_valid_assets",
        "both_factor_valid_assets", "factor_coverage", "selected_assets_hash", "target_weights_hash",
        "gross_return", "net_return", "equity", "turnover", "cost", "PIT_status", "oos_eligibility", "reason",
    }
    assert required.issubset(oos.LEDGER_COLUMNS)


def test_availability_uses_ledger_rebalance_flags_not_date_stride():
    ledger = pd.DataFrame({
        "date": pd.date_range("2026-07-29", periods=180, freq="D"),
        "oos_eligibility": ["VALID"] * 180,
        "rebalance_flag": [True, False, True] + [False] * 177,
    })
    report = oos._availability_from_ledger(ledger, {"months": 9, "rebalances": 3, "trading_days": 180})
    assert report["trading_days"] == 180
    assert report["rebalance_count"] == 2
    assert report["status"] == "INSUFFICIENT_DATA"


def test_frozen_evaluation_rejects_ledger_mutation(tmp_path):
    contract = oos.load_contract()
    ledger = pd.DataFrame({
        "date": pd.date_range("2026-07-29", periods=180, freq="D"),
        "strategy_fingerprint": [shadow.FINGERPRINT] * 180,
        "strategy_id": [shadow.RUNTIME_STRATEGY_ID] * 180,
        "oos_eligibility": ["VALID"] * 180,
        "daily_return": [0.001] * 180,
        "rebalance_flag": [True, False, True] + [False] * 177,
        "turnover": [1.0, 0.0, 1.0] + [0.0] * 177,
        "cost": [0.0] * 180,
    })
    availability = {"status": "PASS", "trading_days": 180, "calendar_months": 9, "rebalance_count": 3}
    first = oos.evaluate_once(tmp_path, availability, ledger, contract)
    mutated = ledger.assign(daily_return=-0.5)
    second = oos.evaluate_once(tmp_path, availability, mutated, contract)
    assert first["status"] == "PASS"
    assert second["status"] == "INVALIDATED"


def test_blocked_branch_writes_required_diagnostics(tmp_path):
    result = oos._blocked_result(tmp_path, pd.Timestamp("2026-09-17"), "STALE_DATA")
    assert result["FRESH_OOS_STATUS"] == "INSUFFICIENT_DATA"
    assert result["BROKER_ORDER_SUBMISSION"] == "DISABLED"
    for name in ("FreshOOSAvailabilityReport.json", "fresh_oos_lineage_audit.json", "shadow_monitoring_report.json", "validation_manifest.json", "runtime_validation_report.md"):
        assert (tmp_path / name).is_file()
    report = json.loads((tmp_path / "shadow_monitoring_report.json").read_text(encoding="utf-8"))
    assert report["blocked_shadow_runs"] == 1
    assert report["data_freshness_failures"] == 1
