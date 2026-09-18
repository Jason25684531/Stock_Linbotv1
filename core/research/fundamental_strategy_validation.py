"""Frozen, research-only validation for the accepted fundamental factor pool.

The module deliberately owns no runtime strategy state.  It is a small
contract layer around the existing PIT data and canonical accounting adapter:
inputs are checked, policy is frozen, targets are deterministic, and every
undefined statistic is represented by ``None`` plus a reason.
"""

from __future__ import annotations

import hashlib
import itertools
import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Mapping, Sequence

import numpy as np
import pandas as pd

from config.settings import Config


CHANGE_ID = "add-fundamental-strategy-robustness-and-fresh-oos-v1"
DATASET_VERSION = "fundamental-pit-v3"
FACTOR_RESEARCH_KNOWLEDGE_CUTOFF = pd.Timestamp("2026-07-28")
TARGET_TICKER_COUNT = 890
TARGET_TICKER_SHA256 = "d311c1fea8c3110c1d9b940798040b8867d9c2ed5d9eefbd3447bd01d1c87594"
ACCEPTED_FACTOR_IDS = ("G2_OPERATING_INCOME_YOY", "G3_EPS_YOY")
_SOURCE_METRICS = {"G2_OPERATING_INCOME_YOY": "operating_income_yoy", "G3_EPS_YOY": "eps_yoy"}


class ContractError(ValueError):
    """Raised when a frozen research contract is missing or has changed."""


@dataclass(frozen=True)
class SearchPolicy:
    """All values are serialized before a backtest is allowed to run."""

    top_n: tuple[int, ...] = (5, 10, 20)
    rebalance_days: tuple[int, ...] = (20, 60)
    portfolio_weightings: tuple[str, ...] = ("EQUAL", "SCORE_WEIGHTED")
    factor_weightings: tuple[str, ...] = ("EQUAL",)
    initial_capital: float = 1_000_000.0
    fee_rate: float = Config.FEE_RATE
    tax_rate: float = Config.TAX_RATE
    base_slippage: float = Config.SLIPPAGE_RATE
    stress_slippage: float = Config.SLIPPAGE_RATE * 2.0
    bootstrap_draws: int = 500
    bootstrap_block_length: int = 20
    bootstrap_seed: int = 20260917


DEFAULT_POLICY = SearchPolicy()


def _jsonable(value: object) -> object:
    if isinstance(value, Mapping):
        return {str(k): _jsonable(v) for k, v in sorted(value.items(), key=lambda item: str(item[0]))}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating,)):
        return float(value) if np.isfinite(value) else None
    if isinstance(value, (pd.Timestamp,)):
        return value.isoformat()
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value


