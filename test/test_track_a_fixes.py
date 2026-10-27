"""Track A engineering fixes (OD-28): history-based dealer streaks, model-complete z-score population, rules-only runs,
model contract fields and the approval-gated training builder. Synthetic data only; no model is fitted."""
import json
from types import SimpleNamespace

import joblib
import numpy as np
import pandas as pd
import pytest

from core.calc_indicators import build_feature_history
from core.research import model_training, native_models
from core.research.native_frame import NativeResearchData
from core.research.native_models import ModelContractError, load_model_contract
from test_daily_selection_recovery import chips
from test_native_recovery import DAYS, LAST, fundamentals, snapshot

MODEL_INPUTS = ["rsi", "bias", "foreign_ratio", "trust_ratio", "dealer_ratio", "chip_score", "dealer_consec_days", "foreign_consec_days"]


def provider(snap=None):
    return NativeResearchData(chips(snapshot()) if snap is None else snap, *fundamentals())


# ---- 1. dealer_consec_days follows the stock history ----
def test_dealer_consec_days_counts_consecutive_sessions_and_stays_null_without_a_flow():
    snap = snapshot().assign(dealer_buy=0.0)
    last5 = (snap.stock_id == "1101") & (snap.trade_date >= DAYS[-5])
    snap.loc[last5, "dealer_buy"] = 10.0
    features = build_feature_history(snap)
    row = features[(features.stock_id == "1101") & (features.trade_date == LAST)].iloc[0]
    assert row.dealer_consec_days == 5  # the old one-day cross-section could only give 0 or 1
    missing = build_feature_history(snap.assign(dealer_buy=np.nan))
    assert missing[missing.trade_date == LAST].dealer_consec_days.isna().all()


def test_institutional_consec_buy_days_uses_the_history_streak():
    snap = chips(snapshot())
    snap.loc[(snap.stock_id == "1101") & (snap.trade_date >= DAYS[-5]), ["foreign_buy", "trust_buy", "dealer_buy"]] = 10.0
    frame, _ = NativeResearchData(snap, *fundamentals()).frame(LAST.strftime("%Y-%m-%d"), [], model_inputs=MODEL_INPUTS)
    row = frame.set_index("stock_id").loc["1101"]
    assert row.dealer_consec_days == 5 and row.institutional_consec_buy_days == row.foreign_consec_days + row.trust_consec_days + 5


# ---- 2. z-score population = rows with every model input; rules come after ----
def zscores(frame):
    return frame.set_index("stock_id")[["rsi_z", "chip_score_z"]]


def test_rule_inputs_never_change_the_zscore_population():
    day = LAST.strftime("%Y-%m-%d")
    data = provider()
    base, _ = data.frame(day, [], model_inputs=MODEL_INPUTS)
    full = data.cross_section(LAST).copy()
    full.loc[full.stock_id == "1101", "revenue_yoy"] = np.nan  # a rule-only input is missing for one stock
    data._cache[LAST] = full
    with_rule, note = data.frame(day, ["revenue_yoy"], model_inputs=MODEL_INPUTS)
    assert list(with_rule.stock_id) == ["1102"] and note["excluded_rule_missing"][0]["stock_id"] == "1101"
    pd.testing.assert_frame_equal(zscores(with_rule), zscores(base).loc[["1102"]])  # the dropped stock still counted in the population


def test_a_row_missing_a_model_input_is_outside_the_population():
    day = LAST.strftime("%Y-%m-%d")
    data = provider()
    full = data.cross_section(LAST).copy()
    full.loc[full.stock_id == "1101", "dealer_ratio"] = np.nan
    data._cache[LAST] = full
    kept, note = data.frame(day, [], model_inputs=MODEL_INPUTS)
    assert list(kept.stock_id) == ["1102"] and note["excluded_model_incomplete"][0]["missing_columns"] == ["dealer_ratio"]
    alone, _ = provider(snapshot().query("stock_id == '1102'").pipe(chips)).frame(day, [], model_inputs=MODEL_INPUTS)
    assert alone.rsi_z.iloc[0] == 0 and kept.rsi_z.iloc[0] == 0  # a population of one has no spread: z = 0, never a filled value


