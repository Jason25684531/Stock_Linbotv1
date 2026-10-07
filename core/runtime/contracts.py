"""Small, side-effect-free runtime result contracts.

These values describe decisions made by an owning runtime.  They do not run
validation, read or write artifacts, change lifecycle state, or submit orders.
"""

from __future__ import annotations

from collections.abc import Mapping, Set
from dataclasses import dataclass, field
from enum import Enum
import math
from types import MappingProxyType
from typing import Any, Protocol, TypeAlias, runtime_checkable


class RuntimeStatus(str, Enum):
    """The only generic verdicts a runtime boundary can expose."""

    PASS = "PASS"
    FAIL = "FAIL"
    BLOCKED = "BLOCKED"


ReasonCode: TypeAlias = str


def _freeze(value: Any) -> Any:
    if isinstance(value, Mapping):
        return MappingProxyType({_freeze(key): _freeze(item) for key, item in value.items()})
    if isinstance(value, list):
        return tuple(_freeze(item) for item in value)
    if isinstance(value, tuple):
        return tuple(_freeze(item) for item in value)
    if isinstance(value, Set) and not isinstance(value, (str, bytes, frozenset)):
        return frozenset(_freeze(item) for item in value)
    if isinstance(value, frozenset):
        return frozenset(_freeze(item) for item in value)
    return value


def _mapping(value: Mapping[str, Any] | None) -> Mapping[str, Any]:
    if value is None:
        return MappingProxyType({})
    frozen = _freeze(value)
    if not isinstance(frozen, Mapping):
        raise TypeError("details must be a mapping")
    return frozen


def _codes(value: tuple[ReasonCode, ...] | list[ReasonCode] | None) -> tuple[ReasonCode, ...]:
    if value is None:
        return ()
    codes = tuple(value)
    if any(not isinstance(code, str) or not code for code in codes):
        raise ValueError("reason codes must be non-empty strings")
    return codes


def _status(value: RuntimeStatus | str | None) -> RuntimeStatus:
    if value is None:
        return RuntimeStatus.BLOCKED
    if isinstance(value, RuntimeStatus):
        return value
    try:
        return RuntimeStatus(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"invalid runtime status: {value!r}") from exc


def _identity(value: str | None, status: RuntimeStatus) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value:
        raise ValueError("strategy_id must be a non-empty string when provided")
    return value


@dataclass(frozen=True, slots=True)
class EvidenceRef:
    """An immutable reference to evidence owned by an existing runtime."""

    uri: str
    artifact: str | None = None
    sha256: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.uri, str) or not self.uri:
            raise ValueError("evidence uri must be a non-empty string")
        if self.artifact is not None and not isinstance(self.artifact, str):
            raise ValueError("evidence artifact must be a string when provided")
        if self.sha256 is not None and not isinstance(self.sha256, str):
            raise ValueError("evidence sha256 must be a string when provided")


@dataclass(frozen=True, slots=True)
class RuntimeResult:
    """Common immutable result fields; missing identity always blocks."""

    status: RuntimeStatus = RuntimeStatus.BLOCKED
    strategy_id: str | None = None
    reason_codes: tuple[ReasonCode, ...] = ()
    evidence: tuple[EvidenceRef, ...] = ()
    details: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        status = _status(self.status)
        strategy_id = _identity(self.strategy_id, status)
        if strategy_id is None and status is not RuntimeStatus.BLOCKED:
            status = RuntimeStatus.BLOCKED
        object.__setattr__(self, "status", status)
        object.__setattr__(self, "strategy_id", strategy_id)
        object.__setattr__(self, "reason_codes", _codes(self.reason_codes))
        object.__setattr__(self, "evidence", tuple(self.evidence))
        object.__setattr__(self, "details", _mapping(self.details))


@dataclass(frozen=True, slots=True)
class ValidationResult(RuntimeResult):
    """Domain-neutral validation verdict; metrics stay in ``details``."""


@dataclass(frozen=True, slots=True)
class FrozenStrategyIdentity:
    """Identity already computed by an owning frozen-strategy implementation."""

    strategy_id: str
    fingerprint: str
    version: str | None = None
    source: EvidenceRef | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not isinstance(self.strategy_id, str) or not self.strategy_id:
            raise ValueError("strategy_id must be a non-empty string")
        if not isinstance(self.fingerprint, str) or not self.fingerprint:
            raise ValueError("fingerprint must be a non-empty string")
        if self.version is not None and not isinstance(self.version, str):
            raise ValueError("version must be a string when provided")
        object.__setattr__(self, "metadata", _mapping(self.metadata))


@runtime_checkable
class FingerprintProvider(Protocol):
    @property
    def fingerprint(self) -> str:
        """Return an existing fingerprint; do not calculate one here."""


@runtime_checkable
class FrozenIdentityProvider(Protocol):
    def frozen_identity(self) -> FrozenStrategyIdentity:
        """Expose an existing frozen identity."""


@dataclass(frozen=True, slots=True)
class ShadowResult(RuntimeResult):
    run_id: str | None = None
    asof_date: str | None = None
    action: str | None = None
    selection: EvidenceRef | None = None


@dataclass(frozen=True, slots=True)
class ParityResult(RuntimeResult):
    compared_identity: str | None = None
    compared_fields: tuple[str, ...] = ()
    mismatches: tuple[str, ...] = ()
    rtol: float = 1e-9
    atol: float = 1e-12

    def __post_init__(self) -> None:
        RuntimeResult.__post_init__(self)
        if not math.isfinite(self.rtol) or self.rtol < 0:
            raise ValueError("rtol must be finite and non-negative")
        if not math.isfinite(self.atol) or self.atol < 0:
            raise ValueError("atol must be finite and non-negative")
        object.__setattr__(self, "compared_fields", tuple(self.compared_fields))
        object.__setattr__(self, "mismatches", tuple(self.mismatches))
        if self.status is RuntimeStatus.PASS and self.mismatches:
            raise ValueError("a parity result with mismatches cannot pass")


@dataclass(frozen=True, slots=True)
class GateResult(RuntimeResult):
    gate_id: str | None = None

    @property
    def eligible(self) -> bool:
        return self.status is RuntimeStatus.PASS


@dataclass(frozen=True, slots=True)
class PromotionDecision(RuntimeResult):
    level: str | None = None


@dataclass(frozen=True, slots=True)
class ProductionEligibility(RuntimeResult):
    eligible: bool = False

    def __post_init__(self) -> None:
        RuntimeResult.__post_init__(self)
        if self.eligible and self.status is not RuntimeStatus.PASS:
            raise ValueError("eligible production result must have PASS status")
        if self.status is RuntimeStatus.PASS and not self.eligible:
            object.__setattr__(self, "status", RuntimeStatus.BLOCKED)


__all__ = [
    "EvidenceRef",
    "FingerprintProvider",
    "FrozenIdentityProvider",
    "FrozenStrategyIdentity",
    "GateResult",
    "ParityResult",
    "ProductionEligibility",
    "PromotionDecision",
    "ReasonCode",
    "RuntimeResult",
    "RuntimeStatus",
    "ShadowResult",
    "ValidationResult",
]
