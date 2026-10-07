"""Identity-only view over the existing Fundamental frozen loader."""

from __future__ import annotations

from . import fundamental_shadow as shadow
from .contracts import EvidenceRef, FingerprintProvider, FrozenStrategyIdentity


class FundamentalIdentityAdapter(FingerprintProvider):
    """Expose existing frozen identity without serializing or hashing it."""

    def __init__(self, loader: shadow.FrozenStrategyLoader | None = None):
        self.loader = loader or shadow.FrozenStrategyLoader()

    def frozen_identity(self) -> FrozenStrategyIdentity:
        spec = self.loader.load()
        payload = spec.payload
        return FrozenStrategyIdentity(
            strategy_id=str(payload["strategy_id"]),
            fingerprint=spec.fingerprint,
            version=str(payload.get("schema_version")) if payload.get("schema_version") else None,
            source=EvidenceRef(str(self.loader.path), artifact="FrozenStrategySpec.json"),
            metadata={
                "runtime_strategy_id": shadow.RUNTIME_STRATEGY_ID,
                "dataset_version": str(payload.get("dataset_version", shadow.DATASET_VERSION)),
                "universe_hash": str(payload.get("universe_hash", shadow.UNIVERSE_HASH)),
            },
        )

    @property
    def fingerprint(self) -> str:
        return self.frozen_identity().fingerprint


__all__ = ["FundamentalIdentityAdapter"]