def test_legacy_frame_behavior_is_unchanged_without_model_inputs():
    day = LAST.strftime("%Y-%m-%d")
    data = provider()
    legacy, note = data.frame(day, ["rsi"])
    assert "excluded_model_incomplete" not in note and len(legacy) == 2


# ---- 3. model contract: training_data_end and specification hash ----
def model_file(tmp_path, monkeypatch, **extra):
    features = ["rsi_z", "chip_score_z"]
    payload = {"model": SimpleNamespace(feature_importances_=[0.6, 0.4]), "features": features, "training_date": "2026-05-20 20:55:00", "version": "t", **extra}
    joblib.dump(payload, tmp_path / "stock_ai_model_vx.pkl")
    monkeypatch.setattr(native_models, "get_model_path", lambda name: str(tmp_path / f"stock_ai_model_{name}.pkl"))


def test_training_data_end_bounds_the_window_and_the_spec_hash_is_checked(tmp_path, monkeypatch):
    model_file(tmp_path, monkeypatch, training_data_end="2026-10-22", spec_sha256="a" * 64)
    available = {"rsi", "chip_score", "foreign_ratio", "trust_ratio", "dealer_ratio", "margin_change_pct"}
    with pytest.raises(ModelContractError, match="WINDOW_NOT_AFTER_TRAINING_DATA_END"):
        load_model_contract("vx", available, "2026-10-22")
    contract = load_model_contract("vx", available, "2026-10-23", expected_spec_sha256="a" * 64)
    assert contract.identity["training_data_end"] == "2026-10-22" and contract.identity["spec_sha256"] == "a" * 64
    with pytest.raises(ModelContractError, match="SPEC_HASH_MISMATCH"):
        load_model_contract("vx", available, "2026-10-23", expected_spec_sha256="b" * 64)


def test_a_model_with_a_spec_hash_requires_every_feature_not_only_those_with_importance(tmp_path, monkeypatch):
    features = ["rsi_z", "natr_z"]
    joblib.dump({"model": SimpleNamespace(feature_importances_=[1.0, 0.0]), "features": features, "training_date": "2026-05-20 20:55:00",
                 "version": "t", "training_data_end": "2026-05-20", "spec_sha256": "a" * 64}, tmp_path / "stock_ai_model_vy.pkl")
    monkeypatch.setattr(native_models, "get_model_path", lambda name: str(tmp_path / f"stock_ai_model_{name}.pkl"))
    assert load_model_contract("vy", {"rsi", "natr"}, "2026-10-23").required_raw == ["natr", "rsi"]  # same population in training and inference


def test_an_old_model_without_the_new_fields_still_loads_as_before(tmp_path, monkeypatch):
    model_file(tmp_path, monkeypatch)
    contract = load_model_contract("vx", {"rsi", "chip_score", "foreign_ratio", "trust_ratio", "dealer_ratio", "margin_change_pct"}, "2026-10-23")
    assert contract.identity["training_data_end"] is None and contract.identity["spec_sha256"] is None


def test_recovered_status_only_blocks_on_missing_cutoff_or_unproven_chip_definition():
    from jobs.run_daily import recovered_model_status

    assert recovered_model_status({"training_data_end": None, "features_used": ["chip_score_z"], "spec_sha256": None}).count("CHIP_SCORE") == 1
    assert recovered_model_status({"training_data_end": "2026-10-22", "features_used": ["chip_score_z"], "spec_sha256": "a" * 64}) is None
    assert "TRAINING_DATA_END_MISSING" in recovered_model_status({"training_data_end": None, "features_used": [], "spec_sha256": "a" * 64})


# ---- 4. training builder: approval gate and parity with the recovered frame ----
class Untouchable:
    def __getattr__(self, name):
        raise AssertionError(f"data was read before approval: {name}")


SPEC = {"sha256": "x", "feature_order": ["rsi_z", "chip_score_z"], "strategies": {}}


