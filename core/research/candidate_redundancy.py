"""D5 candidate-redundancy handoff and diagnostics."""

import hashlib
import json
import os
import shutil
from datetime import datetime, timezone
from pathlib import Path

import matplotlib.pyplot as plt
import pandas as pd


class CandidateRedundancyError(ValueError):
    """Raised when an immutable D5 handoff violates its contract."""


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _source_files(source: Path) -> dict[str, Path]:
    files = {name: source / name for name in ("run_manifest.json", "application_candidates.csv", "factor_correlation.csv", "factor_scoreboard.csv")}
    missing = [name for name, path in files.items() if not path.is_file()]
    if missing:
        raise CandidateRedundancyError(f"source run missing required files: {missing}")
    return files


def _validate_existing_handoff(target: Path, source_hashes: dict[str, str]) -> None:
    manifest_path = target / "handoff_manifest.json"
    if not manifest_path.is_file():
        raise CandidateRedundancyError(f"existing handoff is incomplete: {target}")
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as caught:
        raise CandidateRedundancyError("existing handoff manifest is malformed") from caught
    if manifest.get("source_sha256") != source_hashes:
        raise CandidateRedundancyError("existing handoff source hashes differ; refusing overwrite")
    for name in ("candidate_factors.csv", "factor_correlation.csv", "factor_scoreboard.csv"):
        if not (target / name).is_file():
            raise CandidateRedundancyError(f"existing handoff is incomplete: {target}")


