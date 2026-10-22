"""Synthetic engineering checks for the native-7 research data path; never investment evidence."""
from datetime import datetime
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

from core.backtest.runner import BacktestEngine
from core.research import native_models
from core.research.native_frame import FRAME_INPUTS, NativeResearchData, rule_inputs
from core.research.native_models import ModelContractError, load_model_contract
from core.research.native_snapshot import Official, build_snapshot, corporate_action_check

DAYS = pd.bdate_range("2026-03-02", periods=121)
LAST = DAYS[-1]


def snapshot(stocks=("1101", "1102"), flows=True):
    rng = np.random.default_rng(7)
    rows = []
    for k, sid in enumerate(stocks):
        close = 20 + k + np.cumsum(rng.normal(0, .1, len(DAYS)))
        for i, day in enumerate(DAYS):
            rows.append({"trade_date": day, "stock_id": sid, "open_price": close[i], "high_price": close[i] + .2,
                         "low_price": close[i] - .2, "close_price": close[i], "volume": float(rng.integers(500, 1500)), "amount": 1.0,
                         "foreign_buy": float(rng.integers(-50, 80)) if flows else np.nan, "trust_buy": float(rng.integers(-5, 10)),
                         "dealer_buy": 0.0, "margin_balance": 0.0, "short_balance": np.nan})
    return pd.DataFrame(rows)


def fundamentals(extra=()):
    fin = pd.DataFrame([{"stock_id": s, "year": y, "quarter": q, "revenue": 100.0, "rd_expense": 2.0, "operating_profit": 10.0, "eps": 1.0}
                        for s in ("1101", "1102") for y, q in ((2025, 4), (2026, 1))] + list(extra))
    rev = pd.DataFrame([{"stock_id": s, "year": 2026, "month": m, "revenue": 1.0, "revenue_yoy": 5.0} for s in ("1101", "1102") for m in (4, 5)])
    return fin, rev


def provider(**kw):
    fin, rev = fundamentals(kw.pop("extra_fin", ()))
    return NativeResearchData(kw.pop("snap", snapshot()), fin, rev)


def test_future_filing_never_changes_selection_inputs():
    day = "2026-05-12"  # Q1 2026 is due 05-15, April revenue 05-10
    base = provider().frame(day, ["eps", "op_profit_margin", "revenue_yoy"])[0].set_index("stock_id")
    future = {"stock_id": "1101", "year": 2026, "quarter": 2, "revenue": 100.0, "rd_expense": 0.0, "operating_profit": 90.0, "eps": 9.0}
    injected = provider(extra_fin=[future]).frame(day, ["eps", "op_profit_margin", "revenue_yoy"])[0].set_index("stock_id")
    assert base.loc["1101", "eps"] == injected.loc["1101", "eps"] == 1.0
    assert base.loc["1101", "op_profit_margin"] == pytest.approx(0.1)  # Daily definition: operating_profit / revenue
    assert injected.loc["1101", "op_profit_margin"] == pytest.approx(0.1)
    assert base.loc["1101", "revenue_yoy"] == 5.0
    # The same filing is used once its deadline has passed.
    assert provider(extra_fin=[future]).frame("2026-08-17", ["eps"])[0].set_index("stock_id").loc["1101", "eps"] == 9.0


@pytest.mark.parametrize("column", ["ma60", "kd_k", "natr"])
def test_missing_indicator_is_excluded_not_zero_filled(column, monkeypatch):
    data = provider()
    full = data.cross_section(LAST).copy()
    full.loc[full.stock_id == "1101", column] = np.nan  # e.g. too little history or a missing bar
    monkeypatch.setattr(data, "cross_section", lambda date: full)
    kept, note = data.frame(LAST.strftime("%Y-%m-%d"), [column])
    assert list(kept.stock_id) == ["1102"] and note["missing_input_counts"] == {column: 1}


def test_short_history_leaves_ma60_null():
    early = DAYS[20]  # 21 sessions: MA60 needs 30
    assert provider().cross_section(early).ma60.isna().all()