def test_training_refuses_without_a_matching_approval_before_reading_any_data(tmp_path):
    spec = {**SPEC, "sha256": model_training.spec_hash({k: v for k, v in SPEC.items() if k != "sha256"})}
    with pytest.raises(model_training.OwnerApprovalError, match="approval file missing"):
        model_training.train(Untouchable(), [], spec, "mean_reversion", tmp_path / "none.json", tmp_path)
    approval = tmp_path / "approval.json"
    approval.write_text(json.dumps({"spec_sha256": "0" * 64, "approved_by": "owner"}), encoding="utf-8")
    with pytest.raises(model_training.OwnerApprovalError, match="does not match"):
        model_training.train(Untouchable(), [], spec, "mean_reversion", approval, tmp_path)
    approval.write_text(json.dumps({"spec_sha256": spec["sha256"]}), encoding="utf-8")
    with pytest.raises(model_training.OwnerApprovalError, match="approved_by"):
        model_training.train(Untouchable(), [], spec, "mean_reversion", approval, tmp_path)


def test_training_frame_equals_the_recovered_frame_for_the_same_session():
    day = LAST.strftime("%Y-%m-%d")
    data = provider()
    spec = {"feature_order": ["rsi_z", "bias_z", "chip_score_z"], "model_inputs": MODEL_INPUTS,
            "strategies": {"mean_reversion": {"label": {"look_ahead_sessions": 3, "target_return": 0.01}}}}
    training = model_training.build_training_frame(data, [DAYS[-6]], spec, "mean_reversion", data_end=day)
    recovered, _ = data.frame(DAYS[-6].strftime("%Y-%m-%d"), [], model_inputs=MODEL_INPUTS)
    columns = spec["feature_order"]
    pd.testing.assert_frame_equal(training[["stock_id", *columns]].reset_index(drop=True), recovered[["stock_id", *columns]].reset_index(drop=True),
                                  rtol=1e-9, atol=1e-12)
    assert list(training.columns) == ["stock_id", "trade_date", *columns, "label", "future_max_return"]


def test_labels_use_only_the_next_sessions_and_rows_without_a_full_horizon_are_dropped():
    snap = snapshot().assign(dealer_buy=1.0, margin_balance=1000.0)
    day = DAYS[-6]
    high = snap.loc[(snap.stock_id == "1101") & (snap.trade_date == DAYS[-5]), "high_price"].iloc[0]
    close = snap.loc[(snap.stock_id == "1101") & (snap.trade_date == day), "close_price"].iloc[0]
    spec = {"feature_order": ["rsi_z"], "model_inputs": ["rsi"], "strategies": {"m": {"label": {"look_ahead_sessions": 3, "target_return": 0.0}}}}
    data = NativeResearchData(snap, *fundamentals())
    frame = model_training.build_training_frame(data, [day, DAYS[-2]], spec, "m", data_end=LAST.strftime("%Y-%m-%d"))
    assert set(frame.trade_date) == {day}  # DAYS[-2] has only one session ahead: dropped, not labelled 0
    expected = int(snap[(snap.stock_id == "1101") & snap.trade_date.isin(DAYS[-5:-2])].high_price.max() / close - 1 > 0.0)
    assert int(frame[frame.stock_id == "1101"].label.iloc[0]) == expected and high > 0


def test_a_stock_without_a_bar_inside_the_horizon_has_no_full_horizon_and_is_dropped():
    snap = snapshot().assign(dealer_buy=1.0, margin_balance=1000.0)
    day = DAYS[-6]
    snap = snap[~((snap.stock_id == "1101") & (snap.trade_date == DAYS[-4]))]  # suspended inside the 3-session horizon DAYS[-5:-2]
    spec = {"feature_order": ["rsi_z"], "model_inputs": ["rsi"], "strategies": {"m": {"label": {"look_ahead_sessions": 3, "target_return": 0.0}}}}
    frame = model_training.build_training_frame(NativeResearchData(snap, *fundamentals()), [day], spec, "m", data_end=LAST.strftime("%Y-%m-%d"))
    assert "1101" not in set(frame.stock_id) and len(frame) > 0  # specification: rows without a full horizon are dropped, not labelled from fewer sessions


