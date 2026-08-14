"""Validated immutable input contract for D5 research."""

from dataclasses import dataclass
from pathlib import Path

from core.research.candidate_redundancy import CandidateRedundancyError, load_handoff


@dataclass(frozen=True)
class FrozenHandoff:
    path: Path
    candidates: object
    correlation: object
    manifest: dict

    @property
    def candidate_count(self) -> int:
        return int(self.manifest["candidate_count"])


def load_frozen_handoff(path: Path) -> FrozenHandoff:
    """Load a D4 handoff; its own manifest defines the candidate cardinality."""
    candidates, correlation, manifest = load_handoff(path)
    if len(candidates) != int(manifest["candidate_count"]):
        raise CandidateRedundancyError("handoff candidate count differs from manifest")
    if candidates["factor_id"].duplicated().any():
        raise CandidateRedundancyError("handoff candidate ids are duplicated")
    return FrozenHandoff(Path(path), candidates, correlation, manifest)
