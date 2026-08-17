"""Run the frozen D5 portfolio-research grid."""

import argparse
import hashlib
import json
import platform
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from config.settings import Config
from core.research.composite_factor import build_composite_scores, build_factor_weights
from core.research.candidate_redundancy import build_candidate_matrix
from core.research.d5_handoff import load_frozen_handoff
from core.research.portfolio_research import build_parameter_robustness, parameter_grid, select_shortlist, shortlist_eligibility, summarize_portfolio
from core.research.target_weights import build_rebalance_calendar, build_target_weights
from core.research.vectorbt_adapter import run_vectorbt


def _dataset(path: Path, factor_ids: list[str]) -> pd.DataFrame:
    columns = ["asof_date", "asset_id", "factor_id", "direction_adjusted_rank", "member", "is_tradable_t1", "execution_date", "entry_price"]
    return pd.concat([pd.read_csv(file, usecols=columns) for factor in factor_ids for file in sorted((path / factor).glob("*.csv"))], ignore_index=True)


CANONICAL_SORTS = {
    "composite_factor_weights.csv": ["combination_method", "factor_id"],
    "composite_scores.csv": ["asof_date", "asset_id", "combination_method"],
    "parameter_grid.csv": ["config_id"],
    "portfolio_scoreboard.csv": ["config_id"],
    "parameter_robustness.csv": ["config_id"],
    "shortlisted_configs.csv": ["shortlist_rank", "config_id"],
}
FROZEN_D5_RUN = Path("outputs/portfolio_research/d5_portfolio_research_20260814_v1")


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _tree_sha256(path: Path) -> str:
    entries = [f"{file.relative_to(path).as_posix()}:{_sha256(file)}" for file in sorted(path.rglob("*")) if file.is_file()]
    return hashlib.sha256("\n".join(entries).encode()).hexdigest()


def _write_csv(frame: pd.DataFrame, path: Path, sort_keys: list[str]) -> None:
    frame.sort_values(sort_keys, kind="stable").to_csv(path, index=False, lineterminator="\n")


def _version(name: str) -> str | None:
    try:
        from importlib.metadata import version
        return version(name)
    except Exception:
        return None


def _require_hash(path: Path, expected: str, label: str) -> None:
    if _sha256(path) != expected:
        raise ValueError(f"frozen {label} hash mismatch: {path}")


def _execution_gate(result: dict[str, object], target: pd.DataFrame) -> dict[str, int]:
    scheduled = pd.DatetimeIndex(result["scheduled_instruction_dates"])
    instructions = result["instruction_matrix"]
    instruction_dates = pd.DatetimeIndex(instructions.dropna(how="all").index)
    actual = pd.DatetimeIndex(result["actual_order_dates"])
    if not instruction_dates.equals(scheduled):
        raise ValueError("instruction dates differ from frozen execution dates")
    if instructions.loc[~instructions.index.isin(scheduled)].notna().any().any():
        raise ValueError("target instruction exists on a non-execution date")
    if not actual.isin(scheduled).all() or result["orders_on_non_rebalance_dates"]:
        raise ValueError("orders occurred on a non-rebalance date")
    if not (pd.to_datetime(target["execution_date"]) > pd.to_datetime(target["asof_date"])).all():
        raise ValueError("target violates T+1 execution")
    return {"rebalance_count": len(scheduled), "scheduled_execution_date_count": len(scheduled), "actual_order_date_count": len(actual), "orders_on_non_rebalance_dates": 0}