def test_train_fit_and_dump_record_the_contract_fields_on_synthetic_data(tmp_path):
    """Smoke run of the fit path on synthetic data (a tiny model in tmp_path; never a real model). Real training stays behind the owner approval."""
    spec = {"feature_order": ["rsi_z", "bias_z"], "model_inputs": ["rsi", "bias"], "split": {"train": 0.6, "validation": 0.2, "embargo_sessions": 5},
            "strategies": {"m": {"model_name": "smoke", "label": {"look_ahead_sessions": 3, "target_return": 0.01}}}, "acceptance": ACCEPTANCE, "corporate_actions": POLICY,
            "hyperparameters": {"n_estimators": 5, "learning_rate": 0.1, "max_depth": 2, "min_child_weight": 1, "subsample": 1.0, "colsample_bytree": 1.0, "random_state": 42, "eval_metric": "logloss"}}
    spec["sha256"] = model_training.spec_hash(spec)
    approval = tmp_path / "approval.json"
    approval.write_text(json.dumps({"spec_sha256": spec["sha256"], "approved_by": "TEST-ONLY"}), encoding="utf-8")
    sessions = list(DAYS[30:])
    path = model_training.train(provider(), sessions, spec, "m", approval, tmp_path, events=NO_EVENTS)
    saved = joblib.load(path)
    assert saved["features"] == ["rsi_z", "bias_z"] and saved["spec_sha256"] == spec["sha256"] and saved["acceptance"]["test_evaluations"] == 1
    assert saved["training_data_end"] == str(sessions[-1]) and saved["training_data_start"] == str(sessions[0]) and saved["training_date"]  # freeze time and data end are recorded
    assert saved["split"]["train"][1] < saved["split"]["validation"][0] < saved["split"]["test"][0]  # strictly by date, with purge and embargo between


# ---- 5. rules-only research runs ----
def test_rules_only_engine_needs_no_model_and_excludes_only_on_rule_inputs():
    from core.backtest.runner import BacktestEngine
    from scripts.native_recovery import isolated

    snap = snapshot().assign(dealer_buy=np.nan, margin_balance=np.nan)  # the AI features are unavailable at source
    data = NativeResearchData(snap, *fundamentals())
    sessions = [d.strftime("%Y-%m-%d") for d in DAYS[60:]]
    with isolated(data, {}):
        engine = BacktestEngine(mode="mean_reversion", start_date=sessions[0], end_date=sessions[-1], persist_results=False, persist_to_db=False,
                                execution_contract="next_session_open_v2", trading_sessions=sessions, research_data=data, rules_only=True)
        assert engine.model is None and engine.model_identity is None
        from core.research.native_frame import rule_inputs
        assert engine.required_inputs == sorted(rule_inputs(engine.strategy_obj))
        assert isinstance(engine.find_candidates(sessions[-1]), list)


# ---- 6. acceptance evaluation (specification 9.1) ----
ACCEPTANCE = {"metrics": {"top_decile_lift_mean": {"min": 1.2}, "monthly_lift_positive_share": {"min": 0.6}, "pooled_roc_auc": {"min": 0.55}, "mean_rank_ic": {"min": 0.02}}}


def segment(n_days=60, n_stocks=100, seed=0):
    rng = np.random.default_rng(seed)
    days = pd.bdate_range("2026-01-05", periods=n_days)
    frame = pd.DataFrame({"trade_date": np.repeat(days, n_stocks), "future_max_return": rng.normal(0.02, 0.05, n_days * n_stocks)})
    frame["label"] = (frame.future_max_return > 0.03).astype(int)
    return frame


def test_acceptance_passes_an_informative_score_and_fails_noise():
    frame = segment()
    good = model_training.evaluate_segment(frame, frame.future_max_return.to_numpy(), ACCEPTANCE, seed=42)
    assert good["status"] == "PASS" and all(m["passed"] for m in good["metrics"].values())
    assert 0.48 <= good["guards"]["permuted_label_auc"]["value"] <= 0.52 and good["guards"]["permuted_label_auc"]["passed"]
    noise = model_training.evaluate_segment(frame, np.random.default_rng(7).random(len(frame)), ACCEPTANCE, seed=42)
    assert noise["status"] == "FAIL"


