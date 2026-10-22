"""Synthetic checks for the recovered Daily feature core (P0-1..P0-4); never investment evidence."""
import numpy as np
import pandas as pd
import pytest

from core.calc_indicators import build_feature_history
from core.research.native_frame import NativeResearchData, prepare_fundamentals, rule_inputs
from test_native_recovery import DAYS, LAST, fundamentals, snapshot

COMPARED = ["ma20", "ma60", "rsi", "kd_k", "natr", "volume_ratio", "high_60", "chip_score", "foreign_consec_days"]


def at(features, day, sid=None):
    rows = features[features.trade_date == day]
    return rows.set_index("stock_id") if sid is None else rows.set_index("stock_id").loc[sid]


def chips(snap):
    rng = np.random.default_rng(3)
    return snap.assign(dealer_buy=rng.integers(-20, 20, len(snap)).astype(float),
                       margin_balance=1000.0 + rng.integers(-30, 30, len(snap)).cumsum())


# ---- P0-1 volume_ratio ----
def test_volume_ratio_follows_twenty_session_history_and_differs_by_stock():
    snap = snapshot()
    last = at(build_feature_history(snap), LAST)
    assert last.volume_ratio.nunique() == 2 and not (last.volume_ratio == 1.0).any()
    vol = snap[snap.stock_id == "1101"].volume.tail(20)
    assert last.loc["1101", "volume_ratio"] == pytest.approx(min(vol.iloc[-1] / vol.mean(), 5))  # training formula, clip(0, 5)


def test_volume_ratio_needs_twenty_observations():
    features = build_feature_history(snapshot())
    assert at(features, DAYS[18]).volume_ratio.isna().all()  # 19 sessions: NaN, never 1.0 (OD-1)
    assert at(features, DAYS[19]).volume_ratio.notna().all()


# ---- P0-3 high_60 ----
def test_high_60_is_the_sixty_session_high_not_todays_high():
    snap = snapshot()
    snap.loc[(snap.stock_id == "1101") & (snap.trade_date == DAYS[-30]), "high_price"] = 999.0
    features = build_feature_history(snap)
    assert at(features, LAST, "1101").high_60 == 999.0
    assert at(features, LAST, "1102").high_60 > at(features, LAST, "1102").high_price  # an earlier high, not today's
    assert at(features, DAYS[58]).high_60.isna().all() and at(features, DAYS[59]).high_60.notna().all()  # 60 sessions (OD-2)


def test_v34_uses_history_high_and_keeps_its_threshold(monkeypatch):
    from core.strategies.growth_momentum_breakout import GrowthMomentumBreakoutStrategy

    monkeypatch.setattr("config.Config.USE_MARKET_FILTER", False)
    monkeypatch.setattr("core.db_helper.get_setting", lambda key, default=None: default)
    row = {"stock_id": "1101", "stock_name": "T", "trade_date": "2026-06-01", "close_price": 100.0, "high_price": 101.0,
           "ma20": 90.0, "volume": 9e6, "volume_ratio": 2.0, "revenue_yoy": 50.0}
    one_day = pd.DataFrame([row])
    assert len(GrowthMomentumBreakoutStrategy().filter_candidates(one_day.copy())) == 1  # legacy: high_60 == today's high
    assert GrowthMomentumBreakoutStrategy().filter_candidates(one_day.assign(high_60=120.0)).empty  # 100 < 120 * 0.93
    assert len(GrowthMomentumBreakoutStrategy().filter_candidates(one_day.assign(high_60=107.0))) == 1  # 100 >= 107 * 0.93


# ---- P0-2 chip_score ----
def test_chip_score_is_nan_without_dealer_or_margin_and_varies_with_them():
    snap = snapshot()  # dealer_buy = 0, margin_balance = 0 in the fixture
    missing = at(build_feature_history(snap.assign(dealer_buy=np.nan)), LAST)
    assert missing.chip_score.isna().all()
    assert at(build_feature_history(snap.assign(margin_balance=np.nan)), LAST).chip_score.isna().all()
    full = build_feature_history(chips(snap))
    assert full[full.trade_date >= DAYS[30]].chip_score.notna().all() and full.chip_score.nunique() > 10


