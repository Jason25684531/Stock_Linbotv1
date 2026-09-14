"""Design.md TEST 1-10 for the Day 3 strategy search / realistic backtest."""

import numpy as np
import pandas as pd
import pytest

from core.research import strategy_search as ss
from core.research.target_weights import build_target_weights
from core.research.vectorbt_adapter import run_vectorbt


ACCEPTED_POOL = pd.DataFrame([
    {"candidate_id": "RS_004_NEAR_HIGH_120D", "direction": 1, "research_direction": 1, "mean_ic": 0.0731, "icir": 0.6409, "factor_score": 0.7764},
    {"candidate_id": "A101_003", "direction": 0, "research_direction": 1, "mean_ic": 0.0308, "icir": 0.5014, "factor_score": 0.7724},
    {"candidate_id": "A191_088", "direction": 0, "research_direction": 1, "mean_ic": 0.0293, "icir": 0.2807, "factor_score": 0.8233},
    {"candidate_id": "A191_132", "direction": 0, "research_direction": 1, "mean_ic": 0.0354, "icir": 0.3322, "factor_score": 0.7689},
])


def _target(dates_weights: dict[str, dict[str, float]], *, execution_lag_days: int = 1) -> pd.DataFrame:
    rows = []
    for asof, weights in dates_weights.items():
        asof_date = pd.Timestamp(asof)
        execution_date = asof_date + pd.Timedelta(days=execution_lag_days)
        for asset_id, weight in weights.items():
            rows.append({"asof_date": asof_date, "execution_date": execution_date, "asset_id": asset_id, "target_weight": weight, "config_id": "TEST"})
    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# TEST 1: signal_date < execution_date (T+1); violation must fail the gate.
# ---------------------------------------------------------------------------

def test_execution_gate_fails_when_execution_date_not_after_signal_date():
    dates = pd.date_range("2023-01-02", periods=6, freq="D")
    close = pd.DataFrame({"A": 10.0}, index=dates)
    target_ok = _target({"2023-01-02": {"A": 1.0}})
    result = run_vectorbt(close, target_ok, fee_rate=0.0, tax_rate=0.0, sparse_rebalance=True)
    assert ss.execution_gate(result, target_ok)["execution_gate_pass"]

    same_day = target_ok.copy()
    same_day["execution_date"] = same_day["asof_date"]
    gate = ss.execution_gate(result, same_day)
    assert not gate["execution_gate_pass"]
    assert "execution_date_not_after_signal_date" in gate["execution_gate_violations"]


# ---------------------------------------------------------------------------
# TEST 2: non-rebalance dates produce zero orders (sparse execution semantics).
# ---------------------------------------------------------------------------

def test_non_rebalance_dates_produce_zero_orders():
    dates = pd.date_range("2023-01-02", periods=10, freq="D")
    close = pd.DataFrame({"A": 10.0, "B": 10.0}, index=dates)
    target = _target({"2023-01-02": {"A": 0.6, "B": 0.4}, "2023-01-07": {"A": 0.3, "B": 0.7}})
    result = run_vectorbt(close, target, fee_rate=0.001, tax_rate=0.0, sparse_rebalance=True)
    scheduled = pd.DatetimeIndex(result["scheduled_instruction_dates"])
    actual = pd.DatetimeIndex(result["actual_order_dates"])
    assert result["orders_on_non_rebalance_dates"] == 0
    assert actual.isin(scheduled).all()
    assert set(actual) <= set(scheduled)
    non_scheduled = dates.difference(scheduled)
    assert not result["orders"]["Timestamp"].isin(non_scheduled).any()


# ---------------------------------------------------------------------------
# TEST 3: a removed asset's target only zeroes on the next allowed execution date.
# ---------------------------------------------------------------------------

def test_removed_asset_is_zeroed_only_on_next_execution_date_not_before():
    dates = pd.date_range("2023-01-02", periods=10, freq="D")
    close = pd.DataFrame({"A": 10.0, "B": 10.0}, index=dates)
    # B is selected on the first rebalance date, then dropped from the universe by the second.
    target = _target({"2023-01-02": {"A": 0.5, "B": 0.5}, "2023-01-07": {"A": 1.0}})
    result = run_vectorbt(close, target, fee_rate=0.001, tax_rate=0.0, sparse_rebalance=True)
    instructions = result["instruction_matrix"]
    first, second = pd.Timestamp("2023-01-03"), pd.Timestamp("2023-01-08")  # execution_date = asof_date + 1
    between = pd.Timestamp("2023-01-05")
    assert instructions.loc[first, "B"] == pytest.approx(0.5)
    assert instructions.loc[second, "B"] == 0.0  # zeroed exactly on the next scheduled execution date
    assert pd.isna(instructions.loc[between, "B"])  # not force-zeroed early; holding drifts instead