def test_acceptance_reports_undefined_metrics_as_none_with_a_reason_never_zero():
    frame = segment().assign(label=0)  # one class only: AUC and lift are undefined
    result = model_training.evaluate_segment(frame, frame.future_max_return.to_numpy(), ACCEPTANCE, seed=42)
    auc = result["metrics"]["pooled_roc_auc"]
    assert auc["value"] is None and auc["reason"] and auc["passed"] is None
    assert result["metrics"]["top_decile_lift_mean"]["value"] is None
    assert result["status"] == "NOT_EVALUABLE"


def test_train_evaluates_validation_and_test_once_and_records_the_guards(tmp_path):
    spec = {"feature_order": ["rsi_z", "bias_z"], "model_inputs": ["rsi", "bias"], "split": {"train": 0.6, "validation": 0.2, "embargo_sessions": 5},
            "strategies": {"m": {"model_name": "smoke", "label": {"look_ahead_sessions": 3, "target_return": 0.01}}}, "acceptance": ACCEPTANCE, "corporate_actions": POLICY,
            "hyperparameters": {"n_estimators": 5, "learning_rate": 0.1, "max_depth": 2, "min_child_weight": 1, "subsample": 1.0, "colsample_bytree": 1.0, "random_state": 42, "eval_metric": "logloss"}}
    spec["sha256"] = model_training.spec_hash(spec)
    approval = tmp_path / "approval.json"
    approval.write_text(json.dumps({"spec_sha256": spec["sha256"], "approved_by": "TEST-ONLY"}), encoding="utf-8")
    saved = joblib.load(model_training.train(provider(), list(DAYS[30:]), spec, "m", approval, tmp_path, events=NO_EVENTS))
    acceptance = saved["acceptance"]
    assert set(acceptance["segments"]) == {"validation", "test"} and acceptance["test_evaluations"] == 1
    assert acceptance["guards"]["sessions_disjoint"] and acceptance["guards"]["train_horizon_ends_before_validation"] and acceptance["guards"]["feature_list_equals_spec"]
    assert acceptance["status"] in {"PASS", "FAIL", "NOT_EVALUABLE"}  # two synthetic stocks: the verdict itself is not asserted
    with pytest.raises(model_training.OwnerApprovalError, match="model already exists"):  # the test segment is evaluated once: no silent refit over it
        model_training.train(provider(), list(DAYS[30:]), spec, "m", approval, tmp_path, events=NO_EVENTS)


def test_a_model_that_is_not_accepted_is_never_written_under_the_loadable_name_and_test_is_read_once_per_spec(tmp_path, monkeypatch):
    monkeypatch.setattr(model_training, "TEST_EVALUATION_LEDGER", tmp_path / "ledger.jsonl")
    spec = {"feature_order": ["rsi_z", "bias_z"], "model_inputs": ["rsi", "bias"], "split": {"train": 0.6, "validation": 0.2, "embargo_sessions": 5},
            "strategies": {"m": {"model_name": "smoke", "label": {"look_ahead_sessions": 3, "target_return": 0.01}}}, "acceptance": ACCEPTANCE, "corporate_actions": POLICY,
            "hyperparameters": {"n_estimators": 5, "learning_rate": 0.1, "max_depth": 2, "min_child_weight": 1, "subsample": 1.0, "colsample_bytree": 1.0, "random_state": 42, "eval_metric": "logloss"}}
    spec["sha256"] = model_training.spec_hash(spec)
    approval = tmp_path / "approval.json"
    approval.write_text(json.dumps({"spec_sha256": spec["sha256"], "approved_by": "TEST-ONLY"}), encoding="utf-8")
    path = model_training.train(provider(), list(DAYS[30:]), spec, "m", approval, tmp_path / "a", events=NO_EVENTS)
    saved = joblib.load(path)
    assert saved["acceptance"]["guards"]["data_manifest_matches_registered"] is None  # no registered manifest: undefined, not passed
    assert saved["acceptance"]["status"] != "PASS" and path.name == "stock_ai_model_smoke_rejected.pkl"
    with pytest.raises(model_training.OwnerApprovalError, match="already evaluated"):  # another folder does not reopen the test segment
        model_training.train(provider(), list(DAYS[30:]), spec, "m", approval, tmp_path / "b", events=NO_EVENTS)


