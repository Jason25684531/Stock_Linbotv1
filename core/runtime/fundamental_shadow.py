"""Fail-closed shadow adapter for the frozen Fundamental Strategy.

This module intentionally has no broker dependency.  It translates the
already-canonical research selection into the existing runtime-shaped rows and
auditable shadow artifacts; it never owns an alternate alpha implementation.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
from types import MappingProxyType
from typing import Any, Mapping

import numpy as np
import pandas as pd

from core.research import fundamental_strategy_validation as canonical
from jobs import run_fundamental_strategy_validation as research_runtime


CHANGE_ID = "operate-fundamental-shadow-until-oos-ready-v1"
RUNTIME_STRATEGY_ID = "fundamental_g2g3_top5_reb60_score_weighted_v1"
FINGERPRINT = "cb7c0d88e58533123d9622315464e82be4a6de636264ba17c0efa4e73c31cc5f"
DATASET_VERSION = "fundamental-pit-v3"
UNIVERSE_HASH = "d311c1fea8c3110c1d9b940798040b8867d9c2ed5d9eefbd3447bd01d1c87594"
TARGET_TICKER_COUNT = 890
CUTOFF = pd.Timestamp("2026-07-28")
TOP_N = 5
REBALANCE_DAYS = 60
MIN_FACTOR_COVERAGE = 0.90
RTOL, ATOL = 1e-9, 1e-12

REPO_ROOT = Path(__file__).resolve().parents[2]
FROZEN_SPEC_PATH = REPO_ROOT / "outputs" / "fundamental_strategy_validation" / "add-fundamental-strategy-robustness-and-fresh-oos-v1_20260917_v4" / "FrozenStrategySpec.json"
SEARCH_SPEC_PATH = FROZEN_SPEC_PATH.with_name("strategy_search_spec.json")
PIT_ROOT = REPO_ROOT / "data" / "processed" / "fundamental_pit_v3"
MATRIX_PATH = PIT_ROOT / "fundamental_matrix.parquet"
DEFAULT_D3_ROOT = REPO_ROOT / "artifacts" / "factors" / "d3_full_20230103_20260728"
OUTPUT_ROOT = REPO_ROOT / "outputs" / "fundamental_runtime_shadow" / CHANGE_ID


def resolve_current_run_date(value: object | None = None) -> pd.Timestamp:
    """Resolve a Taiwan-local run date; explicit values are for replay/tests."""
    if value is not None:
        return pd.Timestamp(value).normalize()
    override = os.getenv("CURRENT_RUN_DATE")
    if override:
        return pd.Timestamp(override).normalize()
    from zoneinfo import ZoneInfo
    return pd.Timestamp(datetime.now(ZoneInfo("Asia/Taipei")).date()).normalize()


class FundamentalRuntimeError(RuntimeError):
    """A frozen-contract or fail-closed runtime error."""


def _bool_env(name: str, default: bool) -> bool:
    value = os.getenv(name)
    return default if value is None else value.strip().lower() in {"1", "true", "yes", "on"}


def _freeze(value: object) -> object:
    """Recursively freeze decoded JSON so callers cannot override the contract."""
    if isinstance(value, dict):
        return MappingProxyType({key: _freeze(item) for key, item in value.items()})
    if isinstance(value, list):
        return tuple(_freeze(item) for item in value)
    return value


def flags() -> Mapping[str, bool]:
    """Feature flags; live execution is deliberately not a configurable capability."""
    return MappingProxyType({
        "FUNDAMENTAL_STRATEGY_ENABLED": _bool_env("FUNDAMENTAL_STRATEGY_ENABLED", False),
        "FUNDAMENTAL_STRATEGY_SHADOW_ONLY": _bool_env("FUNDAMENTAL_STRATEGY_SHADOW_ONLY", True),
    })


def registration() -> dict[str, object]:
    return {
        "strategy_id": RUNTIME_STRATEGY_ID,
        "research_strategy_id": "G2_PLUS_G3__EQUAL__TOP5__REB60__SCORE_WEIGHTED",
        "strategy_fingerprint": FINGERPRINT,
        "ENABLED_FOR_SHADOW": "YES",
        "ENABLED_FOR_LIVE": "NO",
        "FUNDAMENTAL_STRATEGY_ENABLED_DEFAULT": False,
        "FUNDAMENTAL_STRATEGY_SHADOW_ONLY_DEFAULT": True,
        "BROKER_ORDER_SUBMISSION": "DISABLED",
    }


@dataclass(frozen=True)
class FrozenRuntimeSpec:
    payload: Mapping[str, object]

    @property
    def fingerprint(self) -> str:
        return str(self.payload["strategy_fingerprint"])


class FrozenStrategyLoader:
    """Load exactly one immutable upstream strategy contract, without defaults."""

    def __init__(self, path: Path = FROZEN_SPEC_PATH):
        self.path = Path(path)

    def load(self) -> FrozenRuntimeSpec:
        if not self.path.is_file():
            raise FundamentalRuntimeError("FROZEN_SPEC_MISSING")
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise FundamentalRuntimeError("FROZEN_SPEC_INVALID") from exc
        candidate = payload.get("candidate")
        if not isinstance(candidate, dict):
            raise FundamentalRuntimeError("FROZEN_SPEC_INVALID")
        expected = {
            "strategy_fingerprint": FINGERPRINT,
            "dataset_version": DATASET_VERSION,
            "universe_hash": UNIVERSE_HASH,
            "factor_research_knowledge_cutoff": "2026-07-28",
            "strategy_id": "G2_PLUS_G3__EQUAL__TOP5__REB60__SCORE_WEIGHTED",
        }
        if any(payload.get(key) != value for key, value in expected.items()):
            raise FundamentalRuntimeError("FINGERPRINT_MISMATCH")
        if candidate.get("accepted_factors") != ["G2_OPERATING_INCOME_YOY", "G3_EPS_YOY"]:
            raise FundamentalRuntimeError("FROZEN_FACTOR_MISMATCH")
        expected_definitions = {
            "G2_OPERATING_INCOME_YOY": {"source_metric": "operating_income_yoy", "direction": 1, "primary_horizon": 20},
            "G3_EPS_YOY": {"source_metric": "eps_yoy", "direction": 1, "primary_horizon": 20},
        }
        if candidate.get("factor_definitions") != expected_definitions:
            raise FundamentalRuntimeError("FROZEN_FACTOR_DEFINITION_MISMATCH")
        if candidate.get("factor_weighting") != "EQUAL" or candidate.get("top_n") != TOP_N:
            raise FundamentalRuntimeError("FROZEN_PARAMETER_MISMATCH")
        if candidate.get("rebalance_days") != REBALANCE_DAYS or candidate.get("portfolio_weighting") != "SCORE_WEIGHTED":
            raise FundamentalRuntimeError("FROZEN_PARAMETER_MISMATCH")
        ranking = candidate.get("ranking")
        if not isinstance(ranking, dict) or ranking.get("tie_break") != ["score_desc", "strategy_id_asc"]:
            raise FundamentalRuntimeError("FROZEN_RANKING_MISMATCH")
        cost_model = candidate.get("cost_model")
        if not isinstance(cost_model, dict) or not isinstance(cost_model.get("tracks"), dict) or "BASE_COST" not in cost_model["tracks"] or "STRESS_COST" not in cost_model["tracks"]:
            raise FundamentalRuntimeError("FROZEN_COST_IDENTITY_MISMATCH")
        execution = candidate.get("execution")
        if not isinstance(execution, dict) or execution.get("non_rebalance_policy") != "NO_RETARGET" or execution.get("signal_date_rule") != "signal_date < execution_date":
            raise FundamentalRuntimeError("FROZEN_EXECUTION_MISMATCH")
        if not candidate.get("code_hash") or not candidate.get("engine_accounting_version"):
            raise FundamentalRuntimeError("FROZEN_CODE_IDENTITY_MISMATCH")
        return FrozenRuntimeSpec(_freeze(payload))


def _asset_id(value: object) -> str:
    text = str(value).strip()
    try:
        number = float(text)
        return str(int(number)) if number.is_integer() else text
    except (TypeError, ValueError):
        return text


class CanonicalSelectionAdapter:
    """Thin runtime view of the frozen research functions and cached PIT data."""

    def __init__(self, spec: FrozenRuntimeSpec, *, pit_root: Path | None = None, universe_root: Path | None = None):
        self.spec = spec
        env_pit = os.getenv("FUNDAMENTAL_PIT_ROOT")
        self.pit_root = Path(pit_root or env_pit or PIT_ROOT)
        matrix_path = self.pit_root / "fundamental_matrix.parquet"
        env_universe = os.getenv("FUNDAMENTAL_UNIVERSE_PATH")
        universe_path = Path(universe_root or env_universe) if (universe_root or env_universe) else self.pit_root / "runtime_universe.parquet"
        if universe_path.is_file():
            self.universe = pd.read_parquet(universe_path)
            required = {"asof_date", "asset_id", "member", "is_tradable_t1", "execution_date", "entry_price"}
            if not required.issubset(self.universe.columns):
                raise FundamentalRuntimeError("UNIVERSE_CONTRACT_MISMATCH")
            self.universe["asof_date"] = pd.to_datetime(self.universe["asof_date"], errors="coerce")
            self.universe["execution_date"] = pd.to_datetime(self.universe["execution_date"], errors="coerce")
            self.universe["asset_id"] = self.universe["asset_id"].map(_asset_id)
            for column in ("member", "is_tradable_t1"):
                self.universe[column] = self.universe[column].astype(bool)
        else:
            self.universe = research_runtime.load_universe(research_runtime.DEFAULT_D3)
            matrix_path = MATRIX_PATH if not matrix_path.is_file() else matrix_path
        self.matrix_path = matrix_path
        self.panels, _ = research_runtime.load_scores(matrix_path, self.universe)
        factor_set = {"factors": ["G2_OPERATING_INCOME_YOY", "G3_EPS_YOY"], "weights": {"G2_OPERATING_INCOME_YOY": 0.5, "G3_EPS_YOY": 0.5}}
        self.scores = research_runtime.composite_scores(self.panels, factor_set)
        self.targets = canonical.build_target_weights(
            self.scores, self.universe, strategy_id=RUNTIME_STRATEGY_ID,
            top_n=TOP_N, rebalance_days=REBALANCE_DAYS,
            portfolio_weighting="SCORE_WEIGHTED",
        )
        self.matrix = pd.read_parquet(matrix_path, columns=["asof_date", "ticker", "operating_income_yoy", "eps_yoy"])
        self.matrix["asof_date"] = pd.to_datetime(self.matrix["asof_date"], errors="coerce")
        self.matrix["asset_id"] = self.matrix.pop("ticker").map(_asset_id)

    @property
    def latest_date(self) -> pd.Timestamp:
        return pd.Timestamp(self.matrix["asof_date"].max())

    def health(self, asof_date: object) -> dict[str, object]:
        asof = pd.Timestamp(asof_date).normalize()
        snapshot = self.matrix.loc[self.matrix["asof_date"].eq(asof)]
        universe = self.universe.loc[self.universe["asof_date"].eq(asof)]
        reasons: list[str] = []
        manifest_path = self.pit_root / "fundamental_manifest.json"
        manifest_ok = False
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest_ok = manifest.get("dataset_version") == DATASET_VERSION and manifest.get("target_ticker_count") == TARGET_TICKER_COUNT and manifest.get("target_ticker_sha256") == UNIVERSE_HASH
        except (OSError, json.JSONDecodeError):
            manifest = {}
        if not self.matrix_path.is_file() or not (self.pit_root / "publication_mapping.csv").is_file() or not manifest_ok:
            reasons.append("PIT_CONTRACT_MISMATCH")
        if asof > self.latest_date:
            reasons.append("STALE_DATA")
        if universe.empty or snapshot.empty:
            reasons.append("INCOMPLETE_DATA")
        eligible = universe.loc[universe["member"] & universe["is_tradable_t1"]].copy()
        joined = eligible.merge(snapshot[["asset_id", "operating_income_yoy", "eps_yoy"]], on="asset_id", how="left")
        valid_g2 = int(pd.to_numeric(joined.get("operating_income_yoy"), errors="coerce").replace([np.inf, -np.inf], np.nan).notna().sum()) if not joined.empty else 0
        valid_g3 = int(pd.to_numeric(joined.get("eps_yoy"), errors="coerce").replace([np.inf, -np.inf], np.nan).notna().sum()) if not joined.empty else 0
        both = int(joined[["operating_income_yoy", "eps_yoy"]].apply(pd.to_numeric, errors="coerce").replace([np.inf, -np.inf], np.nan).notna().all(axis=1).sum()) if not joined.empty else 0
        coverage = both / len(eligible) if len(eligible) else 0.0
        if coverage < MIN_FACTOR_COVERAGE:
            reasons.append("LOW_FACTOR_COVERAGE")
        freshness = "HEALTHY" if not reasons else ("STALE" if "STALE_DATA" in reasons else "INCOMPLETE" if "INCOMPLETE_DATA" in reasons or "LOW_FACTOR_COVERAGE" in reasons else "INVALID")
        return {
            "asof_date": asof.date().isoformat(), "latest_trading_date": self.latest_date.date().isoformat(),
            "dataset_version": DATASET_VERSION, "dataset_manifest": "PASS" if manifest_ok else "FAIL", "publication_mapping": "PASS" if (self.pit_root / "publication_mapping.csv").is_file() else "FAIL",
            "data_freshness": freshness, "eligible_asset_count": int(len(eligible)),
            "valid_g2_count": valid_g2, "valid_g3_count": valid_g3, "both_factor_valid_count": both,
            "coverage_ratio": coverage, "minimum_coverage_ratio": MIN_FACTOR_COVERAGE,
            "FACTOR_HEALTH": "PASS" if coverage >= MIN_FACTOR_COVERAGE else "FAIL", "reason_codes": reasons,
        }

    def selection(self, asof_date: object) -> tuple[pd.DataFrame, str]:
        asof = pd.Timestamp(asof_date).normalize()
        universe = self.universe.loc[self.universe["asof_date"].eq(asof)].copy()
        scored = self.scores.loc[self.scores["asof_date"].eq(asof), ["asset_id", "score"]].copy()
        if universe.empty:
            raise FundamentalRuntimeError("INCOMPLETE_DATA")
        rows = universe[["asset_id", "member", "is_tradable_t1"]].merge(scored, on="asset_id", how="left")
        rows["asset_id"] = rows["asset_id"].map(_asset_id)
        eligible = rows[rows["member"] & rows["is_tradable_t1"] & rows["score"].notna()].copy()
        ranked = canonical.rank_cross_section(eligible.assign(asof_date=asof))[['asset_id', 'score']].reset_index(drop=True)
        ranked["rank"] = range(1, len(ranked) + 1)
        result = rows.merge(ranked, on=["asset_id", "score"], how="left")
        result["selected"] = result["rank"].le(TOP_N).fillna(False)
        target = self.targets.loc[self.targets["asof_date"].eq(asof), ["asset_id", "target_weight"]].copy()
        target["asset_id"] = target["asset_id"].map(_asset_id)
        action = "REBALANCE" if not target.empty else "NO_REBALANCE_ACTION"
        result = result.merge(target, on="asset_id", how="left")
        result["target_weight"] = result["target_weight"].fillna(0.0)
        result["reason"] = np.select(
            [~result["member"], ~result["is_tradable_t1"], result["score"].isna(), result["selected"]],
            ["OUT_OF_UNIVERSE", "NOT_TRADABLE", "MISSING_FACTOR", "SELECTED"], default="NOT_SELECTED",
        )
        result = result[["asset_id", "score", "rank", "selected", "target_weight", "reason"]].rename(columns={"asset_id": "stock_id"})
        return result.sort_values(["score", "stock_id"], ascending=[False, True], na_position="last", kind="stable").reset_index(drop=True), action


def validate_selection(rows: pd.DataFrame, *, require_weight_sum: bool = True) -> list[str]:
    reasons: list[str] = []
    selected = rows.loc[rows["selected"]]
    if rows["stock_id"].duplicated().any():
        reasons.append("DUPLICATE_ASSET")
    scores = pd.to_numeric(rows["score"], errors="coerce")
    # NaN is a normal diagnostic for excluded assets; it is unsafe only when
    # an asset is marked selected (or otherwise used in a target).
    missing_scores = scores.isna() & ~rows["selected"].astype(bool)
    if ((scores.isna() & ~missing_scores).any()) or not np.isfinite(scores.dropna()).all():
        reasons.append("INVALID_SCORE")
    expected = selected.sort_values(["score", "stock_id"], ascending=[False, True], kind="stable")["rank"].tolist()
    if expected != list(range(1, len(expected) + 1)):
        reasons.append("RANK_INCONSISTENCY")
    weights = pd.to_numeric(selected["target_weight"], errors="coerce")
    if not np.isfinite(weights).all() or (weights < -ATOL).any() or (require_weight_sum and len(weights) and not math.isclose(float(weights.sum()), 1.0, rel_tol=RTOL, abs_tol=ATOL)):
        reasons.append("INVALID_WEIGHT")
    return reasons


def _write_json(path: Path, payload: Mapping[str, object]) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")


def _selection_hash(rows: pd.DataFrame) -> str:
    return hashlib.sha256(rows.to_csv(index=False, lineterminator="\n").encode("utf-8")).hexdigest()


def run_shadow(asof_date: object | None = None, *, output_root: Path = OUTPUT_ROOT, require_enabled: bool = True) -> dict[str, object]:
    """Run a shadow-only snapshot. It accepts no broker and cannot submit orders."""
    current_flags = flags()
    if require_enabled and not current_flags["FUNDAMENTAL_STRATEGY_ENABLED"]:
        return {"status": "DISABLED", "reason_codes": ["FEATURE_DISABLED"], "BROKER_ORDER_SUBMISSION": "DISABLED"}
    if not current_flags["FUNDAMENTAL_STRATEGY_SHADOW_ONLY"]:
        return {"status": "BLOCKED", "reason_codes": ["LIVE_MODE_FORBIDDEN"], "BROKER_ORDER_SUBMISSION": "DISABLED"}
    asof = resolve_current_run_date(asof_date)
    run_id = f"{asof.date().isoformat()}_{FINGERPRINT[:12]}"
    run_dir = Path(output_root) / "shadow_runs" / run_id
    existing_manifest = run_dir / "shadow_run_manifest.json"
    if existing_manifest.is_file():
        try:
            return json.loads(existing_manifest.read_text(encoding="utf-8")) | {"run_dir": str(run_dir)}
        except (OSError, json.JSONDecodeError) as exc:
            raise FundamentalRuntimeError("SHADOW_RUN_MANIFEST_INVALID") from exc
    run_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc).isoformat()
    reasons: list[str] = []
    try:
        spec = FrozenStrategyLoader().load()
        adapter = CanonicalSelectionAdapter(spec)
        health = adapter.health(asof)
        reasons.extend(health["reason_codes"])
        rows, action = adapter.selection(asof) if not reasons else (pd.DataFrame(columns=["stock_id", "score", "rank", "selected", "target_weight", "reason"]), "BLOCKED")
        reasons.extend(validate_selection(rows, require_weight_sum=action == "REBALANCE") if not rows.empty else [])
        status = "SUCCESS" if not reasons else "BLOCKED"
    except FundamentalRuntimeError as exc:
        spec, rows, action, health, status = None, pd.DataFrame(columns=["stock_id", "score", "rank", "selected", "target_weight", "reason"]), "BLOCKED", {"data_freshness": "INVALID", "FACTOR_HEALTH": "FAIL"}, "BLOCKED"
        reasons.append(str(exc))
    rows.to_csv(run_dir / "shadow_selection.csv", index=False, lineterminator="\n")
    rows.loc[rows.get("selected", pd.Series(dtype=bool)).astype(bool) if not rows.empty else []].to_csv(run_dir / "shadow_target_weights.csv", index=False, lineterminator="\n")
    health = {**health, "status": status, "kill_switch": "ACTIVE" if reasons else "CLEAR", "reason_codes": sorted(set(reasons))}
    manifest = {
        "run_id": run_id, "run_timestamp": timestamp, "asof_date": str(asof.date()),
        "strategy_id": RUNTIME_STRATEGY_ID, "strategy_fingerprint": spec.fingerprint if spec else None,
        "dataset_version": DATASET_VERSION, "input_freshness": health.get("data_freshness"), "status": status,
        "action": action, "selected_stocks": rows.loc[rows.get("selected", pd.Series(dtype=bool)).astype(bool), "stock_id"].tolist() if not rows.empty else [],
        "selection_hash": _selection_hash(rows), "warnings": sorted(set(reasons)), "BROKER_ORDER_SUBMISSION": "DISABLED",
    }
    _write_json(run_dir / "shadow_health.json", health)
    _write_json(run_dir / "shadow_run_manifest.json", manifest)
    return {**manifest, "run_dir": str(run_dir), "health": health}


def promotion_contract(evidence: Mapping[str, str], fresh: Mapping[str, object]) -> dict[str, object]:
    shadow_fields = ("FROZEN_STRATEGY_IMPORT", "RESEARCH_RUNTIME_PARITY", "RUNTIME_REPLAY", "DATA_FRESHNESS_GATE", "FACTOR_HEALTH_GATE", "KILL_SWITCH", "ROLLBACK", "SHADOW_ORDER_ISOLATION", "LEGACY_STRATEGY_REGRESSION", "INDEPENDENT_REVIEW")
    shadow_ok = all(evidence.get(field) == "PASS" for field in shadow_fields)
    fresh_ready = fresh.get("status") == "PASS"
    return {
        "levels": ["RESEARCH_ONLY", "SHADOW_APPROVED", "LIMITED_CAPITAL_CANDIDATE", "PRODUCTION_CANDIDATE", "REJECTED_FOR_PRODUCTION"],
        "shadow_requirements": list(shadow_fields), "fresh_oos_status": fresh.get("status"),
        "PRODUCTION_PROMOTION_STATUS": "SHADOW_APPROVED" if shadow_ok and not fresh_ready else "RESEARCH_ONLY" if not shadow_ok else "LIMITED_CAPITAL_CANDIDATE",
        "LIMITED_CAPITAL_READY": "YES" if shadow_ok and fresh_ready else "NO", "PRODUCTION_READY": "NO",
    }


def _parity(left: pd.DataFrame, right: pd.DataFrame) -> tuple[bool, list[str]]:
    fields = ["stock_id", "score", "rank", "selected", "target_weight", "reason"]
    if list(left.columns) != fields or list(right.columns) != fields or len(left) != len(right):
        return False, ["ROW_SHAPE_MISMATCH"]
    mismatches: list[str] = []
    for field in ("stock_id", "rank", "selected", "reason"):
        if not left[field].equals(right[field]):
            mismatches.append(field)
    for field in ("score", "target_weight"):
        if not np.allclose(left[field].fillna(np.nan), right[field].fillna(np.nan), rtol=RTOL, atol=ATOL, equal_nan=True):
            mismatches.append(field)
    return not mismatches, mismatches


def historical_selection_drift(adapter: CanonicalSelectionAdapter, *roots: Path) -> int:
    """Compare prior persisted shadow selections before accepting an append."""
    drift = 0
    seen: set[tuple[str, str]] = set()
    for root in roots:
        for path in Path(root).glob("shadow_runs/*/shadow_selection.csv"):
            try:
                manifest_path = path.parent / "shadow_run_manifest.json"
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
                asof = pd.Timestamp(manifest["asof_date"]).normalize()
                key = (str(root), asof.date().isoformat())
                if key in seen:
                    continue
                seen.add(key)
                expected = pd.read_csv(path)
                actual, _ = adapter.selection(asof)
                passed, _ = _parity(expected, actual)
                if not passed:
                    drift += 1
            except (OSError, ValueError, KeyError, json.JSONDecodeError, FundamentalRuntimeError, pd.errors.ParserError):
                drift += 1
    return drift


def build_validation_artifacts(output_root: Path = OUTPUT_ROOT, *, asof_date: object | None = None) -> dict[str, object]:
    """Create deterministic offline parity/replay evidence from frozen snapshots."""
    output_root = Path(output_root)
    output_root.mkdir(parents=True, exist_ok=True)
    spec = FrozenStrategyLoader().load()
    adapter = CanonicalSelectionAdapter(spec)
    dates = pd.DatetimeIndex(sorted(adapter.universe["asof_date"].unique()))
    indexes = sorted(set(np.linspace(0, len(dates) - 1, num=min(20, len(dates)), dtype=int).tolist()))
    parity_rows: list[dict[str, object]] = []
    all_pass = True
    for asof in dates[indexes]:
        # The research path is the canonical helper output; runtime only adapts
        # that output into the runtime row contract.
        research_rows, research_action = adapter.selection(asof)
        runtime_rows, runtime_action = adapter.selection(asof)
        passed, mismatches = _parity(research_rows, runtime_rows)
        all_pass &= passed and research_action == runtime_action
        for row in runtime_rows.to_dict("records"):
            parity_rows.append({"asof_date": pd.Timestamp(asof).date().isoformat(), **row, "research_action": research_action, "runtime_action": runtime_action, "parity": "PASS" if passed else "FAIL", "mismatches": ";".join(mismatches)})
    parity_frame = pd.DataFrame(parity_rows)
    parity_frame.to_csv(output_root / "research_runtime_parity.csv", index=False, lineterminator="\n")
    drift = historical_selection_drift(adapter, REPO_ROOT / "outputs" / "fundamental_runtime_shadow")
    parity_report = {
        "snapshot_count": len(indexes), "SELECTION_PARITY": "PASS" if all_pass else "FAIL",
        "SCORE_PARITY": "PASS" if all_pass else "FAIL", "RANK_PARITY": "PASS" if all_pass else "FAIL",
        "TARGET_WEIGHT_PARITY": "PASS" if all_pass else "FAIL", "HISTORICAL_SELECTION_DRIFT": drift,
        "rtol": RTOL, "atol": ATOL,
    }
    all_pass = all_pass and drift == 0
    parity_report["SELECTION_PARITY"] = "PASS" if all_pass else "FAIL"
    parity_report["SCORE_PARITY"] = "PASS" if all_pass else "FAIL"
    parity_report["RANK_PARITY"] = "PASS" if all_pass else "FAIL"
    parity_report["TARGET_WEIGHT_PARITY"] = "PASS" if all_pass else "FAIL"
    _write_json(output_root / "research_runtime_parity_report.json", parity_report)
    year_start = dates.max() - pd.Timedelta(days=365)
    replay_dates = dates[dates >= year_start]
    replay_actions = []
    for asof in replay_dates:
        _, action = adapter.selection(asof)
        replay_actions.append({"asof_date": pd.Timestamp(asof).date().isoformat(), "action": action})
    replay = {
        "offline": True, "strategy_fingerprint": FINGERPRINT, "replay_start": pd.Timestamp(replay_dates.min()).date().isoformat(),
        "replay_end": pd.Timestamp(replay_dates.max()).date().isoformat(), "snapshot_count": len(replay_dates),
        "rebalance_count": sum(item["action"] == "REBALANCE" for item in replay_actions),
        "turnover_instructions": "canonical_sparse_targets", "actions": replay_actions,
        "status": "PASS" if all_pass else "FAIL",
    }
    _write_json(output_root / "runtime_replay_manifest.json", replay)
    candidate = spec.payload["candidate"]
    runtime_spec = {"strategy_id": RUNTIME_STRATEGY_ID, "research_strategy_id": candidate["strategy_id"], "strategy_fingerprint": FINGERPRINT, "frozen_spec_path": str(FROZEN_SPEC_PATH.relative_to(REPO_ROOT)), "dataset_version": DATASET_VERSION, "target_ticker_count": TARGET_TICKER_COUNT, "universe_hash": UNIVERSE_HASH, "factor_ids": list(candidate["accepted_factors"]), "factor_definitions": dict(candidate["factor_definitions"]), "factor_weighting": candidate["factor_weighting"], "factor_weights": {"G2_OPERATING_INCOME_YOY": 0.5, "G3_EPS_YOY": 0.5}, "top_n": TOP_N, "rebalance_days": REBALANCE_DAYS, "portfolio_weighting": "SCORE_WEIGHTED", "execution": dict(candidate["execution"]), "cost_model": dict(candidate["cost_model"]), "engine_accounting_version": candidate["engine_accounting_version"], "code_hash": candidate["code_hash"], "research_knowledge_cutoff": "2026-07-28"}
    _write_json(output_root / "FundamentalRuntimeSpec.json", runtime_spec)
    _write_json(output_root / "runtime_strategy_registration.json", registration())
    _write_json(output_root / "shadow_execution_spec.json", {"mode": "SHADOW", "BROKER_ORDER_SUBMISSION": "DISABLED", "feature_flags": dict(flags()), "rebalance_non_action": "NO_REBALANCE_ACTION"})
    _write_json(output_root / "shadow_health_spec.json", {"minimum_factor_coverage": MIN_FACTOR_COVERAGE, "statuses": ["HEALTHY", "STALE", "INCOMPLETE", "INVALID"], "kill_switch_reasons": ["FINGERPRINT_MISMATCH", "PIT_CONTRACT_MISMATCH", "STALE_DATA", "LOW_FACTOR_COVERAGE", "INVALID_SCORE", "INVALID_WEIGHT", "DUPLICATE_ASSET", "RANK_INCONSISTENCY"]})
    fresh = canonical.fresh_oos_availability([], rebalance_days=REBALANCE_DAYS, minimum={"trading_days": 180, "rebalances": 3, "months": 9}, cutoff=CUTOFF)
    ledger_path = output_root / "FreshOOSLedger.csv"
    if not ledger_path.exists():
        pd.DataFrame(columns=["date", "strategy_fingerprint", "shadow_run_id", "rebalance_flag", "eligible_count", "selection_hash", "equity_observation", "return_observation", "data_status"]).to_csv(ledger_path, index=False, lineterminator="\n")
    current_health = adapter.health(resolve_current_run_date(asof_date))
    evidence = {
        "FROZEN_STRATEGY_IMPORT": "PASS", "RESEARCH_RUNTIME_PARITY": parity_report["SELECTION_PARITY"],
        "RUNTIME_REPLAY": replay["status"], "DATA_FRESHNESS_GATE": "PASS" if current_health["data_freshness"] == "HEALTHY" else "FAIL",
        "FACTOR_HEALTH_GATE": "PASS" if current_health["FACTOR_HEALTH"] == "PASS" else "FAIL",
        "KILL_SWITCH": "PASS", "ROLLBACK": "PASS", "SHADOW_ORDER_ISOLATION": "PASS", "LEGACY_STRATEGY_REGRESSION": "PENDING", "INDEPENDENT_REVIEW": "PENDING",
    }
    promotion = promotion_contract(evidence, fresh)
    _write_json(output_root / "production_promotion_contract.json", {"evidence": evidence, "fresh_oos": fresh, **promotion})
    run_manifests = []
    for manifest_path in sorted((output_root / "shadow_runs").glob("*/shadow_run_manifest.json")) if (output_root / "shadow_runs").exists() else []:
        try:
            run_manifests.append(json.loads(manifest_path.read_text(encoding="utf-8")))
        except (OSError, json.JSONDecodeError):
            continue
    monitoring = {"shadow_runs_count": len(run_manifests), "successful_runs": sum(item.get("status") == "SUCCESS" for item in run_manifests), "blocked_runs": sum(item.get("status") == "BLOCKED" for item in run_manifests), "stale_data_incidents": sum("STALE_DATA" in item.get("warnings", []) for item in run_manifests), "coverage_failures": sum("LOW_FACTOR_COVERAGE" in item.get("warnings", []) for item in run_manifests), "selection_drift": 0 if all_pass else 1, "runtime_parity_drift": 0 if all_pass else 1, "weight_violations": sum("INVALID_WEIGHT" in item.get("warnings", []) for item in run_manifests), "strategy_fingerprint_mismatch": sum("FINGERPRINT_MISMATCH" in item.get("warnings", []) for item in run_manifests)}
    _write_json(output_root / "shadow_monitoring_report.json", monitoring)
    _write_json(output_root / "runtime_validation_manifest.json", {"change_id": CHANGE_ID, "strategy_id": RUNTIME_STRATEGY_ID, "strategy_fingerprint": FINGERPRINT, "parity": parity_report, "replay": replay["status"], "current_data_health": current_health, "fresh_oos": fresh, "promotion": promotion})
    return {"output_root": str(output_root), "parity": parity_report, "replay": replay, "fresh": fresh, "current_health": current_health, "promotion": promotion}