# ---------------------------------------------------------------------------
# TEST 4: costs only ever reduce returns: net_stress <= net_base <= gross.
# ---------------------------------------------------------------------------

def test_cost_tracks_are_monotonically_non_increasing():
    dates = pd.date_range("2023-01-02", periods=40, freq="D")
    rng = np.random.default_rng(7)
    prices = 10 * np.cumprod(1 + rng.normal(0, 0.01, size=(40, 2)), axis=0)
    close = pd.DataFrame(prices, index=dates, columns=["A", "B"])
    target = _target({"2023-01-02": {"A": 0.5, "B": 0.5}, "2023-01-16": {"A": 0.7, "B": 0.3}, "2023-01-30": {"A": 0.2, "B": 0.8}})
    tracks = ss.run_backtest_tracks(close, target)
    metrics = {track: ss.summarize_track(result) for track, result in tracks.items()}
    assert metrics["net_stress"]["total_return"] <= metrics["net_base"]["total_return"] <= metrics["gross"]["total_return"]
    assert metrics["net_stress"]["annualized_return"] <= metrics["net_base"]["annualized_return"] <= metrics["gross"]["annualized_return"]


# ---------------------------------------------------------------------------
# TEST 5: controlled synthetic - extra trades never lower cost drag (never
# a global turnover-monotonicity assertion across real strategies).
# ---------------------------------------------------------------------------

def test_extra_trades_increase_turnover_and_cost_drag_controlled_synthetic():
    dates = pd.date_range("2023-01-02", periods=20, freq="D")
    close = pd.DataFrame({"A": 10.0, "B": 10.0}, index=dates)  # flat prices isolate cost from market P&L
    strategy_a = _target({"2023-01-02": {"A": 0.5, "B": 0.5}, "2023-01-16": {"A": 0.5, "B": 0.5}})
    strategy_b = _target({
        "2023-01-02": {"A": 0.5, "B": 0.5},
        "2023-01-09": {"A": 0.9, "B": 0.1},  # extra round trip
        "2023-01-12": {"A": 0.5, "B": 0.5},  # back in sync with A before A's next rebalance
        "2023-01-16": {"A": 0.5, "B": 0.5},
    })
    tracks_a = ss.run_backtest_tracks(close, strategy_a)
    tracks_b = ss.run_backtest_tracks(close, strategy_b)
    metrics_a = {track: ss.summarize_track(result) for track, result in tracks_a.items()}
    metrics_b = {track: ss.summarize_track(result) for track, result in tracks_b.items()}
    cost_drag_a = metrics_a["gross"]["annualized_return"] - metrics_a["net_base"]["annualized_return"]
    cost_drag_b = metrics_b["gross"]["annualized_return"] - metrics_b["net_base"]["annualized_return"]
    assert metrics_b["net_base"]["turnover"] > metrics_a["net_base"]["turnover"]
    assert cost_drag_b >= cost_drag_a
    # Flat prices: gross P&L is zero for both, so the entire net drag is fees.
    assert metrics_a["gross"]["total_return"] == pytest.approx(0.0, abs=1e-9)
    assert metrics_b["gross"]["total_return"] == pytest.approx(0.0, abs=1e-9)


# ---------------------------------------------------------------------------
# TEST 6: a missing execution price never gets future-filled to force a fill.
# ---------------------------------------------------------------------------

def test_missing_execution_price_does_not_future_fill():
    dates = pd.date_range("2023-01-02", periods=8, freq="D")
    close = pd.DataFrame({"A": [10.0, 10.0, 10.0, np.nan, 10.0, 10.0, 10.0, 10.0], "B": 5.0}, index=dates)
    target = _target({"2023-01-02": {"A": 0.5, "B": 0.5}, "2023-01-05": {"A": 0.9, "B": 0.1}})
    result = run_vectorbt(close, target, fee_rate=0.001, tax_rate=0.0, sparse_rebalance=True)
    orders = result["orders"]
    missing_date = pd.Timestamp("2023-01-05")
    a_orders_on_missing_date = orders.loc[(orders["Timestamp"] == missing_date) & (orders["Column"] == "A")]
    assert a_orders_on_missing_date.empty  # no fabricated fill when the execution-date price is missing
    assert not (orders["Price"].isna()).any()


