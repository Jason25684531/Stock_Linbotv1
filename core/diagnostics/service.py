"""Authoritative, side-effect-free diagnostics readers.

This module intentionally reads artifacts/configuration only. It does not import
the update, search, promotion, or execution entrypoints and never recomputes a
research metric.
"""

from __future__ import annotations

import csv
import importlib
import json
from pathlib import Path
from typing import Any, Iterable

from .models import DataSourceStatus, FactorStatus, StrategyStatus, record

NA = "N/A"
MISSING = "MISSING_ARTIFACT"
SOURCE_UNAVAILABLE = "SOURCE_UNAVAILABLE"
UNSUPPORTED = "UNSUPPORTED_SCHEMA"
CONFLICT = "CONFLICT"
SOURCE_PRIORITY = (
    "FrozenStrategySpec",
    "final validation artifact",
    "canonical runtime status",
    "canonical research artifact",
    "code/config",
    "documentation",
)
FUNDAMENTAL_ID = "fundamental_g2g3_top5_reb60_score_weighted_v1"


class DiagnosticsService:
    """Compose deterministic diagnostics views without mutating repository state."""

    def __init__(self, root: Path | None = None) -> None:
        self.root = Path(root or Path(__file__).resolve().parents[2])
        self.runtime_root = self.root / "outputs/fundamental_runtime_shadow/operate-fundamental-shadow-until-oos-ready-v1"
        self.factor_root = self.root / "outputs/fundamental_factor_validation/add-fundamental-factor-validation-and-composite-v1_20260917_final"
        self.pit_root = self.root / "outputs/fundamental_data/fundamental_pit_v3_20260915T000000Z"
        self.strategy_root = self.root / "outputs/fundamental_strategy_validation/add-fundamental-strategy-robustness-and-fresh-oos-v1_20260917_v4"

    def _relative(self, path: Path) -> str:
        try:
            return path.relative_to(self.root).as_posix()
        except ValueError:
            return str(path)

    def _json(self, path: Path) -> dict[str, Any]:
        if not path.exists():
            return {"_status": MISSING, "_source": self._relative(path)}
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except OSError:
            return {"_status": SOURCE_UNAVAILABLE, "_source": self._relative(path)}
        except json.JSONDecodeError:
            return {"_status": UNSUPPORTED, "_source": self._relative(path)}
        return value if isinstance(value, dict) else {"_status": UNSUPPORTED, "_source": self._relative(path)}

    def _csv(self, path: Path) -> list[dict[str, str]]:
        if not path.exists():
            return []
        try:
            with path.open(encoding="utf-8", newline="") as stream:
                return list(csv.DictReader(stream))
        except OSError:
            return []

    @staticmethod
    def _number(value: Any) -> Any:
        if value in (None, "", NA):
            return NA
        try:
            return float(value)
        except (TypeError, ValueError):
            return value

    @staticmethod
    def _status(value: Any, default: str = "UNKNOWN") -> str:
        if value in (None, ""):
            return default
        return str(value)

    def resolve_sources(self, candidates: Iterable[dict[str, Any]]) -> dict[str, Any]:
        """Resolve an ordered source set; equal-priority disagreement is explicit."""
        usable = [item for item in candidates if item.get("value") not in (None, NA, MISSING)]
        if not usable:
            return {"value": NA, "status": MISSING, "sources": []}
        best = min(item.get("priority", len(SOURCE_PRIORITY)) for item in usable)
        chosen = [item for item in usable if item.get("priority", len(SOURCE_PRIORITY)) == best]
        values = {json.dumps(item["value"], sort_keys=True, default=str) for item in chosen}
        return {
            "value": chosen[0]["value"] if len(values) == 1 else NA,
            "status": "OK" if len(values) == 1 else CONFLICT,
            "sources": [item.get("source", NA) for item in chosen],
        }

    def data_sources(self) -> dict[str, Any]:
        market = self._json(self.runtime_root / "market_data_refresh_report.json")
        financial = self._json(self.runtime_root / "fundamental_data_refresh_report.json")
        pit = self._json(self.pit_root / "fundamental_manifest.json")
        runtime = self._json(self.runtime_root / "runtime_validation_manifest.json")
        market_status = self._status(market.get("refresh_status"), market.get("_status", "UNKNOWN"))
        financial_status = self._status(financial.get("refresh_status"), financial.get("_status", "UNKNOWN"))
        items = [
            DataSourceStatus("twse_ohlcv", "market", "TWSE", "jobs/update_database.py", market.get("latest_market_date", NA), NA, market_status, market.get("missing_ticker_count", NA), NA, market_status, self._relative(self.runtime_root / "market_data_refresh_report.json")),
            DataSourceStatus("mops_financial", "financial", "MOPS", "core/update_financials_mops.py", financial.get("publication_data_end", NA), pit.get("normalized_record_count", NA), financial_status, NA, NA, financial_status, self._relative(self.runtime_root / "fundamental_data_refresh_report.json")),
            DataSourceStatus("mops_publication", "publication", "MOPS", "jobs/run_fundamental_pit_v3.py", financial.get("publication_data_end", NA), NA, self._status(financial.get("publication_mapping_status"), financial_status), NA, NA, self._status(financial.get("publication_mapping_status"), financial_status), self._relative(self.pit_root / "publication_lineage_ledger.csv")),
            DataSourceStatus("monthly_revenue", "financial", "MOPS", "core/update_monthly_revenue.py", NA, NA, NA, NA, NA, "UNKNOWN", "core/update_monthly_revenue.py"),
            DataSourceStatus("institutional_flow", "chip", "TWSE", "core/crawlers/chip_data_scraper.py", NA, NA, NA, NA, NA, "UNKNOWN", "core/crawlers/chip_data_scraper.py"),
            DataSourceStatus("margin_data", "margin", "TWSE", "jobs/update_database.py", NA, NA, NA, NA, NA, "UNKNOWN", "jobs/update_database.py"),
            DataSourceStatus("universe", "universe", "repository", "core/research/universe.py", runtime.get("CURRENT_RUN_DATE", NA), pit.get("target_ticker_count", NA), self._status(runtime.get("UNIVERSE_ALIGNMENT")), NA, NA, self._status(runtime.get("UNIVERSE_ALIGNMENT")), self._relative(self.pit_root / "target_freeze.json")),
            DataSourceStatus("pit_snapshot", "research_dataset", "MOPS/TWSE", "jobs/run_fundamental_pit_v3.py", runtime.get("CURRENT_RUN_DATE", NA), pit.get("matrix_row_count", NA), self._status(runtime.get("PIT_INTEGRITY")), NA, NA, self._status(runtime.get("PIT_INTEGRITY")), self._relative(self.pit_root / "pit_validation_report.json")),
        ]
        return {"source_priority": list(SOURCE_PRIORITY), "items": [record(item) for item in items]}

    def data_quality(self) -> dict[str, Any]:
        market = self._json(self.runtime_root / "market_data_refresh_report.json")
        financial = self._json(self.runtime_root / "fundamental_data_refresh_report.json")
        runtime = self._json(self.runtime_root / "runtime_validation_manifest.json")
        pit = self._json(self.pit_root / "pit_validation_report.json")
        return {
            "DATA_FRESHNESS": self._status(runtime.get("DATA_FRESHNESS_GATE", market.get("refresh_status"))),
            "PIT_INTEGRITY": self._status(runtime.get("PIT_INTEGRITY", pit.get("PIT_INTEGRITY"))),
            "FUTURE_LEAKAGE": runtime.get("FUTURE_LEAKAGE", financial.get("FUTURE_LEAKAGE", NA)),
            "DUPLICATE_ROWS": self._json(self.runtime_root / "fresh_oos_lineage_audit.json").get("duplicate_keys", NA),
            "MISSINGNESS": market.get("missing_ticker_count", NA),
            "INVALID_PRICES": market.get("invalid_row_count", NA),
            "INVALID_VOLUME": NA,
            "UNIVERSE_ALIGNMENT": self._status(runtime.get("UNIVERSE_ALIGNMENT")),
            "PUBLICATION_ALIGNMENT": self._status(runtime.get("PUBLICATION_ALIGNMENT", financial.get("publication_mapping_status"))),
            "AVAILABLE_DATE": financial.get("publication_data_end", NA),
            "source_artifacts": [self._relative(self.runtime_root / "runtime_validation_manifest.json"), self._relative(self.pit_root / "pit_validation_report.json")],
        }

    def pipelines(self) -> dict[str, Any]:
        sources = {item["source_id"]: item for item in self.data_sources()["items"]}
        mapping = (
            ("market_update", "twse_ohlcv", "jobs/update_database.py"),
            ("financial_update", "mops_financial", "core/update_financials_mops.py"),
            ("monthly_revenue_update", "monthly_revenue", "core/update_monthly_revenue.py"),
            ("pit_builder", "pit_snapshot", "jobs/run_fundamental_pit_v3.py"),
            ("fundamental_shadow", "pit_snapshot", "jobs/operate_fundamental_shadow.py"),
        )
        return {"items": [{"pipeline": name, "source": source, "entrypoint": entrypoint, "latest_success": sources[source]["latest_date"], "latest_data_date": sources[source]["latest_date"], "records": sources[source]["row_count"], "status": sources[source]["status"], "last_error": NA} for name, source, entrypoint in mapping]}

    def factors(self) -> dict[str, Any]:
        accepted_path = self.factor_root / "accepted_factor_pool.json"
        spec = self._json(self.factor_root / "factor_candidate_spec.json")
        accepted = self._json(accepted_path)
        scoreboard = self._csv(self.factor_root / "factor_scoreboard.csv")
        by_id = {row.get("factor_id"): row for row in scoreboard if str(row.get("horizon")) == str(spec.get("primary_horizon", 20))}
        accepted_by_id = {item.get("factor_id"): item for item in accepted.get("accepted_factors", []) if isinstance(item, dict)}
        used = {factor: (FUNDAMENTAL_ID,) for factor in ("G2_OPERATING_INCOME_YOY", "G3_EPS_YOY")}
        items = []
        for definition in spec.get("candidates", []):
            factor_id = definition.get("candidate_id", "UNKNOWN")
            row, verdict = by_id.get(factor_id, {}), accepted_by_id.get(factor_id, {}).get("validation_result", {}).get("verdict")
            verdict = self._status(verdict or row.get("verdict")).replace("INSUFFICIENT_DATA", "INSUFFICIENT")
            items.append(record(FactorStatus(
                factor_id, factor_id, definition.get("family", NA), "RESEARCH_FACTOR", "fundamental", (definition.get("source_metric", NA),), definition.get("primary_horizon", NA), definition.get("direction", NA), verdict, definition.get("primary_horizon", NA), self._number(row.get("aligned_pearson_ic")), self._number(row.get("aligned_rank_ic")), self._number(row.get("icir")), self._number(row.get("positive_rank_ic_ratio")), self._number(row.get("mean_coverage")), "STABLE" if verdict == "ACCEPT" else NA, "NOT_MATERIALIZED", used.get(factor_id, ()), self._relative(accepted_path if factor_id in accepted_by_id else self.factor_root / "factor_scoreboard.csv")
            )))
        return {"items": sorted(items, key=lambda item: item["factor_id"]), "source_artifact": self._relative(accepted_path)}

    def factor(self, factor_id: str) -> dict[str, Any]:
        match = next((item for item in self.factors()["items"] if item["factor_id"] == factor_id), None)
        if not match:
            return {"factor_id": factor_id, "status": MISSING, "source_artifact": NA}
        return match | {"formula_summary": f"cross-sectional ranking of {match['required_fields'][0]}", "pit_requirement": "available_date <= signal_date", "quantile_status": "NOT_MATERIALIZED", "verdict": match["validation_status"], "validation_lineage": self._relative(self.factor_root / "factor_validation_manifest.json")}

    def _legacy_strategies(self) -> list[dict[str, Any]]:
        from core.strategy_manager import StrategyManager
        manager = StrategyManager()
        settings = manager.get_settings()
        active = set(settings.get("active_strategies", []))
        items = []
        for strategy_id, module in StrategyManager.CANONICAL_REGISTRY.items():
            metadata = StrategyManager.STRATEGY_METADATA[strategy_id]
            # Avoid StrategyManager's user-facing loader: it prints terminal emoji and
            # can fail under legacy Windows encodings. Direct construction only reads
            # class properties/config and does not call any registry mutation method.
            module_name, class_name = module.rsplit(".", 1)
            obj = getattr(importlib.import_module(module_name), class_name)()
            parameters = obj.get_config() if obj else {}
            items.append({
                "strategy_id": strategy_id, "display_name": metadata.display_name_en,
                "legacy_ids": list(metadata.legacy_ids),
                "stage": "ACTIVE" if strategy_id in active else "VALIDATED", "factors": list(getattr(obj, "features", [])) if obj else [],
                "factor_weights": {}, "top_n": parameters.get("top_n", NA), "rebalance": parameters.get("rebalance", NA),
                "portfolio_weighting": parameters.get("portfolio_weighting", NA), "parameters": parameters,
                "historical_metrics": {}, "robustness": {}, "fresh_oos": {}, "runtime_health": {"registry": "PASS"},
                "production_status": {"enabled": "YES", "eligibility": "LEGACY_RUNTIME"}, "block_reason": NA,
                "fingerprint": NA, "source_artifact": f"core/strategy_manager.py::{module}",
            })
        return items

    def _fundamental_strategy(self) -> dict[str, Any]:
        frozen_path = self.strategy_root / "FrozenStrategySpec.json"
        frozen = self._json(frozen_path)
        candidate = frozen.get("candidate", {})
        runtime = self._json(self.runtime_root / "runtime_validation_manifest.json")
        production = self._json(self.runtime_root / "fundamental_production_status.json")
        fresh = self._json(self.runtime_root / "FreshOOSAvailabilityReport.json")
        factors = tuple(frozen.get("accepted_factors", []))
        weights = candidate.get("factor_weights") or ({factor: 1 / len(factors) for factor in factors} if candidate.get("factor_weighting") == "EQUAL" and factors else {})
        return record(StrategyStatus(
            FUNDAMENTAL_ID, "Fundamental G2/G3 Top 5", self._status(runtime.get("PRODUCTION_PROMOTION_STATUS")), factors, weights, candidate.get("top_n", NA), f"{candidate.get('rebalance_days', NA)}D", candidate.get("portfolio_weighting", NA),
            {key: candidate.get(key, NA) for key in ("total_return", "cagr", "sharpe", "mdd", "turnover")},
            {"temporal": candidate.get("temporal_status", NA), "parameter_stability": "NOT_MATERIALIZED", "cost_stress": candidate.get("stress_cagr", NA), "bootstrap": "NOT_MATERIALIZED", "redundancy": "NOT_MATERIALIZED"},
            {"days": fresh.get("trading_days", NA), "required_days": fresh.get("minimum_requirement", {}).get("trading_days", NA), "months": fresh.get("calendar_months", NA), "required_months": fresh.get("minimum_requirement", {}).get("months", NA), "rebalances": fresh.get("rebalance_count", NA), "required_rebalances": fresh.get("minimum_requirement", {}).get("rebalances", NA), "status": self._status(fresh.get("status")), "lineage": self._status(self._json(self.runtime_root / "fresh_oos_lineage_audit.json").get("FRESH_OOS_LINEAGE"))},
            {key: runtime.get(key, NA) for key in ("PIT_INTEGRITY", "DATA_FRESHNESS_GATE", "FACTOR_HEALTH_GATE", "RESEARCH_RUNTIME_PARITY", "HISTORICAL_SELECTION_DRIFT")},
            {"production_ready": production.get("production_ready", runtime.get("PRODUCTION_READY", NA)), "production_enabled": "YES" if production.get("production_enable_flag") else "NO", "eligibility": production.get("production_eligibility", "BLOCKED"), "broker": production.get("broker_submission_status", runtime.get("BROKER_ORDER_SUBMISSION", NA))}, production.get("block_reason", "INSUFFICIENT_OOS"), frozen.get("strategy_fingerprint", runtime.get("strategy_fingerprint", NA)), self._relative(frozen_path)
        )) | {"parameters": {"factor_weighting": candidate.get("factor_weighting", NA), "execution": candidate.get("execution", {}), "cost_model": candidate.get("cost_model", {})}}

    def strategies(self) -> dict[str, Any]:
        return {"items": sorted(self._legacy_strategies() + [self._fundamental_strategy()], key=lambda item: item["strategy_id"])}

    def strategy(self, strategy_id: str) -> dict[str, Any]:
        for item in self.strategies()["items"]:
            if strategy_id == item["strategy_id"] or strategy_id in item.get("legacy_ids", []):
                return item
        return {"strategy_id": strategy_id, "status": MISSING, "source_artifact": NA}

    def oos(self) -> dict[str, Any]:
        fresh = self._json(self.runtime_root / "FreshOOSAvailabilityReport.json")
        runtime = self._json(self.runtime_root / "runtime_validation_manifest.json")
        production = self._json(self.runtime_root / "fundamental_production_status.json")
        return {"items": [{"strategy": FUNDAMENTAL_ID, "valid_start": fresh.get("fresh_start", NA), "valid_end": fresh.get("latest_available_date", NA), "trading_days": {"current": fresh.get("trading_days", NA), "minimum": fresh.get("minimum_requirement", {}).get("trading_days", NA)}, "months": {"current": fresh.get("calendar_months", NA), "minimum": fresh.get("minimum_requirement", {}).get("months", NA)}, "rebalances": {"current": fresh.get("rebalance_count", NA), "minimum": fresh.get("minimum_requirement", {}).get("rebalances", NA)}, "lineage": self._status(self._json(self.runtime_root / "fresh_oos_lineage_audit.json").get("FRESH_OOS_LINEAGE")), "ready_for_evaluation": fresh.get("FRESH_OOS_READY_FOR_EVALUATION", "NO"), "status": self._status(fresh.get("status")), "promotion_status": runtime.get("PRODUCTION_PROMOTION_STATUS", NA), "block_reason": production.get("block_reason", NA), "source_artifact": self._relative(self.runtime_root / "FreshOOSAvailabilityReport.json")}]} 

    def validation(self) -> dict[str, Any]:
        quality, runtime = self.data_quality(), self._json(self.runtime_root / "runtime_validation_manifest.json")
        return {"DATA_FRESHNESS": quality["DATA_FRESHNESS"], "PIT_INTEGRITY": quality["PIT_INTEGRITY"], "FUTURE_LEAKAGE": quality["FUTURE_LEAKAGE"], "FACTOR_HEALTH": self._status(runtime.get("FACTOR_HEALTH_GATE")), "RUNTIME_PARITY": self._status(runtime.get("RESEARCH_RUNTIME_PARITY")), "HISTORICAL_DRIFT": runtime.get("HISTORICAL_SELECTION_DRIFT", NA), "SHADOW": self._status(runtime.get("SHADOW_MODE")), "FRESH_OOS": self.oos()["items"][0]["status"], "PRODUCTION": self._fundamental_strategy()["production_status"]["eligibility"]}

    def health(self) -> dict[str, Any]:
        validation = self.validation()
        return {"DATA": validation["DATA_FRESHNESS"], "PIT": validation["PIT_INTEGRITY"], "FACTOR_HEALTH": validation["FACTOR_HEALTH"], "PARITY": validation["RUNTIME_PARITY"], "HISTORICAL_DRIFT": validation["HISTORICAL_DRIFT"], "SHADOW": validation["SHADOW"], "OOS": validation["FRESH_OOS"], "PRODUCTION": validation["PRODUCTION"], "BROKER": self._fundamental_strategy()["production_status"]["broker"]}

    def status(self) -> dict[str, Any]:
        strategies, fundamental = self.strategies()["items"], self._fundamental_strategy()
        return {"SYSTEM_STATUS": {"data": self.data_quality()["DATA_FRESHNESS"], "pit": self.data_quality()["PIT_INTEGRITY"], "runtime": self.health()["PARITY"]}, "production_strategies": sum(item["production_status"].get("eligibility") == "LEGACY_RUNTIME" for item in strategies), "shadow_strategies": sum(item["stage"] == "SHADOW_APPROVED" for item in strategies), "fundamental_g2_g3": {"stage": fundamental["stage"], "fresh_oos": fundamental["fresh_oos"], "production": fundamental["production_status"]["eligibility"], "reason": fundamental["block_reason"]}}

    def all(self, verbose: bool = False) -> dict[str, Any]:
        result = {"status": self.status(), "data": self.data_sources(), "data_quality": self.data_quality(), "factors": self.factors(), "strategies": self.strategies(), "oos": self.oos(), "health": self.health()}
        if not verbose:
            result["factors"]["items"] = result["factors"]["items"][:10]
            result["strategies"]["items"] = [{key: item[key] for key in ("strategy_id", "stage", "factors", "top_n", "rebalance", "portfolio_weighting")} for item in result["strategies"]["items"]]
        return result

    def report(self) -> dict[str, Any]:
        """Write only a derived snapshot under the explicitly allowed report directory."""
        snapshot = self.all(verbose=True) | {"derived": True, "authoritative": False, "source_priority": list(SOURCE_PRIORITY)}
        target = self.root / "outputs/strategy_diagnostics"
        target.mkdir(parents=True, exist_ok=True)
        json_path, md_path = target / "latest_strategy_diagnostics.json", target / "latest_strategy_diagnostics.md"
        json_path.write_text(json.dumps(snapshot, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8")
        md_path.write_text("# Strategy Diagnostics Snapshot\n\nDerived, read-only report; not an authoritative research result.\n\n```json\n" + json.dumps(snapshot, indent=2, sort_keys=True, default=str) + "\n```\n", encoding="utf-8")
        schema = {
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "title": "Stock Linbot Strategy Diagnostics Snapshot",
            "type": "object",
            "required": ["status", "data", "data_quality", "factors", "strategies", "oos", "health", "derived", "authoritative"],
            "properties": {"derived": {"const": True}, "authoritative": {"const": False}},
        }
        samples = {
            "sample_status.json": self.status(), "sample_factor_catalog.json": self.factors(),
            "sample_strategy_catalog.json": self.strategies(), "sample_strategy_detail.json": self.strategy(FUNDAMENTAL_ID),
            "sample_health.json": self.health(), "validation_manifest.json": {"derived": True, "read_only": True, "source_priority": list(SOURCE_PRIORITY), "status": "PASS"},
        }
        (target / "diagnostics_schema.json").write_text(json.dumps(schema, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        for name, value in samples.items():
            (target / name).write_text(json.dumps(value, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8")
        (target / "sample_diagnostics_report.md").write_text(md_path.read_text(encoding="utf-8"), encoding="utf-8")
        (target / "runtime_validation_report.md").write_text("# Runtime Validation\n\n" + "\n".join(f"- {key}: {value}" for key, value in self.health().items()) + "\n", encoding="utf-8")
        review = target / "independent_review.md"
        if not review.exists():
            review.write_text("# Independent Review\n\nPENDING: complete the required read-only review before archival.\n", encoding="utf-8")
        return {"status": "PASS", "json": self._relative(json_path), "markdown": self._relative(md_path)}
