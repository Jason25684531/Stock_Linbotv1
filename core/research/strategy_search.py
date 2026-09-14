"""Day 3 strategy search over the frozen accepted_factor_pool_v1 factors.

Implements the frozen decisions SD-1..SD-14 in
openspec/changes/2026-09-14-add-day3-strategy-search-and-realistic-backtest-v1/design.md.
Search grid, factor weighting, ranking, and gates are fixed before any
backtest result exists; nothing here may be tuned after seeing outcomes.
"""

from __future__ import annotations

import hashlib
import itertools
import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Mapping

import numpy as np
import pandas as pd

from config.settings import Config
from core.research import normalize
from core.research.composite_factor import build_composite_scores
from core.research.day2_candidates import (
    TRAIN_START,
    VALIDATION_END,
    candidate_specs,
    compute_candidate,
    panel_to_long,
)
from core.research.portfolio_research import safe_zscore
from core.research.portfolio_validation import COST_SCENARIOS
from core.research.sources import twse
from core.research.target_weights import build_rebalance_calendar, build_target_weights
from core.research.vectorbt_adapter import run_vectorbt


ACCEPTED_FACTOR_IDS: tuple[str, ...] = ("RS_004_NEAR_HIGH_120D", "A101_003", "A191_088", "A191_132")
IMPLEMENTATION_APPROXIMATE_FACTOR = "A191_132"
UNIVERSE_ID = "UNIV_RESEARCH_V1"
SOURCE_FACTOR_POOL_VERSION = "accepted_factor_pool_v1"
BACKTEST_START = TRAIN_START  # 2023-01-03 (SD-7 development+validation window)
BACKTEST_END = VALIDATION_END  # 2025-12-31
TOP_N_VALUES: tuple[int, ...] = (10, 20, 30)
REBALANCE_DAYS_VALUES: tuple[int, ...] = (20, 60)
PORTFOLIO_WEIGHTINGS: tuple[str, ...] = ("equal", "score_weighted")
FACTOR_WEIGHTING_METHODS: tuple[str, ...] = ("equal", "icir", "factor_score")
SLIPPAGE_BASE = COST_SCENARIOS["BASE"]  # 0.0
SLIPPAGE_SCREENING_STRESS = COST_SCENARIOS["STRESS"]  # 0.001 (10bps)
COST_TRACKS: tuple[str, ...] = ("gross", "net_base", "net_stress")
STRATEGY_VERSION = "day3_strategy_search_v1"
SHORTLIST_TARGET = 5
RANKING_WEIGHTS: dict[str, float] = {
    "net_base_annualized_return": 0.30,
    "net_base_sharpe": 0.25,
    "net_base_max_drawdown": 0.20,
    "positive_year_ratio": 0.15,
    "cost_drag": -0.10,
}
EXPECTED_COMPOSITE_COUNT = 25
EXPECTED_GRID_SIZE = 300


# ---------------------------------------------------------------------------
# SD-3: effective_strategy_direction (BLOCKING fix, never uses `direction`)
# ---------------------------------------------------------------------------

def resolve_effective_directions(pool: pd.DataFrame) -> pd.DataFrame:
    """Freeze RESEARCH_DIRECTION unless it is 0, then fall back to sign(pre-OOS mean_ic).

    Never reads the raw `direction` column and never learns a direction from
    any Day 3 backtest result.
    """

    pool = pool.loc[pool["candidate_id"].isin(ACCEPTED_FACTOR_IDS)].copy()
    if sorted(pool["candidate_id"]) != sorted(ACCEPTED_FACTOR_IDS):
        raise ValueError(f"DAY3_BLOCKED: accepted pool candidate_id set != {sorted(ACCEPTED_FACTOR_IDS)}")
    rows = []
    for row in pool.itertuples():
        research_direction = int(row.research_direction)
        mean_ic_sign = int(np.sign(row.mean_ic)) if pd.notna(row.mean_ic) else 0
        if research_direction in (-1, 1):
            effective, provenance = research_direction, "RESEARCH_DIRECTION"
        else:
            effective, provenance = mean_ic_sign, "SIGN_OF_PREOOS_MEAN_IC"
        if effective == 0 or effective != mean_ic_sign:
            raise ValueError(f"DAY3_BLOCKED: effective_strategy_direction unresolved for {row.candidate_id}")
        rows.append({
            "candidate_id": row.candidate_id,
            "source_research_direction": research_direction,
            "effective_strategy_direction": effective,
            "direction_provenance": provenance,
        })
    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# SD-2/SD-3: frozen search grid (25 composites x 3 TopN x 2 rebalance x 2 weighting = 300)