def verify_repro(canonical_dir: Path, repro_dir: Path) -> dict[str, object]:
    """Require byte-identical deterministic CSV artifacts from a corrected rerun."""
    canonical = {path.relative_to(canonical_dir).as_posix(): _sha256(path) for path in canonical_dir.rglob("*.csv")}
    repro = {path.relative_to(repro_dir).as_posix(): _sha256(path) for path in repro_dir.rglob("*.csv")}
    paths = sorted(set(canonical) | set(repro))
    mismatches = [path for path in paths if canonical.get(path) != repro.get(path)]
    report = {"canonical_run_id": canonical_dir.name, "repro_run_id": repro_dir.name, "artifact_count": len(paths), "exact_match_count": len(paths) - len(mismatches), "mismatch_count": len(mismatches), "mismatching_artifacts": mismatches}
    (repro_dir / "reproducibility.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    if mismatches:
        raise ValueError(f"reproducibility mismatch: {', '.join(mismatches)}")
    return report


def run(*, handoff_path: Path, dataset_path: Path, output_dir: Path, initial_capital: float = 1_000_000.0, frozen_run_dir: Path = FROZEN_D5_RUN) -> Path:
    handoff = load_frozen_handoff(handoff_path)
    frozen_manifest = json.loads((frozen_run_dir / "run_manifest.json").read_text(encoding="utf-8"))
    correlation = build_candidate_matrix(handoff.candidates, handoff.correlation)
    data = _dataset(dataset_path, handoff.candidates["factor_id"].tolist())
    universe = data.groupby(["asof_date", "asset_id"], as_index=False).agg(member=("member", "all"), is_tradable_t1=("is_tradable_t1", "all"), execution_date=("execution_date", "first"), entry_price=("entry_price", "first"))
    universe["execution_date"] = pd.to_datetime(universe["execution_date"])
    close = universe.pivot(index="execution_date", columns="asset_id", values="entry_price").sort_index().ffill()
    grid = parameter_grid()
    weights, scores = {}, {}
    for method in grid.combination_method.unique():
        weights[method] = build_factor_weights(handoff.candidates, correlation, method, source_handoff_id=handoff.handoff_id)
        scores[method] = build_composite_scores(data, weights[method], method, source_handoff_id=handoff.handoff_id)
    rows = []
    output_dir.mkdir(parents=True, exist_ok=False)
    (output_dir / "target_weights").mkdir()
    _write_csv(grid, output_dir / "parameter_grid.csv", CANONICAL_SORTS["parameter_grid.csv"])
    _require_hash(output_dir / "parameter_grid.csv", frozen_manifest["artifact_sha256"]["parameter_grid.csv"], "parameter grid")
    _write_csv(pd.concat(weights.values(), ignore_index=True), output_dir / "composite_factor_weights.csv", CANONICAL_SORTS["composite_factor_weights.csv"])
    _write_csv(pd.concat(scores.values(), ignore_index=True), output_dir / "composite_scores.csv", CANONICAL_SORTS["composite_scores.csv"])
    targets = {}
    for config in grid.to_dict("records"):
        method_scores = scores[config["combination_method"]].copy()
        method_scores["asof_date"] = pd.to_datetime(method_scores["asof_date"])
        selected_dates = build_rebalance_calendar(method_scores.asof_date, config["rebalance_days"])
        target = build_target_weights(method_scores.loc[method_scores.asof_date.isin(selected_dates)], universe, source_handoff_id=handoff.handoff_id, **config)
        _write_csv(target, output_dir / "target_weights" / f'{config["config_id"]}.csv', ["asof_date", "execution_date", "asset_id"])
        _require_hash(output_dir / "target_weights" / f'{config["config_id"]}.csv', frozen_manifest["artifact_sha256"][f'target_weights/{config["config_id"]}.csv'], "target weight")
        targets[config["config_id"]] = target
    for config in grid.to_dict("records"):
        result = run_vectorbt(close, targets[config["config_id"]], fee_rate=Config.FEE_RATE, tax_rate=Config.TAX_RATE, initial_capital=initial_capital, sparse_rebalance=True)
        rows.append(summarize_portfolio(config, result) | _execution_gate(result, targets[config["config_id"]]) | {"source_handoff_id": handoff.handoff_id})
    scoreboard = pd.DataFrame(rows)
    robustness = build_parameter_robustness(scoreboard)
    scoreboard = scoreboard.merge(robustness, on="config_id", validate="one_to_one")
    _write_csv(scoreboard, output_dir / "portfolio_scoreboard.csv", CANONICAL_SORTS["portfolio_scoreboard.csv"])
    _write_csv(robustness, output_dir / "parameter_robustness.csv", CANONICAL_SORTS["parameter_robustness.csv"])
    shortlist = select_shortlist(scoreboard)
    _write_csv(shortlist, output_dir / "shortlisted_configs.csv", CANONICAL_SORTS["shortlisted_configs.csv"])
    old_scoreboard = pd.read_csv(frozen_run_dir / "portfolio_scoreboard.csv")
    old_shortlist = set(pd.read_csv(frozen_run_dir / "shortlisted_configs.csv")["config_id"])
    comparison_metrics = ["sharpe", "total_return", "annualized_return", "max_drawdown", "turnover", "trade_count"]
    old = old_scoreboard[["config_id", *comparison_metrics]].rename(columns={metric: f"old_{metric}" for metric in comparison_metrics})
    comparison = scoreboard[["config_id", *comparison_metrics]].merge(old, on="config_id", validate="one_to_one")
    comparison["new_rank"] = comparison["sharpe"].rank(method="first", ascending=False).astype(int)
    comparison["old_rank"] = comparison["old_sharpe"].rank(method="first", ascending=False).astype(int)
    comparison["rank_change"] = comparison["old_rank"] - comparison["new_rank"]
    comparison["old_shortlisted"] = comparison["config_id"].isin(old_shortlist)
    comparison["new_shortlisted"] = comparison["config_id"].isin(set(shortlist["config_id"]))
    comparison["trade_count_reduction"] = comparison["old_trade_count"] - comparison["trade_count"]
    comparison["turnover_change"] = comparison["turnover"] - comparison["old_turnover"]
    comparison["metric_change"] = comparison["sharpe"] - comparison["old_sharpe"]
    _write_csv(comparison, output_dir / "old_vs_corrected_d5.csv", ["config_id"])
    artifact_hashes = {path.name: _sha256(path) for path in sorted(output_dir.glob("*.csv"))}
    artifact_hashes.update({f"target_weights/{path.name}": _sha256(path) for path in sorted((output_dir / "target_weights").glob("*.csv"))})
    target_hashes = {path.name: _sha256(path) for path in sorted((output_dir / "target_weights").glob("*.csv"))}
    equivalent = [ids for ids in ({hash_: sorted(name for name, value in target_hashes.items() if value == hash_) for hash_ in set(target_hashes.values())}).values() if len(ids) > 1]
    manifest = {"schema_version": 2, "run_id": output_dir.name, "generated_at": datetime.now(timezone.utc).isoformat(), "status": "success", "supersedes_run_id": "d5_portfolio_research_20260814_v1", "supersession_reason": "VECTORBT_NON_REBALANCE_DAILY_RETARGETING", "root_cause_diagnostic_run_id": "d6_engine_failure_diagnosis_20260817_provenanced", "root_cause_diagnostic_sha256": _tree_sha256(Path("outputs/engine_diagnostics/d6_engine_failure_diagnosis_20260817_provenanced")), "provenance": {"source_handoff_id": handoff.handoff_id, "source_handoff_sha256": _tree_sha256(handoff_path), "d3_dataset_sha256": _tree_sha256(dataset_path), "git_commit": subprocess.run(["git", "rev-parse", "HEAD"], text=True, capture_output=True, check=False).stdout.strip()}, "candidate_count": handoff.candidate_count, "candidate_ids": handoff.candidates.factor_id.tolist(), "configuration_count": len(grid), "environment": {"python": platform.python_version(), "numpy": _version("numpy"), "pandas": _version("pandas"), "numba": _version("numba"), "vectorbt": _version("vectorbt")}, "execution": {"initial_capital": initial_capital, "execution_lag": "T+1", "fee_rate": Config.FEE_RATE, "tax_rate": Config.TAX_RATE, "vectorbt_fee_approximation": "fee + tax / 2", "execution_semantics_version": "DISCRETE_EXECUTION_DATE_ONLY", "rebalance_semantics": "DISCRETE_EXECUTION_DATE_ONLY"}, "execution_gate": {"configs_passed": len(scoreboard), "scheduled_instruction_date_parity": True, "orders_on_non_rebalance_dates": int(scoreboard["orders_on_non_rebalance_dates"].sum())}, "config_equivalence": {"CONFIG_EQUIVALENCE": bool(equivalent), "equivalent_target_weight_groups": equivalent}, "shortlist_policy": {"minimum": 3, "maximum": 5, "eligible": len(shortlist_eligibility(scoreboard).query("shortlist_eligible"))}, "shortlist_config_ids": shortlist.config_id.tolist(), "artifact_sha256": artifact_hashes, "known_limitations": ["research-only VectorBT results require new D6/D7 validation", "VectorBT uses fee + tax / 2 symmetric approximation", "momentum_12_1 redundancy remains UNKNOWN", "no OOS or production claim"]}
    (output_dir / "run_manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return output_dir


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-id", default=datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ_d5"))
    parser.add_argument("--handoff", type=Path, default=Path("artifacts/d5_handoff/d4_acceptance_20260811_v8_with_scoreboard"))
    parser.add_argument("--dataset", type=Path, default=Path("artifacts/factors/d3_full_20230103_20260728/research_dataset"))
    parser.add_argument("--output-root", type=Path, default=Path("outputs/portfolio_research"))
    parser.add_argument("--initial-capital", type=float, default=1_000_000.0)
    parser.add_argument("--repro-against", type=Path)
    args = parser.parse_args(argv)
    output_dir = run(handoff_path=args.handoff, dataset_path=args.dataset, output_dir=args.output_root / args.run_id, initial_capital=args.initial_capital)
    if args.repro_against:
        verify_repro(args.repro_against, output_dir)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
