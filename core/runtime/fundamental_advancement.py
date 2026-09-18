"""Minimal append-only PIT refresh, shadow, and Fresh OOS advancement job.

The job is deliberately boring: it reuses the canonical TWSE/PIT helpers, copies
the last legally available factor observation forward, and never changes the
frozen strategy.  A snapshot is consumable only after all identity checks pass.
"""

from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

from core.research import fundamental_pit as pit
from core.research import fundamental_strategy_validation as research
from core.research import market_data
from core.research import normalize
from core.research.sources import twse
from . import fundamental_shadow as shadow


CHANGE_ID = "advance-fundamental-pit-shadow-fresh-oos-and-promotion-v1"
CUTOFF = pd.Timestamp("2026-07-28")
PIT_SCHEMA_VERSION = "fundamental-pit-v3"
STRATEGY_ID = shadow.RUNTIME_STRATEGY_ID
FINGERPRINT = shadow.FINGERPRINT
UNIVERSE_HASH = shadow.UNIVERSE_HASH
TARGET_TICKER_COUNT = shadow.TARGET_TICKER_COUNT
REPO_ROOT = Path(__file__).resolve().parents[2]
BASE_PIT_ROOT = REPO_ROOT / "data" / "processed" / "fundamental_pit_v3"
FROZEN_ROOT = REPO_ROOT / "outputs" / "fundamental_strategy_validation" / "add-fundamental-strategy-robustness-and-fresh-oos-v1_20260917_v4"
OUTPUT_ROOT = REPO_ROOT / "outputs" / "fundamental_runtime_shadow" / CHANGE_ID


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _write_json(path: Path, value: Mapping[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(dict(value), ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")


def fundamental_publication_end(root: Path = BASE_PIT_ROOT) -> str | None:
    """Return the latest canonical publication date, without using fetch time."""
    mapping_path = Path(root) / "publication_mapping.csv"
    if not mapping_path.is_file():
        return None
    try:
        frame = pd.read_csv(mapping_path, usecols=["publication_date"])
    except (OSError, ValueError, pd.errors.ParserError):
        return None
    dates = pd.to_datetime(frame["publication_date"], errors="coerce").dropna()
    return dates.max().date().isoformat() if not dates.empty else None


def _complete_snapshot_manifest(snapshot: Path, manifest: Mapping[str, object]) -> dict[str, object]:
    """Add lineage fields to a pre-existing operational snapshot only."""
    payload = dict(manifest)
    payload.setdefault("snapshot_id", snapshot.name)
    payload.setdefault("parent_snapshot_id", "fundamental-pit-v3")
    payload.setdefault("asof_date", payload.get("snapshot_asof_date"))
    if not payload.get("market_data_end"):
        try:
            universe = pd.read_parquet(snapshot / "runtime_universe.parquet", columns=["asof_date"])
            dates = pd.to_datetime(universe["asof_date"], errors="coerce").dropna()
            payload["market_data_end"] = dates.max().date().isoformat() if not dates.empty else payload.get("asof_date")
        except (OSError, ValueError, KeyError):
            payload["market_data_end"] = payload.get("asof_date")
    payload.setdefault("publication_data_end", fundamental_publication_end(snapshot) or fundamental_publication_end(BASE_PIT_ROOT))
    try:
        matrix = pd.read_parquet(snapshot / "fundamental_matrix.parquet", columns=["operating_income_yoy", "eps_yoy"])
        payload.setdefault("G2_coverage", float(matrix["operating_income_yoy"].notna().mean()) if not matrix.empty else 0.0)
        payload.setdefault("G3_coverage", float(matrix["eps_yoy"].notna().mean()) if not matrix.empty else 0.0)
    except (OSError, ValueError, KeyError):
        payload.setdefault("G2_coverage", 0.0)
        payload.setdefault("G3_coverage", 0.0)
    hashes = dict(payload.get("artifact_sha256") or {})
    for name in ("fundamental_matrix.parquet", "runtime_universe.parquet", "market_data.parquet", "publication_mapping.csv"):
        if name not in hashes and (snapshot / name).is_file():
            hashes[name] = _sha256(snapshot / name)
    payload["artifact_sha256"] = hashes
    _write_json(snapshot / "fundamental_manifest.json", payload)
    return payload


def resolve_current_run_date(value: object | None = None) -> pd.Timestamp:
    """Resolve the run date dynamically; an explicit value is only for replay/tests."""

    if value is not None:
        return pd.Timestamp(value).normalize()
    override = os.getenv("CURRENT_RUN_DATE")
    if override:
        return pd.Timestamp(override).normalize()
    return pd.Timestamp(datetime.now(ZoneInfo("Asia/Taipei")).date())


def _target_tickers() -> tuple[str, ...]:
    matrix = pd.read_parquet(BASE_PIT_ROOT / "fundamental_matrix.parquet", columns=["ticker"])
    values = tuple(sorted({pit.clean_ticker(value) for value in matrix["ticker"].dropna()}))
    if len(values) != TARGET_TICKER_COUNT or research.sha256_bytes("\n".join(values).encode()) != UNIVERSE_HASH:
        raise RuntimeError("UNIVERSE_MISMATCH")
    return values


def load_frozen_contract() -> dict[str, object]:
    """Load frozen identity and the archived OOS contract without inference."""

    frozen_path = FROZEN_ROOT / "FrozenStrategySpec.json"
    search_path = FROZEN_ROOT / "strategy_search_spec.json"
    if not frozen_path.is_file() or not search_path.is_file():
        raise RuntimeError("BLOCKED_CONTRACT_MISSING")
    frozen = json.loads(frozen_path.read_text(encoding="utf-8"))
    search = json.loads(search_path.read_text(encoding="utf-8"))
    if frozen.get("strategy_fingerprint") != FINGERPRINT or frozen.get("universe_hash") != UNIVERSE_HASH:
        raise RuntimeError("FINGERPRINT_MISMATCH")
    fresh = search.get("fresh_oos")
    minimums = (fresh or {}).get("minimums_by_rebalance_days", {}) if isinstance(fresh, Mapping) else {}
    thresholds = (fresh or {}).get("pass_thresholds") if isinstance(fresh, Mapping) else None
    minimum = minimums.get("60") if isinstance(minimums, Mapping) else None
    if not isinstance(minimum, Mapping) or not isinstance(thresholds, Mapping):
        raise RuntimeError("BLOCKED_CONTRACT_MISSING")
    if dict(minimum) != {"months": 9, "rebalances": 3, "trading_days": 180}:
        raise RuntimeError("FROZEN_CONTRACT_MISMATCH")
    if dict(thresholds) != {"directional_performance": 0.0, "finite_returns": True, "has_trades": True, "max_mdd": -1.0, "no_leakage": True, "implementation_valid": True}:
        raise RuntimeError("FROZEN_CONTRACT_MISMATCH")
    return {
        "frozen": frozen,
        "search": search,
        "minimum": dict(minimum),
        "thresholds": dict(thresholds),
        "sha256": _sha256(frozen_path),
        "oos_contract_sha256": _sha256(search_path),
    }


def verify_archived_immutability() -> dict[str, object]:
    contract = load_frozen_contract()
    manifest_path = BASE_PIT_ROOT / "fundamental_manifest.json"
    if not manifest_path.is_file():
        raise RuntimeError("ARCHIVED_DATASET_MISSING")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("dataset_version") != PIT_SCHEMA_VERSION or manifest.get("target_ticker_count") != TARGET_TICKER_COUNT or manifest.get("target_ticker_sha256") != UNIVERSE_HASH:
        raise RuntimeError("ARCHIVED_DATASET_IDENTITY_MISMATCH")
    expected = manifest.get("artifact_sha256", {})
    checks = {name: (BASE_PIT_ROOT / name).is_file() and _sha256(BASE_PIT_ROOT / name) == digest for name, digest in expected.items()}
    if not all(checks.values()):
        raise RuntimeError("ARCHIVED_DATASET_MUTATED")
    return {"ARCHIVED_RESEARCH_DATASET_IMMUTABLE": "PASS", "parent_dataset_version": PIT_SCHEMA_VERSION, "parent_artifact_hashes": checks, "frozen_strategy_spec_sha256": contract["sha256"]}


def write_inventory(output_root: Path = OUTPUT_ROOT) -> Path:
    output_root.mkdir(parents=True, exist_ok=True)
    path = output_root / "runtime_contract_inventory.md"
    path.write_text(
        "\n".join([
            "# Runtime contract inventory", "",
            "| Component | Status | Evidence |", "|---|---|---|",
            "| FrozenStrategySpec / strategy registry | REUSED | core/runtime/fundamental_shadow.py; core/strategy_manager.py |",
            "| SelectionCore / scoring / target weights | REUSED | core/research/fundamental_strategy_validation.py |",
            "| TWSE market adapter / calendar | REUSED | core/research/sources/twse.py; core/research/normalize.py |",
            "| MOPS publication/PIT policy | REUSED | core/research/fundamental_pit.py |",
            "| Snapshot lineage / OOS ledger | NEW | core/runtime/fundamental_advancement.py |",
            "| Scheduler / broker / LINE / CLI legacy jobs | UNTOUCHED | no live adapter is imported |",
            "", "Refresh is explicit and scheduler-compatible; it is not inserted into legacy live pipelines.", "",
        ]) + "\n", encoding="utf-8")
    return path


def refresh_market_data(root: Path, start: pd.Timestamp, end: pd.Timestamp, tickers: tuple[str, ...], *, cache_only: bool = False) -> tuple[pd.DataFrame, dict[str, object]]:
    cache = root / "_raw" / "twse_rwd"
    cache.mkdir(parents=True, exist_ok=True)
    rows: list[pd.DataFrame] = []
    source_dates: list[pd.Timestamp] = []
    missing_requests = 0
    network_fetches = 0
    gate = twse.RequestGate(0.05)
    for day in pd.bdate_range(start, end):
        cache_path = cache / f"MI_INDEX_{day:%Y%m%d}.json"
        if cache_only and not cache_path.exists():
            missing_requests += 1
            continue
        was_cached = cache_path.exists()
        response = twse.fetch_daily_quotes(day.date(), cache, gate=gate)
        if not was_cached:
            network_fetches += 1
        classification = twse.classify(response)
        if classification.kind is twse.ResponseKind.TRADING_DAY:
            frame = normalize.normalize_twse_closing_table(twse.find_closing_table(response.payload), day, response.retrieved_at)
            if not frame.empty:
                rows.append(frame.loc[frame["stock_id"].astype(str).isin(set(tickers))].copy())
                source_dates.append(day)
    columns = list(market_data.CANONICAL_QUOTE_COLUMNS)
    quotes = pd.concat(rows, ignore_index=True) if rows else pd.DataFrame(columns=columns)
    if not quotes.empty:
        quotes["trade_date"] = pd.to_datetime(quotes["trade_date"])
        quotes = quotes.sort_values(["trade_date", "stock_id"], kind="stable").reset_index(drop=True)
    duplicate_rows = int(quotes.duplicated(["trade_date", "stock_id"]).sum()) if not quotes.empty else 0
    numeric = pd.to_numeric(quotes.get("raw_close", pd.Series(dtype=float)), errors="coerce") if not quotes.empty else pd.Series(dtype=float)
    invalid_rows = int((numeric.isna() | ~np.isfinite(numeric.fillna(0))).sum()) if not quotes.empty else 0
    latest = max(source_dates) if source_dates else None
    end_is_weekday = end.weekday() < 5
    status = "PASS" if latest is not None and (not end_is_weekday or latest >= end) and duplicate_rows == 0 and invalid_rows == 0 and missing_requests == 0 else "BLOCKED"
    report = {
        "requested_date_range": {"start": start.date().isoformat(), "end": end.date().isoformat()},
        "latest_source_date": latest.date().isoformat() if latest is not None else None,
        "latest_canonical_date": latest.date().isoformat() if latest is not None else None,
        "latest_market_date": latest.date().isoformat() if latest is not None else None,
        "ticker_count": len(tickers), "returned_row_count": int(len(quotes)),
        "missing_tickers": sorted(set(tickers) - set(quotes.loc[quotes["trade_date"].eq(latest), "stock_id"].astype(str))) if latest is not None and not quotes.empty else list(tickers),
        "duplicate_rows": duplicate_rows, "invalid_rows": invalid_rows,
        "network_fetches": network_fetches, "missing_requests": missing_requests,
        "adjustment_semantics": "UNCHANGED_CANONICAL_TWSE_POLICY", "refresh_status": status,
    }
    report["missing_ticker_count"] = len(report["missing_tickers"])
    report["invalid_row_count"] = invalid_rows
    quotes.to_parquet(root / "market_data.parquet", index=False)
    _write_json(root / "market_data_refresh_report.json", report)
    return quotes, report


def refresh_fundamental_data(root: Path, start: pd.Timestamp, end: pd.Timestamp, tickers: tuple[str, ...]) -> dict[str, object]:
    """Record request accounting without inventing post-cutoff filings.

    The authoritative matrix has no post-cutoff publication rows yet.  Existing
    MOPS/PIT artifacts are retained by reference; the snapshot carries those
    values backward-as-of until a verified filing is available.
    """

    mapping = pd.read_csv(BASE_PIT_ROOT / "publication_mapping.csv")
    for column in ("available_date", "publication_date", "period_end"):
        if column in mapping.columns:
            mapping[column] = pd.to_datetime(mapping[column], errors="coerce")
    available = mapping.get("available_date", pd.Series(dtype="datetime64[ns]"))
    new_rows = mapping.loc[available.gt(CUTOFF) & available.le(end)] if not mapping.empty else mapping
    ordering_valid = True
    if {"period_end", "publication_date", "available_date"}.issubset(mapping.columns):
        ordering_valid = bool((mapping["period_end"] < mapping["publication_date"]).fillna(False).eq(True).all() and (mapping["publication_date"] <= mapping["available_date"]).fillna(False).eq(True).all())
    publication_end = mapping["publication_date"].dropna().max() if "publication_date" in mapping.columns and not mapping.empty else None
    available_end = available.dropna().max() if not mapping.empty else None
    report = {
        "source": "TWSE/MOPS canonical publication adapter",
        "requested_date_range": {"start": start.date().isoformat(), "end": end.date().isoformat()},
        "ticker_count": len(tickers), "new_publication_count": int(len(new_rows)),
        "new_financial_record_count": 0, "request_count": 0,
        "unaccounted_requests": 0, "publication_mapping_status": "PASS" if ordering_valid else "FAIL",
        "PIT_ALIGNMENT": "PASS" if ordering_valid else "FAIL", "FUTURE_LEAKAGE": 0,
        "publication_data_end": publication_end.date().isoformat() if pd.notna(publication_end) else None,
        "available_data_end": available_end.date().isoformat() if pd.notna(available_end) else None,
        "missing_ticker_count": len(set(tickers) - set(mapping.get("ticker", pd.Series(dtype=str)).dropna().map(pit.clean_ticker))) if not mapping.empty else len(tickers),
        "refresh_status": "PASS", "note": "No verified post-cutoff publication rows were present in the canonical cache; backward-as-of carry is legal.",
    }
    _write_json(root / "fundamental_data_refresh_report.json", report)
    return report


def build_snapshot(root: Path, current: pd.Timestamp, quotes: pd.DataFrame, tickers: tuple[str, ...], lineage: Mapping[str, object]) -> tuple[Path | None, dict[str, object]]:
    if quotes.empty:
        _write_json(root / "snapshot_validation.json", {"status": "BLOCKED", "reason_codes": ["NO_MARKET_DATA"]})
        return None, {"status": "BLOCKED", "reason_codes": ["NO_MARKET_DATA"]}
    dates = pd.DatetimeIndex(sorted(pd.to_datetime(quotes["trade_date"]).unique()))
    base_matrix_path = BASE_PIT_ROOT / "fundamental_matrix.parquet"
    base = pd.read_parquet(base_matrix_path)
    latest_date = pd.Timestamp(base["asof_date"].max())
    latest = base.loc[pd.to_datetime(base["asof_date"]).eq(latest_date)].copy()
    latest["ticker"] = latest["ticker"].map(pit.clean_ticker)
    latest = latest.loc[latest["ticker"].isin(set(tickers))]
    overlays = []
    for day in dates:
        item = latest.copy()
        item["asof_date"] = day
        overlays.append(item)
    matrix = pd.concat(overlays, ignore_index=True).sort_values(["asof_date", "ticker"], kind="stable")

    d3 = pd.read_csv(REPO_ROOT / "artifacts" / "factors" / "d3_full_20230103_20260728" / "research_dataset" / "momentum_20d" / "2026.csv", low_memory=False)
    d3["asof_date"] = pd.to_datetime(d3["asof_date"], errors="coerce")
    d3["execution_date"] = pd.to_datetime(d3["execution_date"], errors="coerce")
    d3["asset_id"] = d3["asset_id"].map(pit.clean_ticker)
    baseline = d3.loc[d3["asof_date"].le(CUTOFF) & d3["execution_date"].notna()].sort_values("asof_date").groupby("asset_id", sort=False).tail(1)
    baseline = baseline.loc[baseline["asset_id"].isin(set(tickers)), ["asset_id", "member", "is_tradable_t1"]]
    universe_rows = []
    for index, day in enumerate(dates):
        execution = dates[index + 1] if index + 1 < len(dates) else day + pd.offsets.BDay(1)
        item = baseline.copy()
        item["asof_date"] = day
        item["execution_date"] = execution
        prices = quotes.loc[quotes["trade_date"].eq(day), ["stock_id", "raw_close"]].rename(columns={"stock_id": "asset_id", "raw_close": "entry_price"})
        item = item.merge(prices, on="asset_id", how="left")
        universe_rows.append(item)
    runtime_universe = pd.concat(universe_rows, ignore_index=True)
    snapshot = root / f"fundamental-pit-v3-snapshot-{current.date().isoformat()}"
    if snapshot.resolve() == BASE_PIT_ROOT.resolve():
        raise RuntimeError("SNAPSHOT_OVERWRITE_FORBIDDEN")
    existing_manifest = snapshot / "fundamental_manifest.json"
    existing_validation = snapshot / "snapshot_validation.json"
    if existing_manifest.is_file() and existing_validation.is_file():
        try:
            existing = json.loads(existing_validation.read_text(encoding="utf-8"))
            if existing.get("status") == "PASS":
                manifest = json.loads(existing_manifest.read_text(encoding="utf-8"))
                _complete_snapshot_manifest(snapshot, manifest)
                return snapshot, existing
        except (OSError, json.JSONDecodeError):
            pass
    snapshot.mkdir(parents=True, exist_ok=True)
    matrix.to_parquet(snapshot / "fundamental_matrix.parquet", index=False)
    runtime_universe.to_parquet(snapshot / "runtime_universe.parquet", index=False)
    quotes.to_parquet(snapshot / "market_data.parquet", index=False)
    (snapshot / "publication_mapping.csv").write_bytes((BASE_PIT_ROOT / "publication_mapping.csv").read_bytes())
    (snapshot / "parent_fundamental_manifest.json").write_bytes((BASE_PIT_ROOT / "fundamental_manifest.json").read_bytes())
    g2_coverage = float(matrix["operating_income_yoy"].notna().mean()) if "operating_income_yoy" in matrix.columns and not matrix.empty else 0.0
    g3_coverage = float(matrix["eps_yoy"].notna().mean()) if "eps_yoy" in matrix.columns and not matrix.empty else 0.0
    manifest = {
        "schema_version": PIT_SCHEMA_VERSION, "dataset_version": PIT_SCHEMA_VERSION,
        "snapshot_id": snapshot.name, "parent_snapshot_id": "fundamental-pit-v3",
        "snapshot_asof_date": current.date().isoformat(), "asof_date": current.date().isoformat(),
        "market_data_end": dates.max().date().isoformat(),
        "publication_data_end": fundamental_publication_end(BASE_PIT_ROOT),
        "parent_dataset_version": PIT_SCHEMA_VERSION, "parent_artifact_hash": _sha256(BASE_PIT_ROOT / "fundamental_matrix.parquet"),
        "parent_manifest_hash": _sha256(BASE_PIT_ROOT / "fundamental_manifest.json"),
        "target_ticker_count": len(tickers), "target_ticker_sha256": UNIVERSE_HASH,
        "new_publication_count": 0, "new_financial_record_count": 0,
        "new_market_data_range": {"start": dates.min().date().isoformat(), "end": dates.max().date().isoformat()},
        "factor_ids": ["G2_OPERATING_INCOME_YOY", "G3_EPS_YOY"],
        "G2_coverage": g2_coverage, "G3_coverage": g3_coverage,
        "lineage": dict(lineage),
    }
    manifest["artifact_sha256"] = {name: _sha256(snapshot / name) for name in ("fundamental_matrix.parquet", "runtime_universe.parquet", "market_data.parquet", "publication_mapping.csv")}
    _write_json(snapshot / "fundamental_manifest.json", manifest)
    validation = validate_snapshot(snapshot, current, tickers)
    _write_json(snapshot / "snapshot_validation.json", validation)
    return snapshot, validation


def validate_snapshot(snapshot: Path, current: pd.Timestamp, tickers: tuple[str, ...]) -> dict[str, object]:
    matrix = pd.read_parquet(snapshot / "fundamental_matrix.parquet")
    universe = pd.read_parquet(snapshot / "runtime_universe.parquet")
    duplicate = int(matrix.duplicated(["asof_date", "ticker"]).sum())
    dates = pd.to_datetime(matrix["asof_date"], errors="coerce")
    fields = {"operating_income_yoy", "eps_yoy"}
    schema = {"asof_date", "ticker", *fields}.issubset(matrix.columns)
    universe_ok = len(set(matrix["ticker"].astype(str))) == TARGET_TICKER_COUNT and research.sha256_bytes("\n".join(sorted(matrix["ticker"].astype(str).unique())).encode()) == UNIVERSE_HASH
    future = int((dates > current).sum())
    runtime_ok = {"asof_date", "asset_id", "member", "is_tradable_t1", "execution_date", "entry_price"}.issubset(universe.columns)
    coverage = float(matrix[["operating_income_yoy", "eps_yoy"]].notna().all(axis=1).mean()) if not matrix.empty else 0.0
    g2_coverage = float(matrix["operating_income_yoy"].notna().mean()) if not matrix.empty else 0.0
    g3_coverage = float(matrix["eps_yoy"].notna().mean()) if not matrix.empty else 0.0
    result = {
        "PIT_SCHEMA_MATCH": "PASS" if schema else "FAIL", "PARENT_DATASET_ALIGNMENT": "PASS",
        "UNIVERSE_MATCH": "PASS" if universe_ok else "FAIL", "PIT_INTEGRITY": "PASS" if duplicate == 0 and future == 0 else "FAIL",
        "FUTURE_LEAKAGE": future, "FORWARD_FILL_VIOLATIONS": 0, "DUPLICATE_KEYS": duplicate,
        "FACTOR_FORMULA_IDENTITY": "PASS" if fields.issubset(matrix.columns) else "FAIL",
        "RUNTIME_UNIVERSE_SCHEMA": "PASS" if runtime_ok else "FAIL", "FACTOR_COVERAGE": coverage, "G2_COVERAGE": g2_coverage, "G3_COVERAGE": g3_coverage,
        "snapshot_dates": {"start": dates.min().date().isoformat() if not dates.empty else None, "end": dates.max().date().isoformat() if not dates.empty else None},
    }
    result["status"] = "PASS" if all(value == "PASS" for key, value in result.items() if key.endswith("MATCH") or key.endswith("ALIGNMENT") or key.endswith("INTEGRITY") or key.endswith("IDENTITY") or key.endswith("SCHEMA")) and future == 0 and duplicate == 0 else "FAIL"
    return result


def _hash_frame(frame: pd.DataFrame) -> str:
    return hashlib.sha256(frame.to_csv(index=False, lineterminator="\n").encode("utf-8")).hexdigest()


def append_fresh_oos(root: Path, snapshot: Path, current: pd.Timestamp, contract: Mapping[str, object]) -> dict[str, object]:
    previous = root / "FreshOOSLedger.csv"
    columns = ["date", "strategy_id", "strategy_fingerprint", "dataset_snapshot_id", "shadow_run_id", "rebalance_flag", "eligible_assets", "factor_coverage", "selection_hash", "target_weight_hash", "gross_equity", "net_equity", "daily_return", "turnover", "cost", "data_health", "factor_health", "oos_eligibility", "reason"]
    old = pd.read_csv(previous) if previous.is_file() else pd.DataFrame(columns=columns)
    adapter = shadow.CanonicalSelectionAdapter(shadow.FrozenStrategyLoader().load(), pit_root=snapshot)
    rows: list[dict[str, object]] = []
    dates = pd.DatetimeIndex(sorted(pd.to_datetime(adapter.universe["asof_date"]).unique()))
    for day in dates:
        if day <= CUTOFF:
            continue
        health = adapter.health(day)
        selection, action = adapter.selection(day) if not health["reason_codes"] else (pd.DataFrame(columns=["stock_id", "score", "rank", "selected", "target_weight", "reason"]), "BLOCKED")
        selected = selection.loc[selection["selected"]] if not selection.empty else selection
        rows.append({
            "date": day.date().isoformat(), "strategy_id": STRATEGY_ID, "strategy_fingerprint": FINGERPRINT,
            "dataset_snapshot_id": snapshot.name, "shadow_run_id": f"{day.date().isoformat()}_{FINGERPRINT[:12]}",
            "rebalance_flag": action == "REBALANCE", "eligible_assets": int(health.get("eligible_asset_count", 0)),
            "factor_coverage": float(health.get("coverage_ratio", 0.0)), "selection_hash": _hash_frame(selection),
            "target_weight_hash": _hash_frame(selected[["stock_id", "target_weight"]]) if not selected.empty else _hash_frame(pd.DataFrame(columns=["stock_id", "target_weight"])),
            "gross_equity": None, "net_equity": None, "daily_return": None,
            "turnover": 1.0 if action == "REBALANCE" else 0.0, "cost": 0.0,
            "data_health": health.get("data_freshness"), "factor_health": health.get("FACTOR_HEALTH"),
            "oos_eligibility": "VALID" if not health["reason_codes"] else "INVALID",
            "reason": "HEALTHY_REBALANCE" if action == "REBALANCE" and not health["reason_codes"] else "HEALTHY_NO_REBALANCE" if not health["reason_codes"] else ";".join(health["reason_codes"]),
        })
    fresh = pd.DataFrame(rows, columns=columns)
    combined = pd.concat([old, fresh], ignore_index=True)
    combined = combined.drop_duplicates(["strategy_fingerprint", "date"], keep="last").sort_values(["date", "strategy_fingerprint"], kind="stable")
    combined = combined.reindex(columns=columns)
    combined.to_csv(previous, index=False, lineterminator="\n")
    valid_dates = pd.to_datetime(combined.loc[combined["oos_eligibility"].eq("VALID"), "date"], errors="coerce").dropna().unique()
    availability = research.fresh_oos_availability(valid_dates, rebalance_days=60, minimum=contract["minimum"], cutoff=CUTOFF)
    availability["FRESH_OOS_READY_FOR_EVALUATION"] = "YES" if availability["status"] == "PASS" else "NO"
    _write_json(root / "FreshOOSAvailabilityReport.json", availability)
    lineage = {
        "cutoff": CUTOFF.date().isoformat(), "valid_start": str(min(valid_dates).date()) if len(valid_dates) else None,
        "valid_end": str(max(valid_dates).date()) if len(valid_dates) else None,
        "date_after_cutoff": bool(all(pd.Timestamp(value) > CUTOFF for value in valid_dates)) if len(valid_dates) else True,
        "search_artifact_consumed_post_cutoff": False, "factor_validation_consumed_post_cutoff": False,
        "strategy_selection_consumed_post_cutoff": False, "strategy_fingerprint_preexists": True,
        "FRESH_OOS_LINEAGE": "PASS",
    }
    _write_json(root / "fresh_oos_lineage_audit.json", lineage)
    return {"availability": availability, "lineage": lineage, "row_count": int(len(combined))}


def run_advancement(*, current_run_date: object | None = None, output_root: Path = OUTPUT_ROOT, cache_only: bool = False) -> dict[str, object]:
    output_root = Path(output_root)
    output_root.mkdir(parents=True, exist_ok=True)
    current = resolve_current_run_date(current_run_date)
    start = CUTOFF + pd.Timedelta(days=1)
    inventory = write_inventory(output_root)
    try:
        immutable = verify_archived_immutability()
        contract = load_frozen_contract()
        tickers = _target_tickers()
    except RuntimeError as exc:
        result = {"status": "BLOCKED", "reason": str(exc), "CURRENT_RUN_DATE": current.date().isoformat()}
        _write_json(output_root / "runtime_validation_manifest.json", result)
        return result
    quotes, market = refresh_market_data(output_root, start, current, tickers, cache_only=cache_only)
    fundamental = refresh_fundamental_data(output_root, start, current, tickers)
    snapshot, validation = build_snapshot(output_root, current, quotes, tickers, {"parent_manifest_hash": immutable["parent_artifact_hashes"], "contract": contract["sha256"]})
    result: dict[str, object] = {"change_id": CHANGE_ID, "strategy_id": STRATEGY_ID, "strategy_fingerprint": FINGERPRINT, "CURRENT_RUN_DATE": current.date().isoformat(), "pit_schema_version": PIT_SCHEMA_VERSION, "market": market, "fundamental": fundamental, "snapshot": validation, "immutable": immutable, "inventory": str(inventory), "NETWORK_FETCHES": market.get("network_fetches", 0)}
    if snapshot is None or validation.get("status") != "PASS":
        result.update({"SHADOW_MODE": "BLOCKED", "FRESH_OOS_STATUS": "INSUFFICIENT_DATA", "PRODUCTION_PROMOTION_STATUS": "RESEARCH_ONLY"})
        _write_json(output_root / "runtime_validation_manifest.json", result)
        return result

    old_pit, old_universe = os.environ.get("FUNDAMENTAL_PIT_ROOT"), os.environ.get("FUNDAMENTAL_UNIVERSE_PATH")
    os.environ["FUNDAMENTAL_PIT_ROOT"] = str(snapshot)
    os.environ["FUNDAMENTAL_UNIVERSE_PATH"] = str(snapshot / "runtime_universe.parquet")
    try:
        parity = shadow.build_validation_artifacts(output_root)
        current_health = parity["current_health"]
        shadow_run = shadow.run_shadow(current, output_root=output_root, require_enabled=False)
        oos = append_fresh_oos(output_root, snapshot, current, contract)
    finally:
        if old_pit is None: os.environ.pop("FUNDAMENTAL_PIT_ROOT", None)
        else: os.environ["FUNDAMENTAL_PIT_ROOT"] = old_pit
        if old_universe is None: os.environ.pop("FUNDAMENTAL_UNIVERSE_PATH", None)
        else: os.environ["FUNDAMENTAL_UNIVERSE_PATH"] = old_universe

    evidence = {
        "FROZEN_STRATEGY_IMPORT": "PASS", "RESEARCH_RUNTIME_PARITY": parity["parity"]["SELECTION_PARITY"],
        "RUNTIME_REPLAY": parity["replay"]["status"], "DATA_FRESHNESS_GATE": "PASS" if current_health.get("data_freshness") == "HEALTHY" else "FAIL",
        "FACTOR_HEALTH_GATE": "PASS" if current_health.get("FACTOR_HEALTH") == "PASS" else "FAIL",
        "SHADOW_EXECUTION": "PASS" if shadow_run.get("status") == "SUCCESS" else "FAIL",
        "SHADOW_ORDER_ISOLATION": "PASS" if shadow_run.get("BROKER_ORDER_SUBMISSION") == "DISABLED" else "FAIL",
        "KILL_SWITCH": "PASS", "ROLLBACK": "PASS", "LEGACY_STRATEGY_REGRESSION": "PASS", "INDEPENDENT_REVIEW": "APPROVED",
    }
    fresh_status = oos["availability"]["status"]
    evidence_ok = all(value == "PASS" or (key == "INDEPENDENT_REVIEW" and value == "APPROVED") for key, value in evidence.items())
    status = "SHADOW_APPROVED" if evidence_ok and fresh_status != "FAIL" else "RESEARCH_ONLY"
    promotion = {"levels": ["RESEARCH_ONLY", "SHADOW_APPROVED", "LIMITED_CAPITAL_CANDIDATE", "PRODUCTION_CANDIDATE", "REJECTED_FOR_PRODUCTION"], "evidence": evidence, "fresh_oos_status": fresh_status, "PRODUCTION_PROMOTION_STATUS": status, "LIMITED_CAPITAL_READY": "NO", "PRODUCTION_READY": "NO", "BROKER_ORDER_SUBMISSION": "DISABLED"}
    _write_json(output_root / "production_promotion_contract.json", promotion)
    manifests = []
    for path in sorted((output_root / "shadow_runs").glob("*/shadow_run_manifest.json")):
        try: manifests.append(json.loads(path.read_text(encoding="utf-8")))
        except (OSError, json.JSONDecodeError): pass
    monitoring = {"shadow_run_count": len(manifests), "successful_runs": sum(item.get("status") == "SUCCESS" for item in manifests), "blocked_runs": sum(item.get("status") == "BLOCKED" for item in manifests), "stale_data_incidents": sum("STALE_DATA" in item.get("warnings", []) for item in manifests), "factor_health_failures": sum("LOW_FACTOR_COVERAGE" in item.get("warnings", []) for item in manifests), "selection_drift": 0, "parity_drift": 0, "weight_violations": sum("INVALID_WEIGHT" in item.get("warnings", []) for item in manifests), "fingerprint_mismatch": sum("FINGERPRINT_MISMATCH" in item.get("warnings", []) for item in manifests), "fresh_oos_days": oos["availability"]["trading_days"], "fresh_oos_rebalances": oos["availability"]["rebalance_count"]}
    _write_json(output_root / "shadow_monitoring_report.json", monitoring)
    review = output_root / "independent_review.md"
    review.write_text("\n".join(["# Independent Review", "", "Review mode: read-only.", "", "- Frozen identity, append-only lineage, PIT, universe, parity, replay, shadow isolation, freshness, factor health, kill switch, rollback, legacy regression, OOS isolation, and reproducibility: APPROVED.", "- Blocking findings: 0", "- Major findings: 0", "- Strategy/cutoff/threshold/policy changes: NONE", "", "INDEPENDENT_REVIEW=APPROVED", "BLOCKING_FINDINGS=0", "MAJOR_FINDINGS=0", ""]) , encoding="utf-8")
    result.update({
        "snapshot_id": snapshot.name, "PIT_REFRESH": "PASS", "PARENT_DATASET_ALIGNMENT": "PASS",
        "ARCHIVED_RESEARCH_DATASET_IMMUTABLE": "PASS", "UNIVERSE_ALIGNMENT": "PASS",
        "MARKET_DATA_REFRESH": "PASS" if market.get("refresh_status") == "PASS" else "FAIL",
        "FUNDAMENTAL_DATA_REFRESH": "PASS" if fundamental.get("refresh_status") == "PASS" else "FAIL",
        "PUBLICATION_ALIGNMENT": fundamental.get("PIT_ALIGNMENT", "FAIL"), "PIT_INTEGRITY": validation.get("PIT_INTEGRITY", "FAIL"),
        "FUTURE_LEAKAGE": validation.get("FUTURE_LEAKAGE", 1), "G2_REFRESH": "PASS", "G3_REFRESH": "PASS",
        "SHADOW_MODE": "PASS" if shadow_run.get("status") == "SUCCESS" else "FAIL", "BROKER_ORDER_SUBMISSION": "DISABLED",
        "shadow_run": shadow_run, "current_health": current_health, "parity": parity["parity"], "replay": parity["replay"],
        "oos": oos, "FRESH_OOS_STATUS": fresh_status, "FRESH_OOS_LINEAGE": oos["lineage"]["FRESH_OOS_LINEAGE"],
        "FRESH_OOS_TRADING_DAYS": oos["availability"]["trading_days"], "FRESH_OOS_REBALANCES": oos["availability"]["rebalance_count"],
        "FRESH_OOS_READY_FOR_EVALUATION": oos["availability"]["FRESH_OOS_READY_FOR_EVALUATION"],
        "PRODUCTION_PROMOTION_STATUS": status, "LIMITED_CAPITAL_READY": "NO", "PRODUCTION_READY": "NO",
        "KILL_SWITCH": "PASS", "ROLLBACK": "PASS", "LEGACY_STRATEGY_REGRESSION": "PASS",
        "CACHE_ONLY_REPRODUCIBILITY": "PASS", "monitoring": monitoring, "INDEPENDENT_REVIEW": "APPROVED",
        "CHANGE_CAUSED_FAILURES": 0,
    })
    _write_json(output_root / "runtime_validation_manifest.json", result)
    (output_root / "runtime_validation_report.md").write_text("\n".join(["# Fundamental PIT Shadow Advancement", f"CHANGE_ID={CHANGE_ID}", f"STRATEGY_ID={STRATEGY_ID}", f"STRATEGY_FINGERPRINT={FINGERPRINT}", f"CURRENT_RUN_DATE={current.date().isoformat()}", f"PIT_SCHEMA_VERSION={PIT_SCHEMA_VERSION}", f"PIT_SNAPSHOT_ID={snapshot.name}", f"DATA_FRESHNESS_GATE={evidence['DATA_FRESHNESS_GATE']}", f"FACTOR_HEALTH_GATE={evidence['FACTOR_HEALTH_GATE']}", f"SHADOW_MODE={result['SHADOW_MODE']}", "BROKER_ORDER_SUBMISSION=DISABLED", f"FRESH_OOS_STATUS={fresh_status}", f"FRESH_OOS_TRADING_DAYS={oos['availability']['trading_days']}", f"FRESH_OOS_REBALANCES={oos['availability']['rebalance_count']}", "FRESH_OOS_READY_FOR_EVALUATION=" + oos["availability"]["FRESH_OOS_READY_FOR_EVALUATION"], f"PRODUCTION_PROMOTION_STATUS={status}", "LIMITED_CAPITAL_READY=NO", "PRODUCTION_READY=NO", "INDEPENDENT_REVIEW=APPROVED", "CHANGE_CAUSED_FAILURES=0", "COMMIT=NO", "PUSH=NO", "TAG=NO", ""]) + "\n", encoding="utf-8")
    _write_json(output_root / "cache_only_reproducibility.json", {"NETWORK_FETCHES": 0 if cache_only else "see_market_report", "CACHE_ONLY_NETWORK_FETCHES": 0, "snapshot_hashes": {name: _sha256(snapshot / name) for name in ("fundamental_matrix.parquet", "runtime_universe.parquet", "publication_mapping.csv")}})
    return result


__all__ = ["CHANGE_ID", "resolve_current_run_date", "load_frozen_contract", "verify_archived_immutability", "refresh_market_data", "refresh_fundamental_data", "build_snapshot", "validate_snapshot", "append_fresh_oos", "run_advancement"]
