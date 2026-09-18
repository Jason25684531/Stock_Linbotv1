"""Fail-closed production boundary for the frozen Fundamental strategy.

This module only wires existing evidence and the canonical Fundamental runtime
into the application's recommendation contract. It does not implement factor
formulas, ranking, Top5 selection, or broker execution.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
from typing import Any, Mapping

import pandas as pd

from . import fundamental_shadow as shadow


STRATEGY_ID = shadow.RUNTIME_STRATEGY_ID
STRATEGY_FINGERPRINT = shadow.FINGERPRINT
BROKER_ORDER_SUBMISSION = "DISABLED"
PRODUCTION_FLAG = "ENABLE_FUNDAMENTAL_PRODUCTION"
OPERATION_ROOT = shadow.REPO_ROOT / "outputs" / "fundamental_runtime_shadow" / "operate-fundamental-shadow-until-oos-ready-v1"
STATUS_FILENAME = "fundamental_production_status.json"
REQUIRED_RUNTIME_GATES = (
    "PIT_INTEGRITY",
    "DATA_FRESHNESS_GATE",
    "FACTOR_HEALTH_GATE",
    "RESEARCH_RUNTIME_PARITY",
    "RUNTIME_REPLAY",
)
FRESH_OOS_MINIMUMS = {"trading_days": 180, "months": 9, "rebalances": 3}


@dataclass(frozen=True)
class GateResult:
    eligible: bool
    reason: str | None
    evidence: Mapping[str, Any]


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def _sha256(path: Path) -> str | None:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return None


def _flag(value: object | None = None) -> bool:
    if value is None:
        value = os.getenv(PRODUCTION_FLAG)
    if value is None:
        return False
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def registration() -> dict[str, object]:
    """Return the immutable registry record without enabling production."""

    return {
        "strategy_id": STRATEGY_ID,
        "strategy_fingerprint": STRATEGY_FINGERPRINT,
        "frozen_spec_path": str(shadow.FROZEN_SPEC_PATH.relative_to(shadow.REPO_ROOT)),
        "ENABLED_FOR_SHADOW": "YES",
        "ENABLED_FOR_LIVE": "NO",
        "production_feature_flag": PRODUCTION_FLAG,
        "production_feature_flag_default": False,
        "BROKER_ORDER_SUBMISSION": BROKER_ORDER_SUBMISSION,
    }


def _evidence(root: Path) -> tuple[dict[str, Any], str | None]:
    validation = _read_json(root / "runtime_validation_manifest.json")
    promotion = _read_json(root / "promotion_review.json")
    fresh = _read_json(root / "FreshOOSAvailabilityReport.json")
    runtime_spec = _read_json(root / "FundamentalRuntimeSpec.json")
    if not all((validation, promotion, fresh, runtime_spec)):
        return {}, "PROMOTION_EVIDENCE_INVALID"

    if validation.get("strategy_id") != STRATEGY_ID or validation.get("strategy_fingerprint") != STRATEGY_FINGERPRINT:
        return {}, "FINGERPRINT_MISMATCH"
    if runtime_spec.get("strategy_id") != STRATEGY_ID or runtime_spec.get("strategy_fingerprint") != STRATEGY_FINGERPRINT:
        return {}, "PROMOTION_EVIDENCE_INVALID"
    if promotion.get("strategy_id") != STRATEGY_ID or promotion.get("strategy_fingerprint") != STRATEGY_FINGERPRINT:
        return {}, "PROMOTION_EVIDENCE_INVALID"
    if validation.get("BROKER_ORDER_SUBMISSION") != BROKER_ORDER_SUBMISSION:
        return {}, "PROMOTION_EVIDENCE_INVALID"
    if validation.get("HISTORICAL_SELECTION_DRIFT") not in (0, "0"):
        return {}, "HISTORICAL_DRIFT"
    failed = [name for name in REQUIRED_RUNTIME_GATES if validation.get(name) != "PASS"]
    if failed:
        return {}, failed[0]

    minimum = fresh.get("minimum_requirement") or {}
    if minimum != FRESH_OOS_MINIMUMS:
        return {}, "PROMOTION_EVIDENCE_INVALID"
    status = str(fresh.get("status") or "INSUFFICIENT_DATA")
    if promotion.get("fresh_oos_status") != status or promotion.get("BROKER_ORDER_SUBMISSION") != BROKER_ORDER_SUBMISSION:
        return {}, "PROMOTION_EVIDENCE_INVALID"
    window = promotion.get("evaluation_window")
    if not isinstance(window, Mapping):
        return {}, "PROMOTION_EVIDENCE_INVALID"
    expected_window = {
        "cutoff": shadow.CUTOFF.date().isoformat(),
        "fresh_start": fresh.get("fresh_start"),
        "latest_available_date": fresh.get("latest_available_date"),
        "trading_days": int(fresh.get("trading_days") or 0),
        "calendar_months": int(fresh.get("calendar_months") or 0),
        "rebalance_count": int(fresh.get("rebalance_count") or 0),
    }
    if dict(window) != expected_window:
        return {}, "PROMOTION_EVIDENCE_INVALID"
    artifact_identity = promotion.get("artifact_identity")
    expected_artifacts = {
        "fundamental_runtime_spec_sha256": _sha256(root / "FundamentalRuntimeSpec.json"),
        "fresh_oos_availability_sha256": _sha256(root / "FreshOOSAvailabilityReport.json"),
        "runtime_replay_manifest_sha256": _sha256(root / "runtime_replay_manifest.json"),
    }
    if not isinstance(artifact_identity, Mapping) or any(
        not expected_hash or artifact_identity.get(name) != expected_hash
        for name, expected_hash in expected_artifacts.items()
    ):
        return {}, "PROMOTION_EVIDENCE_INVALID"
    availability = {
        "trading_days": int(fresh.get("trading_days") or 0),
        "months": int(fresh.get("calendar_months") or 0),
        "rebalances": int(fresh.get("rebalance_count") or 0),
        "status": status,
        "minimum_trading_days": FRESH_OOS_MINIMUMS["trading_days"],
        "minimum_months": FRESH_OOS_MINIMUMS["months"],
        "minimum_rebalances": FRESH_OOS_MINIMUMS["rebalances"],
    }
    artifact_hashes = {
        "runtime_validation_manifest": _sha256(root / "runtime_validation_manifest.json"),
        "promotion_review": _sha256(root / "promotion_review.json"),
        "fresh_oos_availability": _sha256(root / "FreshOOSAvailabilityReport.json"),
        "fundamental_runtime_spec": _sha256(root / "FundamentalRuntimeSpec.json"),
    }
    return {
        "validation": validation,
        "promotion": promotion,
        "fresh_oos": availability,
        "artifact_hashes": artifact_hashes,
    }, None


def check_production_eligibility(
    *,
    evidence_root: Path = OPERATION_ROOT,
    enable_production: object | None = None,
) -> GateResult:
    """Evaluate the immutable production gate in deterministic priority order."""

    evidence, error = _evidence(Path(evidence_root))
    if error:
        return GateResult(False, error, evidence)

    fresh = evidence["fresh_oos"]
    if (
        fresh["status"] != "PASS"
        or fresh["trading_days"] < fresh["minimum_trading_days"]
        or fresh["months"] < fresh["minimum_months"]
        or fresh["rebalances"] < fresh["minimum_rebalances"]
    ):
        return GateResult(False, "INSUFFICIENT_OOS", evidence)

    promotion = evidence["promotion"]
    if promotion.get("PRODUCTION_READY") != "YES" or promotion.get("PRODUCTION_PROMOTION_STATUS") not in {
        "PRODUCTION_CANDIDATE",
        "LIMITED_CAPITAL_CANDIDATE",
    }:
        return GateResult(False, "PROMOTION_EVIDENCE_INVALID", evidence)
    if not _flag(enable_production):
        return GateResult(False, "PRODUCTION_DISABLED", evidence)
    return GateResult(True, None, evidence)


def _status_payload(
    gate: GateResult,
    *,
    asof_date: object,
    recommendation_count: int = 0,
    action: str = "BLOCKED",
    enable_production: object | None = None,
) -> dict[str, object]:
    fresh = gate.evidence.get("fresh_oos", {})
    validation = gate.evidence.get("validation", {})
    return {
        "strategy_id": STRATEGY_ID,
        "fingerprint": STRATEGY_FINGERPRINT,
        "PIT_snapshot_id": validation.get("PIT_SNAPSHOT_ID"),
        "strategy_registered": True,
        "production_enable_flag": _flag(enable_production),
        "production_eligibility": "PASS" if gate.eligible else "BLOCKED",
        "block_reason": gate.reason,
        "FRESH_OOS_TRADING_DAYS": fresh.get("trading_days"),
        "FRESH_OOS_MONTHS": fresh.get("months"),
        "FRESH_OOS_REBALANCES": fresh.get("rebalances"),
        "FRESH_OOS_STATUS": fresh.get("status"),
        "promotion_status": (gate.evidence.get("promotion") or {}).get("PRODUCTION_PROMOTION_STATUS"),
        "production_ready": (gate.evidence.get("promotion") or {}).get("PRODUCTION_READY", "NO"),
        "data_freshness": validation.get("DATA_FRESHNESS_GATE"),
        "factor_health": validation.get("FACTOR_HEALTH_GATE"),
        "PIT_integrity": validation.get("PIT_INTEGRITY"),
        "historical_selection_drift": validation.get("HISTORICAL_SELECTION_DRIFT"),
        "rebalance_flag": action == "REBALANCE",
        "selection_hash": None,
        "weights_hash": None,
        "recommendation_count": recommendation_count,
        "broker_submission_status": BROKER_ORDER_SUBMISSION,
        "asof_date": str(pd.Timestamp(asof_date).date()),
        "artifact_hashes": gate.evidence.get("artifact_hashes", {}),
    }


def _write_status(root: Path, payload: Mapping[str, object]) -> None:
    root.mkdir(parents=True, exist_ok=True)
    (root / STATUS_FILENAME).write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")


def normalize_recommendations(rows: pd.DataFrame, *, asof_date: object) -> pd.DataFrame:
    """Map canonical runtime rows to the shared recommendation-shaped columns."""

    columns = ["stock_id", "strategy_id", "strategy", "asof_date", "score", "rank", "selected", "target_weight", "reason", "close_price", "ai_score", "rsi", "volume", "news_boost_reason"]
    if rows is None or rows.empty:
        return pd.DataFrame(columns=columns)
    result = rows.copy()
    result["strategy_id"] = STRATEGY_ID
    result["strategy"] = STRATEGY_ID
    result["asof_date"] = pd.Timestamp(asof_date).date().isoformat()
    result["ai_score"] = pd.to_numeric(result["score"], errors="coerce")
    entry_price = result["entry_price"] if "entry_price" in result else pd.Series(0.0, index=result.index)
    result["close_price"] = pd.to_numeric(entry_price, errors="coerce").fillna(0.0)
    result["rsi"] = None
    result["volume"] = None
    result["news_boost_reason"] = result["reason"]
    return result[[column for column in columns if column in result.columns]]


def evaluate_production(
    asof_date: object,
    *,
    output_root: Path = OPERATION_ROOT,
    evidence_root: Path = OPERATION_ROOT,
    enable_production: object | None = None,
    write_status: bool = True,
) -> dict[str, object]:
    """Return status plus normalized rows; never submits broker orders."""

    asof = pd.Timestamp(asof_date).normalize()
    gate = check_production_eligibility(evidence_root=Path(evidence_root), enable_production=enable_production)
    if not gate.eligible:
        status = _status_payload(gate, asof_date=asof, enable_production=enable_production)
        if write_status:
            _write_status(Path(output_root), status)
        return {"status": status, "rows": pd.DataFrame(), "BROKER_ORDER_SUBMISSION": BROKER_ORDER_SUBMISSION}

    try:
        spec = shadow.FrozenStrategyLoader().load()
        adapter = shadow.CanonicalSelectionAdapter(spec)
        health = adapter.health(asof)
        if health.get("reason_codes"):
            blocked = GateResult(False, str(health["reason_codes"][0]), gate.evidence)
            status = _status_payload(blocked, asof_date=asof, enable_production=enable_production)
            if write_status:
                _write_status(Path(output_root), status)
            return {"status": status, "rows": pd.DataFrame(), "BROKER_ORDER_SUBMISSION": BROKER_ORDER_SUBMISSION}
        selected, action = adapter.selection(asof)
        selected = selected.loc[selected["selected"].astype(bool)].copy()
        universe = adapter.universe.loc[adapter.universe["asof_date"].eq(asof), ["asset_id", "entry_price"]].copy()
        universe["stock_id"] = universe["asset_id"].map(shadow._asset_id)
        selected = selected.merge(universe[["stock_id", "entry_price"]], on="stock_id", how="left")
        normalized = normalize_recommendations(selected, asof_date=asof)
        status = _status_payload(gate, asof_date=asof, recommendation_count=len(normalized), action=action, enable_production=enable_production)
        status["rebalance_flag"] = action == "REBALANCE"
        status["selection_hash"] = shadow._selection_hash(normalized[["stock_id", "score", "rank", "selected", "target_weight"]]) if not normalized.empty else None
        status["weights_hash"] = shadow._selection_hash(normalized[["stock_id", "target_weight"]]) if not normalized.empty else None
        if write_status:
            _write_status(Path(output_root), status)
        return {"status": status, "rows": normalized, "BROKER_ORDER_SUBMISSION": BROKER_ORDER_SUBMISSION}
    except Exception as exc:
        blocked = GateResult(False, "RUNTIME_FAILURE", gate.evidence)
        status = _status_payload(blocked, asof_date=asof, enable_production=enable_production)
        status["runtime_error"] = str(exc)
        if write_status:
            _write_status(Path(output_root), status)
        return {"status": status, "rows": pd.DataFrame(), "BROKER_ORDER_SUBMISSION": BROKER_ORDER_SUBMISSION}


__all__ = [
    "BROKER_ORDER_SUBMISSION",
    "GateResult",
    "OPERATION_ROOT",
    "PRODUCTION_FLAG",
    "STATUS_FILENAME",
    "STRATEGY_FINGERPRINT",
    "STRATEGY_ID",
    "check_production_eligibility",
    "evaluate_production",
    "normalize_recommendations",
    "registration",
]