def test_zero_filling_would_create_a_false_signal_but_exclusion_prevents_it(monkeypatch):
    """Strategies coerce NaN to 0, so ma60 = 0 and kd_k = 0 satisfy 'close > ma60' and 'kd_k < 35'."""
    from core.strategies.mean_reversion import MeanReversionStrategy

    monkeypatch.setattr("config.Config.USE_MARKET_FILTER", False)
    monkeypatch.setattr("core.db_helper.get_setting", lambda key, default=None: default)
    row = {"stock_id": "1101", "close_price": 50.0, "ma60": np.nan, "volume": 9e6, "volume_ratio": 0.5, "kd_k": np.nan,
           "bb_width": 5.0, "bias": 0.0, "rsi": 40.0, "trade_date": "2026-06-01", "stock_name": "T"}
    assert len(MeanReversionStrategy().filter_candidates(pd.DataFrame([row]))) == 1  # the hazard
    data = provider()
    full = data.cross_section(LAST).copy()
    full.loc[:, ["ma60", "kd_k"]] = np.nan
    monkeypatch.setattr(data, "cross_section", lambda date: full)
    kept, _ = data.frame(LAST.strftime("%Y-%m-%d"), sorted(rule_inputs(MeanReversionStrategy())))
    assert kept.empty  # nothing reaches the strategy


def test_missing_flows_on_signal_date_exclude_the_stock():
    snap = snapshot()
    snap.loc[(snap.stock_id == "1101") & (snap.trade_date == LAST), ["foreign_buy", "trust_buy"]] = np.nan
    kept, note = NativeResearchData(snap, *fundamentals()).frame(LAST.strftime("%Y-%m-%d"), ["foreign_ratio", "trust_ratio"])
    assert list(kept.stock_id) == ["1102"] and note["missing_input_counts"]["foreign_ratio"] == 1


def test_z_matrix_is_built_only_from_included_rows():
    kept, _ = provider().frame(LAST.strftime("%Y-%m-%d"), ["ma60", "rsi", "kd_k"])
    assert {"rsi_z", "foreign_ratio_z", "volume_ratio_z"} <= set(kept.columns)
    assert np.isclose(kept.rsi_z.mean(), 0, atol=1e-9)


def test_daily_pipeline_defects_are_reproduced_not_hidden():
    """Daily applies calculate_ratio_features to a one-day frame, so volume_ratio is constant 1.0 there."""
    from core.calc_indicators import calculate_ratio_features

    one_day = snapshot().query("trade_date == @LAST")
    assert (calculate_ratio_features(one_day).volume_ratio == 1.0).all()
    research = provider().cross_section(LAST)
    assert research.volume_ratio.nunique() > 1  # research keeps the intended 20-day definition


def test_research_indicators_match_daily_pipeline(monkeypatch):
    """Complete inputs, same window: indicator values equal legacy jobs.run_daily.compute_indicators_from_history.

    A zero margin balance makes margin_change_pct undefined; legacy turns it into 0 and the shared core keeps NaN,
    so the fixture uses a positive balance (test_daily_selection_recovery covers the missing cases)."""
    from jobs import run_daily

    snap = snapshot().assign(margin_balance=lambda d: 1000.0 + d.groupby("stock_id").cumcount())
    history = snap.assign(dealer_buy=0.0).drop(columns=["amount", "short_balance"])
    window = lambda day: history[history.trade_date >= pd.Timestamp(day) - pd.Timedelta(days=150)].copy()
    monkeypatch.setattr(run_daily.pd, "read_sql", lambda sql, engine, params: window(params["date"]))
    monkeypatch.setattr(run_daily.Config, "MARKET_SYMBOL", "1101")
    live = run_daily.compute_indicators_from_history(LAST.strftime("%Y-%m-%d"), engine=None, dry_run=True).set_index("stock_id")
    research = provider(snap=snap).cross_section(LAST).set_index("stock_id")
    for column in ("ma5", "ma20", "ma60", "rsi", "macd_hist", "kd_k", "bb_width", "atr", "natr", "std_20", "foreign_consec_days",
                   "trust_consec_days", "chip_score", "foreign_ratio", "trust_ratio"):
        np.testing.assert_allclose(research.loc[live.index, column], live[column], rtol=1e-9, atol=1e-12, err_msg=column)