# ---------------------------------------------------------------------------

def factor_combinations() -> list[tuple[str, ...]]:
    """4 singles + C(4,2)=6 pairs + 1 full-4 = 11 factor sets."""

    ids = ACCEPTED_FACTOR_IDS
    singles = [(factor_id,) for factor_id in ids]
    pairs = list(itertools.combinations(ids, 2))
    full = [ids]
    return singles + pairs + full


def _composite_id(factor_set: tuple[str, ...], method: str) -> str:
    return f"{'+'.join(factor_set)}__{method.upper()}"


def _factor_weights(pool: pd.DataFrame, factor_set: tuple[str, ...], method: str) -> dict[str, float]:
    if method == "equal":
        weight = 1.0 / len(factor_set)
        return {factor_id: weight for factor_id in factor_set}
    column = {"icir": "icir", "factor_score": "factor_score"}[method]
    subset = pool.loc[pool["candidate_id"].isin(factor_set)].set_index("candidate_id")
    quality = subset.loc[list(factor_set), column].astype(float).abs()
    if not (quality.sum() > 0):
        raise ValueError(f"DAY3_BLOCKED: non-positive {column} sum for factor_set={factor_set}")
    normalized = quality / quality.sum()
    return {factor_id: float(normalized[factor_id]) for factor_id in factor_set}


def composite_definitions(pool: pd.DataFrame) -> list[dict[str, object]]:
    """25 composite weight definitions: single-factor sets only use EQUAL (degenerate)."""

    definitions = []
    for factor_set in factor_combinations():
        methods = ("equal",) if len(factor_set) == 1 else FACTOR_WEIGHTING_METHODS
        for method in methods:
            definitions.append({
                "composite_id": _composite_id(factor_set, method),
                "factor_set": factor_set,
                "factor_weighting": method,
                "factor_weights": _factor_weights(pool, factor_set, method),
            })
    if len(definitions) != EXPECTED_COMPOSITE_COUNT:
        raise ValueError(f"DAY3_BLOCKED: composite definition count={len(definitions)}, expected {EXPECTED_COMPOSITE_COUNT}")
    return definitions


def search_grid(pool: pd.DataFrame) -> tuple[pd.DataFrame, dict[str, dict[str, float]]]:
    """Freeze the 300-row grid and its config_hash before any backtest runs."""

    composites = composite_definitions(pool)
    weight_lookup = {item["composite_id"]: item["factor_weights"] for item in composites}
    rows = []
    for composite, top_n, rebalance_days, portfolio_weighting in itertools.product(
        composites, TOP_N_VALUES, REBALANCE_DAYS_VALUES, PORTFOLIO_WEIGHTINGS
    ):
        strategy_id = f"{composite['composite_id']}__TOP{top_n}__REB{rebalance_days}__{portfolio_weighting.upper()}"
        implementation_risk = "A191_132_APPROXIMATE" if IMPLEMENTATION_APPROXIMATE_FACTOR in composite["factor_set"] else "NONE"
        identity = {
            "factor_set": list(composite["factor_set"]),
            "factor_weighting": composite["factor_weighting"],
            "factor_weights": composite["factor_weights"],
            "top_n": top_n,
            "rebalance_days": rebalance_days,
            "portfolio_weighting": portfolio_weighting,
            "universe_id": UNIVERSE_ID,
            "fee_rate": Config.FEE_RATE,
            "tax_rate": Config.TAX_RATE,
            "slippage_base": SLIPPAGE_BASE,
            "slippage_screening_stress": SLIPPAGE_SCREENING_STRESS,
            "start_date": str(BACKTEST_START.date()),
            "end_date": str(BACKTEST_END.date()),
            "source_factor_pool_version": SOURCE_FACTOR_POOL_VERSION,
        }
        rows.append({
            "strategy_id": strategy_id,
            "composite_id": composite["composite_id"],
            "factor_set": "+".join(composite["factor_set"]),
            "factor_weighting": composite["factor_weighting"],
            "top_n": top_n,
            "rebalance_days": rebalance_days,
            "portfolio_weighting": portfolio_weighting,
            "implementation_risk": implementation_risk,
            "config_hash": hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest(),
        })
    grid = pd.DataFrame(rows)
    if len(grid) != EXPECTED_GRID_SIZE:
        raise ValueError(f"DAY3_BLOCKED: grid size={len(grid)}, expected {EXPECTED_GRID_SIZE}")
    if grid["strategy_id"].duplicated().any() or grid["config_hash"].duplicated().any():
        raise ValueError("DAY3_BLOCKED: duplicate strategy_id or config_hash in frozen grid")
    return grid.sort_values("strategy_id", kind="stable").reset_index(drop=True), weight_lookup


