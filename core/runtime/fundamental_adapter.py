"""Composition adapters for existing Fundamental runtime results.

The adapters translate owner-produced values; they do not implement selection,
parity, promotion, or production-gate policy.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

from . import fundamental_production as production
from . import fundamental_shadow as shadow
from .contracts import (
    EvidenceRef,
    GateResult,
    ParityResult,
    ProductionEligibility,
    PromotionDecision,
    RuntimeStatus,
    ShadowResult,
)
from .fundamental_identity import FundamentalIdentityAdapter


_PARITY_FIELDS = ("stock_id", "score", "rank", "selected", "target_weight", "reason")


def _status(value: object) -> RuntimeStatus:
    if value in (
        "SUCCESS",
        "PASS",
        "SHADOW_APPROVED",
        "LIMITED_CAPITAL_CANDIDATE",
        "PRODUCTION_CANDIDATE",
    ):
        return RuntimeStatus.PASS
    if value in ("FAIL", "REJECTED_FOR_PRODUCTION"):
        return RuntimeStatus.FAIL
    return RuntimeStatus.BLOCKED


def _ref(root: Path, name: str, *, artifact: str | None = None) -> EvidenceRef | None:
    path = root / name
    return EvidenceRef(str(path), artifact=artifact or name) if path.is_file() else None


def _read_json(path: Path) -> Mapping[str, Any] | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, Mapping) else None


class FundamentalShadowAdapter:
    """Translate existing Fundamental shadow/parity/decision owners."""

    def __init__(self, loader: shadow.FrozenStrategyLoader | None = None):
        self.identity = FundamentalIdentityAdapter(loader)

    def shadow_result(
        self,
        asof_date: object | None = None,
        *,
        output_root: Path = shadow.OUTPUT_ROOT,
        require_enabled: bool = False,
    ) -> ShadowResult:
        result = shadow.run_shadow(asof_date, output_root=output_root, require_enabled=require_enabled)
        run_dir = Path(result["run_dir"]) if result.get("run_dir") else Path(output_root)
        refs = tuple(ref for ref in (
            _ref(run_dir, "shadow_run_manifest.json"),
            _ref(run_dir, "shadow_health.json"),
            _ref(run_dir, "shadow_selection.csv"),
        ) if ref is not None)
        selection = _ref(run_dir, "shadow_selection.csv")
        reasons = tuple(result.get("reason_codes") or result.get("warnings") or ())
        return ShadowResult(
            status=_status(result.get("status")),
            strategy_id=result.get("strategy_id"),
            reason_codes=reasons,
            evidence=refs,
            run_id=result.get("run_id"),
            asof_date=result.get("asof_date"),
            action=result.get("action"),
            selection=selection,
            details={
                "BROKER_ORDER_SUBMISSION": result.get("BROKER_ORDER_SUBMISSION", "DISABLED"),
                "health": result.get("health", {}),
            },
        )

    def parity_result(
        self,
        *,
        output_root: Path = shadow.OUTPUT_ROOT,
        asof_date: object | None = None,
    ) -> ParityResult:
        result = shadow.build_validation_artifacts(output_root, asof_date=asof_date)
        report = result["parity"]
        mismatches = tuple(
            field for field in ("SELECTION_PARITY", "SCORE_PARITY", "RANK_PARITY", "TARGET_WEIGHT_PARITY")
            if report.get(field) != "PASS"
        )
        if report.get("HISTORICAL_SELECTION_DRIFT", 0) not in (0, "0"):
            mismatches += ("HISTORICAL_SELECTION_DRIFT",)
        refs = tuple(ref for ref in (
            _ref(Path(output_root), "research_runtime_parity_report.json"),
            _ref(Path(output_root), "research_runtime_parity.csv"),
            _ref(Path(output_root), "runtime_replay_manifest.json"),
        ) if ref is not None)
        return ParityResult(
            status=RuntimeStatus.PASS if not mismatches else RuntimeStatus.FAIL,
            strategy_id=shadow.RUNTIME_STRATEGY_ID,
            reason_codes=("HISTORICAL_DRIFT",) if "HISTORICAL_SELECTION_DRIFT" in mismatches else (),
            evidence=refs,
            compared_identity=f"{shadow.RUNTIME_STRATEGY_ID}/{shadow.FINGERPRINT}",
            compared_fields=_PARITY_FIELDS,
            mismatches=mismatches,
            rtol=float(report.get("rtol", shadow.RTOL)),
            atol=float(report.get("atol", shadow.ATOL)),
            details={"parity": report, "replay": result.get("replay", {})},
        )

    def promotion_decision(self, *, output_root: Path = shadow.OUTPUT_ROOT) -> PromotionDecision:
        root = Path(output_root)
        payload = _read_json(root / "promotion_review.json") or _read_json(root / "production_promotion_contract.json")
        if payload is None:
            return PromotionDecision(
                status=RuntimeStatus.BLOCKED,
                strategy_id=shadow.RUNTIME_STRATEGY_ID,
                reason_codes=("PROMOTION_EVIDENCE_INVALID",),
            )
        level = payload.get("PRODUCTION_PROMOTION_STATUS")
        refs = tuple(ref for ref in (
            _ref(root, "promotion_review.json"),
            _ref(root, "FreshOOSAvailabilityReport.json"),
        ) if ref is not None)
        return PromotionDecision(
            status=_status(level),
            strategy_id=payload.get("strategy_id", shadow.RUNTIME_STRATEGY_ID),
            reason_codes=tuple(payload.get("reason_codes") or ()),
            evidence=refs,
            level=str(level) if level is not None else None,
            details=payload,
        )

    def gate_result(
        self,
        *,
        evidence_root: Path = production.OPERATION_ROOT,
        enable_production: object | None = None,
    ) -> GateResult:
        direct = production.check_production_eligibility(
            evidence_root=Path(evidence_root), enable_production=enable_production
        )
        root = Path(evidence_root)
        refs = tuple(ref for ref in (
            _ref(root, "runtime_validation_manifest.json"),
            _ref(root, "promotion_review.json"),
            _ref(root, "FreshOOSAvailabilityReport.json"),
            _ref(root, "FundamentalRuntimeSpec.json"),
            _ref(root, "runtime_replay_manifest.json"),
        ) if ref is not None)
        return GateResult(
            status=RuntimeStatus.PASS if direct.eligible else RuntimeStatus.BLOCKED,
            strategy_id=production.STRATEGY_ID,
            reason_codes=(direct.reason,) if direct.reason else (),
            details={
                "direct_eligible": direct.eligible,
                "broker_order_submission": production.BROKER_ORDER_SUBMISSION,
                "direct_evidence": direct.evidence,
            },
            evidence=refs,
            gate_id="fundamental_production_eligibility",
        )

    def production_eligibility(
        self,
        *,
        evidence_root: Path = production.OPERATION_ROOT,
        enable_production: object | None = None,
    ) -> ProductionEligibility:
        gate = self.gate_result(
            evidence_root=evidence_root, enable_production=enable_production
        )
        return ProductionEligibility(
            status=gate.status,
            strategy_id=gate.strategy_id,
            reason_codes=gate.reason_codes,
            evidence=gate.evidence,
            details={**gate.details, "gate_id": gate.gate_id},
            eligible=gate.eligible,
        )


class FundamentalRuntimeAdapter:
    """Composed contract boundary over the existing Fundamental owners."""

    BROKER_ORDER_SUBMISSION = production.BROKER_ORDER_SUBMISSION

    def __init__(self, loader: shadow.FrozenStrategyLoader | None = None):
        identity = FundamentalIdentityAdapter(loader)
        self.identity = identity
        self.shadow = FundamentalShadowAdapter(identity.loader)

    def frozen_identity(self):
        return self.identity.frozen_identity()

    @property
    def fingerprint(self) -> str:
        return self.identity.fingerprint

    def shadow_result(self, *args: Any, **kwargs: Any) -> ShadowResult:
        return self.shadow.shadow_result(*args, **kwargs)

    def parity_result(self, *args: Any, **kwargs: Any) -> ParityResult:
        return self.shadow.parity_result(*args, **kwargs)

    def promotion_decision(self, *args: Any, **kwargs: Any) -> PromotionDecision:
        return self.shadow.promotion_decision(*args, **kwargs)

    def gate_result(self, *args: Any, **kwargs: Any) -> GateResult:
        return self.shadow.gate_result(*args, **kwargs)

    def production_eligibility(self, *args: Any, **kwargs: Any) -> ProductionEligibility:
        return self.shadow.production_eligibility(*args, **kwargs)

    def evaluate_production(
        self,
        asof_date: object,
        *,
        output_root: Path = production.OPERATION_ROOT,
        evidence_root: Path = production.OPERATION_ROOT,
        enable_production: object | None = None,
        write_status: bool = True,
    ) -> dict[str, object]:
        """Delegate application production evaluation to its existing owner.

        This is an application-consumption boundary only: it does not interpret
        eligibility, construct selections, or activate a lifecycle.
        """

        return production.evaluate_production(
            asof_date,
            output_root=output_root,
            evidence_root=evidence_root,
            enable_production=enable_production,
            write_status=write_status,
        )


__all__ = ["FundamentalRuntimeAdapter", "FundamentalShadowAdapter"]
