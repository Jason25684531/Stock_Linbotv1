from __future__ import annotations

import json

import pytest

from core.runtime.fundamental_identity import FundamentalIdentityAdapter
from core.runtime.fundamental_shadow import FundamentalRuntimeError, FrozenStrategyLoader


def test_adapter_exposes_exact_existing_frozen_identity():
    loader = FrozenStrategyLoader()
    direct = loader.load()
    identity = FundamentalIdentityAdapter(loader).frozen_identity()

    assert identity.strategy_id == direct.payload["strategy_id"]
    assert identity.fingerprint == direct.fingerprint
    assert identity.fingerprint == "cb7c0d88e58533123d9622315464e82be4a6de636264ba17c0efa4e73c31cc5f"
    assert identity.source is not None
    assert identity.source.uri == str(loader.path)


def test_adapter_preserves_loader_failure_reason_for_missing_spec(tmp_path):
    with pytest.raises(FundamentalRuntimeError, match="FROZEN_SPEC_MISSING"):
        FundamentalIdentityAdapter(FrozenStrategyLoader(tmp_path / "missing.json")).frozen_identity()


def test_adapter_preserves_loader_fingerprint_failure(tmp_path):
    source = FrozenStrategyLoader().path
    payload = json.loads(source.read_text(encoding="utf-8"))
    payload["strategy_fingerprint"] = "changed"
    path = tmp_path / "FrozenStrategySpec.json"
    path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(FundamentalRuntimeError, match="FINGERPRINT_MISMATCH"):
        FundamentalIdentityAdapter(FrozenStrategyLoader(path)).frozen_identity()