def grid_sha256(grid: pd.DataFrame) -> str:
    payload = grid.sort_values("strategy_id", kind="stable").to_csv(index=False)
    return hashlib.sha256(payload.encode()).hexdigest()


# ---------------------------------------------------------------------------
# StrategySpec (SD-12): every strategy result must rebuild from this
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class StrategySpec:
    strategy_id: str
    strategy_version: str
    config_hash: str
    factor_ids: tuple[str, ...]
    factor_weights: tuple[tuple[str, float], ...]
    source_research_direction: tuple[tuple[str, int], ...]
    effective_strategy_direction: tuple[tuple[str, int], ...]
    direction_provenance: tuple[tuple[str, str], ...]
    universe_id: str
    ranking_method: str
    normalization_method: str
    top_n: int
    rebalance_frequency: int
    portfolio_weighting: str
    max_position_weight: float | None
    max_sector_weight: str
    transaction_cost: tuple[float, float]
    slippage_base: float
    slippage_screening_stress: float
    execution_lag: str
    signal_date_rule: str
    execution_date_rule: str
    start_date: str
    end_date: str
    source_factor_pool_version: str
    implementation_risk: str

    def record(self) -> dict[str, object]:
        return asdict(self)


def build_strategy_spec(grid_row: Mapping[str, object], weights: Mapping[str, float], directions: pd.DataFrame) -> StrategySpec:
    directions = directions.set_index("candidate_id")
    factor_ids = tuple(sorted(weights))
    return StrategySpec(
        strategy_id=str(grid_row["strategy_id"]),
        strategy_version=STRATEGY_VERSION,
        config_hash=str(grid_row["config_hash"]),
        factor_ids=factor_ids,
        factor_weights=tuple(sorted((factor_id, weights[factor_id]) for factor_id in factor_ids)),
        source_research_direction=tuple((factor_id, int(directions.at[factor_id, "source_research_direction"])) for factor_id in factor_ids),
        effective_strategy_direction=tuple((factor_id, int(directions.at[factor_id, "effective_strategy_direction"])) for factor_id in factor_ids),
        direction_provenance=tuple((factor_id, str(directions.at[factor_id, "direction_provenance"])) for factor_id in factor_ids),
        universe_id=UNIVERSE_ID,
        ranking_method="composite_rank_weighted_sum",
        normalization_method="cross_sectional_percentile_rank",
        top_n=int(grid_row["top_n"]),
        rebalance_frequency=int(grid_row["rebalance_days"]),
        portfolio_weighting=str(grid_row["portfolio_weighting"]),
        max_position_weight=(1.0 / int(grid_row["top_n"])) if grid_row["portfolio_weighting"] == "equal" else None,
        max_sector_weight="NOT_APPLICABLE",
        transaction_cost=(Config.FEE_RATE, Config.TAX_RATE),
        slippage_base=SLIPPAGE_BASE,
        slippage_screening_stress=SLIPPAGE_SCREENING_STRESS,
        execution_lag="T+1",
        signal_date_rule="close of signal_date",
        execution_date_rule="next trading day, discrete execution dates only",
        start_date=str(BACKTEST_START.date()),
        end_date=str(BACKTEST_END.date()),
        source_factor_pool_version=SOURCE_FACTOR_POOL_VERSION,
        implementation_risk=str(grid_row["implementation_risk"]),
    )


# ---------------------------------------------------------------------------
# Remediation (2026-09-14 review BLOCKING-1): hard window enforcement.
# Nothing that touches Strategy Search may carry a date past BACKTEST_END —
# the Strict OOS window (2026-01-01..2026-07-28) starts the very next day.
# ---------------------------------------------------------------------------

