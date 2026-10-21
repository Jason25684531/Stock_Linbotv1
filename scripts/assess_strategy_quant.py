"""Assess fresh replay curves using existing metrics and an archived market series."""
import argparse
from dataclasses import asdict
import json
from pathlib import Path
import sys

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from core.backtest.metrics import calculate_metrics
from scripts.audit_strategy_inventory import digest
from scripts.validate_strategy_inventory import write_json

REGIMES = ("BULL", "BEAR", "SIDEWAYS", "HIGH_VOLATILITY")
RISK = ("cagr", "sharpe", "sortino", "max_drawdown")


def risk(values):
    metrics = calculate_metrics([float(v) for v in values])
    return {key: asdict(metrics.get(key)) for key in RISK}


def regime_labels(prices):
    returns = prices.pct_change(fill_method=None)
    average = prices.rolling(200, min_periods=200).mean()
    momentum = prices.pct_change(60, fill_method=None)
    volatility = returns.rolling(20, min_periods=20).std(ddof=0) * np.sqrt(252)
    labels = pd.Series("UNKNOWN", index=prices.index)
    labels.loc[average.notna()] = "SIDEWAYS"
    labels.loc[(prices > average) & (momentum > .05)] = "BULL"
    labels.loc[(prices < average) & (momentum < -.05)] = "BEAR"
    labels.loc[volatility > .30] = "HIGH_VOLATILITY"
    return labels


def episodes(values, dates, labels, minimum=20):
    """Use contiguous actual equity episodes; never concatenate disjoint wealth levels."""
    assigned = labels.reindex(pd.to_datetime(dates).normalize()).fillna("UNKNOWN").tolist()
    results = {name: {"return_observations": 0, "episodes": []} for name in REGIMES}
    start = 1
    while start < len(values):
        end = start + 1
        while end < len(values) and assigned[end] == assigned[start]:
            end += 1
        label = assigned[start]
        if label in results:
            results[label]["return_observations"] += end - start
            if end - start >= minimum:
                results[label]["episodes"].append({"start": dates[start], "end": dates[end - 1],
                    "metrics": risk(values[start - 1:end])})
        start = end
    for item in results.values():
        item["calculation_status"] = "COMPLETED" if item["episodes"] else "BLOCKED"
        item["reason"] = "contiguous historical episode diagnostic; not a strategy certification" if item["episodes"] else "INSUFFICIENT_CONTIGUOUS_REGIME_SAMPLES"
    return results