def model_files(tmp_path, monkeypatch, *, name="v99_test", importance=(0.7, 0.3, 0.0), trained="2026-05-20 20:55:00", extra=None):
    import joblib

    features = ["rsi_z", "foreign_ratio_z", "dealer_ratio_z"]
    model = SimpleNamespace(feature_importances_=list(importance))
    payload = {"model": model, "features": features, "training_date": trained, "version": "t"}
    payload.update(extra or {})
    joblib.dump(payload, tmp_path / f"stock_ai_model_{name}.pkl")
    monkeypatch.setattr(native_models, "get_model_path", lambda strategy: str(tmp_path / f"stock_ai_model_{strategy}.pkl"))
    return name


def test_model_contract_reports_identity_and_ignores_zero_importance_features(tmp_path, monkeypatch):
    name = model_files(tmp_path, monkeypatch)
    contract = load_model_contract(name, FRAME_INPUTS, "2026-06-01")
    assert contract.required_raw == ["foreign_ratio", "rsi"]
    assert contract.identity["features_ignored_by_model"] == ["dealer_ratio_z"] and len(contract.identity["sha256"]) == 64


def test_model_contract_never_falls_back_or_guesses(tmp_path, monkeypatch):
    name = model_files(tmp_path, monkeypatch)
    with pytest.raises(ModelContractError, match="MODEL_MISSING"):
        load_model_contract("v98_absent", FRAME_INPUTS, "2026-06-01")  # a generic model exists elsewhere; it must not be used
    with pytest.raises(ModelContractError, match="FEATURE_UNAVAILABLE"):
        load_model_contract(name, FRAME_INPUTS - {"foreign_ratio"}, "2026-06-01")
    with pytest.raises(ModelContractError, match="WINDOW_NOT_AFTER_TRAINING"):
        load_model_contract(name, FRAME_INPUTS, "2026-05-20")
    bare = model_files(tmp_path, monkeypatch, name="v97_bare", extra={"training_date": None})
    (tmp_path / "stock_ai_model_v96_old.pkl").write_bytes((tmp_path / f"stock_ai_model_{bare}.pkl").read_bytes())
    import joblib
    joblib.dump({"model": SimpleNamespace(feature_importances_=[1]), "features": ["rsi_z"]}, tmp_path / "stock_ai_model_v95_nometa.pkl")
    with pytest.raises(ModelContractError, match="METADATA_INCOMPLETE"):
        load_model_contract("v95_nometa", FRAME_INPUTS, "2026-06-01")


def test_null_source_blocks_the_contract_and_only_diagnostic_mode_fills_it(tmp_path, monkeypatch):
    import joblib

    features = ["rsi_z", "chip_score_z"]
    joblib.dump({"model": SimpleNamespace(feature_importances_=[0.9, 0.1]), "features": features, "training_date": "2026-05-20 20:55:00"},
                tmp_path / "stock_ai_model_v94_chip.pkl")
    monkeypatch.setattr(native_models, "get_model_path", lambda name: str(tmp_path / f"stock_ai_model_{name}.pkl"))
    snap = snapshot().assign(dealer_buy=np.nan, margin_balance=np.nan)  # sources never populated
    fin, rev = fundamentals()
    strict = NativeResearchData(snap, fin, rev)
    empty = strict.unavailable_inputs(DAYS[0].strftime("%Y-%m-%d"), LAST.strftime("%Y-%m-%d"))
    assert {"dealer_buy", "dealer_ratio", "margin_change_pct", "chip_score"} <= empty
    with pytest.raises(ModelContractError, match="FEATURE_UNAVAILABLE_AT_SOURCE"):
        load_model_contract("v94_chip", FRAME_INPUTS, "2026-06-01", empty)
    # Diagnostic mode reproduces Daily (NULL -> 0) and says so; it never reports the sources as available data.
    diagnostic = NativeResearchData(snap, fin, rev, daily_equivalent_fill=True)
    assert diagnostic.unavailable_inputs("2026-03-02", "2026-08-14") == frozenset()
    kept, note = diagnostic.frame(LAST.strftime("%Y-%m-%d"), ["chip_score", "dealer_buy", "foreign_ratio"])
    assert len(kept) == 2 and (kept.chip_score == 0).all() and note["excluded"] == 0
    assert strict.frame(LAST.strftime("%Y-%m-%d"), ["chip_score"])[0].empty  # strict research excludes instead of filling