def _assert_within_window(frame: pd.DataFrame, *, end: pd.Timestamp, date_columns: tuple[str, ...]) -> None:
    for column in date_columns:
        if (pd.to_datetime(frame[column]) > end).any():
            raise ValueError(f"DAY3_BLOCKED: STRATEGY_OOS_CONTAMINATION_DETECTED in column={column}")


def enforce_window(frame: pd.DataFrame, *, start: pd.Timestamp, end: pd.Timestamp, date_columns: tuple[str, ...]) -> pd.DataFrame:
    """Drop rows whose signal or execution date falls outside [start, end], then fail-fast if any survived."""

    mask = pd.Series(True, index=frame.index)
    for column in date_columns:
        values = pd.to_datetime(frame[column])
        mask &= (values >= start) & (values <= end)
    clipped = frame.loc[mask].copy()
    _assert_within_window(clipped, end=end, date_columns=date_columns)
    return clipped


# ---------------------------------------------------------------------------
# Data loading: raw OHLCV panels + canonical universe (member/tradable/T+1 price)
# ---------------------------------------------------------------------------

def load_market_frames(d3_root: Path, *, end: pd.Timestamp = BACKTEST_END) -> dict[str, pd.DataFrame]:
    """Rebuild raw OHLCV panels from the cached TWSE closing tables.

    ponytail: mirrors jobs/run_factor_validation.py's _load_raw_quotes /
    _quote_frames; duplicated instead of importing jobs/ from core/ (wrong
    dependency direction) or importing one jobs/ runner from another.
    """

    cache = Path(d3_root) / "_raw" / "twse_rwd"
    rows = []
    for path in sorted(cache.glob("MI_INDEX_*.json")):
        try:
            day = pd.Timestamp(path.stem.removeprefix("MI_INDEX_"))
        except ValueError:
            continue
        if day > end:
            continue
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            table = twse.find_closing_table(payload)
            frame = normalize.normalize_twse_closing_table(table, day, None)
            if not frame.empty:
                rows.append(frame.loc[:, ["trade_date", "stock_id", "raw_open", "raw_high", "raw_low", "raw_close", "volume", "amount"]])
        except (OSError, ValueError, twse.SchemaDriftError):
            continue
    if not rows:
        raise ValueError(f"DAY3_BLOCKED: no cached TWSE quotes found under {cache}")
    quotes = pd.concat(rows, ignore_index=True)
    quotes["trade_date"] = pd.to_datetime(quotes["trade_date"])
    quotes["stock_id"] = quotes["stock_id"].astype(str)
    quotes = quotes.drop_duplicates(["trade_date", "stock_id"]).sort_values(["trade_date", "stock_id"])
    index = pd.Index(sorted(quotes["trade_date"].unique()), name="asof_date")
    columns = pd.Index(sorted(quotes["stock_id"].unique()), name="asset_id")
    sources = {"open": "raw_open", "high": "raw_high", "low": "raw_low", "close": "raw_close", "volume": "volume", "amount": "amount"}
    return {key: quotes.pivot(index="trade_date", columns="stock_id", values=source).reindex(index=index, columns=columns) for key, source in sources.items()}


def load_universe(d3_root: Path, *, start: pd.Timestamp = BACKTEST_START, end: pd.Timestamp = BACKTEST_END) -> pd.DataFrame:
    """Canonical per-(date,asset) universe: member / is_tradable_t1 / execution_date / entry_price.

    ponytail: reuses the momentum_20d research_dataset partition Day 2
    already treats as the shared label/universe source; every accepted
    factor is scored against this one universe definition.
    """

    directory = Path(d3_root) / "research_dataset" / "momentum_20d"
    columns = ["asof_date", "asset_id", "member", "is_tradable_t1", "execution_date", "entry_price"]
    paths = sorted(directory.glob("*.csv"))
    if not paths:
        raise ValueError(f"DAY3_BLOCKED: universe partition missing under {directory}")
    universe = pd.concat([pd.read_csv(path, usecols=columns) for path in paths], ignore_index=True)
    universe["asof_date"] = pd.to_datetime(universe["asof_date"])
    universe["execution_date"] = pd.to_datetime(universe["execution_date"])
    universe["asset_id"] = universe["asset_id"].astype(str)
    universe["member"] = universe["member"].fillna(False).astype(bool)
    universe["is_tradable_t1"] = universe["is_tradable_t1"].fillna(False).astype(bool)
    universe = enforce_window(universe, start=start, end=end, date_columns=("asof_date", "execution_date"))
    if universe.empty:
        raise ValueError(f"DAY3_BLOCKED: universe partition empty for window {start.date()}..{end.date()}")
    return universe