def assess(output):
    plan = {"purpose": "historical diagnostics, never new Fresh OOS certification", "annualization": 252,
        "regime": {"ma_sessions": 200, "momentum_sessions": 60, "momentum_threshold": .05,
            "vol_sessions": 20, "vol_threshold_annualized": .30, "high_vol_priority": True,
            "minimum_contiguous_return_samples": 20},
        "oos": "fixed 70/30 and 252/63 expanding historical segments; frozen parameters; preselected period remains NOT_VERIFIABLE",
        "benchmark": "configured MARKET_SYMBOL proxy only; verify instrument identity, total-return history and asset match before certification",
        "correction": "v1 incorrectly called Config.MARKET_SYMBOL TAIEX; config/settings.py sets 2330 (single stock). Same formulas/thresholds; proxy regimes cannot establish market regimes."}
    plan_path = output / "quant_assessment_plan_v2.json"
    if plan_path.exists():
        assert json.loads(plan_path.read_text(encoding="utf-8")) == plan, "Do not retune the plan after observing outcomes"
    else:
        write_json(plan_path, plan)
    benchmark_path = output / "snapshot_benchmark.parquet"
    if not benchmark_path.exists():
        from config import Config
        from core.db_helper import get_db_engine
        from sqlalchemy import text
        with get_db_engine().connect() as connection:
            connection.exec_driver_sql("START TRANSACTION WITH CONSISTENT SNAPSHOT, READ ONLY")
            benchmark = pd.read_sql(text("SELECT trade_date, close_price FROM daily_market_data WHERE stock_id=:sid AND trade_date BETWEEN :start AND :end ORDER BY trade_date"),
                connection, params={"sid": Config.MARKET_SYMBOL, "start": "2022-01-01", "end": "2026-10-20"})
        assert not benchmark.empty, "DATA_BLOCKED: market index missing"
        assert not benchmark.trade_date.duplicated().any(), "DATA_BLOCKED: duplicate market dates"
        benchmark.to_parquet(benchmark_path, index=False)
        write_json(output / "benchmark_manifest.json", {"sha256": digest(benchmark_path), "source": "daily_market_data",
            "symbol": Config.MARKET_SYMBOL, "basis": "configured proxy; instrument identity/total-return/PIT vintage not certified"})
    manifest = json.loads((output / 'benchmark_manifest.json').read_text(encoding='utf-8'))
    write_json(output / 'benchmark_identity_correction.json', {'symbol': manifest['symbol'],
        'instrument_type': 'SINGLE_STOCK' if manifest['symbol'] == '2330' else 'UNVERIFIED',
        'status': 'BLOCKED', 'reason': 'Configured proxy is not a verified broad-market total-return benchmark. v1 price-index/TAIEX labels are invalid; archived observations are unchanged.'})
    benchmark = pd.read_parquet(benchmark_path)
    prices = pd.Series(benchmark.close_price.to_numpy(dtype=float), index=pd.to_datetime(benchmark.trade_date).dt.normalize())
    labels = regime_labels(prices)
    stock_snapshot = output / 'snapshot_daily_market_data.parquet'
    if stock_snapshot.exists():
        observations = pd.read_parquet(stock_snapshot, columns=['stock_id', 'trade_date'])
        market_dates = pd.to_datetime(observations['trade_date'])
        write_json(output / 'snapshot_quality.json', {
            'source': stock_snapshot.name, 'sha256': digest(stock_snapshot),
            'rows': len(observations), 'assets': int(observations.stock_id.nunique()),
            'duplicate_asset_dates': int(observations.duplicated(['stock_id', 'trade_date']).sum()),
            'weekend_observations': int((market_dates.dt.dayofweek > 4).sum()),
            'weekend_dates': sorted(market_dates[market_dates.dt.dayofweek > 4].dt.strftime('%Y-%m-%d').unique().tolist()),
            'benchmark_weekend_observations': int((prices.index.dayofweek > 4).sum()),
            'status': 'BLOCKED',
            'reason': 'Stored observations are not independently certified historical prices. Taiwan exchange calendar, asset scope, provider/corporate-action provenance and PIT versions require validation; no rows were silently filtered or manufactured.'})
    assessments = {}
    for path in sorted(output.glob("*_diagnostics.json")):
        group = path.name.removesuffix("_diagnostics.json")
        captures = json.loads(path.read_text(encoding="utf-8"))
        for sid, tracks in captures.items():
            base = tracks.get("BASE_COST", tracks.get("net_base", next(iter(tracks.values()))))
            dates = base["dates"]
            aligned = prices.reindex(pd.to_datetime(dates).normalize())
            price_diagnostic = {"status": "BLOCKED", "reason": "missing benchmark dates"}
            if len(aligned) > 1 and aligned.notna().all() and aligned.iloc[0] > 0:
                price_diagnostic = {"calculation_status": "COMPLETED", "validation_status": "BLOCKED", "scope": "configured single-instrument proxy diagnostic; not a market index or benchmark certification",
                    "metrics": risk((aligned / aligned.iloc[0] * 1_000_000).tolist())}
            assessments[group + ":" + sid] = {"regimes": {name: {'status': 'BLOCKED', 'reason': 'BROAD_MARKET_SERIES_NOT_VERIFIED'} for name in REGIMES},
                "regime_proxy_diagnostic": episodes(base["equity"], dates, labels),
                "benchmark_proxy_diagnostic": price_diagnostic, "benchmark_symbol": manifest['symbol'], "benchmark_certification": "BLOCKED",
                "oos_status": "BLOCKED", "oos_reason": base["oos_reason"],
                "fold_count": len(base["folds"]), "cost_tracks": list(tracks), "source": path.name}
    stocks_path = output / "stocks.json"
    if stocks_path.exists():
        for sid, record in json.loads(stocks_path.read_text(encoding="utf-8")).items():
            if record.get("dates"):
                assessments[sid] = {"regimes": {name: {'status': 'BLOCKED', 'reason': 'BROAD_MARKET_SERIES_NOT_VERIFIED'} for name in REGIMES},
                    "regime_proxy_diagnostic": episodes(record["equity"], record["dates"], labels),
                    "benchmark_certification": "BLOCKED", "oos_status": "BLOCKED",
                    "reason": "short characterization window, same-close execution and unverified PIT"}
    write_json(output / "quant_assessments.json", {"plan": plan, "records": assessments})
    print("ASSESSED", len(assessments), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    assess(args.output)
