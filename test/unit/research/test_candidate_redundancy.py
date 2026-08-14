import hashlib
import json

import pandas as pd
import pytest

from core.research.candidate_redundancy import CandidateRedundancyError, analyze_handoff, build_candidate_matrix, build_pairs, build_summary, freeze_handoff, load_handoff


def _sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _source_run(tmp_path):
    root = tmp_path / "outputs" / "factor_research" / "d4"
    root.mkdir(parents=True)
    (root / "run_manifest.json").write_text(json.dumps({"status": "success"}), encoding="utf-8")
    pd.DataFrame([
        {"factor_id": "alpha", "status": "CANDIDATE", "provenance": "d4"},
        {"factor_id": "beta", "status": "CANDIDATE", "provenance": "d4"},
        {"factor_id": "gamma", "status": "WEAK", "provenance": "d4"},
    ]).to_csv(root / "application_candidates.csv", index=False)
    pd.DataFrame([
        {"factor_id": "alpha", "other_factor_id": "alpha", "correlation": 1.0},
        {"factor_id": "alpha", "other_factor_id": "beta", "correlation": 0.5},
        {"factor_id": "beta", "other_factor_id": "alpha", "correlation": 0.5},
        {"factor_id": "beta", "other_factor_id": "beta", "correlation": 1.0},
    ]).to_csv(root / "factor_correlation.csv", index=False)
    pd.DataFrame([{"factor_id": "alpha", "redundancy_flag": "MODERATE"}]).to_csv(root / "factor_scoreboard.csv", index=False)
    return root


def test_freeze_handoff_copies_candidate_contract_with_provenance_and_is_idempotent(tmp_path):
    source = _source_run(tmp_path)
    before = {path.name: _sha256(path) for path in source.iterdir()}

    handoff = freeze_handoff("d4", source_root=source.parent, output_root=tmp_path / "handoff")

    manifest = json.loads((handoff / "handoff_manifest.json").read_text(encoding="utf-8"))
    frozen = pd.read_csv(handoff / "candidate_factors.csv")
    assert handoff == tmp_path / "handoff" / "d4"
    assert frozen["factor_id"].tolist() == ["alpha", "beta"]
    assert frozen["provenance"].tolist() == ["d4", "d4"]
    assert manifest["source_run_id"] == "d4"
    assert manifest["candidate_count"] == 2
    assert manifest["source_sha256"] == before
    assert (handoff / "factor_correlation.csv").read_bytes() == (source / "factor_correlation.csv").read_bytes()
    assert (handoff / "factor_scoreboard.csv").read_bytes() == (source / "factor_scoreboard.csv").read_bytes()
    assert {path.name: _sha256(path) for path in source.iterdir()} == before
    assert freeze_handoff("d4", source_root=source.parent, output_root=tmp_path / "handoff") == handoff


def test_load_handoff_preserves_candidates_and_rejects_manifest_count_mismatch(tmp_path):
    handoff = freeze_handoff("d4", source_root=_source_run(tmp_path).parent, output_root=tmp_path / "handoff")

    candidates, correlation, manifest = load_handoff(handoff)

    assert candidates["factor_id"].tolist() == ["alpha", "beta"]
    assert set(correlation.columns) == {"factor_id", "other_factor_id", "correlation"}
    assert manifest["candidate_count"] == 2
    manifest["candidate_count"] = 3
    (handoff / "handoff_manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(CandidateRedundancyError, match="candidate count"):
        load_handoff(handoff)


def test_candidate_matrix_is_complete_symmetric_and_preserves_nan():
    candidates = pd.DataFrame({"factor_id": ["alpha", "beta"], "status": ["CANDIDATE", "CANDIDATE"]})
    correlation = pd.DataFrame([
        {"factor_id": "alpha", "other_factor_id": "alpha", "correlation": 1.0},
        {"factor_id": "alpha", "other_factor_id": "beta", "correlation": float("nan")},
        {"factor_id": "beta", "other_factor_id": "alpha", "correlation": float("nan")},
        {"factor_id": "beta", "other_factor_id": "beta", "correlation": 1.0},
    ])

    matrix = build_candidate_matrix(candidates, correlation)

    assert matrix.index.tolist() == ["alpha", "beta"]
    assert matrix.columns.tolist() == ["alpha", "beta"]
    assert matrix.loc["alpha", "alpha"] == pytest.approx(1.0)
    assert pd.isna(matrix.loc["alpha", "beta"])
    correlation.loc[1, "correlation"] = 0.4
    with pytest.raises(CandidateRedundancyError, match="symmetric"):
        build_candidate_matrix(candidates, correlation)


def test_pairs_classify_boundaries_preserve_sign_and_sort_unknown_last():
    matrix = pd.DataFrame(
        [[1.0, -0.80, 0.50], [-0.80, 1.0, float("nan")], [0.50, float("nan"), 1.0]],
        index=["alpha", "beta", "gamma"], columns=["alpha", "beta", "gamma"],
    )

    pairs = build_pairs(matrix)

    assert pairs[["factor_a", "factor_b"]].values.tolist() == [["alpha", "beta"], ["alpha", "gamma"], ["beta", "gamma"]]
    assert pairs["redundancy_level"].tolist() == ["HIGH", "MODERATE", "UNKNOWN"]
    assert pairs.loc[0, "correlation"] == pytest.approx(-0.80)
    assert pairs.loc[0, "high_redundancy_edge"] == True
    assert pd.isna(pairs.loc[2, "abs_correlation"])


def test_summary_groups_high_components_and_keeps_unknown_ungrouped():
    matrix = pd.DataFrame(
        [[1.0, 0.85, float("nan")], [0.85, 1.0, float("nan")], [float("nan"), float("nan"), float("nan")]],
        index=["alpha", "beta", "gamma"], columns=["alpha", "beta", "gamma"],
    )

    summary = build_summary(matrix, build_pairs(matrix)).set_index("factor_id")

    assert summary.loc["alpha", "high_redundancy_group_id"] == "HIGH_GROUP_1"
    assert summary.loc["beta", "high_redundancy_peer_count"] == 1
    assert pd.isna(summary.loc["gamma", "strongest_peer"])
    assert summary.loc["gamma", "redundancy_level"] == "UNKNOWN"
    assert pd.isna(summary.loc["gamma", "high_redundancy_group_id"])


def test_analyze_handoff_writes_matrix_pairs_summary_heatmap_and_report(tmp_path):
    handoff = freeze_handoff("d4", source_root=_source_run(tmp_path).parent, output_root=tmp_path / "handoff")

    artifacts = analyze_handoff(handoff)

    assert set(artifacts) == {
        "candidate_correlation_matrix.csv", "candidate_correlation_pairs.csv", "candidate_redundancy_summary.csv",
        "candidate_correlation_heatmap.png", "CANDIDATE_REDUNDANCY_REPORT.md",
    }
    assert all(path.is_file() for path in artifacts.values())
    assert artifacts["candidate_correlation_heatmap.png"].stat().st_size > 10_000
    report = artifacts["CANDIDATE_REDUNDANCY_REPORT.md"].read_text(encoding="utf-8")
    assert "D5 Implications" in report
    assert "production weight" not in report