def compute_factor_panels(quote_frames: Mapping[str, pd.DataFrame], master_path: Path) -> dict[str, pd.DataFrame]:
    specs = {spec.candidate_id: spec for spec in candidate_specs(master_path) if spec.candidate_id in ACCEPTED_FACTOR_IDS}
    missing = set(ACCEPTED_FACTOR_IDS) - set(specs)
    if missing:
        raise ValueError(f"DAY3_BLOCKED: accepted factor spec(s) missing from Day 2 candidate universe: {sorted(missing)}")
    return {factor_id: compute_candidate(specs[factor_id], quote_frames) for factor_id in ACCEPTED_FACTOR_IDS}


# ---------------------------------------------------------------------------
# SD-4: direction-adjusted cross-sectional percentile rank -> composite score
# ---------------------------------------------------------------------------

def build_ranks_long(panels: Mapping[str, pd.DataFrame], directions: pd.DataFrame) -> pd.DataFrame:
    effective = directions.set_index("candidate_id")["effective_strategy_direction"]
    frames = [panel_to_long(panels[factor_id], factor_id=factor_id, direction=int(effective.at[factor_id])) for factor_id in ACCEPTED_FACTOR_IDS]
    return pd.concat(frames, ignore_index=True)


def build_all_composite_scores(ranks_long: pd.DataFrame, weight_lookup: Mapping[str, Mapping[str, float]]) -> dict[str, pd.DataFrame]:
    scores = {}
    for composite_id, weights in weight_lookup.items():
        weights_df = pd.DataFrame({"factor_id": list(weights), "final_factor_weight": list(weights.values())})
        scores[composite_id] = build_composite_scores(ranks_long, weights_df, composite_id, source_handoff_id=SOURCE_FACTOR_POOL_VERSION)
    return scores


def build_all_target_weights(grid: pd.DataFrame, scores: Mapping[str, pd.DataFrame], universe: pd.DataFrame) -> dict[str, pd.DataFrame]:
    """Anchor the rebalance calendar to the declared backtest window.

    Factor panels are warmed up from well before BACKTEST_START so lookback
    windows (e.g. near_high_120d) are already valid on day one; composite
    scores therefore exist before the window too. The 20D/60D cadence must
    still be counted from the backtest window itself, not from incidental
    warmup availability, so the calendar basis is restricted to universe
    dates before slicing every Nth day.
    """

    window_dates = pd.DatetimeIndex(universe["asof_date"].unique())
    targets = {}
    for row in grid.itertuples():
        method_scores = scores[row.composite_id].copy()
        method_scores["asof_date"] = pd.to_datetime(method_scores["asof_date"])
        method_scores = method_scores.loc[method_scores["asof_date"].isin(window_dates)]
        selected_dates = build_rebalance_calendar(method_scores["asof_date"], row.rebalance_days)
        subset = method_scores.loc[method_scores["asof_date"].isin(selected_dates)]
        targets[row.strategy_id] = build_target_weights(
            subset, universe, config_id=row.strategy_id, top_n=row.top_n, stock_weighting=row.portfolio_weighting,
            combination_method=row.composite_id, rebalance_days=row.rebalance_days, source_handoff_id=SOURCE_FACTOR_POOL_VERSION,
        )
    return targets


def build_close_matrix(universe: pd.DataFrame) -> pd.DataFrame:
    """T+1 execution-date entry-price matrix vectorbt fills orders against.

    Remediation (MINOR-1): no ffill. This is the execution price matrix, not
    the research/signal matrix — a missing execution-date price must mean
    "this instruction does not execute", never a stale forward-filled price.
    vectorbt's own from_orders skips both the fill and that bar's valuation
    update on NaN close, which is exactly the desired no-fill semantics.
    """

    return universe.pivot(index="execution_date", columns="asset_id", values="entry_price").sort_index()


# ---------------------------------------------------------------------------
# SD-5/SD-6: realistic three-track backtest + execution gate
# ---------------------------------------------------------------------------