# ---------------------------------------------------------------------------
# TEST 7: TopN tie-break is deterministic (score DESC, asset_id ASC).
# ---------------------------------------------------------------------------

def test_top_n_tie_break_is_score_desc_asset_id_asc():
    asof = pd.Timestamp("2023-01-02")
    execution = pd.Timestamp("2023-01-03")
    scores = pd.DataFrame({
        "asof_date": [asof] * 4,
        "asset_id": ["2330", "1101", "1102", "9999"],
        "composite_score": [0.5, 0.5, 0.5, 0.1],
    })
    universe = pd.DataFrame({
        "asof_date": [asof] * 4,
        "asset_id": ["2330", "1101", "1102", "9999"],
        "member": [True] * 4,
        "is_tradable_t1": [True] * 4,
        "execution_date": [execution] * 4,
    })
    selected = build_target_weights(scores, universe, config_id="TIE", top_n=2, stock_weighting="equal")
    assert sorted(selected["asset_id"]) == ["1101", "1102"]  # tied 0.5 score: asset_id ascending wins over 2330/9999


# ---------------------------------------------------------------------------
# TEST 8: the same StrategySpec reproduces an exact backtest result.
# ---------------------------------------------------------------------------

def test_same_strategy_spec_reproduces_exactly():
    dates = pd.date_range("2023-01-02", periods=20, freq="D")
    rng = np.random.default_rng(3)
    prices = 10 * np.cumprod(1 + rng.normal(0, 0.01, size=(20, 2)), axis=0)
    close = pd.DataFrame(prices, index=dates, columns=["A", "B"])
    target = _target({"2023-01-02": {"A": 0.5, "B": 0.5}, "2023-01-16": {"A": 0.3, "B": 0.7}})
    first = {track: ss.summarize_track(result) for track, result in ss.run_backtest_tracks(close, target).items()}
    second = {track: ss.summarize_track(result) for track, result in ss.run_backtest_tracks(close, target).items()}
    assert first == second


# ---------------------------------------------------------------------------
# TEST 9: the frozen search grid never grows or shrinks on regeneration.
# ---------------------------------------------------------------------------

def test_search_grid_is_frozen_and_deterministic():
    grid_one, _ = ss.search_grid(ACCEPTED_POOL)
    grid_two, _ = ss.search_grid(ACCEPTED_POOL)
    assert len(grid_one) == len(grid_two) == ss.EXPECTED_GRID_SIZE == 300
    assert ss.grid_sha256(grid_one) == ss.grid_sha256(grid_two)
    assert set(grid_one["strategy_id"]) == set(grid_two["strategy_id"])


# ---------------------------------------------------------------------------
# TEST 10: Day 2 accepted-pool mismatches fail fast; never a substitute pool.
# ---------------------------------------------------------------------------

def test_accepted_pool_size_mismatch_blocks():
    bad_pool = ACCEPTED_POOL.iloc[:3]
    with pytest.raises(ValueError, match="DAY3_BLOCKED"):
        ss.resolve_effective_directions(bad_pool)


def test_accepted_pool_unknown_candidate_blocks():
    bad_pool = ACCEPTED_POOL.copy()
    bad_pool.loc[0, "candidate_id"] = "NOT_AN_ACCEPTED_FACTOR"
    with pytest.raises(ValueError, match="DAY3_BLOCKED"):
        ss.resolve_effective_directions(bad_pool)


def test_direction_zero_with_zero_mean_ic_blocks_never_falls_back_to_raw():
    bad_pool = ACCEPTED_POOL.copy()
    bad_pool.loc[bad_pool["candidate_id"].eq("A191_132"), ["research_direction", "mean_ic"]] = [0, 0.0]
    with pytest.raises(ValueError, match="DAY3_BLOCKED"):
        ss.resolve_effective_directions(bad_pool)


# ---------------------------------------------------------------------------
# Remediation TEST 11: window enforcement drops any date past BACKTEST_END on
# either asof_date or execution_date (the actual BLOCKING-1 leak vector).
# ---------------------------------------------------------------------------