@pytest.fixture(autouse=True)
def isolated_test_evaluation_ledger(tmp_path, monkeypatch):
    monkeypatch.setattr(model_training, "TEST_EVALUATION_LEDGER", tmp_path / "test_evaluation_ledger.jsonl")  # never the real ledger


# ---- 7. OD-T1 label policy: official factors for verified events only; unverified events and unexplained jumps are excluded ----
POLICY = {"label_policy": "OFFICIAL_FACTORS_VERIFIED_ONLY"}


def ex_dividend(snap, sid, day, factor):
    """Official ex-dividend: every price from `day` on is scaled by `factor`; the event row carries the official pre-ex close."""
    after = (snap.stock_id == sid) & (snap.trade_date >= day)
    pre = snap.loc[(snap.stock_id == sid) & (snap.trade_date < day), "close_price"].iloc[-1]
    snap = snap.copy()
    snap.loc[after, ["open_price", "high_price", "low_price", "close_price"]] *= factor
    return snap, pd.DataFrame([{"ex_date": day.strftime("%Y-%m-%d"), "stock_id": sid, "pre_ex_close": pre, "event_factor": factor}])


def label_frame(snap, events, policy=POLICY, day=None):
    spec = {"feature_order": ["rsi_z"], "model_inputs": ["rsi"], "corporate_actions": policy, "strategies": {"m": {"label": {"look_ahead_sessions": 3, "target_return": 0.0}}}}
    return model_training.build_training_frame(NativeResearchData(snap, *fundamentals()), [day or DAYS[-6]], spec, "m", data_end=LAST.strftime("%Y-%m-%d"), events=events)


def test_a_verified_official_event_is_adjusted_so_the_label_equals_the_label_without_the_event():
    base = snapshot().assign(dealer_buy=1.0, margin_balance=1000.0)
    snap, events = ex_dividend(base, "1101", DAYS[-5], 0.9)  # first session of the 3-session horizon of DAYS[-6]
    unadjusted = label_frame(base, events.iloc[:0], policy={"label_policy": "RAW_PRICES"})
    raw = label_frame(snap, events.iloc[:0], policy={"label_policy": "RAW_PRICES"})
    adjusted = label_frame(snap, events)
    pick = lambda f: int(f[f.stock_id == "1101"].label.iloc[0])
    assert pick(raw) == 0 and pick(unadjusted) == 1  # the raw ex-dividend drop flips the label
    assert pick(adjusted) == pick(unadjusted)


def test_events_that_official_data_cannot_prove_are_excluded_never_estimated():
    base = snapshot().assign(dealer_buy=1.0, margin_balance=1000.0)
    snap, events = ex_dividend(base, "1101", DAYS[-5], 0.9)  # first session of the 3-session horizon of DAYS[-6]
    wrong_close = events.assign(pre_ex_close=events.pre_ex_close + 1.0)  # does not match the official close of the previous session
    above_one = events.assign(event_factor=1.02)
    for bad in (wrong_close, above_one):
        frame = label_frame(snap, bad)
        assert "1101" not in set(frame.stock_id) and "1102" in set(frame.stock_id)


def test_an_unexplained_jump_is_a_possible_split_or_capital_reduction_and_excludes_the_row():
    base = snapshot().assign(dealer_buy=1.0, margin_balance=1000.0)
    snap, _ = ex_dividend(base, "1101", DAYS[-5], 0.5)  # no official event: possible capital reduction / split
    frame = label_frame(snap, pd.DataFrame(columns=["ex_date", "stock_id", "pre_ex_close", "event_factor"]))
    assert "1101" not in set(frame.stock_id) and "1102" in set(frame.stock_id)
    calm = label_frame(base, pd.DataFrame(columns=["ex_date", "stock_id", "pre_ex_close", "event_factor"]))
    assert {"1101", "1102"} <= set(calm.stock_id)