def run_backtest_tracks(close: pd.DataFrame, target: pd.DataFrame, *, initial_capital: float = 1_000_000.0) -> dict[str, dict[str, object]]:
    tracks = {
        "gross": {"fee_rate": 0.0, "tax_rate": 0.0, "slippage": 0.0},
        "net_base": {"fee_rate": Config.FEE_RATE, "tax_rate": Config.TAX_RATE, "slippage": SLIPPAGE_BASE},
        "net_stress": {"fee_rate": Config.FEE_RATE, "tax_rate": Config.TAX_RATE, "slippage": SLIPPAGE_SCREENING_STRESS},
    }
    return {track: run_vectorbt(close, target, initial_capital=initial_capital, sparse_rebalance=True, **params) for track, params in tracks.items()}


def execution_gate(result: Mapping[str, object], target: pd.DataFrame) -> dict[str, object]:
    scheduled = pd.DatetimeIndex(result["scheduled_instruction_dates"])
    instructions = result["instruction_matrix"]
    instruction_dates = pd.DatetimeIndex(instructions.dropna(how="all").index)
    actual = pd.DatetimeIndex(result["actual_order_dates"])
    violations = []
    if not instruction_dates.equals(scheduled):
        violations.append("instruction_dates_mismatch")
    if instructions.loc[~instructions.index.isin(scheduled)].notna().any().any():
        violations.append("instruction_outside_execution_dates")
    if not actual.isin(scheduled).all() or int(result["orders_on_non_rebalance_dates"]):
        violations.append("orders_on_non_rebalance_dates")
    if not (pd.to_datetime(target["execution_date"]) > pd.to_datetime(target["asof_date"])).all():
        violations.append("execution_date_not_after_signal_date")
    return {
        "rebalance_count": len(scheduled),
        "scheduled_execution_date_count": len(scheduled),
        "actual_order_date_count": len(actual),
        "non_rebalance_orders": int(result["orders_on_non_rebalance_dates"]),
        "execution_gate_pass": not violations,
        "execution_gate_violations": ";".join(violations),
    }


def summarize_track(result: Mapping[str, object]) -> dict[str, object]:
    """Finite-safe return/risk/trading metrics for one cost track."""

    returns = pd.Series(result["returns"], dtype=float).replace([np.inf, -np.inf], np.nan).dropna()
    value = pd.Series(result["value"], dtype=float).replace([np.inf, -np.inf], np.nan).dropna()
    volatility = returns.std(ddof=0) * np.sqrt(252) if len(returns) else np.nan
    annualized = (1 + returns).prod() ** (252 / len(returns)) - 1 if len(returns) else np.nan
    total = value.iloc[-1] / value.iloc[0] - 1 if len(value) > 1 and value.iloc[0] else np.nan
    drawdown = (value / value.cummax() - 1).min() if len(value) else np.nan
    downside = returns.loc[returns < 0].std(ddof=0) * np.sqrt(252)
    orders = result["orders"]
    initial = value.iloc[0] if len(value) else np.nan
    turnover = (orders["Size"].abs() * orders["Price"]).sum() / initial if initial else np.nan
    return {
        "observations": len(value),
        "total_return": total,
        "annualized_return": annualized,
        "annualized_volatility": volatility,
        "sharpe": annualized / volatility if volatility else np.nan,
        "sortino": annualized / downside if downside else np.nan,
        "max_drawdown": drawdown,
        "calmar": annualized / abs(drawdown) if drawdown else np.nan,
        "turnover": turnover,
        "estimated_cost": float(orders["Fees"].sum()) if len(orders) else 0.0,
        "trade_count": len(orders),
        "ending_value": value.iloc[-1] if len(value) else np.nan,
    }


# ---------------------------------------------------------------------------
# SD-8: calendar-year temporal stability (development validation, not OOS)
# ---------------------------------------------------------------------------

def yearly_stability(daily_returns: pd.Series) -> pd.DataFrame:
    returns = pd.Series(daily_returns, dtype=float).replace([np.inf, -np.inf], np.nan).dropna().sort_index()
    rows = []
    for year, group in returns.groupby(returns.index.year):
        std = group.std(ddof=0)
        total_return = float((1 + group).prod() - 1) if len(group) else float("nan")
        sharpe = float(group.mean() / std * np.sqrt(252)) if len(group) > 1 and std else float("nan")
        wealth = (1 + group).cumprod()
        mdd = float((wealth / wealth.cummax() - 1).min()) if len(group) else float("nan")
        rows.append({"year": int(year), "yearly_return": total_return, "yearly_sharpe": sharpe, "yearly_mdd": mdd})
    return pd.DataFrame(rows)


