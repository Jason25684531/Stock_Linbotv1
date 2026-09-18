"""Small immutable contracts for research-stage hand-offs.

The business meanings of L2, L4, and S3 are intentionally not inferred here.
They remain explicit ``UNMAPPED`` catalog entries until approved evidence maps
them to an owner and schema.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

STAGE_ORDER = (
    "TWSE_OFFICIAL",
    "ADJUSTMENT",
    "L2",
    "L4",
    "S3",
    "TOP5",
    "TARGET",
)


@dataclass(frozen=True)
class StageDefinition:
    stage: str
    status: str
    owner: str | None
    input_schema: str
    output_schema: str
    as_of_policy: str


@dataclass(frozen=True)
class StageContract:
    stage: str
    schema_version: str
    run_id: str
    as_of: str
    provenance_hash: str
    output_id: str
    diagnostics: tuple[Mapping[str, object], ...] = ()
    status: str = "READY"

    def __post_init__(self) -> None:
        if self.stage not in STAGE_ORDER:
            raise ValueError(f"unknown research stage: {self.stage}")
        if not self.run_id or not self.output_id:
            raise ValueError("run_id and output_id are required")
        if self.status not in {"READY", "BLOCKED", "INVALID"}:
            raise ValueError(f"unsupported contract status: {self.status}")


def stage_catalog() -> tuple[StageDefinition, ...]:
    """Return the versioned catalog without guessing undefined stage meaning."""

    return tuple(
        StageDefinition(
            stage=stage,
            status="MAPPED" if stage in {"TWSE_OFFICIAL", "ADJUSTMENT", "TOP5", "TARGET"} else "UNMAPPED",
            owner={
                "TWSE_OFFICIAL": "core.research.sources",
                "ADJUSTMENT": "core.research.normalize",
                "TOP5": "approved selection boundary",
                "TARGET": "approved target-weight boundary",
            }.get(stage),
            input_schema="unmapped/v1" if stage in {"L2", "L4", "S3"} else "declared/v1",
            output_schema="unmapped/v1" if stage in {"L2", "L4", "S3"} else "declared/v1",
            as_of_policy="point_in_time" if stage != "TWSE_OFFICIAL" else "source_retrieved_at",
        )
        for stage in STAGE_ORDER
    )


def require_upstream(contract: StageContract, expected_stage: str) -> StageContract:
    """Fail closed when an upstream contract is missing or invalid."""

    if contract.stage != expected_stage:
        raise ValueError(f"expected {expected_stage}, received {contract.stage}")
    if contract.status != "READY":
        raise ValueError(f"upstream {expected_stage} is {contract.status}")
    if any(item.get("severity") == "FATAL" for item in contract.diagnostics):
        raise ValueError(f"upstream {expected_stage} has fatal diagnostics")
    return contract


__all__ = ["STAGE_ORDER", "StageContract", "StageDefinition", "require_upstream", "stage_catalog"]
