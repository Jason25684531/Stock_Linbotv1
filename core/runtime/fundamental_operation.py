"""Operate the frozen Fundamental shadow/OOS process under this change ID."""

from __future__ import annotations

import json
from pathlib import Path

from . import fresh_oos_promotion as continuation


CHANGE_ID = "operate-fundamental-shadow-until-oos-ready-v1"
OUTPUT_ROOT = continuation.advancement.REPO_ROOT / "outputs" / "fundamental_runtime_shadow" / CHANGE_ID


def _rewrite_change_id(path: Path) -> None:
    if not path.is_file():
        return
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return
    if isinstance(payload, dict):
        payload["change_id"] = CHANGE_ID
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")


def run_operation(*, current_run_date: object | None = None, output_root: Path = OUTPUT_ROOT, cache_only: bool = True) -> dict[str, object]:
    """Run one deterministic operation cycle; broker submission stays disabled."""

    result = continuation.run_accumulation(current_run_date=current_run_date, output_root=Path(output_root), cache_only=cache_only)
    result["change_id"] = CHANGE_ID
    root = Path(output_root)
    for name in ("validation_manifest.json", "runtime_validation_manifest.json"):
        _rewrite_change_id(root / name)
    report = root / "runtime_validation_report.md"
    if report.is_file():
        text = report.read_text(encoding="utf-8")
        if "change_id=" in text.lower():
            lines = [f"CHANGE_ID={CHANGE_ID}" if line.lower().startswith("change_id=") else line for line in text.splitlines()]
            report.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return result


__all__ = ["CHANGE_ID", "OUTPUT_ROOT", "run_operation"]