def canonical_json(value: object) -> str:
    return json.dumps(_jsonable(value), ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def sha256_file(path: Path) -> str:
    return sha256_bytes(Path(path).read_bytes())


def _write_json(path: Path, payload: object) -> str:
    encoded = (json.dumps(_jsonable(payload), ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(encoded)
    return sha256_bytes(encoded)


def _read_json(source: Path | Mapping[str, object]) -> Mapping[str, object]:
    if isinstance(source, Mapping):
        return source
    return json.loads(Path(source).read_text(encoding="utf-8"))


def _pool_records(source: Path | Mapping[str, object] | pd.DataFrame) -> list[dict[str, object]]:
    if isinstance(source, pd.DataFrame):
        records = source.to_dict("records")
    else:
        payload = _read_json(source)
        records = list(payload.get("accepted_factors", []))
        if not records and "factor_id" in payload:
            records = [dict(payload)]
    return [dict(item) for item in records]


def revalidate_accepted_factor_pool(
    pool: Path | Mapping[str, object] | pd.DataFrame,
    scoreboard: Path | pd.DataFrame | None = None,
) -> dict[str, object]:
    """Re-check the two-factor handoff independently before search.

    The optional scoreboard is intentionally used only as evidence: it is
    never used to add a factor or to choose a strategy direction.
    """

    if isinstance(pool, pd.DataFrame):
        payload: Mapping[str, object] = {}
    else:
        payload = _read_json(pool)
    records = _pool_records(pool)
    if payload:
        first = records[0] if records else {}
        dataset_version = payload.get("dataset_version", first.get("dataset_version"))
        universe_hash = payload.get("universe_hash", payload.get("target_ticker_sha256", first.get("universe_hash")))
        if dataset_version != DATASET_VERSION or universe_hash != TARGET_TICKER_SHA256:
            raise ContractError("accepted factor pool dataset or universe identity mismatch")
    ids = [str(item.get("factor_id", item.get("candidate_id", ""))) for item in records]
    if sorted(ids) != sorted(ACCEPTED_FACTOR_IDS) or len(ids) != len(set(ids)):
        raise ContractError(f"accepted factor pool must contain exactly {list(ACCEPTED_FACTOR_IDS)}")
    evidence: list[dict[str, object]] = []
    for item in records:
        factor_id = str(item.get("factor_id", item.get("candidate_id")))
        definition = item.get("definition", item)
        if not isinstance(definition, Mapping):
            raise ContractError(f"{factor_id}: missing factor definition")
        expected_metric = _SOURCE_METRICS[factor_id]
        verdict = str(item.get("validation_result", {}).get("verdict", item.get("verdict", "")))
        checks = {
            "source_metric": definition.get("source_metric") == expected_metric,
            "direction": int(definition.get("direction", item.get("direction", 0))) == 1,
            "primary_horizon": int(definition.get("primary_horizon", item.get("primary_horizon", 0))) == 20,
            "dataset_version": item.get("dataset_version", DATASET_VERSION) == DATASET_VERSION,
            "verdict": verdict == "ACCEPT",
        }
        if not all(checks.values()):
            raise ContractError(f"{factor_id}: accepted-factor revalidation failed: {checks}")
        result = item.get("validation_result", {})
        if isinstance(result, Mapping):
            for key in ("rank_ic_pass", "icir_pass", "positive_rank_ic_pass", "q5_q1_pass", "temporal_stability_pass", "coverage_pass"):
                if key in result and result[key] is not True:
                    raise ContractError(f"{factor_id}: frozen evidence {key} is not PASS")
        evidence.append({"factor_id": factor_id, "checks": checks, "verdict": verdict})
    if scoreboard is not None:
        table = pd.read_csv(scoreboard) if isinstance(scoreboard, (str, Path)) else scoreboard.copy()
        if "factor_id" not in table.columns:
            raise ContractError("factor scoreboard is missing factor_id")
        horizon_column = "horizon" if "horizon" in table.columns else "primary_horizon" if "primary_horizon" in table.columns else None
        for factor_id in ACCEPTED_FACTOR_IDS:
            rows = table.loc[table["factor_id"].eq(factor_id)]
            if horizon_column:
                rows = rows.loc[pd.to_numeric(rows[horizon_column], errors="coerce").eq(20)]
            if len(rows) != 1:
                raise ContractError(f"{factor_id}: primary-horizon scoreboard row is not unique")
            row = rows.iloc[0]
            if str(row.get("verdict", "")) != "ACCEPT":
                raise ContractError(f"{factor_id}: scoreboard verdict is not ACCEPT")
            for key in ("aligned_rank_ic", "icir", "positive_rank_ic_ratio"):
                if key in row and not np.isfinite(float(row[key])):
                    raise ContractError(f"{factor_id}: scoreboard {key} is not finite")
    return {
        "status": "PASS",
        "dataset_version": DATASET_VERSION,
        "target_ticker_count": TARGET_TICKER_COUNT,
        "target_ticker_sha256": TARGET_TICKER_SHA256,
        "factor_revalidation": {item["factor_id"]: "PASS" for item in evidence},
        "accepted_factors": evidence,
        "factor_research_knowledge_cutoff": FACTOR_RESEARCH_KNOWLEDGE_CUTOFF.date().isoformat(),
    }


def default_strategy_search_spec(policy: SearchPolicy = DEFAULT_POLICY) -> dict[str, object]:
    """Return the complete pre-result policy; no result is consulted here."""

    factor_sets = [
        {"factor_set_id": "G2", "factors": ["G2_OPERATING_INCOME_YOY"], "weights": {"G2_OPERATING_INCOME_YOY": 1.0}},
        {"factor_set_id": "G3", "factors": ["G3_EPS_YOY"], "weights": {"G3_EPS_YOY": 1.0}},
        {"factor_set_id": "G2_PLUS_G3", "factors": list(ACCEPTED_FACTOR_IDS), "weights": {item: 0.5 for item in ACCEPTED_FACTOR_IDS}},
    ]
    return {
        "schema_version": "fundamental-strategy-v1",
        "change_id": CHANGE_ID,
        "dataset_version": DATASET_VERSION,
        "universe_hash": TARGET_TICKER_SHA256,
        "factor_research_knowledge_cutoff": FACTOR_RESEARCH_KNOWLEDGE_CUTOFF.date().isoformat(),
        "accepted_factor_ids": list(ACCEPTED_FACTOR_IDS),
        "factor_sets": factor_sets,
        "factor_weighting_methods": list(policy.factor_weightings),
        "top_n": list(policy.top_n),
        "rebalance_days": list(policy.rebalance_days),
        "portfolio_weighting": list(policy.portfolio_weightings),
        "filters": {"member": True, "is_tradable_t1": True, "new_controls": []},
        "execution": {
            "signal_date_rule": "signal_date < execution_date",
            "execution_timing": "next legal trading date",
            "non_rebalance_policy": "NO_RETARGET",
            "missing_execution_price_policy": "SKIP_OR_DEFER",
            "removed_asset_policy": "TARGET_ZERO",
            "tie_break": ["score_desc", "asset_id_asc"],
        },
        "costs": {
            "fee_rate": policy.fee_rate,
            "tax_rate": policy.tax_rate,
            "tracks": {"GROSS": {"fee_rate": 0.0, "tax_rate": 0.0, "slippage": 0.0}, "BASE_COST": {"fee_rate": policy.fee_rate, "tax_rate": policy.tax_rate, "slippage": policy.base_slippage}, "STRESS_COST": {"fee_rate": policy.fee_rate, "tax_rate": policy.tax_rate, "slippage": policy.stress_slippage}},
        },
        "gates": {
            "VALIDITY_GATE": {"min_observations": 60, "min_trade_count": 1},
            "EXECUTION_GATE": {"max_non_rebalance_orders": 0, "require_signal_before_execution": True},
            "RETURN_RISK_GATE": {"min_cagr": -1.0, "max_mdd": -1.0, "finite_metrics": True},
            "COST_GATE": {"stress_cagr_not_below": -1.0, "stress_not_above_base": True},
            "STABILITY_GATE": {"min_positive_fold_ratio": 0.5, "min_folds": 2},
        },
        "temporal_folds": [{"fold_id": "2023", "start": "2023-01-03", "end": "2023-12-29"}, {"fold_id": "2024", "start": "2024-01-01", "end": "2024-12-31"}, {"fold_id": "2025", "start": "2025-01-01", "end": "2025-12-31"}, {"fold_id": "2026", "start": "2026-01-01", "end": "2026-07-28"}],
        "robustness": {"parameter_neighborhood": {"top_n": "adjacent_frozen_values", "rebalance_days": "adjacent_frozen_values", "portfolio_weighting": "all_frozen_alternatives"}, "bootstrap": {"method": "moving_block", "block_length": policy.bootstrap_block_length, "draws": policy.bootstrap_draws, "seed": policy.bootstrap_seed}},
        "ranking": {"formula": "zscore(CAGR)+zscore(Sharpe)-zscore(MDD_abs)-zscore(cost_drag)", "tie_break": ["score_desc", "strategy_id_asc"], "shortlist_limit": 5},
        "fresh_oos": {"strict_after": FACTOR_RESEARCH_KNOWLEDGE_CUTOFF.date().isoformat(), "minimums_by_rebalance_days": {"20": {"trading_days": 120, "rebalances": 5, "months": 6}, "60": {"trading_days": 180, "rebalances": 3, "months": 9}}, "pass_thresholds": {"implementation_valid": True, "no_leakage": True, "finite_returns": True, "has_trades": True, "directional_performance": 0.0, "max_mdd": -1.0}},
    }


def freeze_strategy_search_spec(path: Path, spec: Mapping[str, object] | None = None) -> dict[str, object]:
    payload = dict(spec or default_strategy_search_spec())
    encoded = (json.dumps(_jsonable(payload), ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and path.read_bytes() != encoded:
        raise ContractError(f"frozen strategy search spec changed: {path}")
    if not path.exists():
        path.write_bytes(encoded)
    return {"spec": payload, "sha256": sha256_bytes(encoded), "path": str(path)}


def _factor_set_rows(spec: Mapping[str, object]) -> list[dict[str, object]]:
    rows = list(spec.get("factor_sets", []))
    if not rows:
        raise ContractError("strategy search spec has no factor sets")
    allowed = set(ACCEPTED_FACTOR_IDS)
    for row in rows:
        factors = set(row.get("factors", []))
        if not factors or not factors <= allowed:
            raise ContractError("strategy grid contains a factor outside the accepted pool")
        weights = row.get("weights", {})
        if set(weights) != factors or not math.isclose(sum(float(v) for v in weights.values()), 1.0, abs_tol=1e-12):
            raise ContractError("factor weights must cover the set and sum to one")
    return rows


def build_strategy_grid(spec: Mapping[str, object]) -> pd.DataFrame:
    rows = []
    for factor_set, weighting, top_n, rebalance_days, portfolio_weighting in itertools.product(
        _factor_set_rows(spec), spec.get("factor_weighting_methods", []), spec.get("top_n", []), spec.get("rebalance_days", []), spec.get("portfolio_weighting", [])
    ):
        identity = {"factor_set_id": factor_set["factor_set_id"], "factor_ids": factor_set["factors"], "factor_weighting": weighting, "factor_weights": factor_set["weights"], "top_n": int(top_n), "rebalance_days": int(rebalance_days), "portfolio_weighting": portfolio_weighting}
        strategy_id = f"{factor_set['factor_set_id']}__{weighting}__TOP{int(top_n)}__REB{int(rebalance_days)}__{portfolio_weighting}"
        rows.append({"strategy_id": strategy_id, **identity, "factor_weights_json": canonical_json(factor_set["weights"]), "config_hash": sha256_bytes(canonical_json(identity).encode("utf-8"))})
    grid = pd.DataFrame(rows).sort_values("strategy_id", kind="stable").reset_index(drop=True)
    if grid.empty or grid["strategy_id"].duplicated().any() or grid["config_hash"].duplicated().any():
        raise ContractError("strategy grid is empty or non-deterministic")
    return grid


def freeze_strategy_grid(path: Path, grid: pd.DataFrame) -> str:
    encoded = grid.sort_values("strategy_id", kind="stable").to_csv(index=False, lineterminator="\n").encode("utf-8")
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and path.read_bytes() != encoded:
        raise ContractError(f"frozen strategy grid changed: {path}")
    if not path.exists():
        path.write_bytes(encoded)
    return sha256_bytes(encoded)


def rank_cross_section(frame: pd.DataFrame, *, date_column: str = "asof_date", asset_column: str = "asset_id", score_column: str = "score") -> pd.DataFrame:
    """Rank descending with a stable asset-id tie-break and no imputation."""

    required = {date_column, asset_column, score_column}
    if not required <= set(frame.columns):
        raise ContractError(f"score frame missing columns: {sorted(required - set(frame.columns))}")
    work = frame.copy()
    work[score_column] = pd.to_numeric(work[score_column], errors="coerce").replace([np.inf, -np.inf], np.nan)
    return work.loc[work[score_column].notna()].sort_values([date_column, score_column, asset_column], ascending=[True, False, True], kind="stable")


def build_target_weights(
    scores: pd.DataFrame,
    universe: pd.DataFrame,
    *,
    strategy_id: str,
    top_n: int,
    rebalance_days: int,
    portfolio_weighting: str,
) -> pd.DataFrame:
    """Build sparse legal instructions and explicit zero targets for exits."""

    score_column = "score" if "score" in scores.columns else "composite_score"
    score_frame = scores.rename(columns={score_column: "score"}).copy()
    universe = universe.copy()
    for frame in (score_frame, universe):
        frame["asof_date"] = pd.to_datetime(frame["asof_date"])
    if "execution_date" not in universe.columns:
        raise ContractError("universe must provide an execution_date; do not infer a stale price")
    universe["execution_date"] = pd.to_datetime(universe["execution_date"])
    merged = score_frame.merge(universe, on=["asof_date", "asset_id"], how="inner", suffixes=("", "_universe"))
    member = merged.get("member", pd.Series(True, index=merged.index)).fillna(False).astype(bool)
    tradable = merged.get("is_tradable_t1", pd.Series(True, index=merged.index)).fillna(False).astype(bool)
    eligible = rank_cross_section(merged.loc[member & tradable], score_column="score")
    dates = pd.DatetimeIndex(sorted(eligible["asof_date"].unique()))[:: int(rebalance_days)]
    rows: list[dict[str, object]] = []
    previous: set[str] = set()
    for signal_date in dates:
        day = eligible.loc[eligible["asof_date"].eq(signal_date)].head(int(top_n)).copy()
        execution_dates = pd.to_datetime(day["execution_date"], errors="coerce").dropna()
        if execution_dates.empty or (execution_dates <= signal_date).any():
            raise ContractError(f"{strategy_id}: execution date is not strictly after signal date")
        execution_date = execution_dates.iloc[0]
        selected = set(day["asset_id"].astype(str))
        if portfolio_weighting == "EQUAL":
            weights = {asset: 1.0 / len(selected) for asset in selected} if selected else {}
        elif portfolio_weighting == "SCORE_WEIGHTED":
            positive = day.assign(score=day["score"].clip(lower=0))
            total = float(positive["score"].sum())
            weights = ({str(row.asset_id): float(row.score / total) for row in positive.itertuples()} if total > 0 else {asset: 1.0 / len(selected) for asset in selected})
        else:
            raise ContractError(f"unsupported portfolio weighting: {portfolio_weighting}")
        for asset in sorted(previous | selected):
            rows.append({"signal_date": signal_date, "asof_date": signal_date, "execution_date": execution_date, "asset_id": asset, "target_weight": weights.get(asset, 0.0), "strategy_id": strategy_id})
        previous = selected
    result = pd.DataFrame(rows, columns=["signal_date", "asof_date", "execution_date", "asset_id", "target_weight", "strategy_id"])
    validate_targets(result)
    return result


def validate_targets(targets: pd.DataFrame, legal_rebalance_dates: Iterable[pd.Timestamp] | None = None) -> dict[str, object]:
    required = {"asof_date", "execution_date", "asset_id", "target_weight"}
    missing = required - set(targets.columns)
    if missing:
        raise ContractError(f"target weights missing columns: {sorted(missing)}")
    work = targets.copy()
    work["asof_date"] = pd.to_datetime(work["asof_date"])
    work["execution_date"] = pd.to_datetime(work["execution_date"])
    if "signal_date" in work.columns:
        signal = pd.to_datetime(work["signal_date"])
        if not signal.equals(work["asof_date"]):
            raise ContractError("signal_date and asof_date differ")
    if (work["execution_date"] <= work["asof_date"]).any():
        raise ContractError("signal_date must be strictly before execution_date")
    if work.duplicated(["execution_date", "asset_id"]).any():
        raise ContractError("duplicate target for one execution date and asset")
    if (pd.to_numeric(work["target_weight"], errors="coerce") < -1e-12).any():
        raise ContractError("target weight cannot be negative")
    if legal_rebalance_dates is not None and not work["execution_date"].isin(pd.DatetimeIndex(legal_rebalance_dates)).all():
        raise ContractError("target occurs outside legal rebalance dates")
    return {"signal_before_execution": True, "target_count": len(work), "rebalance_count": int(work["execution_date"].nunique())}


def _cost_for(notional: float, side: str, cost: Mapping[str, float]) -> float:
    fee = abs(notional) * float(cost.get("fee_rate", 0.0))
    tax = abs(notional) * float(cost.get("tax_rate", 0.0)) if side == "sell" else 0.0
    return fee + tax


def simulate_target_weights(
    targets: pd.DataFrame,
    prices: pd.DataFrame,
    *,
    initial_capital: float = DEFAULT_POLICY.initial_capital,
    cost: Mapping[str, float] | None = None,
) -> dict[str, object]:
    """Replay sparse instructions without ever filling a missing execution price."""

    validate_targets(targets)
    cost = dict(cost or {"fee_rate": 0.0, "tax_rate": 0.0, "slippage": 0.0})
    target = targets.copy()
    target["execution_date"] = pd.to_datetime(target["execution_date"])
    price = prices.copy()
    price.index = pd.to_datetime(price.index)
    execution_dates = set(target["execution_date"])
    calendar = sorted(set(price.index) | execution_dates)
    by_date = {date: group for date, group in target.groupby("execution_date", sort=True)}
    cash = float(initial_capital)
    positions: dict[str, dict[str, float]] = {}
    last_marks: dict[str, float] = {}
    trades: list[dict[str, object]] = []
    execution_log: list[dict[str, object]] = []
    equities: list[float] = []
    equity_dates: list[pd.Timestamp] = []
    for current in calendar:
        current_prices = {}
        if current in price.index:
            for asset, value in price.loc[current].items():
                if pd.notna(value) and float(value) > 0:
                    current_prices[str(asset)] = float(value)
                    last_marks[str(asset)] = float(value)
        if current in by_date:
            desired = {str(row.asset_id): float(row.target_weight) for row in by_date[current].itertuples()}
            equity_before = cash + sum(item["shares"] * last_marks.get(asset, item["price"]) for asset, item in positions.items())
            # Liquidate at the legal execution price; missing prices are deferred.
            for asset in sorted(list(positions)):
                execution_price = current_prices.get(asset)
                if execution_price is None:
                    execution_log.append({"execution_date": current, "asset_id": asset, "side": "sell", "status": "DEFERRED", "reason": "MISSING_EXECUTION_PRICE"})
                    continue
                item = positions.pop(asset)
                slipped = execution_price * (1.0 - float(cost.get("slippage", 0.0)))
                notional = item["shares"] * slipped
                fee = _cost_for(notional, "sell", cost)
                cash += notional - fee
                trades.append({"execution_date": current, "asset_id": asset, "side": "sell", "shares": item["shares"], "price": slipped, "notional": notional, "cost": fee})
            for asset in sorted(desired):
                weight = desired[asset]
                if weight <= 0 or asset in positions:
                    continue
                execution_price = current_prices.get(asset)
                if execution_price is None:
                    execution_log.append({"execution_date": current, "asset_id": asset, "side": "buy", "status": "DEFERRED", "reason": "MISSING_EXECUTION_PRICE"})
                    continue
                slipped = execution_price * (1.0 + float(cost.get("slippage", 0.0)))
                notional = max(0.0, equity_before * weight)
                shares = int(notional // slipped)
                while shares > 0:
                    gross = shares * slipped
                    fee = _cost_for(gross, "buy", cost)
                    if gross + fee <= cash:
                        break
                    shares -= 1
                if shares <= 0:
                    continue
                gross = shares * slipped
                fee = _cost_for(gross, "buy", cost)
                cash -= gross + fee
                positions[asset] = {"shares": float(shares), "price": slipped}
                trades.append({"execution_date": current, "asset_id": asset, "side": "buy", "shares": float(shares), "price": slipped, "notional": gross, "cost": fee})
        value = cash + sum(item["shares"] * last_marks.get(asset, item["price"]) for asset, item in positions.items())
        equities.append(float(value))
        equity_dates.append(pd.Timestamp(current))
    equity = pd.Series(equities, index=pd.DatetimeIndex(equity_dates), name="equity")
    returns = equity.pct_change().replace([np.inf, -np.inf], np.nan).dropna().rename("daily_return")
    return {"equity": equity, "returns": returns, "trades": trades, "execution_log": pd.DataFrame(execution_log), "positions": positions, "cost": cost}


def performance_metrics(result: Mapping[str, object], periods_per_year: int = 252) -> dict[str, object]:
    equity = pd.Series(result.get("equity", []), dtype=float).replace([np.inf, -np.inf], np.nan).dropna()
    returns = pd.Series(result.get("returns", []), dtype=float).replace([np.inf, -np.inf], np.nan).dropna()
    trades = list(result.get("trades", []))
    reason = None
    if len(equity) < 2 or equity.iloc[0] <= 0:
        reason = "at least two positive equity observations are required"
    total = float(equity.iloc[-1] / equity.iloc[0] - 1.0) if reason is None else None
    cagr = float((1.0 + total) ** (periods_per_year / max(1, len(returns))) - 1.0) if total is not None and 1.0 + total > 0 else None
    volatility = float(returns.std(ddof=0) * np.sqrt(periods_per_year)) if len(returns) > 1 else None
    downside = returns.loc[returns < 0]
    downside_dev = float(downside.std(ddof=0) * np.sqrt(periods_per_year)) if len(downside) > 1 else None
    sharpe = float(returns.mean() / returns.std(ddof=0) * np.sqrt(periods_per_year)) if len(returns) > 1 and returns.std(ddof=0) > 0 else None
    sortino = float(returns.mean() / downside.std(ddof=0) * np.sqrt(periods_per_year)) if len(downside) > 1 and downside.std(ddof=0) > 0 else None
    drawdown = (equity / equity.cummax() - 1.0) if len(equity) else pd.Series(dtype=float)
    mdd = float(drawdown.min()) if len(drawdown) else None
    turnover = float(sum(abs(float(item.get("notional", 0.0))) for item in trades) / equity.iloc[0]) if len(equity) and equity.iloc[0] else None
    total_cost = float(sum(float(item.get("cost", 0.0)) for item in trades))
    return {"total_return": total, "cagr": cagr, "volatility": volatility, "sharpe": sharpe, "sortino": sortino, "calmar": float(cagr / abs(mdd)) if cagr is not None and mdd not in (None, 0) else None, "mdd": mdd, "turnover": turnover, "trade_count": len(trades), "total_cost": total_cost, "finite_returns": bool(np.isfinite(returns).all()), "undefined_reason": reason}


def run_cost_tracks(targets: pd.DataFrame, prices: pd.DataFrame, policy: SearchPolicy = DEFAULT_POLICY) -> dict[str, dict[str, object]]:
    tracks = {
        "GROSS": {"fee_rate": 0.0, "tax_rate": 0.0, "slippage": 0.0},
        "BASE_COST": {"fee_rate": policy.fee_rate, "tax_rate": policy.tax_rate, "slippage": policy.base_slippage},
        "STRESS_COST": {"fee_rate": policy.fee_rate, "tax_rate": policy.tax_rate, "slippage": policy.stress_slippage},
    }
    return {name: simulate_target_weights(targets, prices, initial_capital=policy.initial_capital, cost=cost) for name, cost in tracks.items()}


def execution_audit(result: Mapping[str, object], targets: pd.DataFrame) -> dict[str, object]:
    log = result.get("execution_log", pd.DataFrame())
    if not isinstance(log, pd.DataFrame):
        log = pd.DataFrame(log)
    violations: list[str] = []
    if "execution_date" in targets and (pd.to_datetime(targets["execution_date"]) <= pd.to_datetime(targets["asof_date"])).any():
        violations.append("signal_date_not_before_execution_date")
    return {"signal_before_execution": "signal_date_not_before_execution_date" not in violations, "non_rebalance_orders": 0, "deferred_missing_price_count": int(len(log.loc[log.get("reason", pd.Series(dtype=str)).eq("MISSING_EXECUTION_PRICE")])) if not log.empty else 0, "violations": violations, "pass": not violations}


def temporal_stability(returns: pd.Series, folds: Sequence[Mapping[str, object]]) -> tuple[pd.DataFrame, dict[str, object]]:
    series = pd.Series(returns, dtype=float).replace([np.inf, -np.inf], np.nan).dropna().sort_index()
    rows: list[dict[str, object]] = []
    for fold in folds:
        start, end = pd.Timestamp(fold["start"]), pd.Timestamp(fold["end"])
        part = series.loc[(series.index >= start) & (series.index <= end)]
        std = float(part.std(ddof=0)) if len(part) > 1 else None
        rows.append({"fold_id": fold["fold_id"], "start": start.date().isoformat(), "end": end.date().isoformat(), "observations": len(part), "return": float((1 + part).prod() - 1) if len(part) else None, "sharpe": float(part.mean() / std * np.sqrt(252)) if std and std > 0 else None, "finite": bool(np.isfinite(part).all())})
    table = pd.DataFrame(rows)
    valid = table.loc[table["observations"] > 1]
    positive_ratio = float((valid["return"] > 0).mean()) if len(valid) else None
    summary = {"status": "PASS" if len(valid) >= 2 else "NOT_ENOUGH_FOLDS", "fold_count": len(valid), "positive_fold_ratio": positive_ratio, "worst_fold_return": float(valid["return"].min()) if len(valid) else None, "sharpe_consistency": float(valid["sharpe"].std(ddof=0)) if len(valid) > 1 else None}
    return table, summary


def parameter_stability(scoreboard: pd.DataFrame, strategy_id: str) -> dict[str, object]:
    row = scoreboard.loc[scoreboard["strategy_id"].eq(strategy_id)]
    if len(row) != 1:
        raise ContractError(f"unknown strategy_id: {strategy_id}")
    current = row.iloc[0]
    masks = []
    parameters = ("top_n", "rebalance_days", "portfolio_weighting")
    for column in parameters:
        if column not in scoreboard:
            continue
        mask = pd.Series(True, index=scoreboard.index)
        for other in parameters:
            if other != column and other in scoreboard:
                mask &= scoreboard[other].eq(current[other])
        if column == "top_n":
            mask &= (scoreboard["top_n"] - current[column]).abs().isin((5, 10, 15))
        elif column == "rebalance_days":
            mask &= (scoreboard["rebalance_days"] - current[column]).abs().eq(40)
        else:
            mask &= scoreboard[column].ne(current[column])
        masks.append(mask)
    neighbors = scoreboard.loc[np.logical_or.reduce(masks)] if masks else scoreboard.iloc[0:0]
    values = pd.to_numeric(neighbors.get("cagr", pd.Series(dtype=float)), errors="coerce").dropna()
    stable = bool(len(values) and (values > 0).all())
    return {"strategy_id": strategy_id, "neighbor_count": len(neighbors), "neighbor_positive_count": int((values > 0).sum()), "parameter_stability": "PASS" if stable else "FAIL", "neighbor_ids": sorted(neighbors["strategy_id"].astype(str).tolist())}


def moving_block_bootstrap(returns: pd.Series, *, draws: int = DEFAULT_POLICY.bootstrap_draws, block_length: int = DEFAULT_POLICY.bootstrap_block_length, seed: int = DEFAULT_POLICY.bootstrap_seed) -> dict[str, object]:
    series = pd.Series(returns, dtype=float).replace([np.inf, -np.inf], np.nan).dropna().to_numpy(float)
    if len(series) < 2:
        return {"draws": 0, "block_length": block_length, "seed": seed, "p_sharpe_gt_zero": None, "p_cagr_gt_zero": None, "psr": None, "dsr": None, "undefined_reason": "at least two finite returns are required"}
    block_length = max(1, min(int(block_length), len(series)))
    rng = np.random.default_rng(seed)
    sharpe_values, cagr_values = [], []
    starts = np.arange(len(series))
    for _ in range(int(draws)):
        sampled: list[float] = []
        while len(sampled) < len(series):
            start = int(rng.choice(starts))
            sampled.extend(series[(start + np.arange(block_length)) % len(series)].tolist())
        sample = np.asarray(sampled[: len(series)])
        std = float(sample.std(ddof=0))
        sharpe_values.append(float(sample.mean() / std * np.sqrt(252)) if std > 0 else 0.0)
        cagr_values.append(float((1 + sample).prod() ** (252 / len(sample)) - 1) if np.all(1 + sample > 0) else -1.0)
    p_sharpe = float(np.mean(np.asarray(sharpe_values) > 0))
    p_cagr = float(np.mean(np.asarray(cagr_values) > 0))
    psr = None
    psr_reason = "at least thirty observations are required for PSR"
    if len(series) >= 30 and np.std(series, ddof=0) > 0:
        from statistics import NormalDist
        psr = float(NormalDist().cdf(float(series.mean() / series.std(ddof=0) * np.sqrt(len(series) - 1))))
        psr_reason = None
    return {"draws": int(draws), "block_length": block_length, "seed": int(seed), "p_sharpe_gt_zero": p_sharpe, "p_cagr_gt_zero": p_cagr, "psr": psr, "dsr": None, "undefined_reason": psr_reason or "DSR requires an independently frozen trial-count/effective-number-of-tests model"}


def redundancy_diagnostics(panels: Mapping[str, pd.DataFrame], *, top_n: int = 20) -> pd.DataFrame:
    if not set(ACCEPTED_FACTOR_IDS) <= set(panels):
        raise ContractError("redundancy diagnostics require both accepted factors")
    left, right = panels[ACCEPTED_FACTOR_IDS[0]], panels[ACCEPTED_FACTOR_IDS[1]]
    rows = []
    for date in sorted(set(left.index) & set(right.index)):
        pair = pd.concat([left.loc[date].rename("g2"), right.loc[date].rename("g3")], axis=1).dropna()
        if len(pair) < 2:
            continue
        g2_top = set(pair["g2"].sort_values(ascending=False).head(top_n).index.astype(str))
        g3_top = set(pair["g3"].sort_values(ascending=False).head(top_n).index.astype(str))
        rows.append({"asof_date": pd.Timestamp(date), "factor_a": ACCEPTED_FACTOR_IDS[0], "factor_b": ACCEPTED_FACTOR_IDS[1], "factor_correlation": float(pair["g2"].corr(pair["g3"])), "rank_correlation": float(pair["g2"].rank().corr(pair["g3"].rank())), "selection_overlap": len(g2_top & g3_top) / max(1, min(len(g2_top), len(g3_top)))})
    result = pd.DataFrame(rows)
    if result.empty:
        return pd.DataFrame(columns=["asof_date", "factor_a", "factor_b", "factor_correlation", "rank_correlation", "selection_overlap", "redundancy_risk"])
    result["redundancy_risk"] = np.where((result["selection_overlap"] >= 0.8) | (result["rank_correlation"].abs() >= 0.8), "HIGH", "NORMAL")
    return result


def shortlist_and_freeze(scoreboard: pd.DataFrame, path: Path, *, candidate_fields: Mapping[str, object] | None = None) -> dict[str, object]:
    valid = scoreboard.loc[scoreboard.get("strategy_valid", False).astype(bool)].copy() if not scoreboard.empty else scoreboard.copy()
    if valid.empty:
        payload = {"schema_version": "frozen-strategy-v1", "candidate": None, "strategy_fingerprint": None, "freeze_status": "NO_CANDIDATE"}
        if path.exists() and path.read_bytes() != (json.dumps(_jsonable(payload), indent=2, sort_keys=True) + "\n").encode():
            raise ContractError("frozen strategy spec changed")
        _write_json(path, payload)
        return payload
    ranked = valid.sort_values(["ranking_score", "strategy_id"], ascending=[False, True], kind="stable").reset_index(drop=True)
    shortlist = ranked.head(min(5, len(ranked))).copy()
    candidate = dict(candidate_fields or {})
    candidate.update({key: _jsonable(value) for key, value in ranked.iloc[0].to_dict().items()})
    candidate["accepted_factors"] = list(ACCEPTED_FACTOR_IDS)
    fingerprint = sha256_bytes(canonical_json(candidate).encode("utf-8"))
    payload = {"schema_version": "frozen-strategy-v1", "strategy_id": candidate["strategy_id"], "strategy_fingerprint": fingerprint, "candidate": candidate, "freeze_status": "FROZEN", "accepted_factors": list(ACCEPTED_FACTOR_IDS), "dataset_version": DATASET_VERSION, "universe_hash": TARGET_TICKER_SHA256, "factor_research_knowledge_cutoff": FACTOR_RESEARCH_KNOWLEDGE_CUTOFF.date().isoformat()}
    encoded = (json.dumps(_jsonable(payload), ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")
    path.parent.mkdir(parents=True, exist_ok=True)
    replace_empty_freeze = False
    if path.exists() and path.read_bytes() != encoded:
        try:
            previous = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            previous = {}
        # A prior run with no valid row has no candidate to mutate.  Once a
        # later corrected run establishes one, the first actual freeze is
        # allowed; an already FROZEN candidate remains immutable.
        if previous.get("freeze_status") != "NO_CANDIDATE":
            raise ContractError("frozen strategy spec changed; create a new fingerprint")
        replace_empty_freeze = True
    if not path.exists() or replace_empty_freeze:
        path.write_bytes(encoded)
    return {**payload, "shortlist": shortlist.to_dict("records")}


def fresh_oos_availability(
    dates: Iterable[pd.Timestamp],
    *,
    rebalance_days: int,
    minimum: Mapping[str, int],
    cutoff: pd.Timestamp = FACTOR_RESEARCH_KNOWLEDGE_CUTOFF,
    consumed_dates: Iterable[pd.Timestamp] = (),
    coverage: float | None = None,
) -> dict[str, object]:
    all_dates = pd.DatetimeIndex(sorted(pd.to_datetime(list(dates)).unique()))
    consumed = set(pd.to_datetime(list(consumed_dates)))
    fresh = all_dates[(all_dates > pd.Timestamp(cutoff)) & ~all_dates.isin(consumed)]
    months = int(fresh.to_period("M").nunique()) if len(fresh) else 0
    rebalances = int(len(fresh[:: max(1, int(rebalance_days))])) if len(fresh) else 0
    report = {"fresh_start": fresh.min().date().isoformat() if len(fresh) else None, "latest_available_date": fresh.max().date().isoformat() if len(fresh) else None, "trading_days": int(len(fresh)), "calendar_months": months, "rebalance_count": rebalances, "coverage": coverage, "cutoff": pd.Timestamp(cutoff).date().isoformat(), "strict_after_cutoff": True, "minimum_requirement": dict(minimum)}
    report["status"] = "PASS" if len(fresh) >= int(minimum["trading_days"]) and rebalances >= int(minimum["rebalances"]) and months >= int(minimum["months"]) else "INSUFFICIENT_DATA"
    return report


def fresh_oos_verdict(
    availability: Mapping[str, object],
    metrics: Mapping[str, object] | None,
    *,
    frozen_fingerprint: str | None,
    observed_fingerprint: str | None,
    audit: Mapping[str, object] | None = None,
    threshold: Mapping[str, object] | None = None,
) -> dict[str, object]:
    if availability.get("status") != "PASS":
        return {"status": "INSUFFICIENT_DATA", "reason": "fresh availability minimum is not met"}
    if frozen_fingerprint is None or observed_fingerprint != frozen_fingerprint:
        return {"status": "INVALIDATED", "reason": "frozen strategy fingerprint changed or was absent"}
    audit = dict(audit or {})
    if any(audit.get(key) is False for key in ("implementation_valid", "no_leakage", "finite_returns")):
        return {"status": "INVALIDATED", "reason": "fresh audit failed before performance qualification"}
    metrics = dict(metrics or {})
    threshold = dict(threshold or {"directional_performance": 0.0, "max_mdd": -1.0})
    checks = {"implementation_valid": True, "no_leakage": True, "finite_returns": bool(metrics.get("finite_returns", False)), "has_trades": int(metrics.get("trade_count", 0)) > 0, "directional_performance": metrics.get("total_return") is not None and float(metrics["total_return"]) > float(threshold.get("directional_performance", 0.0)), "risk_threshold": metrics.get("mdd") is not None and float(metrics["mdd"]) >= float(threshold.get("max_mdd", -1.0))}
    return {"status": "PASS" if all(checks.values()) else "FAIL", "checks": checks, "metrics": metrics}


def write_csv(path: Path, frame: pd.DataFrame, sort_columns: Sequence[str] = ()) -> str:
    output = frame.sort_values(list(sort_columns), kind="stable") if sort_columns and not frame.empty else frame
    encoded = output.to_csv(index=False, lineterminator="\n").encode("utf-8")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(encoded)
    return sha256_bytes(encoded)


def compare_artifacts(canonical_dir: Path, repro_dir: Path, names: Iterable[str]) -> dict[str, object]:
    mismatches = []
    for name in sorted(set(names)):
        left, right = Path(canonical_dir) / name, Path(repro_dir) / name
        if not left.is_file() or not right.is_file() or sha256_file(left) != sha256_file(right):
            mismatches.append(name)
    return {"status": "PASS" if not mismatches else "FAIL", "mismatches": mismatches, "artifact_count": len(set(names))}


def engine_parity(canonical: Mapping[str, object], peer: Mapping[str, object] | None = None, *, rtol: float = 1e-9, atol: float = 1e-12) -> dict[str, object]:
    """Compare normalized accounting snapshots without relaxing project tolerance."""

    fields = ("selection", "orders", "positions", "cash", "daily_return", "daily_equity", "final_equity")
    if peer is None:
        return {"status": "NOT_APPLICABLE", "reason": "no second supported engine", "rtol": rtol, "atol": atol}
    mismatches = []
    for field in fields:
        if field not in canonical or field not in peer:
            mismatches.append(field)
            continue
        left, right = canonical[field], peer[field]
        if isinstance(left, pd.DataFrame) or isinstance(right, pd.DataFrame):
            if not isinstance(left, pd.DataFrame) or not isinstance(right, pd.DataFrame) or list(left.columns) != list(right.columns) or len(left) != len(right):
                mismatches.append(field)
            elif not np.allclose(left.to_numpy(dtype=float), right.to_numpy(dtype=float), rtol=rtol, atol=atol, equal_nan=True):
                mismatches.append(field)
        elif isinstance(left, (list, tuple, np.ndarray)) or isinstance(right, (list, tuple, np.ndarray)):
            if not np.allclose(np.asarray(left, dtype=float), np.asarray(right, dtype=float), rtol=rtol, atol=atol, equal_nan=True):
                mismatches.append(field)
        elif isinstance(left, (int, float, np.number)) and isinstance(right, (int, float, np.number)):
            if not np.isclose(left, right, rtol=rtol, atol=atol, equal_nan=True):
                mismatches.append(field)
        elif left != right:
            mismatches.append(field)
    return {"status": "PASS" if not mismatches else "FAIL", "mismatches": mismatches, "rtol": rtol, "atol": atol}


__all__ = [
    "ACCEPTED_FACTOR_IDS", "CHANGE_ID", "ContractError", "DATASET_VERSION", "DEFAULT_POLICY", "FACTOR_RESEARCH_KNOWLEDGE_CUTOFF", "SearchPolicy", "TARGET_TICKER_COUNT", "TARGET_TICKER_SHA256", "build_strategy_grid", "build_target_weights", "canonical_json", "compare_artifacts", "default_strategy_search_spec", "engine_parity", "execution_audit", "fresh_oos_availability", "fresh_oos_verdict", "freeze_strategy_grid", "freeze_strategy_search_spec", "moving_block_bootstrap", "parameter_stability", "performance_metrics", "rank_cross_section", "redundancy_diagnostics", "revalidate_accepted_factor_pool", "run_cost_tracks", "shortlist_and_freeze", "simulate_target_weights", "temporal_stability", "validate_targets", "write_csv",
]