def test_institutional_strategy_declares_flow_inputs_and_refuses_without_them():
    from core.strategies.institutional_flow_confirmation import InstitutionalFlowConfirmationStrategy as Strategy

    needed = rule_inputs(Strategy())
    assert {"chip_score", "foreign_consec_days", "trust_consec_days"} <= needed
    snap = snapshot(flows=False)  # no usable foreign flows at all
    kept, note = NativeResearchData(snap, *fundamentals()).frame(LAST.strftime("%Y-%m-%d"), sorted(needed))
    assert kept.empty and note["missing_input_counts"]["chip_score"] == note["rows"]


def test_research_engine_requires_provider_and_never_zero_fills_features():
    with pytest.raises(ValueError, match="requires research_data"):
        BacktestEngine(execution_contract="next_session_open_v2", persist_results=False, persist_to_db=False,
                       trading_sessions=["2026-10-19"])


def official(rows, sessions):
    return Official(pd.DatetimeIndex(sessions), [], pd.DataFrame(rows), pd.Series(dtype=float), pd.DataFrame(), "h", 1)


def test_snapshot_builder_quarantines_misdated_duplicate_and_missing_rows():
    d1, d2, d3 = (pd.Timestamp(x) for x in ("2026-04-01", "2026-04-02", "2026-04-03"))
    bar = lambda day, sid, c: {"trade_date": day, "stock_id": sid, "open_price": c, "high_price": c, "low_price": c, "close_price": c, "volume": 10.0, "amount": 1.0}
    off = official([bar(d1, "1101", 10), bar(d2, "1101", 11), bar(d3, "1101", 12), bar(d1, "1102", 5)], [d1, d2, d3])
    flow = {"foreign_buy": 1.0, "trust_buy": 2.0, "dealer_buy": 0.0, "margin_balance": 0.0, "short_balance": np.nan}
    row = lambda day, sid, c, **f: {"trade_date": day, "stock_id": sid, "open_price": c, "high_price": c, "low_price": c, "close_price": c,
                                    "volume": 10.0, **{**flow, **f}}
    daily = pd.DataFrame([
        row(d1, "1101", 10), row(d1, "1101", 99, foreign_buy=7.0),   # duplicate: the official bar wins, flows come from it
        row(d2, "1101", 77),                                          # bar disagrees with official: flows untrusted
        row(pd.Timestamp("2026-03-28"), "1101", 12),                  # Saturday
        row(d1, "9999", 3),                                           # not an official TWSE id (TPEx): excluded
    ])
    snap, audit = build_snapshot(off, daily)
    got = snap.set_index(["stock_id", "trade_date"])
    assert len(snap) == 4 and got.loc[("1101", d1), "foreign_buy"] == 1.0
    assert np.isnan(got.loc[("1101", d2), "foreign_buy"]) and np.isnan(got.loc[("1101", d3), "foreign_buy"])
    assert got.loc[("1101", d2), "close_price"] == 11  # official price, never the misdated source price
    assert audit["rows_twse_weekend"] == 1 and audit["duplicate_keys"] == 1 and audit["duplicate_keys_resolved_by_official_bar"] == 1
    assert audit["rows_four_digit_not_in_official_twse_list(tpex)"] == 1 and audit["price_mismatch_rows_by_date_top10"] == {"2026-04-01": 1, "2026-04-02": 1}


def test_corporate_action_check_requires_official_previous_close():
    d1, d2 = pd.Timestamp("2026-04-01"), pd.Timestamp("2026-04-02")
    bars = pd.DataFrame([{"trade_date": d, "stock_id": "1101", "open_price": c, "high_price": c, "low_price": c, "close_price": c,
                          "volume": 1.0, "amount": 1.0} for d, c in ((d1, 10.0), (d2, 9.0))])
    actions = pd.DataFrame([{"ex_date": d2.date(), "stock_id": "1101", "pre_ex_close": 10.0, "ex_reference_price": 9.0}])
    off = Official(pd.DatetimeIndex([d1, d2]), [], bars, pd.Series(dtype=float), actions, "h", 1)
    assert corporate_action_check(off)["pre_ex_close_equals_previous_official_close"] == 1
