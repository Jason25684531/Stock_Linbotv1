"""Capture canonical metrics from existing research runners, without changing fills."""
import argparse
from dataclasses import asdict
import math
from pathlib import Path
import sys
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from core.backtest.metrics import calculate_metrics
from core.validation.split import split_is_oos
from core.validation.walk_forward import walk_forward_folds
from scripts.validate_strategy_inventory import write_json


def closed_trades(fills):
    """Normalize actual long-only fills into flat-to-flat net-cash trade records."""
    positions, closed = {}, []
    for fill in fills:
        asset, side = fill['asset'], fill['side'].lower()
        quantity, price, fee = float(fill['quantity']), float(fill['price']), float(fill['fee'])
        if not all(math.isfinite(v) for v in (quantity, price, fee)) or quantity <= 0 or price <= 0:
            raise ValueError('invalid execution fill')
        if side == 'buy':
            position = positions.setdefault(asset, {'quantity': 0., 'spent': 0., 'received': 0., 'buy_date': fill['date']})
            position['quantity'] += quantity
            position['spent'] += quantity * price + fee
        elif side == 'sell':
            if asset not in positions:
                raise ValueError('short or orphan sell is outside the long-only research contract')
            position = positions[asset]
            position['quantity'] -= quantity
            position['received'] += quantity * price - fee
            if math.isclose(position['quantity'], 0., rel_tol=1e-9, abs_tol=1e-12):
                closed.append({'stock_id': asset, 'buy_date': position['buy_date'], 'sell_date': fill['date'],
                    'profit': position['received'] - position['spent'], 'quantity_residual': position['quantity']})
                del positions[asset]
            elif position['quantity'] < 0:
                raise ValueError('sell exceeds the observed long position')
        else:
            raise ValueError('unknown fill side')
    return closed


def summarize(result):
    equity = result.get("equity", result.get("value"))
    values = [float(v) for v in equity]
    if "orders" in result:
        orders = result["orders"]
        fills = [{"asset": str(r.Column), "side": r.Side, "quantity": float(r.Size), "price": float(r.Price),
                  "fee": float(r.Fees), "date": str(r.Timestamp), "notional": float(r.Size * r.Price)} for r in orders.itertuples()]
    else:
        fills = ([{"asset": str(r['asset_id']), "side": r['side'], "quantity": r['shares'], "price": r['price'],
                   "fee": r['cost'], "date": str(r['execution_date']), "notional": r['notional']} for r in result['trades']]
                 if 'trades' in result else None)
    closed = closed_trades(fills) if fills is not None else None
    metrics = calculate_metrics(values, closed)
    if fills is not None:
        metrics.values["turnover"] = calculate_metrics(values, fills).get("turnover")
    payload = {k: asdict(metrics.get(k)) for k in ("cagr", "sharpe", "sortino", "max_drawdown", "win_rate", "trade_count", "turnover")}
    if fills is None:
        for name in ('win_rate', 'trade_count', 'turnover'):
            payload[name] = {"value": None, "reason": "order ledger not exported by this research boundary"}
    for value in payload.values():
        if value["value"] is not None and not math.isfinite(value["value"]):
            value.update(value=None, reason="existing canonical metrics returned nonfinite value")
    segments = {}
    if len(values) > 2:
        train, test = split_is_oos(values, .7)
        segments = {"train_end": str(equity.index[len(train) - 1]), "test_start": str(equity.index[len(train)]),
            "is": {k: asdict(calculate_metrics(train).get(k)) for k in ("cagr", "sharpe", "sortino", "max_drawdown")},
            "oos": {k: asdict(calculate_metrics(test).get(k)) for k in ("cagr", "sharpe", "sortino", "max_drawdown")}}
    folds = walk_forward_folds(len(values), 252, 63, expanding=True) if len(values) > 315 else []
    for fold in folds:
        fold["train_metrics"] = {k: asdict(calculate_metrics(values[fold["train_start"]:fold["train_end"] + 1]).get(k)) for k in ("cagr", "sharpe", "sortino", "max_drawdown")}
        fold["test_metrics"] = {k: asdict(calculate_metrics(values[fold["test_start"]:fold["test_end"] + 1]).get(k)) for k in ("cagr", "sharpe", "sortino", "max_drawdown")}
        fold["parameters"] = {"policy": "unchanged frozen historical target weights; no retuning"}
        assert fold["train_end"] < fold["test_start"]
    return {"metrics": payload, "order_count": len(fills) if fills is not None else None, "equity": values,
        "fills": fills, "closed_trades": closed,
        "trade_definition": "long-only flat-to-flat cash cycle, net of all observed fill fees; open cycles excluded",
        "dates": [str(d) for d in equity.index], "segments": segments, "folds": folds,
        "oos_status": "BLOCKED", "oos_reason": "historical selections already used this interval; segment diagnostics are not untouched Fresh OOS",
        "regimes_status": "NOT_TESTED", "benchmark_status": "BLOCKED: versioned asset-matched total-return benchmark missing"}


