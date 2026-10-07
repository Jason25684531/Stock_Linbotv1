from __future__ import annotations

import json
import hashlib
from pathlib import Path

import pytest

from core.runtime import fundamental_production as production
from core.runtime import fundamental_shadow as shadow
from core.runtime.contracts import RuntimeStatus
from core.runtime.fundamental_adapter import FundamentalShadowAdapter


FIXTURE = Path(__file__).parents[2] / "fixtures" / "baseline" / "fundamental_production_evidence_2026-09-17.json"


def _read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def _copy_evidence(tmp_path):
    fixture = _read(FIXTURE)
    for name in (
        "runtime_validation_manifest.json",
        "promotion_review.json",
        "FreshOOSAvailabilityReport.json",
        "FundamentalRuntimeSpec.json",
        "runtime_replay_manifest.json",
    ):
        (tmp_path / name).write_text(json.dumps(fixture[name]), encoding="utf-8")
    _refresh_promotion_identity(tmp_path)
    return tmp_path


def _refresh_promotion_identity(root):
    promotion = _read(root / "promotion_review.json")
    fresh = _read(root / "FreshOOSAvailabilityReport.json")
    promotion.update(
        {
            "strategy_id": shadow.RUNTIME_STRATEGY_ID,
            "strategy_fingerprint": shadow.FINGERPRINT,
            "fresh_oos_status": fresh["status"],
            "evaluation_window": {
                "cutoff": "2026-07-28",
                "fresh_start": fresh["fresh_start"],
                "latest_available_date": fresh["latest_available_date"],
                "trading_days": fresh["trading_days"],
                "calendar_months": fresh["calendar_months"],
                "rebalance_count": fresh["rebalance_count"],
            },
            "artifact_identity": {
                "fundamental_runtime_spec_sha256": hashlib.sha256((root / "FundamentalRuntimeSpec.json").read_bytes()).hexdigest(),
                "fresh_oos_availability_sha256": hashlib.sha256((root / "FreshOOSAvailabilityReport.json").read_bytes()).hexdigest(),
                "runtime_replay_manifest_sha256": hashlib.sha256((root / "runtime_replay_manifest.json").read_bytes()).hexdigest(),
            },
        }
    )
    (root / "promotion_review.json").write_text(json.dumps(promotion), encoding="utf-8")


def test_shadow_adapter_delegates_existing_run_and_preserves_broker_boundary(tmp_path, monkeypatch):
    monkeypatch.setenv("FUNDAMENTAL_STRATEGY_ENABLED", "true")
    monkeypatch.setenv("FUNDAMENTAL_STRATEGY_SHADOW_ONLY", "true")
    direct = shadow.run_shadow("2026-09-17", output_root=tmp_path, require_enabled=False)
    adapted = FundamentalShadowAdapter().shadow_result(
        "2026-09-17", output_root=tmp_path, require_enabled=False
    )

    assert adapted.status is RuntimeStatus.BLOCKED
    assert adapted.run_id == direct["run_id"]
    assert adapted.asof_date == direct["asof_date"]
    assert adapted.action == direct["action"]
    assert adapted.strategy_id == direct["strategy_id"]
    assert adapted.reason_codes == tuple(direct["warnings"])
    assert adapted.details["BROKER_ORDER_SUBMISSION"] == "DISABLED"
    assert adapted.selection is not None


def test_parity_adapter_preserves_direct_report_fields_and_tolerance(tmp_path, monkeypatch):
    report = {
        "parity": {
            "SELECTION_PARITY": "PASS",
            "SCORE_PARITY": "PASS",
            "RANK_PARITY": "PASS",
            "TARGET_WEIGHT_PARITY": "PASS",
            "HISTORICAL_SELECTION_DRIFT": 0,
            "rtol": 1e-9,
            "atol": 1e-12,
        },
        "replay": {"status": "PASS"},
    }
    monkeypatch.setattr(shadow, "build_validation_artifacts", lambda *args, **kwargs: report)
    result = FundamentalShadowAdapter().parity_result(output_root=tmp_path)
    assert result.status is RuntimeStatus.PASS
    assert result.compared_fields == ("stock_id", "score", "rank", "selected", "target_weight", "reason")
    assert (result.rtol, result.atol) == (1e-9, 1e-12)
    assert result.mismatches == ()