def train_spec(**extra):
    spec = {"feature_order": ["rsi_z", "bias_z"], "model_inputs": ["rsi", "bias"], "split": {"train": 0.6, "validation": 0.2, "embargo_sessions": 5},
            "strategies": {"m": {"model_name": "smoke", "label": {"look_ahead_sessions": 3, "target_return": 0.01}}}, "acceptance": ACCEPTANCE, "corporate_actions": POLICY,
            "hyperparameters": {"n_estimators": 5, "learning_rate": 0.1, "max_depth": 2, "min_child_weight": 1, "subsample": 1.0, "colsample_bytree": 1.0, "random_state": 42, "eval_metric": "logloss"}}
    spec.update(extra)
    spec["sha256"] = model_training.spec_hash(spec)
    return spec


def approve(spec, tmp_path):
    approval = tmp_path / "approval.json"
    approval.write_text(json.dumps({"spec_sha256": spec["sha256"], "approved_by": "TEST-ONLY"}), encoding="utf-8")
    return approval


NO_EVENTS = pd.DataFrame(columns=["ex_date", "stock_id", "pre_ex_close", "event_factor"])


def test_training_refuses_raw_price_labels_or_missing_official_events(tmp_path):
    raw = train_spec(corporate_actions={"label_policy": "RAW_PRICES"})
    with pytest.raises(model_training.OwnerApprovalError, match="label_policy"):
        model_training.train(provider(), list(DAYS[30:]), raw, "m", approve(raw, tmp_path), tmp_path / "a", events=NO_EVENTS)
    spec = train_spec()
    with pytest.raises(model_training.OwnerApprovalError, match="official events"):
        model_training.train(provider(), list(DAYS[30:]), spec, "m", approve(spec, tmp_path), tmp_path / "b")


# ---- 8. guard 2 enforced before fitting: features of a session must not change when later data is removed ----
class Leaky(NativeResearchData):
    def frame(self, day, required, model_inputs=None):
        frame, note = super().frame(day, required, model_inputs=model_inputs)
        return frame.assign(rsi_z=frame.rsi_z + len(self.snapshot) * 1e-3), note  # depends on how much data exists after `day`


def test_training_stops_before_fitting_when_features_depend_on_future_data(tmp_path, monkeypatch):
    spec = train_spec()
    leaky = Leaky(chips(snapshot()), *fundamentals())
    monkeypatch.setattr("xgboost.XGBClassifier.fit", lambda *a, **k: pytest.fail("fit must not run"))
    with pytest.raises(model_training.FutureLeakageError, match="guard 2"):
        model_training.train(leaky, list(DAYS[30:]), spec, "m", approve(spec, tmp_path), tmp_path / "a", events=NO_EVENTS)
    assert not (tmp_path / "a").exists()


def test_truncated_provider_has_no_bar_after_the_cut_and_the_same_features():
    data = provider()
    day = DAYS[-10]
    cut = data.truncated(day)
    assert cut.snapshot.trade_date.max() == day and data.snapshot.trade_date.max() == LAST
    full, _ = data.frame(day.strftime("%Y-%m-%d"), [], model_inputs=MODEL_INPUTS)
    short, _ = cut.frame(day.strftime("%Y-%m-%d"), [], model_inputs=MODEL_INPUTS)
    pd.testing.assert_frame_equal(full.reset_index(drop=True), short.reset_index(drop=True), rtol=1e-9, atol=1e-12)


def test_the_test_segment_is_recorded_before_it_is_read_so_a_crash_cannot_reopen_it(tmp_path, monkeypatch):
    spec = train_spec()
    approval = approve(spec, tmp_path)

    def crash(*a, **k):
        raise RuntimeError("crash while scoring")
    monkeypatch.setattr(model_training, "evaluate_segment", crash)
    with pytest.raises(RuntimeError, match="crash while scoring"):
        model_training.train(provider(), list(DAYS[30:]), spec, "m", approval, tmp_path / "a", events=NO_EVENTS)
    monkeypatch.undo()
    monkeypatch.setattr(model_training, "TEST_EVALUATION_LEDGER", tmp_path / "test_evaluation_ledger.jsonl")
    with pytest.raises(model_training.OwnerApprovalError, match="already evaluated"):
        model_training.train(provider(), list(DAYS[30:]), spec, "m", approval, tmp_path / "b", events=NO_EVENTS)