def freeze_handoff(source_run_id: str, *, handoff_id: str | None = None, source_root: Path = Path("outputs/factor_research"), output_root: Path = Path("artifacts/d5_handoff")) -> Path:
    """Atomically freeze the D4 files needed by D5, without mutating D4."""

    source = Path(source_root) / source_run_id
    files = _source_files(source)
    try:
        source_manifest = json.loads(files["run_manifest.json"].read_text(encoding="utf-8"))
    except json.JSONDecodeError as caught:
        raise CandidateRedundancyError("source run manifest is malformed") from caught
    if source_manifest.get("status") != "success":
        raise CandidateRedundancyError("source run is not successful")
    candidates = pd.read_csv(files["application_candidates.csv"])
    if not {"factor_id", "status"} <= set(candidates):
        raise CandidateRedundancyError("application candidates missing factor_id or status")
    frozen = candidates.loc[candidates["status"].eq("CANDIDATE")].copy()
    if frozen.empty:
        raise CandidateRedundancyError("source run has no candidate factors")
    source_hashes = {name: _sha256(path) for name, path in files.items()}
    target = Path(output_root) / (handoff_id or source_run_id)
    if target.exists():
        _validate_existing_handoff(target, source_hashes)
        return target

    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.parent / f".{target.name}.tmp-{os.getpid()}"
    if temporary.exists():
        raise CandidateRedundancyError(f"temporary handoff path already exists: {temporary}")
    try:
        temporary.mkdir()
        frozen.to_csv(temporary / "candidate_factors.csv", index=False)
        shutil.copyfile(files["factor_correlation.csv"], temporary / "factor_correlation.csv")
        shutil.copyfile(files["factor_scoreboard.csv"], temporary / "factor_scoreboard.csv")
        manifest = {
            "source_run_id": source_run_id,
            "handoff_id": target.name,
            "source_sha256": source_hashes,
            "candidate_count": len(frozen),
            "candidate_factor_ids": frozen["factor_id"].tolist(),
            "evaluation_config": source_manifest.get("evaluation_config"),
            "evaluation_config_hash": source_manifest.get("evaluation_config_hash"),
            "frozen_at_utc": datetime.now(timezone.utc).isoformat(),
        }
        (temporary / "handoff_manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        os.replace(temporary, target)
    except Exception:
        if temporary.exists():
            shutil.rmtree(temporary)
        raise
    return target


def load_handoff(handoff_dir: Path) -> tuple[pd.DataFrame, pd.DataFrame, dict[str, object]]:
    """Load the immutable D5 contract and validate its candidate collection."""

    handoff = Path(handoff_dir)
    try:
        manifest = json.loads((handoff / "handoff_manifest.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as caught:
        raise CandidateRedundancyError("handoff manifest is missing or malformed") from caught
    try:
        candidates = pd.read_csv(handoff / "candidate_factors.csv")
        correlation = pd.read_csv(handoff / "factor_correlation.csv")
    except OSError as caught:
        raise CandidateRedundancyError("handoff contract files are missing") from caught
    if not {"factor_id", "status"} <= set(candidates):
        raise CandidateRedundancyError("handoff candidates missing factor_id or status")
    if not {"factor_id", "other_factor_id", "correlation"} <= set(correlation):
        raise CandidateRedundancyError("handoff correlation has invalid schema")
    if not candidates["status"].eq("CANDIDATE").all():
        raise CandidateRedundancyError("handoff candidates include a non-candidate")
    if len(candidates) != manifest.get("candidate_count"):
        raise CandidateRedundancyError("handoff candidate count differs from manifest")
    if candidates["factor_id"].tolist() != manifest.get("candidate_factor_ids"):
        raise CandidateRedundancyError("handoff candidate ids differ from manifest")
    return candidates, correlation, manifest


def build_candidate_matrix(candidates: pd.DataFrame, correlation: pd.DataFrame) -> pd.DataFrame:
    """Return the complete, symmetric candidate-only correlation matrix."""

    factor_ids = candidates["factor_id"].tolist()
    if len(factor_ids) != len(set(factor_ids)):
        raise CandidateRedundancyError("candidate factor ids are duplicated")
    selected = correlation.loc[
        correlation["factor_id"].isin(factor_ids) & correlation["other_factor_id"].isin(factor_ids),
        ["factor_id", "other_factor_id", "correlation"],
    ]
    if selected.duplicated(["factor_id", "other_factor_id"]).any():
        raise CandidateRedundancyError("candidate correlation has duplicate entries")
    matrix = selected.pivot(index="factor_id", columns="other_factor_id", values="correlation").reindex(index=factor_ids, columns=factor_ids)
    if matrix.isna().all(axis=None) and selected.empty:
        raise CandidateRedundancyError("candidate correlation is missing")
    expected = len(factor_ids) ** 2
    if len(selected) != expected:
        raise CandidateRedundancyError("candidate correlation is incomplete")
    values = matrix.to_numpy(dtype=float)
    mirror = values.T
    same_missing = pd.isna(values) == pd.isna(mirror)
    same_values = pd.isna(values) | (abs(values - mirror) <= 1e-9)
    if not (same_missing & same_values).all():
        raise CandidateRedundancyError("candidate correlation is not symmetric")
    diagonal = matrix.to_numpy(dtype=float).diagonal()
    if not ((pd.isna(diagonal)) | (abs(diagonal - 1.0) <= 1e-9)).all():
        raise CandidateRedundancyError("candidate correlation diagonal must be 1.0 or NaN")
    matrix.index.name = "factor_id"
    matrix.columns.name = "factor_id"
    return matrix


def _redundancy_level(value: float) -> str:
    if pd.isna(value):
        return "UNKNOWN"
    if abs(value) < 0.50:
        return "LOW"
    if abs(value) < 0.80:
        return "MODERATE"
    return "HIGH"


def build_pairs(matrix: pd.DataFrame) -> pd.DataFrame:
    """Return one deterministic diagnostic row for each unique candidate pair."""

    rows = []
    factor_ids = matrix.index.tolist()
    for index, factor_a in enumerate(factor_ids):
        for factor_b in factor_ids[index + 1:]:
            correlation = matrix.loc[factor_a, factor_b]
            absolute = abs(correlation) if pd.notna(correlation) else float("nan")
            level = _redundancy_level(correlation)
            rows.append({
                "factor_a": factor_a,
                "factor_b": factor_b,
                "correlation": correlation,
                "abs_correlation": absolute,
                "redundancy_level": level,
                "high_redundancy_edge": level == "HIGH",
            })
    pairs = pd.DataFrame(rows)
    return pairs.sort_values(
        ["abs_correlation", "factor_a", "factor_b"], ascending=[False, True, True], na_position="last", kind="stable"
    ).reset_index(drop=True)


def build_summary(matrix: pd.DataFrame, pairs: pd.DataFrame) -> pd.DataFrame:
    """Summarize strongest peers and HIGH-only connected components."""

    factor_ids = matrix.index.tolist()
    neighbors = {factor_id: set() for factor_id in factor_ids}
    for row in pairs.loc[pairs["high_redundancy_edge"]].itertuples(index=False):
        neighbors[row.factor_a].add(row.factor_b)
        neighbors[row.factor_b].add(row.factor_a)
    groups: dict[str, str] = {}
    group_number = 0
    for factor_id in factor_ids:
        if factor_id in groups or not neighbors[factor_id]:
            continue
        group_number += 1
        queue = [factor_id]
        while queue:
            current = queue.pop(0)
            if current in groups:
                continue
            groups[current] = f"HIGH_GROUP_{group_number}"
            queue.extend(sorted(neighbors[current] - set(groups)))
    rows = []
    for factor_id in factor_ids:
        peers = matrix.loc[factor_id].drop(labels=factor_id).dropna()
        if peers.empty:
            strongest_peer = pd.NA
            strongest = float("nan")
        else:
            strongest_peer = peers.abs().sort_values(ascending=False, kind="stable").index[0]
            strongest = peers[strongest_peer]
        rows.append({
            "factor_id": factor_id,
            "strongest_peer": strongest_peer,
            "strongest_correlation": strongest,
            "strongest_abs_correlation": abs(strongest) if pd.notna(strongest) else float("nan"),
            "redundancy_level": _redundancy_level(strongest),
            "high_redundancy_group_id": groups.get(factor_id, pd.NA),
            "high_redundancy_peer_count": len(neighbors[factor_id]),
        })
    return pd.DataFrame(rows)


def _write_heatmap(matrix: pd.DataFrame, output: Path, *, source_run_id: str, handoff_id: str) -> None:
    figure, axis = plt.subplots(figsize=(8, 7))
    cmap = plt.colormaps["coolwarm"].copy()
    cmap.set_bad("lightgray")
    image = axis.imshow(matrix.to_numpy(dtype=float), vmin=-1, vmax=1, cmap=cmap)
    axis.set_xticks(range(len(matrix.columns)), matrix.columns, rotation=45, ha="right")
    axis.set_yticks(range(len(matrix.index)), matrix.index)
    for row, factor_a in enumerate(matrix.index):
        for column, factor_b in enumerate(matrix.columns):
            value = matrix.loc[factor_a, factor_b]
            axis.text(column, row, "NaN" if pd.isna(value) else f"{value:.3f}", ha="center", va="center", fontsize=8)
    axis.set_title(f"D4 Candidate Redundancy\nsource: {source_run_id}; handoff: {handoff_id}")
    figure.colorbar(image, ax=axis, label="Correlation")
    figure.tight_layout()
    figure.savefig(output, dpi=160)
    plt.close(figure)


def _markdown_table(frame: pd.DataFrame, *, include_index: bool = False) -> str:
    table = frame.reset_index() if include_index else frame
    columns = [str(column) for column in table.columns]
    rows = [["" if pd.isna(value) else str(value) for value in row] for row in table.itertuples(index=False, name=None)]
    return "\n".join([
        "| " + " | ".join(columns) + " |",
        "| " + " | ".join("---" for _ in columns) + " |",
        *("| " + " | ".join(row) + " |" for row in rows),
    ])


def _report(matrix: pd.DataFrame, pairs: pd.DataFrame, summary: pd.DataFrame, manifest: dict[str, object]) -> str:
    counts = pairs["redundancy_level"].value_counts().to_dict()
    known = pairs.dropna(subset=["correlation"])
    strongest_positive = known.loc[known["correlation"].idxmax()] if not known.empty else None
    strongest_negative = known.loc[known["correlation"].idxmin()] if not known.empty else None
    strongest_absolute = known.loc[known["abs_correlation"].idxmax()] if not known.empty else None
    pair_table = _markdown_table(pairs)
    matrix_table = _markdown_table(matrix, include_index=True)
    group_table = _markdown_table(summary)
    describe = lambda row: "無可用資料" if row is None else f"{row.factor_a} × {row.factor_b}: {row.correlation:.3f}"
    return f"""# D4 Candidate Redundancy Report

## Executive Summary

- Handoff：`{manifest['source_run_id']}`；候選因子：{len(matrix)}；唯一配對：{len(pairs)}。
- 分級：HIGH {counts.get('HIGH', 0)}、MODERATE {counts.get('MODERATE', 0)}、LOW {counts.get('LOW', 0)}、UNKNOWN {counts.get('UNKNOWN', 0)}。
- 最強正相關：{describe(strongest_positive)}；最強負相關：{describe(strongest_negative)}；最大絕對相關：{describe(strongest_absolute)}。
- `momentum_12_1` 型的 NaN 以 UNKNOWN 呈現，為資料缺口，不以 0 或重算值替代。

## Candidate Correlation Matrix

{matrix_table}

## Pair Ranking

{pair_table}

## HIGH Redundancy Groups

{group_table}

## D5 Implications

本報告僅供研究診斷。D5 可比較等權 baseline、cluster-aware、代表因子、冗餘調整與家族權重上限等設計；本次不剔除候選、不變更 D4 status，也不指派任何 production 權重。

## Provenance

來源 run：`{manifest['source_run_id']}`；凍結時間：`{manifest['frozen_at_utc']}`；來源 SHA-256：`{json.dumps(manifest['source_sha256'], sort_keys=True)}`。
"""


def analyze_handoff(handoff_dir: Path) -> dict[str, Path]:
    """Generate all D5 diagnostic artifacts from an already frozen handoff."""

    candidates, correlation, manifest = load_handoff(handoff_dir)
    matrix = build_candidate_matrix(candidates, correlation)
    pairs = build_pairs(matrix)
    summary = build_summary(matrix, pairs)
    output = Path(handoff_dir) / "redundancy"
    if output.exists():
        raise CandidateRedundancyError(f"redundancy output already exists: {output}")
    temporary = output.parent / f".{output.name}.tmp-{os.getpid()}"
    try:
        temporary.mkdir()
        matrix.to_csv(temporary / "candidate_correlation_matrix.csv")
        pairs.to_csv(temporary / "candidate_correlation_pairs.csv", index=False)
        summary.to_csv(temporary / "candidate_redundancy_summary.csv", index=False)
        _write_heatmap(matrix, temporary / "candidate_correlation_heatmap.png", source_run_id=str(manifest["source_run_id"]), handoff_id=Path(handoff_dir).name)
        (temporary / "CANDIDATE_REDUNDANCY_REPORT.md").write_text(_report(matrix, pairs, summary, manifest), encoding="utf-8")
        os.replace(temporary, output)
    except Exception:
        if temporary.exists():
            shutil.rmtree(temporary)
        raise
    return {name: output / name for name in ("candidate_correlation_matrix.csv", "candidate_correlation_pairs.csv", "candidate_redundancy_summary.csv", "candidate_correlation_heatmap.png", "CANDIDATE_REDUNDANCY_REPORT.md")}