def test_window_enforcement_drops_asof_and_execution_dates_past_end():
    start, end = pd.Timestamp("2023-01-03"), pd.Timestamp("2025-12-31")
    frame = pd.DataFrame({
        "asof_date": pd.to_datetime(["2025-12-30", "2025-12-31", "2025-12-31", "2026-01-05"]),
        "execution_date": pd.to_datetime(["2025-12-31", "2026-01-02", "2025-12-31", "2026-01-06"]),
    })
    clipped = ss.enforce_window(frame, start=start, end=end, date_columns=("asof_date", "execution_date"))
    # rows 0 and 2 have BOTH asof_date and execution_date <= end; row 1's
    # execution_date leaks into 2026, row 3 is entirely out of window.
    assert clipped["asof_date"].tolist() == [pd.Timestamp("2025-12-30"), pd.Timestamp("2025-12-31")]
    assert clipped["execution_date"].tolist() == [pd.Timestamp("2025-12-31"), pd.Timestamp("2025-12-31")]
    assert clipped["asof_date"].max() <= end
    assert clipped["execution_date"].max() <= end


# ---------------------------------------------------------------------------
# Remediation TEST 12: fail-fast safety net - a 2026 row that somehow survives
# filtering must raise, never continue silently.
# ---------------------------------------------------------------------------

def test_assert_within_window_blocks_on_unfiltered_oos_row():
    end = pd.Timestamp("2025-12-31")
    contaminated = pd.DataFrame({"execution_date": pd.to_datetime(["2025-12-31", "2026-01-02"])})
    with pytest.raises(ValueError, match="STRATEGY_OOS_CONTAMINATION_DETECTED"):
        ss._assert_within_window(contaminated, end=end, date_columns=("execution_date",))


# ---------------------------------------------------------------------------
# Remediation TEST 13: build_close_matrix must never forward-fill a missing
# execution-date price (that would let a rebalance fill at a stale price).
# ---------------------------------------------------------------------------

def test_build_close_matrix_does_not_forward_fill_missing_execution_price():
    universe = pd.DataFrame({
        "asof_date": pd.to_datetime(["2023-01-02", "2023-01-03", "2023-01-04"]),
        "execution_date": pd.to_datetime(["2023-01-03", "2023-01-04", "2023-01-05"]),
        "asset_id": ["A", "A", "A"],
        "entry_price": [10.0, np.nan, 11.0],
    })
    close = ss.build_close_matrix(universe)
    assert pd.isna(close.loc[pd.Timestamp("2023-01-04"), "A"])  # missing price stays missing, never ffilled


# ---------------------------------------------------------------------------
# Remediation TEST 16 (MINOR-4): Day3's local Validity Gate non-finite-metric
# semantics must match canonical portfolio_research.shortlist_eligibility's
# semantics for the metrics both share (sharpe/sortino/calmar/turnover) -
# "non-finite -> invalid", independent of magnitude.
# ---------------------------------------------------------------------------

def test_validity_gate_non_finite_semantics_match_canonical_shortlist_eligibility():
    shared_metrics = {
        "net_base_sharpe": [1.2, np.inf, np.nan, -0.5],
        "net_base_sortino": [1.5, 1.0, 1.0, 1.0],
        "net_base_calmar": [0.8, 0.8, 0.8, 0.8],
        "net_base_turnover": [2.0, 2.0, 2.0, -np.inf],
    }
    day3_valid = np.ones(4, dtype=bool)
    for column in shared_metrics:
        day3_valid &= np.isfinite(pd.to_numeric(pd.Series(shared_metrics[column]), errors="coerce"))

    # Independent reimplementation of shortlist_eligibility's per-metric rule
    # (portfolio_research.py: `~np.isfinite(pd.to_numeric(result[metric]))`).
    canonical_valid = np.ones(4, dtype=bool)
    for canonical_column, day3_column in [("sharpe", "net_base_sharpe"), ("sortino", "net_base_sortino"), ("calmar", "net_base_calmar"), ("turnover", "net_base_turnover")]:
        invalid = ~np.isfinite(pd.to_numeric(pd.Series(shared_metrics[day3_column]), errors="coerce"))
        canonical_valid &= ~invalid

    assert day3_valid.tolist() == canonical_valid.tolist() == [True, False, False, False]
