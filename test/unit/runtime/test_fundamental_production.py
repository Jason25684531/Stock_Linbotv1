from __future__ import annotations

import json
import hashlib
import shutil
from pathlib import Path

import pandas as pd

from core.runtime import fundamental_production as production
from core.runtime.fundamental_shadow import FINGERPRINT, RUNTIME_STRATEGY_ID
from core.strategy_manager import StrategyManager


EVIDENCE_ROOT = production.OPERATION_ROOT


def _copy_evidence(tmp_path: Path) -> Path:
    for name in (
        "runtime_validation_manifest.json",
        "promotion_review.json",
        "FreshOOSAvailabilityReport.json",
        "FundamentalRuntimeSpec.json",
        "runtime_replay_manifest.json",
    ):
        shutil.copy2(EVIDENCE_ROOT / name, tmp_path / name)
    return tmp_path


def _refresh_promotion_identity(root: Path) -> None:
    promotion = _read(root / "promotion_review.json")
    fresh = _read(root / "FreshOOSAvailabilityReport.json")
    promotion.update(
        {
            "strategy_id": RUNTIME_STRATEGY_ID,
            "strategy_fingerprint": FINGERPRINT,
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


def _read(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def test_registration_is_exact_and_legacy_listing_is_unchanged():
    registration = production.registration()
    assert registration["strategy_id"] == RUNTIME_STRATEGY_ID
    assert registration["strategy_fingerprint"] == FINGERPRINT
    assert registration["ENABLED_FOR_LIVE"] == "NO"
    assert registration["BROKER_ORDER_SUBMISSION"] == "DISABLED"
    assert RUNTIME_STRATEGY_ID in StrategyManager.runtime_strategy_registrations()
    assert len(StrategyManager().list_strategies()) == 7


def test_current_evidence_blocks_on_insufficient_oos():
    result = production.check_production_eligibility(evidence_root=EVIDENCE_ROOT, enable_production=True)
    assert result.eligible is False
    assert result.reason == "INSUFFICIENT_OOS"


def test_disabled_flag_is_checked_after_oos_gate(tmp_path):
    root = _copy_evidence(tmp_path)
    fresh = _read(root / "FreshOOSAvailabilityReport.json")
    fresh["status"] = "PASS"
    fresh["trading_days"] = 180
    fresh["calendar_months"] = 9
    fresh["rebalance_count"] = 3
    (root / "FreshOOSAvailabilityReport.json").write_text(json.dumps(fresh), encoding="utf-8")
    promotion = _read(root / "promotion_review.json")
    promotion["PRODUCTION_PROMOTION_STATUS"] = "PRODUCTION_CANDIDATE"
    promotion["PRODUCTION_READY"] = "YES"
    (root / "promotion_review.json").write_text(json.dumps(promotion), encoding="utf-8")
    _refresh_promotion_identity(root)
    result = production.check_production_eligibility(evidence_root=root, enable_production=False)
    assert result.reason == "PRODUCTION_DISABLED"


def test_identity_mismatch_blocks(tmp_path):
    root = _copy_evidence(tmp_path)
    validation = _read(root / "runtime_validation_manifest.json")
    validation["strategy_fingerprint"] = "bad"
    (root / "runtime_validation_manifest.json").write_text(json.dumps(validation), encoding="utf-8")
    result = production.check_production_eligibility(evidence_root=root, enable_production=True)
    assert result.reason == "FINGERPRINT_MISMATCH"


def test_missing_promotion_evidence_blocks(tmp_path):
    root = _copy_evidence(tmp_path)
    (root / "promotion_review.json").unlink()
    result = production.check_production_eligibility(evidence_root=root, enable_production=True)
    assert result.reason == "PROMOTION_EVIDENCE_INVALID"


def test_promotion_identity_and_artifact_hashes_are_required(tmp_path):
    root = _copy_evidence(tmp_path)
    promotion = _read(root / "promotion_review.json")
    promotion["strategy_fingerprint"] = "bad"
    (root / "promotion_review.json").write_text(json.dumps(promotion), encoding="utf-8")
    result = production.check_production_eligibility(evidence_root=root, enable_production=True)
    assert result.reason == "PROMOTION_EVIDENCE_INVALID"


def test_normalize_recommendations_preserves_canonical_fields():
    rows = production.normalize_recommendations(
        pd.DataFrame(
            {
                "stock_id": ["2330"],
                "score": [0.9],
                "rank": [1],
                "selected": [True],
                "target_weight": [1.0],
                "reason": ["SELECTED"],
                "entry_price": [700.0],
            }
        ),
        asof_date="2026-07-27",
    )
    assert rows.loc[0, "strategy"] == RUNTIME_STRATEGY_ID
    assert rows.loc[0, "strategy_id"] == RUNTIME_STRATEGY_ID
    assert rows.loc[0, "ai_score"] == 0.9
    assert rows.loc[0, "close_price"] == 700.0


def test_blocked_evaluation_writes_zero_status(tmp_path):
    result = production.evaluate_production(
        "2026-09-17",
        output_root=tmp_path,
        evidence_root=EVIDENCE_ROOT,
        enable_production=True,
    )
    assert result["rows"].empty
    status = _read(tmp_path / production.STATUS_FILENAME)
    assert status["production_eligibility"] == "BLOCKED"
    assert status["block_reason"] == "INSUFFICIENT_OOS"
    assert status["recommendation_count"] == 0
    assert status["broker_submission_status"] == "DISABLED"


def test_synthetic_future_pass_produces_runtime_rows_without_broker(tmp_path):
    root = _copy_evidence(tmp_path)
    fresh = _read(root / "FreshOOSAvailabilityReport.json")
    fresh.update({"status": "PASS", "trading_days": 180, "calendar_months": 9, "rebalance_count": 3})
    (root / "FreshOOSAvailabilityReport.json").write_text(json.dumps(fresh), encoding="utf-8")
    promotion = _read(root / "promotion_review.json")
    promotion.update({"PRODUCTION_PROMOTION_STATUS": "PRODUCTION_CANDIDATE", "PRODUCTION_READY": "YES"})
    (root / "promotion_review.json").write_text(json.dumps(promotion), encoding="utf-8")
    _refresh_promotion_identity(root)
    result = production.evaluate_production(
        "2026-07-27",
        output_root=root,
        evidence_root=root,
        enable_production=True,
    )
    assert result["BROKER_ORDER_SUBMISSION"] == "DISABLED"
    assert result["status"]["production_eligibility"] == "PASS"
    assert result["status"]["recommendation_count"] == 5
    assert set(("stock_id", "score", "rank", "selected", "target_weight")).issubset(result["rows"].columns)
