from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from core.research.fundamental_pit import build_target_universe, clean_ticker
from core.research.universe import build_membership_v2
from core.research.universe_recovery import (
    MEMBERSHIP_COLUMNS,
    UniverseLineage,
    create_pre_run_snapshot,
    discover_universe_lineage,
    expansion_unlock_status,
    membership_parity,
    recover_research_universe,
)


def _membership(days: int = 260) -> pd.DataFrame:
    dates = pd.bdate_range("2024-01-02", periods=days)
    rows = []
    for ticker in ("2330", "2317"):
        for day in dates:
            rows.append({
                "trade_date": day,
                "stock_id": ticker,
                "market": "TWSE",
                "raw_close": 20.0,
                "volume": 1_000_000,
                "amount": 25_000_000,
            })
    quotes = pd.DataFrame(rows)
    return build_membership_v2(
        quotes,
        {"2330": dates[0], "2317": dates[0]},
        trading_calendar=dates,
    )


def test_canonical_builder_produces_membership_with_eligibility_column():
    result = _membership()
    assert set(MEMBERSHIP_COLUMNS) <= set(result.columns)
    assert result["member"].dtype == bool


def test_recovery_schema_preserves_date_dependent_eligibility(tmp_path: Path):
    result = recover_research_universe(_membership(), tmp_path / "research_universe.parquet")
    frame = pd.read_parquet(result.output_path)
    assert {"ticker", "is_eligible", "eligibility_date", "trade_date", "universe_id"} <= set(frame.columns)
    assert frame["universe_id"].eq("UNIV_RESEARCH_V1").all()
    assert frame["eligibility_date"].equals(frame["trade_date"])
    assert result.research_universe_status == "REBUILT"


def test_clean_ticker_is_deterministic_and_keeps_nonstandard_identifier():
    assert clean_ticker(" 2330.TW ") == "2330"
    assert clean_ticker("912000") == "912000"
    assert clean_ticker("912000") == clean_ticker("912000")


def test_is_eligible_semantics_are_member_verdicts(tmp_path: Path):
    result = recover_research_universe(_membership(), tmp_path / "research_universe.parquet")
    frame = pd.read_parquet(result.output_path)
    assert frame["is_eligible"].equals(frame["member"])


def test_target_freeze_is_deterministic_over_recovered_frame(tmp_path: Path):
    path = tmp_path / "research_universe.parquet"
    recover_research_universe(_membership(), path)
    first = build_target_universe(path)
    second = build_target_universe(path)
    assert first.tickers == second.tickers
    assert first.sha256 == second.sha256


def test_target_hash_changes_when_recovered_membership_changes(tmp_path: Path):
    path = tmp_path / "research_universe.parquet"
    recover_research_universe(_membership(), path)
    frame = pd.read_parquet(path)
    frame.loc[0, "is_eligible"] = True
    frame.loc[len(frame) - 1, "ticker"] = "2454"
    changed = build_target_universe(frame)
    original = build_target_universe(path)
    assert changed.sha256 != original.sha256


def test_acceptance_union_is_normalized_and_sorted(tmp_path: Path):
    path = tmp_path / "research_universe.parquet"
    recover_research_universe(_membership(), path)
    target = build_target_universe(path, ["2330.TW", "2454"])
    assert target.tickers == ("2317", "2330", "2454")


def test_cache_inventory_does_not_change_target(tmp_path: Path):
    path = tmp_path / "research_universe.parquet"
    recover_research_universe(_membership(), path)
    target = build_target_universe(path)
    (tmp_path / "cache").mkdir()
    (tmp_path / "cache" / "2330.json").write_text(json.dumps({"ticker": "2330"}), encoding="utf-8")
    assert target == build_target_universe(path)


def test_pre_run_snapshot_is_deterministic_and_network_free(tmp_path: Path):
    first = create_pre_run_snapshot(["2330.TW", "2317"], tmp_path / "cache", tmp_path / "snapshot")
    second = create_pre_run_snapshot(["2317", "2330"], tmp_path / "cache", tmp_path / "snapshot2")
    assert first["network_fetch_count"] == second["network_fetch_count"] == 0
    assert first["target_ticker_sha256"] == second["target_ticker_sha256"]
    assert json.loads((tmp_path / "snapshot" / "pre_expansion_manifest.json").read_text())["snapshot_status"] == "PASS"


def test_universe_parity_failure_locks_expansion(tmp_path: Path):
    frame = _membership()
    altered = frame.copy()
    altered.loc[0, "member"] = not bool(altered.loc[0, "member"])
    parity, _ = membership_parity(altered, frame)
    lineage = UniverseLineage("builder", "source", {}, "ESTABLISHED")
    result = recover_research_universe(altered, tmp_path / "recovered.parquet", reference=frame, lineage=lineage)
    assert parity == "FAIL"
    assert result.output_path is None
    assert expansion_unlock_status(lineage, result, {"snapshot_status": "PASS"}) == "NO"


def test_lineage_discovery_reports_missing_source_as_not_established(tmp_path: Path):
    lineage = discover_universe_lineage(tmp_path, tmp_path / "missing-run")
    assert lineage.status == "NOT_ESTABLISHED"
    assert lineage.builder is None


def test_lineage_discovery_finds_existing_d3_builder():
    lineage = discover_universe_lineage()
    assert lineage.status == "ESTABLISHED"
    assert lineage.builder == "core.research.universe.build_membership_v2"