def temporal_summary(stability: pd.DataFrame) -> dict[str, object]:
    if stability.empty:
        return {"positive_year_ratio": np.nan, "excluding_best_year_return": np.nan, "single_year_dependent": True}
    positive_ratio = float((stability["yearly_return"] > 0).mean())
    if len(stability) > 1:
        remaining = stability.loc[stability["yearly_return"] != stability["yearly_return"].max()]
        combined = float(np.prod(1 + remaining["yearly_return"]) - 1) if not remaining.empty else float("nan")
    else:
        combined = float("nan")
    return {
        "positive_year_ratio": positive_ratio,
        "excluding_best_year_return": combined,
        "single_year_dependent": bool(pd.notna(combined) and combined <= -0.10),
    }


# ---------------------------------------------------------------------------
# SD-9: Validity -> Execution -> Return/Risk -> Cost -> Stability gates
# ---------------------------------------------------------------------------

def apply_gates(scoreboard: pd.DataFrame) -> pd.DataFrame:
    result = scoreboard.copy()
    result["gate_valid_pass"] = (
        (result["net_base_observations"] >= 500)
        & (result["rebalance_count"] >= 10)
        & (result["net_base_trade_count"] >= 30)
        & np.isfinite(pd.to_numeric(result["net_base_sharpe"], errors="coerce"))
        & np.isfinite(pd.to_numeric(result["net_base_sortino"], errors="coerce"))
        & np.isfinite(pd.to_numeric(result["net_base_calmar"], errors="coerce"))
        & np.isfinite(pd.to_numeric(result["net_base_turnover"], errors="coerce"))
    )
    result["gate_execution_pass"] = result["execution_gate_pass"].astype(bool)
    result["gate_return_risk_pass"] = (
        (result["net_base_annualized_return"] > 0)
        & (result["net_base_max_drawdown"] > -0.60)
        & np.isfinite(pd.to_numeric(result["net_base_sharpe"], errors="coerce"))
    )
    cost_efficient = (result["net_base_annualized_return"] >= 0.5 * result["gross_annualized_return"]) | (result["cost_drag"] <= 0.05)
    stress_positive = result["net_stress_total_return"] > 0
    result["gate_cost_pass"] = cost_efficient & stress_positive
    result["gate_stability_pass"] = (result["positive_year_ratio"] >= 0.5) & (result["excluding_best_year_return"] > -0.10)
    gates_in_order = ("gate_valid_pass", "gate_execution_pass", "gate_return_risk_pass", "gate_cost_pass", "gate_stability_pass")
    result["strategy_valid"] = result[list(gates_in_order)].all(axis=1)
    reason_labels = {
        "gate_valid_pass": "VALIDITY_GATE_FAILED",
        "gate_execution_pass": "REALISTIC_EXECUTION_GATE_FAILED",
        "gate_return_risk_pass": "RETURN_RISK_GATE_FAILED",
        "gate_cost_pass": "COST_GATE_FAILED",
        "gate_stability_pass": "STABILITY_GATE_FAILED",
    }
    result["rejection_reason"] = ""
    for gate in gates_in_order:
        needs_reason = ~result[gate] & result["rejection_reason"].eq("")
        result.loc[needs_reason, "rejection_reason"] = reason_labels[gate]
    return result


# ---------------------------------------------------------------------------
# SD-10/SD-11: composite ranking + deterministic shortlist size
# ---------------------------------------------------------------------------

def rank_strategies(valid: pd.DataFrame) -> pd.DataFrame:
    result = valid.copy()
    for column, weight in RANKING_WEIGHTS.items():
        result[f"z_{column}"] = safe_zscore(result[column]) * (1.0 if weight > 0 else -1.0)
    result["strategy_ranking_score"] = sum(result[f"z_{column}"] * abs(weight) for column, weight in RANKING_WEIGHTS.items())
    return result.sort_values(["strategy_ranking_score", "strategy_id"], ascending=[False, True], kind="stable").reset_index(drop=True)


