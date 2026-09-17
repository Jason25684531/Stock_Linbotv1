"""Rebuild ``data/processed/research_universe.parquet`` from the D3 cache.

The command is deliberately cache-only.  It is a recovery step, not a market
or fundamental fetcher; a missing upstream cache fails loudly.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.research import normalize, pipeline, universe
from core.research.fundamental_pit import build_target_universe
from core.research.sources import twse
from core.research.universe_recovery import (
    CANONICAL_UNIVERSE_PATH,
    DEFAULT_SOURCE_RUN,
    create_pre_run_snapshot,
    discover_universe_lineage,
    expansion_unlock_status,
    recover_research_universe,
    write_recovery_report,
)


def rebuild_from_cached_d3(
    source_run: str | Path = DEFAULT_SOURCE_RUN,
    output_path: str | Path = CANONICAL_UNIVERSE_PATH,
    *,
    report_path: str | Path = "openspec/changes/expand-fundamental-pit-coverage-v1/research_universe_recovery_report.md",
    snapshot_root: str | Path = "outputs/fundamental_data/fundamental_pit_coverage_v2/pre_run_snapshot",
    acceptance_tickers: tuple[str, ...] = (),
) -> dict[str, object]:
    source_run = Path(source_run)
    lineage = discover_universe_lineage(".", source_run)
    if lineage.status != "ESTABLISHED":
        write_recovery_report(None, lineage, report_path)
        return {"status": "BLOCKED", "lineage": lineage}

    manifest = json.loads((source_run / "run_manifest.json").read_text(encoding="utf-8"))
    start = manifest["requested_window"]["start"]
    end = manifest["requested_window"]["end"]
    config = pipeline.RunConfig(
        run_id="research_universe_recovery",
        generated_at="recovery",
        adjustment_as_of=pd.Timestamp(manifest.get("adjustment_as_of", end)),
        requested_start=start,
        requested_end=end,
        output_dir=source_run,
        no_fetch=True,
    )
    quotes = pipeline.load_twse_quotes(config)
    if quotes.empty:
        raise RuntimeError("cached D3 quote source produced no rows")
    profile = twse.fetch_company_profile(source_run / "_raw" / "twse_rwd")
    listing_dates = normalize.normalize_company_profile_listing_dates(profile.payload)
    if not listing_dates:
        raise RuntimeError("cached D3 listing-date source produced no mappings")
    calendar = tuple(sorted(pd.to_datetime(quotes["trade_date"]).unique()))
    membership = universe.build_membership_v2(quotes, listing_dates, trading_calendar=calendar)
    dates = pd.to_datetime(membership["trade_date"])
    membership = membership.loc[(dates >= pd.Timestamp(start)) & (dates <= pd.Timestamp(end))].copy()
    reference = source_run / "universe_membership.csv"
    result = recover_research_universe(membership, output_path, reference=reference, lineage=lineage)
    if result.output_path is None:
        write_recovery_report(
            result,
            lineage,
            report_path,
            target_status="BLOCKED",
            legacy_baseline_status="NOT_ESTABLISHED",
            pre_run_snapshot="NOT_RUN",
            full_expansion_unlocked="NO",
            unblock_status="BLOCKED",
        )
        return {
            "status": "BLOCKED",
            "lineage": lineage,
            "result": result,
            "target_status": "BLOCKED",
            "full_expansion_unlocked": "NO",
            "unblock_status": "BLOCKED",
        }
    target = build_target_universe(output_path, acceptance_tickers)
    # No machine-readable review fingerprint exists in this repository.  Keep
    # the computed target, but disclose the comparison as blocked instead of
    # pretending that the default PASS means historical parity.
    target_status = "BLOCKED"
    snapshot = create_pre_run_snapshot(target.tickers, "outputs/fundamental_data/fundamental_pit_coverage_v2/publication_cache", snapshot_root, target_source=output_path)
    unlocked = expansion_unlock_status(lineage, result, snapshot)
    write_recovery_report(
        result,
        lineage,
        report_path,
        target_status=target_status,
        legacy_baseline_status="NOT_ESTABLISHED",
        pre_run_snapshot="PASS",
        full_expansion_unlocked=unlocked,
        unblock_status="COMPLETE" if unlocked == "YES" else "BLOCKED",
    )
    return {"status": "RECOVERED", "lineage": lineage, "result": result, "target": target, "target_status": target_status, "snapshot": snapshot, "full_expansion_unlocked": unlocked, "unblock_status": "COMPLETE" if unlocked == "YES" else "BLOCKED"}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-run", default=str(DEFAULT_SOURCE_RUN))
    parser.add_argument("--output", default=str(CANONICAL_UNIVERSE_PATH))
    parser.add_argument("--report", default="openspec/changes/expand-fundamental-pit-coverage-v1/research_universe_recovery_report.md")
    parser.add_argument("--snapshot-root", default="outputs/fundamental_data/fundamental_pit_coverage_v2/pre_run_snapshot")
    args = parser.parse_args(argv)
    result = rebuild_from_cached_d3(args.source_run, args.output, report_path=args.report, snapshot_root=args.snapshot_root)
    print(json.dumps({"status": result["status"], "full_expansion_unlocked": result.get("full_expansion_unlocked", "NO")}, ensure_ascii=False))
    return 0 if result["status"] == "RECOVERED" else 2


if __name__ == "__main__":
    raise SystemExit(main())
