"""CLI for the isolated factor-research pipeline."""

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.research import normalize, pipeline
from core.research.sources import twse


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", required=True)
    parser.add_argument("--end", required=True)
    parser.add_argument("--run-id", default=datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ_manual"))
    parser.add_argument("--no-fetch", action="store_true")
    parser.add_argument("--reconciliation-seed", type=int, default=0)
    parser.add_argument("--allow-vendor-fallback", action="store_true")
    args = parser.parse_args(argv)
    output_dir = Path("artifacts") / "factors" / args.run_id
    initial = pipeline.RunConfig(
        run_id=args.run_id, generated_at=datetime.now(timezone.utc).isoformat(),
        adjustment_as_of=datetime.now(timezone.utc), requested_start=args.start, requested_end=args.end,
        output_dir=output_dir, quotes=None,
        reconciliation_seed=args.reconciliation_seed, allow_vendor_fallback=args.allow_vendor_fallback, no_fetch=args.no_fetch,
    )
    quotes = pipeline.load_twse_quotes(initial)
    cache_dir = output_dir / "_raw" / "twse_rwd"
    if args.no_fetch and not (cache_dir / "t187ap03_L.json").exists():
        print("listing-date cache required for --no-fetch", file=sys.stderr)
        return 1
    profile = twse.fetch_company_profile(cache_dir)
    listing_dates = normalize.normalize_company_profile_listing_dates(profile.payload)
    if not listing_dates:
        print("listing dates unavailable; D3 run cannot start", file=sys.stderr)
        return 1
    assets = set(quotes["stock_id"].astype(str))
    calendar = tuple(sorted(pd.to_datetime(quotes["trade_date"]).unique()))
    provenance = {
        "listing_date_source": "twse_openapi/t187ap03_L",
        "listing_date_cache_used": bool(profile.metadata.get("cache_used")),
        "listing_date_retrieved_at": None if profile.metadata.get("cache_used") else profile.retrieved_at.isoformat(),
        "listing_date_asset_count": len(assets),
        "listing_date_missing_count": len(assets - set(listing_dates)),
    }
    result = pipeline.run(pipeline.RunConfig(
        **{**initial.__dict__, "quotes": quotes, "listing_dates": listing_dates, "trading_calendar": calendar,
           "listing_date_provenance": provenance, "require_research_dataset": True}
    ))
    return 0 if result.status == "success" else 1


if __name__ == "__main__":
    raise SystemExit(main())