def select_shortlist(ranked: pd.DataFrame) -> pd.DataFrame:
    eligible = len(ranked)
    if eligible < 3:
        raise ValueError(f"DAY3_STATUS=PARTIAL: only {eligible} strategies passed all gates (< 3 minimum)")
    count = SHORTLIST_TARGET if eligible >= SHORTLIST_TARGET else eligible
    shortlist = ranked.head(count).copy()
    shortlist.insert(0, "shortlist_rank", range(1, len(shortlist) + 1))
    shortlist["shortlist_reason"] = (
        "rank " + shortlist["shortlist_rank"].astype(str) + " of " + str(eligible)
        + " gate-eligible strategies by frozen composite ranking score"
    )
    return shortlist


# ---------------------------------------------------------------------------
# SD-1: Day 2 frozen-input preflight (fail-fast, never recompute a substitute)
# ---------------------------------------------------------------------------

def sha256_of(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def day2_input_hashes(day2_dir: Path) -> dict[str, str]:
    day2_dir = Path(day2_dir)
    return {path.name: sha256_of(path) for path in sorted(day2_dir.glob("*")) if path.is_file()}


def preflight_accepted_pool(day2_dir: Path) -> dict[str, object]:
    """SD-1: exactly 4 factors, IDs match, directions resolvable, hashes recorded."""

    day2_dir = Path(day2_dir)
    pool_path = day2_dir / "accepted_factor_pool.csv"
    manifest_path = day2_dir / "run_manifest.json"
    if not pool_path.is_file():
        raise ValueError(f"DAY3_BLOCKED: Day 2 accepted_factor_pool.csv missing at {pool_path}")
    pool = pd.read_csv(pool_path)
    if len(pool) != 4:
        raise ValueError(f"DAY3_BLOCKED: accepted pool row count={len(pool)}, expected 4")
    if sorted(pool["candidate_id"]) != sorted(ACCEPTED_FACTOR_IDS):
        raise ValueError(f"DAY3_BLOCKED: accepted pool candidate_id set != {sorted(ACCEPTED_FACTOR_IDS)}")
    directions = resolve_effective_directions(pool)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path.is_file() else {}
    return {
        "factor_pool_version": SOURCE_FACTOR_POOL_VERSION,
        "factor_pool_size": len(pool),
        "day2_status": manifest.get("day2_status"),
        "tier2_status": manifest.get("tier2_status"),
        "day2_input_hashes": day2_input_hashes(day2_dir),
        "effective_directions": directions.to_dict("records"),
        "pool": pool.loc[:, ["candidate_id", "direction", "research_direction", "mean_ic", "icir", "factor_score"]].to_dict("records"),
    }


def frozen_parameters() -> dict[str, object]:
    """Echo the SD-2..SD-7 constants a preflight/manifest must show unchanged."""

    return {
        "backtest_window": {"start": str(BACKTEST_START.date()), "end": str(BACKTEST_END.date())},
        "universe_id": UNIVERSE_ID,
        "top_n_values": list(TOP_N_VALUES),
        "rebalance_days_values": list(REBALANCE_DAYS_VALUES),
        "portfolio_weightings": list(PORTFOLIO_WEIGHTINGS),
        "factor_weighting_methods": list(FACTOR_WEIGHTING_METHODS),
        "expected_composite_count": EXPECTED_COMPOSITE_COUNT,
        "expected_grid_size": EXPECTED_GRID_SIZE,
        "cost_model": {"fee_rate": Config.FEE_RATE, "tax_rate": Config.TAX_RATE, "vectorbt_fee_approximation": "fee + tax / 2"},
        "slippage_base": SLIPPAGE_BASE,
        "slippage_screening_stress": SLIPPAGE_SCREENING_STRESS,
        "execution_semantics_version": "DISCRETE_EXECUTION_DATE_ONLY",
        "execution_lag": "T+1",
        "shortlist_target": SHORTLIST_TARGET,
        "ranking_weights": RANKING_WEIGHTS,
        "strategy_oos_status": "NOT_ESTABLISHED",
        "tier2_factor_blocker": "NOT_ESTABLISHED_DB_UNAVAILABLE",
        "factor_pool_v1_incomplete_families": ["Growth", "Quality", "InstitutionalFlow"],
    }