def test_institutional_stage_two_really_filters(monkeypatch):
    from core.strategies.institutional_flow_confirmation import InstitutionalFlowConfirmationStrategy as Strategy

    monkeypatch.setattr("config.Config.USE_MARKET_FILTER", False)
    base = {"stock_name": "T", "trade_date": "2026-10-15", "close_price": 110.0, "ma20": 105.0, "ma60": 100.0, "volume": 9e6,
            "volume_ratio": 1.2, "rsi": 60.0, "bias": 5.0, "foreign_ratio": 0.1, "trust_ratio": 0.0, "dealer_ratio": 0.0,
            "margin_change_pct": 0.0, "macd_hist": 0.1}
    rows = [{**base, "stock_id": "1101", "chip_score": 60.0, "foreign_consec_days": 3, "trust_consec_days": 0},  # passes stage 2
            {**base, "stock_id": "1102", "chip_score": 50.0, "foreign_consec_days": 5, "trust_consec_days": 0},  # chip_score < 55
            {**base, "stock_id": "1103", "chip_score": 70.0, "foreign_consec_days": 1, "trust_consec_days": 1}]  # no consecutive buying
    assert list(Strategy().filter_candidates(pd.DataFrame(rows)).stock_id) == ["1101"]
    # Existing relaxed rule: with stage 2 empty, stage-1 rows with chip_score >= 27.5 continue (rule unchanged, reported).
    relaxed = Strategy().filter_candidates(pd.DataFrame(rows[1:]))
    assert list(relaxed.stock_id) == ["1103", "1102"]


def test_institutional_strategy_sees_no_row_with_missing_chip_score():
    from core.strategies.institutional_flow_confirmation import InstitutionalFlowConfirmationStrategy as Strategy

    snap = chips(snapshot())
    snap.loc[(snap.stock_id == "1101") & (snap.trade_date == LAST), "dealer_buy"] = np.nan
    kept, note = NativeResearchData(snap, *fundamentals()).frame(LAST.strftime("%Y-%m-%d"), sorted(rule_inputs(Strategy())))
    assert list(kept.stock_id) == ["1102"]
    assert note["excluded_rows"] == [{"stock_id": "1101", "missing_columns": ["chip_score", "dealer_ratio"], "reason": "CHIP_SOURCE_MISSING"}]


# ---- shared core, point-in-time, P0-4 ----
def test_future_sessions_never_change_past_features():
    snap = chips(snapshot())
    cut = DAYS[90]
    past = at(build_feature_history(snap[snap.trade_date <= cut]), cut)[COMPARED]
    full = at(build_feature_history(snap), cut)[COMPARED]
    pd.testing.assert_frame_equal(past, full, rtol=1e-9, atol=1e-12)


def test_missing_indicators_are_recorded_with_reason(monkeypatch):
    data = NativeResearchData(snapshot(), *fundamentals())
    full = data.cross_section(LAST).copy()
    full.loc[full.stock_id == "1101", ["ma60", "kd_k"]] = np.nan
    monkeypatch.setattr(data, "cross_section", lambda date: full)
    kept, note = data.frame(LAST.strftime("%Y-%m-%d"), ["kd_k", "ma60"])
    assert list(kept.stock_id) == ["1102"]
    assert note["excluded_rows"] == [{"stock_id": "1101", "missing_columns": ["kd_k", "ma60"], "reason": "INSUFFICIENT_HISTORY_OR_MISSING_BAR"}]


def test_actual_publication_date_precedes_the_statutory_deadline():
    fin, rev = fundamentals()
    published = pd.DataFrame([{"stock_id": "1101", "year": 2026, "quarter": 1, "publication_date": pd.Timestamp("2026-04-28")}])
    table, _ = prepare_fundamentals(fin, rev, published)
    row = table[(table.stock_id == "1101") & (table.due == pd.Timestamp("2026-04-28"))]
    assert len(row) == 1 and row.availability_source.iloc[0] == "mops_publication"
    assert set(table[table.stock_id == "1102"].availability_source) == {"statutory_deadline"}
    data = NativeResearchData(snapshot(), fin, rev, publications=published)
    assert data.frame("2026-04-29", ["eps"])[0].set_index("stock_id").fundamental_availability_source.to_dict() == {
        "1101": "mops_publication", "1102": "statutory_deadline"}


