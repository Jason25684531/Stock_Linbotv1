from __future__ import annotations

import json
import hashlib
from pathlib import Path

from core.runtime import fundamental_production as production
from core.runtime import fundamental_shadow as shadow
from core.runtime.contracts import RuntimeStatus
from core.runtime.fundamental_adapter import FundamentalRuntimeAdapter


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


def test_composed_adapter_matches_direct_frozen_identity_and_shadow(tmp_path, monkeypatch):
    monkeypatch.setenv("FUNDAMENTAL_STRATEGY_ENABLED", "true")
    monkeypatch.setenv("FUNDAMENTAL_STRATEGY_SHADOW_ONLY", "true")
    adapter = FundamentalRuntimeAdapter()
    direct_identity = shadow.FrozenStrategyLoader().load()
    direct_shadow = shadow.run_shadow("2026-09-17", output_root=tmp_path, require_enabled=False)
    adapted_identity = adapter.frozen_identity()
    adapted_shadow = adapter.shadow_result("2026-09-17", output_root=tmp_path, require_enabled=False)

    assert adapted_identity.fingerprint == direct_identity.fingerprint
    assert adapted_identity.strategy_id == direct_identity.payload["strategy_id"]
    assert adapted_shadow.status is RuntimeStatus.BLOCKED
    assert adapted_shadow.run_id == direct_shadow["run_id"]
    assert adapted_shadow.action == direct_shadow["action"]
    assert adapted_shadow.reason_codes == tuple(direct_shadow["warnings"])
    assert adapter.BROKER_ORDER_SUBMISSION == "DISABLED"


def test_composed_adapter_matches_direct_parity_result(tmp_path, monkeypatch):
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
    direct = report["parity"]
    result = FundamentalRuntimeAdapter().parity_result(output_root=tmp_path)
    assert result.status is RuntimeStatus.PASS
    assert result.mismatches == ()
    assert result.rtol == direct["rtol"]
    assert result.atol == direct["atol"]


def test_composed_adapter_matches_direct_promotion_and_gate(tmp_path):
    root = _copy_evidence(tmp_path)
    promotion_payload = {
        "strategy_id": shadow.RUNTIME_STRATEGY_ID,
        "strategy_fingerprint": shadow.FINGERPRINT,
        "PRODUCTION_PROMOTION_STATUS": "SHADOW_APPROVED",
        "PRODUCTION_READY": "NO",
    }
    (root / "promotion_review.json").write_text(json.dumps(promotion_payload), encoding="utf-8")
    direct_promotion = _read(root / "promotion_review.json")
    adapter = FundamentalRuntimeAdapter()
    adapted_promotion = adapter.promotion_decision(output_root=root)
    assert adapted_promotion.level == direct_promotion["PRODUCTION_PROMOTION_STATUS"]
    assert adapted_promotion.status is RuntimeStatus.PASS

    direct_gate = production.check_production_eligibility(evidence_root=root, enable_production=True)
    adapted_gate = adapter.gate_result(evidence_root=root, enable_production=True)
    assert adapted_gate.reason_codes == (direct_gate.reason,)
    assert adapted_gate.status is RuntimeStatus.BLOCKED


def test_composed_adapter_matches_direct_success_eligibility_and_no_broker(tmp_path):
    root = _copy_evidence(tmp_path)
    fresh = _read(root / "FreshOOSAvailabilityReport.json")
    fresh.update({"status": "PASS", "trading_days": 180, "calendar_months": 9, "rebalance_count": 3})
    (root / "FreshOOSAvailabilityReport.json").write_text(json.dumps(fresh), encoding="utf-8")
    promotion = _read(root / "promotion_review.json")
    promotion.update({"PRODUCTION_PROMOTION_STATUS": "PRODUCTION_CANDIDATE", "PRODUCTION_READY": "YES"})
    (root / "promotion_review.json").write_text(json.dumps(promotion), encoding="utf-8")
    _refresh_promotion_identity(root)

    direct = production.check_production_eligibility(evidence_root=root, enable_production=True)
    adapted = FundamentalRuntimeAdapter().production_eligibility(evidence_root=root, enable_production=True)
    assert direct.eligible and adapted.eligible
    assert adapted.status is RuntimeStatus.PASS
    assert adapted.details["broker_order_submission"] == "DISABLED"


def test_composed_adapter_delegates_application_production_result(monkeypatch, tmp_path):
    expected = {
        "status": {"production_eligibility": "BLOCKED", "block_reason": "INSUFFICIENT_OOS"},
        "rows": object(),
        "BROKER_ORDER_SUBMISSION": "DISABLED",
    }
    calls = []

    def direct(asof_date, **kwargs):
        calls.append((asof_date, kwargs))
        return expected

    monkeypatch.setattr(production, "evaluate_production", direct)
    result = FundamentalRuntimeAdapter().evaluate_production(
        "2026-10-06",
        output_root=tmp_path,
        evidence_root=tmp_path,
        enable_production=False,
        write_status=False,
    )

    assert result is expected
    assert calls == [
        (
            "2026-10-06",
            {
                "output_root": tmp_path,
                "evidence_root": tmp_path,
                "enable_production": False,
                "write_status": False,
            },
        )
    ]
    assert result["BROKER_ORDER_SUBMISSION"] == "DISABLED"