def run(group, output):
    captures = {}
    if group == "d5":
        from jobs import run_portfolio_research as job
        original = job.run_vectorbt

        def capture(close, targets, **kwargs):
            result = original(close, targets, **kwargs)
            sid = str(targets["config_id"].iloc[0])
            from core.research.portfolio_validation import COST_SCENARIOS
            gross = original(close, targets, **(kwargs | {'fee_rate': 0., 'tax_rate': 0., 'slippage': 0.}))
            stress = original(close, targets, **(kwargs | {'slippage': COST_SCENARIOS['STRESS']}))
            captures[sid] = {"BASE_COST": summarize(result), 'GROSS': summarize(gross), 'STRESS_COST': summarize(stress)}
            write_json(output.parent / "portfolio_research_diagnostics.checkpoint.json", captures)
            return result

        with patch.object(job, "run_vectorbt", side_effect=capture):
            job.run(handoff_path=Path("artifacts/d5_handoff/d4_acceptance_20260811_v8_with_scoreboard"),
                dataset_path=Path("artifacts/factors/d3_full_20230103_20260728/research_dataset"), output_dir=output)
        namespace = "portfolio_research"
    elif group == 'fundamental':
        from jobs import run_fundamental_strategy_validation as job
        original = job.f.run_cost_tracks

        def capture(targets, prices, *args, **kwargs):
            results = original(targets, prices, *args, **kwargs)
            sid = str(targets["strategy_id"].iloc[0])
            captures[sid] = {track: summarize(result) for track, result in results.items()}
            write_json(output.parent / "fundamental_strategy_validation_diagnostics.checkpoint.json", captures)
            return results

        with patch.object(job.f, "run_cost_tracks", side_effect=capture):
            job.run_pipeline(output_dir=output)
        namespace = "fundamental_strategy_validation"
    else:
        from jobs import run_strategy_search as job
        original = job.ss.run_backtest_tracks

        def capture(close, targets, *args, **kwargs):
            results = original(close, targets, *args, **kwargs)
            sid = str(targets['config_id'].iloc[0])
            captures[sid] = {track: summarize(result) for track, result in results.items()}
            write_json(output.parent / 'strategy_research_diagnostics.checkpoint.json', captures)
            return results

        with patch.object(job.ss, 'run_backtest_tracks', side_effect=capture):
            job.run(day2_dir=job.DEFAULT_DAY2_DIR, d3_root=job.DEFAULT_D3_ROOT,
                master_path=job.DEFAULT_MASTER_PATH, output_dir=output)
        namespace = 'strategy_research'
    write_json(output.parent / (namespace + "_diagnostics.json"), captures)
    print("CAPTURED", namespace, len(captures), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("group", choices=("d5", "fundamental", "day3"))
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    assert not args.output.exists(), "Use a new run directory"
    run(args.group, args.output)