# ---- recovered Daily: same core, guarded switch, strict scoring ----
def fake_db(monkeypatch, run_daily, snap):
    fin, rev = fundamentals()
    history = snap.drop(columns=["amount"]).assign(revenue_yoy=0.0, ma60=0.0)  # legacy write-backs in the DB table are ignored

    def read_sql(sql, engine, params=None):
        text = str(sql)
        if "financial_statements" in text:
            return fin.copy()
        if "monthly_revenue" in text:
            return rev.copy()
        return history[(history.trade_date >= pd.Timestamp(params["date"]) - pd.Timedelta(days=150))
                       & (history.trade_date <= pd.Timestamp(params["date"]))].copy()

    monkeypatch.setattr(run_daily.pd, "read_sql", read_sql)
    monkeypatch.setattr(run_daily.Config, "MARKET_SYMBOL", "1101")
    monkeypatch.setattr(run_daily, "load_publication_dates", lambda: None)
    return fin, rev


def test_recovered_daily_frame_equals_research_frame(monkeypatch):
    from jobs import run_daily

    snap = chips(snapshot())
    fin, rev = fake_db(monkeypatch, run_daily, snap)
    day = LAST.strftime("%Y-%m-%d")
    daily = run_daily.load_recovered_data(day, engine=None).frame(day, ["ma60", "volume_ratio", "chip_score"])[0]
    research = NativeResearchData(snap, fin, rev).frame(day, ["ma60", "volume_ratio", "chip_score"])[0]
    pd.testing.assert_frame_equal(daily.reset_index(drop=True), research[daily.columns].reset_index(drop=True), check_dtype=False)


def test_recovered_mode_is_dry_run_only(monkeypatch):
    from jobs import run_daily

    monkeypatch.setattr(run_daily.Config, "DAILY_FEATURE_MODE", "recovered")  # even when the environment selects it (OD-7)
    with pytest.raises(RuntimeError, match="RECOVERED_MODE_DRY_RUN_ONLY"):
        run_daily.run_daily_for_date("2026-10-15", dry_run=False)
    with pytest.raises(RuntimeError, match="RECOVERED_MODE_DRY_RUN_ONLY"):
        run_daily.main(["--date", "2026-10-15", "--feature-mode", "recovered"])
    monkeypatch.setattr(run_daily.Config, "DAILY_FEATURE_MODE", "legacy")
    assert run_daily.resolve_feature_mode(None, dry_run=False) == "legacy"


def test_recovered_scoring_never_fills_features():
    from types import SimpleNamespace

    from jobs import run_daily

    class Model:
        def predict_proba(self, X):
            return np.column_stack([1 - X.iloc[:, 0], X.iloc[:, 0]])

    strategy = SimpleNamespace(name="t", display_name="T", target_return=1, look_ahead_days=1,
                               filter_candidates=lambda df: df)
    frame = pd.DataFrame({"stock_id": ["1101", "1102"], "close_price": [1.0, 1.0], "rsi_z": [0.2, 0.8]})
    blocked = run_daily.run_strategy(strategy, frame, "2026-10-15", None, dry_run=True, recovered=True, model_status="MODEL_BLOCKED: X")
    assert blocked.ai_score.isna().all() and set(blocked.ai_status) == {"MODEL_BLOCKED: X"}
    contract = SimpleNamespace(model=Model(), features=["rsi_z"])
    scored = run_daily.run_strategy(strategy, frame, "2026-10-15", None, dry_run=True, recovered=True, contract=contract,
                                    model_status="MODEL_BLOCKED: X")
    assert list(scored.stock_id) == ["1102", "1101"]
    with pytest.raises(ValueError, match="MODEL_FEATURE_MISSING_VALUE"):
        run_daily.run_strategy(strategy, frame.assign(rsi_z=[np.nan, 1.0]), "2026-10-15", None, dry_run=True, recovered=True,
                               contract=contract, model_status="MODEL_BLOCKED: X")
