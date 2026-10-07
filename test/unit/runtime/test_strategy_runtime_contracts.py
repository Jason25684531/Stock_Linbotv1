from __future__ import annotations

import inspect

import pytest

from core.runtime.contracts import (
    EvidenceRef,
    FrozenStrategyIdentity,
    ParityResult,
    ProductionEligibility,
    RuntimeStatus,
    ValidationResult,
)


def test_results_are_immutable_and_freeze_nested_details():
    result = ValidationResult(
        status=RuntimeStatus.PASS,
        strategy_id="example",
        details={"nested": {"values": [1, 2]}},
    )
    assert result.details["nested"]["values"] == (1, 2)
    with pytest.raises(TypeError):
        result.details["nested"] = {}  # type: ignore[index]
    with pytest.raises((AttributeError, TypeError)):
        result.status = RuntimeStatus.FAIL  # type: ignore[misc]


def test_missing_identity_or_status_is_blocked_without_inventing_a_pass():
    missing = ValidationResult(status=RuntimeStatus.PASS)
    unspecified = ValidationResult(strategy_id="example", status=None)
    assert missing.status is RuntimeStatus.BLOCKED
    assert unspecified.status is RuntimeStatus.BLOCKED


def test_evidence_reference_is_a_literal_reference_and_has_no_write_side_effect():
    ref = EvidenceRef(__file__, artifact="existing")
    identity = FrozenStrategyIdentity("strategy", "fingerprint", source=ref)
    assert identity.source == ref
    assert ref.uri == __file__


def test_parity_defaults_keep_repository_tolerance_and_reject_pass_with_mismatch():
    result = ParityResult(
        status=RuntimeStatus.PASS,
        strategy_id="example",
        compared_identity="example/fingerprint",
        compared_fields=("stock_id", "score"),
    )
    assert (result.rtol, result.atol) == (1e-9, 1e-12)
    with pytest.raises(ValueError):
        ParityResult(
            status=RuntimeStatus.PASS,
            strategy_id="example",
            mismatches=("score",),
        )


def test_eligibility_is_only_a_value_and_positive_status_is_explicit():
    blocked = ProductionEligibility(
        strategy_id="example",
        status=RuntimeStatus.BLOCKED,
        reason_codes=("PRODUCTION_DISABLED",),
    )
    eligible = ProductionEligibility(
        strategy_id="example",
        status=RuntimeStatus.PASS,
        eligible=True,
    )
    assert not blocked.eligible
    assert eligible.eligible


def test_contract_module_has_no_application_or_transport_imports():
    source = inspect.getsource(__import__("core.runtime.contracts", fromlist=["*"]))
    for forbidden in ("flask", "linebot", "scheduler", "StrategyManager", "sqlalchemy"):
        assert forbidden.lower() not in source.lower()
