from __future__ import annotations

import json

from core.runtime import fundamental_operation as operation


def test_operation_relabels_evidence_without_enabling_live(tmp_path, monkeypatch):
    monkeypatch.setattr(
        operation.continuation,
        "run_accumulation",
        lambda **_: {"change_id": "accumulate-fresh-oos-and-run-production-promotion-v1", "SHADOW_MODE": "PASS", "BROKER_ORDER_SUBMISSION": "DISABLED"},
    )
    (tmp_path / "validation_manifest.json").write_text(json.dumps({"change_id": "old"}), encoding="utf-8")
    (tmp_path / "runtime_validation_report.md").write_text("CHANGE_ID=old\nBROKER_ORDER_SUBMISSION=DISABLED\n", encoding="utf-8")
    result = operation.run_operation(output_root=tmp_path)
    assert result["change_id"] == operation.CHANGE_ID
    assert result["BROKER_ORDER_SUBMISSION"] == "DISABLED"
    assert json.loads((tmp_path / "validation_manifest.json").read_text())["change_id"] == operation.CHANGE_ID
    assert f"CHANGE_ID={operation.CHANGE_ID}" in (tmp_path / "runtime_validation_report.md").read_text()