def test_parity_adapter_preserves_direct_mismatch_verdict(tmp_path, monkeypatch):
    report = {
        "parity": {
            "SELECTION_PARITY": "FAIL",
            "SCORE_PARITY": "PASS",
            "RANK_PARITY": "PASS",
            "TARGET_WEIGHT_PARITY": "PASS",
            "HISTORICAL_SELECTION_DRIFT": 0,
            "rtol": 1e-9,
            "atol": 1e-12,
        },
        "replay": {"status": "FAIL"},
    }
    monkeypatch.setattr(shadow, "build_validation_artifacts", lambda *args, **kwargs: report)
    result = FundamentalShadowAdapter().parity_result(output_root=tmp_path)
    assert result.status is RuntimeStatus.FAIL
    assert result.mismatches == ("SELECTION_PARITY",)


def test_gate_adapter_preserves_direct_reason_and_precedence(monkeypatch, tmp_path):
    direct = production.GateResult(False, "HISTORICAL_DRIFT", {"source": "fixture"})
    monkeypatch.setattr(production, "check_production_eligibility", lambda **_: direct)
    result = FundamentalShadowAdapter().gate_result(evidence_root=tmp_path)
    assert result.status is RuntimeStatus.BLOCKED
    assert result.reason_codes == ("HISTORICAL_DRIFT",)
    assert result.gate_id == "fundamental_production_eligibility"
    assert result.details["broker_order_submission"] == "DISABLED"


def test_gate_adapter_matches_direct_precedence_for_identity_and_oos(tmp_path):
    root = _copy_evidence(tmp_path)
    adapter = FundamentalShadowAdapter()

    direct = production.check_production_eligibility(evidence_root=root, enable_production=True)
    adapted = adapter.gate_result(evidence_root=root, enable_production=True)
    assert direct.reason == "INSUFFICIENT_OOS"
    assert adapted.reason_codes == (direct.reason,)

    validation = _read(root / "runtime_validation_manifest.json")
    validation["strategy_fingerprint"] = "bad"
    (root / "runtime_validation_manifest.json").write_text(json.dumps(validation), encoding="utf-8")
    direct = production.check_production_eligibility(evidence_root=root, enable_production=True)
    adapted = adapter.gate_result(evidence_root=root, enable_production=True)
    assert direct.reason == "FINGERPRINT_MISMATCH"
    assert adapted.reason_codes == (direct.reason,)


def test_gate_adapter_matches_direct_success_and_keeps_broker_disabled(tmp_path):
    root = _copy_evidence(tmp_path)
    fresh = _read(root / "FreshOOSAvailabilityReport.json")
    fresh.update({"status": "PASS", "trading_days": 180, "calendar_months": 9, "rebalance_count": 3})
    (root / "FreshOOSAvailabilityReport.json").write_text(json.dumps(fresh), encoding="utf-8")
    promotion = _read(root / "promotion_review.json")
    promotion.update({"PRODUCTION_PROMOTION_STATUS": "PRODUCTION_CANDIDATE", "PRODUCTION_READY": "YES"})
    (root / "promotion_review.json").write_text(json.dumps(promotion), encoding="utf-8")
    _refresh_promotion_identity(root)

    direct = production.check_production_eligibility(evidence_root=root, enable_production=True)
    adapted = FundamentalShadowAdapter().production_eligibility(evidence_root=root, enable_production=True)
    assert direct.eligible
    assert adapted.eligible
    assert adapted.status is RuntimeStatus.PASS
    assert adapted.details["broker_order_submission"] == "DISABLED"


def test_gate_adapter_preserves_success_without_activation(monkeypatch, tmp_path):
    direct = production.GateResult(True, None, {"source": "fixture"})
    monkeypatch.setattr(production, "check_production_eligibility", lambda **_: direct)
    result = FundamentalShadowAdapter().production_eligibility(evidence_root=tmp_path, enable_production=True)
    assert result.status is RuntimeStatus.PASS
    assert result.eligible
    assert result.details["broker_order_submission"] == "DISABLED"
    assert not (tmp_path / "fundamental_production_status.json").exists()


def test_promotion_adapter_reads_existing_artifact_without_writing(tmp_path):
    payload = {
        "strategy_id": shadow.RUNTIME_STRATEGY_ID,
        "PRODUCTION_PROMOTION_STATUS": "SHADOW_APPROVED",
        "PRODUCTION_READY": "NO",
    }
    (tmp_path / "promotion_review.json").write_text(json.dumps(payload), encoding="utf-8")
    before = set(path.name for path in tmp_path.iterdir())
    result = FundamentalShadowAdapter().promotion_decision(output_root=tmp_path)
    after = set(path.name for path in tmp_path.iterdir())
    assert result.status is RuntimeStatus.PASS
    assert result.level == "SHADOW_APPROVED"
    assert before == after
