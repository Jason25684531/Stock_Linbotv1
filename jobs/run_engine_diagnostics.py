"""Run read-only D6 engine diagnostics against frozen Day 2/Day 3 evidence."""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from config.settings import Config
from core.backtest.costs import CostModel
from core.backtest.research_adapter import replay_config
from core.research.engine_diagnostics import normalized_metrics, rebalance_semantics
from core.research.portfolio_validation import load_price_matrix, sha256_of, verify_day2_run
from core.research.vectorbt_adapter import run_vectorbt


DEFAULT_DAY2 = Path("outputs/portfolio_research/d5_portfolio_research_20260814_v1")
DEFAULT_DAY3 = Path("outputs/portfolio_validation/d6_d7_portfolio_validation_20260817_v1")
DEFAULT_DATASET = Path("artifacts/factors/d3_full_20230103_20260728/research_dataset")


def _hashes(paths: list[Path]) -> dict[str, str]:
    return {str(path): sha256_of(path) for path in paths if path.is_file()}


def _artifact_hashes(output: Path) -> dict[str, str]:
    return {file.relative_to(output).as_posix(): sha256_of(file) for file in sorted(output.rglob("*")) if file.is_file() and file.name != "run_manifest.json"}


def _custom_metrics(path: Path) -> dict[str, float]:
    frame = pd.read_csv(path)
    return dict(zip(frame["metric"], frame["value"]))


