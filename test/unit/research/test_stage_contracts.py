from dataclasses import FrozenInstanceError

import pytest

from core.research.stage_contracts import STAGE_ORDER, StageContract, require_upstream, stage_catalog


def _contract(stage="TWSE_OFFICIAL", **kwargs):
    return StageContract(stage=stage, schema_version="v1", run_id="run", as_of="2026-09-18", provenance_hash="p", output_id="o", **kwargs)


def test_catalog_keeps_undefined_labels_unmapped():
    catalog = {item.stage: item for item in stage_catalog()}
    assert tuple(catalog) == STAGE_ORDER
    assert [catalog[name].status for name in ("L2", "L4", "S3")] == ["UNMAPPED"] * 3
    assert all(catalog[name].owner is None for name in ("L2", "L4", "S3"))


def test_contract_is_immutable_and_preserves_provenance():
    contract = _contract()
    assert contract.provenance_hash == "p"
    with pytest.raises(FrozenInstanceError):
        contract.stage = "ADJUSTMENT"


def test_upstream_rejects_wrong_stage_or_fatal_diagnostic():
    with pytest.raises(ValueError, match="expected ADJUSTMENT"):
        require_upstream(_contract(), "ADJUSTMENT")
    with pytest.raises(ValueError, match="fatal diagnostics"):
        require_upstream(_contract("ADJUSTMENT", diagnostics=({"severity": "FATAL"},)), "ADJUSTMENT")
