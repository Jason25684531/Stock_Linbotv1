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


def run(*, handoff_path: Path, dataset_path: Path, output_dir: Path, initial_capital: float = 1_000_000.0) -> Path:
    handoff = load_frozen_handoff(handoff_path)
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
    _write_csv(pd.concat(weights.values(), ignore_index=True), output_dir / "composite_factor_weights.csv", CANONICAL_SORTS["composite_factor_weights.csv"])
    _write_csv(pd.concat(scores.values(), ignore_index=True), output_dir / "composite_scores.csv", CANONICAL_SORTS["composite_scores.csv"])
    for config in grid.to_dict("records"):
        method_scores = scores[config["combination_method"]].copy()
        method_scores["asof_date"] = pd.to_datetime(method_scores["asof_date"])
        selected_dates = build_rebalance_calendar(method_scores.asof_date, config["rebalance_days"])
        target = build_target_weights(method_scores.loc[method_scores.asof_date.isin(selected_dates)], universe, source_handoff_id=handoff.handoff_id, **config)
        _write_csv(target, output_dir / "target_weights" / f'{config["config_id"]}.csv', ["asof_date", "execution_date", "asset_id"])
        rows.append(summarize_portfolio(config, run_vectorbt(close, target, fee_rate=Config.FEE_RATE, tax_rate=Config.TAX_RATE, initial_capital=initial_capital)) | {"source_handoff_id": handoff.handoff_id})
    scoreboard = pd.DataFrame(rows)
    robustness = build_parameter_robustness(scoreboard)
    scoreboard = scoreboard.merge(robustness, on="config_id", validate="one_to_one")
    _write_csv(scoreboard, output_dir / "portfolio_scoreboard.csv", CANONICAL_SORTS["portfolio_scoreboard.csv"])
    _write_csv(robustness, output_dir / "parameter_robustness.csv", CANONICAL_SORTS["parameter_robustness.csv"])
    shortlist = select_shortlist(scoreboard)
    _write_csv(shortlist, output_dir / "shortlisted_configs.csv", CANONICAL_SORTS["shortlisted_configs.csv"])
    artifact_hashes = {path.name: _sha256(path) for path in sorted(output_dir.glob("*.csv"))}
    artifact_hashes.update({f"target_weights/{path.name}": _sha256(path) for path in sorted((output_dir / "target_weights").glob("*.csv"))})
    manifest = {"schema_version": 1, "run_id": output_dir.name, "generated_at": datetime.now(timezone.utc).isoformat(), "status": "success", "provenance": {"source_handoff_id": handoff.handoff_id, "source_handoff_sha256": _tree_sha256(handoff_path), "d3_dataset_sha256": _tree_sha256(dataset_path), "git_commit": subprocess.run(["git", "rev-parse", "HEAD"], text=True, capture_output=True, check=False).stdout.strip()}, "candidate_count": handoff.candidate_count, "candidate_ids": handoff.candidates.factor_id.tolist(), "configuration_count": len(grid), "environment": {"python": platform.python_version(), "numpy": _version("numpy"), "pandas": _version("pandas"), "numba": _version("numba"), "vectorbt": _version("vectorbt")}, "execution": {"initial_capital": initial_capital, "execution_lag": "T+1", "fee_rate": Config.FEE_RATE, "tax_rate": Config.TAX_RATE, "vectorbt_fee_approximation": "fee + tax / 2"}, "shortlist_policy": {"minimum": 3, "maximum": 5, "eligible": len(shortlist_eligibility(scoreboard).query("shortlist_eligible"))}, "shortlist_config_ids": shortlist.config_id.tolist(), "artifact_sha256": artifact_hashes, "known_limitations": ["research-only VectorBT results require Day 3 custom-engine parity", "VectorBT uses fee + tax / 2 symmetric approximation", "momentum_12_1 redundancy remains UNKNOWN", "no OOS or production claim"]}
    (output_dir / "run_manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return output_dir


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-id", default=datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ_d5"))
    parser.add_argument("--handoff", type=Path, default=Path("artifacts/d5_handoff/d4_acceptance_20260811_v8_with_scoreboard"))
    parser.add_argument("--dataset", type=Path, default=Path("artifacts/factors/d3_full_20230103_20260728/research_dataset"))
    parser.add_argument("--output-root", type=Path, default=Path("outputs/portfolio_research"))
    parser.add_argument("--initial-capital", type=float, default=1_000_000.0)
    args = parser.parse_args(argv)
    run(handoff_path=args.handoff, dataset_path=args.dataset, output_dir=args.output_root / args.run_id, initial_capital=args.initial_capital)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