def run(*, output_dir: Path, day2_dir: Path = DEFAULT_DAY2, day3_dir: Path = DEFAULT_DAY3, dataset_path: Path = DEFAULT_DATASET) -> Path:
    """Write a DIAGNOSTIC_SHADOW run.  Frozen source hashes are checked twice."""
    manifest = verify_day2_run(day2_dir)
    source_files = [day2_dir / "run_manifest.json", day3_dir / "run_manifest.json", day3_dir / "vectorbt_vs_custom.csv", day3_dir / "validation_scoreboard.csv"]
    for config_id in manifest["shortlist_config_ids"]:
        source_files += [day2_dir / "target_weights" / f"{config_id}.csv", day3_dir / "custom_engine" / config_id / "daily_returns.csv", day3_dir / "custom_engine" / config_id / "performance_metrics.csv"]
    before = _hashes(source_files)
    output_dir.mkdir(parents=True, exist_ok=False)
    # Frozen Day 2 used the then-canonical ffill price matrix.  Keep this replay
    # local so diagnostics prove old lineage without changing today's adapter.
    price_matrix = load_price_matrix(dataset_path, manifest["candidate_ids"]).ffill()
    cost_model = CostModel(Config.FEE_RATE, Config.TAX_RATE, 20.0)
    canonical = pd.read_csv(day3_dir / "vectorbt_vs_custom.csv")
    inventory = canonical.loc[canonical["comparison_status"].eq("UNEXPLAINED_DIFFERENCE")].copy()
    inventory = inventory.rename(columns={"comparison_status": "existing_status", "difference_reason": "existing_reason"})
    inventory.to_csv(output_dir / "engine_failure_inventory.csv", index=False)
    normalized_rows, semantics_rows, sparse_rows, provenance = [], [], [], []
    for config_id in manifest["shortlist_config_ids"]:
        targets = pd.read_csv(day2_dir / "target_weights" / f"{config_id}.csv", parse_dates=["asof_date", "execution_date"])
        vbt = run_vectorbt(price_matrix, targets, fee_rate=Config.FEE_RATE, tax_rate=Config.TAX_RATE, sparse_rebalance=False)
        custom_returns_path = day3_dir / "custom_engine" / config_id / "daily_returns.csv"
        custom_returns = pd.read_csv(custom_returns_path, index_col="date", parse_dates=True).iloc[:, 0]
        vbt_returns = pd.Series(vbt["returns"]).dropna()
        provenance.append({"config_id": config_id, "source": "deterministic_canonical_vectorbt_adapter_rerun", "target_weights_sha256": sha256_of(day2_dir / "target_weights" / f"{config_id}.csv"), "prices_source": str(dataset_path), "daily_returns_sha256": hashlib.sha256(vbt_returns.to_csv().encode()).hexdigest(), "native_order_count": len(vbt["orders"])})
        vbt_normalized, custom_normalized = normalized_metrics(vbt_returns), normalized_metrics(custom_returns)
        frozen_total = float(pd.read_csv(day2_dir / "portfolio_scoreboard.csv").set_index("config_id").loc[config_id, "total_return"])
        if not np.isclose(vbt_normalized["total_return"], frozen_total, rtol=1e-9, atol=1e-12):
            raise ValueError(f"STOP: canonical VectorBT replay differs from frozen Day 2 total_return for {config_id}")
        for metric, vbt_value in vbt_normalized.items():
            custom_value = custom_normalized[metric]
            native = canonical.loc[(canonical.config_id == config_id) & (canonical.metric == metric)].iloc[0]
            native_gap = abs(float(native.custom_value) - float(native.vectorbt_value))
            normalized_gap = abs(custom_value - vbt_value) if vbt_value is not None and custom_value is not None else None
            normalized_rows.append({"config_id": config_id, "metric": metric, "vectorbt_native": native.vectorbt_value, "custom_native": native.custom_value, "vectorbt_normalized": vbt_value, "custom_normalized": custom_value, "native_gap": native_gap, "normalized_gap": normalized_gap, "difference_removed_by_metric_normalization": native_gap - normalized_gap if normalized_gap is not None else None, "classification": "METRIC_CONVENTION_CANDIDATE" if normalized_gap is not None and normalized_gap < native_gap else "RETURN_PATH_DIFFERENCE"})
        semantics_rows.append(rebalance_semantics(config_id, vbt["orders"], pd.DatetimeIndex(targets.execution_date.unique())))
        sparse = run_vectorbt(price_matrix, targets, fee_rate=Config.FEE_RATE, tax_rate=Config.TAX_RATE, sparse_rebalance=True)
        canonical_total = float(vbt_normalized["total_return"])
        sparse_total = float(normalized_metrics(pd.Series(sparse["returns"]).dropna())["total_return"])
        custom_total = float(custom_normalized["total_return"])
        before_gap, after_gap = abs(canonical_total - custom_total), abs(sparse_total - custom_total)
        sparse_rows.append({"config_id": config_id, "canonical_vbt_total_return": canonical_total, "sparse_vbt_total_return": sparse_total, "custom_total_return": custom_total, "gap_before": before_gap, "gap_after_sparse_rebalance": after_gap, "percentage_of_gap_explained": None if before_gap == 0 else 100 * (1 - after_gap / before_gap), "sparse_order_count": len(sparse["orders"]), "root_cause_candidate": "VECTORBT_DAILY_RETARGETING" if after_gap < before_gap else "NOT_CONFIRMED"})
    pd.DataFrame(normalized_rows).to_csv(output_dir / "normalized_metric_comparison.csv", index=False)
    pd.DataFrame(semantics_rows).to_csv(output_dir / "vectorbt_rebalance_semantics.csv", index=False)
    pd.DataFrame(sparse_rows).to_csv(output_dir / "vectorbt_sparse_rebalance_diagnostic.csv", index=False)
    pd.DataFrame(provenance).to_csv(output_dir / "raw_returns_provenance.csv", index=False)
    defect = any(row["semantics_status"] == "POTENTIAL_DAY2_EXECUTION_SEMANTICS_DEFECT" for row in semantics_rows)
    decision = "C" if defect else "PENDING_REMAINING_DIAGNOSTICS"
    report = "# Engine Failure Root Cause Report\n\n" + f"FINAL_DECISION = {decision}\n" + f"DAY2_PORTFOLIO_RESEARCH_REQUIRES_REOPEN = {'YES' if defect else 'NO'}\n"
    (output_dir / "ENGINE_FAILURE_ROOT_CAUSE_REPORT.md").write_text(report, encoding="utf-8")
    after = _hashes(source_files)
    if before != after:
        raise ValueError("STOP: frozen source artifact hash changed during diagnostics")
    run_manifest = {"run_id": output_dir.name, "lineage_type": "DIAGNOSTIC_SHADOW", "source_hashes": before, "raw_returns_provenance": "raw_returns_provenance.csv", "decision_branch": decision, "artifact_sha256": _artifact_hashes(output_dir)}
    if output_dir.name.endswith("_repro"):
        reference = output_dir.parent / output_dir.name.removesuffix("_repro")
        if not reference.is_dir():
            raise ValueError(f"missing diagnostic reference run: {reference}")
        expected = json.loads((reference / "run_manifest.json").read_text(encoding="utf-8"))["artifact_sha256"]
        actual = run_manifest["artifact_sha256"]
        names = sorted(set(expected) | set(actual))
        mismatch_count = sum(expected.get(name) != actual.get(name) for name in names)
        run_manifest["reproducibility"] = {"artifact_count": len(names), "exact_match_count": len(names) - mismatch_count, "mismatch_count": mismatch_count}
        if mismatch_count:
            raise ValueError("STOP: diagnostic repro numeric artifacts differ")
    (output_dir / "run_manifest.json").write_text(json.dumps(run_manifest, indent=2), encoding="utf-8")
    return output_dir


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--output-root", type=Path, default=Path("outputs/engine_diagnostics"))
    parser.add_argument("--day2-dir", type=Path, default=DEFAULT_DAY2)
    parser.add_argument("--day3-dir", type=Path, default=DEFAULT_DAY3)
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    args = parser.parse_args()
    run(output_dir=args.output_root / args.run_id, day2_dir=args.day2_dir, day3_dir=args.day3_dir, dataset_path=args.dataset)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
