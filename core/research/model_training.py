"""Training data construction and the approval-gated training function for the seven native models.

The training frame is built with the same provider function as recovered Daily (`NativeResearchData.frame` with
`model_inputs`), so a session's features are identical in training and inference. `train` refuses to run without an
owner approval file carrying the approved specification SHA-256, and checks it before any data is read.
"""
from __future__ import annotations

import hashlib
import json
import subprocess
from datetime import datetime
from pathlib import Path

import joblib
import pandas as pd


# one line per (spec_sha256, strategy) whose test segment was scored; the test segment is read once per specification version
TEST_EVALUATION_LEDGER = Path(__file__).resolve().parents[2] / "outputs/model_training/test_evaluation_ledger.jsonl"


class OwnerApprovalError(RuntimeError):
    """Training was attempted without a matching owner approval of the specification."""


class FutureLeakageError(RuntimeError):
    """A session's features changed when later data was removed (spec 9.1 guard 2); nothing was fitted."""


RAW_PRICES = "RAW_PRICES"  # audit only; train() refuses it
VERIFIED_ONLY = "OFFICIAL_FACTORS_VERIFIED_ONLY"  # OD-T1 (2026-10-24): adjust only officially proven events, exclude the rest
DAILY_LIMIT_JUMP = 0.105  # 10% TWSE daily limit plus tick rounding; a larger close-to-close move without an official event is a possible split / capital reduction


def corporate_action_adjustment(events: pd.DataFrame, close: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Cumulative official factor per (session, stock) and the sessions whose price move cannot be explained by a proven event.

    An event is proven when its stock has bars, its pre-ex close equals the official close of the last session before the ex-date
    (|diff| <= 0.005) and 0 < factor <= 1; it applies on the first session on or after the ex-date (an ex-date on a market closure
    applies on the next open session). Every other event, and every move beyond the daily limit with no event, marks its session
    as unexplained: rows whose label horizon covers it are excluded, never estimated.
    """
    factor = pd.DataFrame(1.0, index=close.index, columns=close.columns)
    unexplained = pd.DataFrame(False, index=close.index, columns=close.columns)
    has_event = unexplained.copy()
    for row in events.itertuples():
        sid, i = str(row.stock_id), close.index.searchsorted(pd.Timestamp(row.ex_date))
        if sid not in close.columns or i >= len(close.index):
            continue  # not in the training universe or after the data end
        j = close.columns.get_loc(sid)
        has_event.iat[i, j] = True
        previous = close.iloc[:i, j].dropna()
        proven = len(previous) > 0 and abs(previous.iloc[-1] - float(row.pre_ex_close)) <= 0.005 and 0 < float(row.event_factor) <= 1
        if proven:
            factor.iat[i, j] *= float(row.event_factor)
        else:
            unexplained.iat[i, j] = True
    move = (close / close.ffill().shift(1) - 1).abs()  # ffill: a resumption after a suspension is compared with the last close
    unexplained |= (move > DAILY_LIMIT_JUMP) & ~has_event
    return factor.cumprod(), unexplained



def spec_hash(spec: dict) -> str:
    """SHA-256 of the specification without its own `sha256` field (keys sorted)."""
    body = {k: v for k, v in spec.items() if k != "sha256"}
    return hashlib.sha256(json.dumps(body, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()


def require_approval(spec: dict, approval_path) -> dict:
    path = Path(approval_path)
    if not path.is_file():
        raise OwnerApprovalError(f"approval file missing: {path.name}; training needs the owner's signed approval of the specification")
    if spec_hash(spec) != spec.get("sha256"):
        raise OwnerApprovalError("specification content does not match its recorded sha256")
    approval = json.loads(path.read_text(encoding="utf-8"))
    if approval.get("spec_sha256") != spec["sha256"]:
        raise OwnerApprovalError("approval spec_sha256 does not match the specification being trained")
    if not approval.get("approved_by"):
        raise OwnerApprovalError("approval has no approved_by")
    return approval


def split_sessions(sessions, purge: int, embargo: int, ratios=(0.6, 0.2)) -> dict:
    """Train / validation / test sessions; `purge` sessions before and `embargo` after each cut keep label horizons from crossing it."""
    n = len(sessions)
    a, b = int(n * ratios[0]), int(n * (ratios[0] + ratios[1]))
    return {"train": sessions[:a - purge], "validation": sessions[a + embargo:b - purge], "test": sessions[b + embargo:]}


def build_training_frame(provider, sessions, spec: dict, strategy_key: str, data_end, events: pd.DataFrame | None = None) -> pd.DataFrame:
    """Features of every session (same frame as recovered Daily) plus the label of the strategy; rows without a full horizon are dropped."""
    label = spec["strategies"][strategy_key]["label"]
    horizon, target = int(label["look_ahead_sessions"]), float(label["target_return"])
    columns = list(spec["feature_order"])
    bars = provider.snapshot[provider.snapshot.trade_date <= pd.Timestamp(data_end)]
    high = bars.pivot(index="trade_date", columns="stock_id", values="high_price").sort_index()
    close = bars.pivot(index="trade_date", columns="stock_id", values="close_price").sort_index()
    excluded = pd.DataFrame(False, index=close.index, columns=close.columns)
    if spec.get("corporate_actions", {}).get("label_policy", RAW_PRICES) == VERIFIED_ONLY:
        if events is None:
            raise ValueError("label_policy OFFICIAL_FACTORS_VERIFIED_ONLY needs the official events table")
        cumulative, unexplained = corporate_action_adjustment(events, close)
        high, close = high / cumulative, close / cumulative  # prices in the terms before the first event; labels are ratios, so the level cancels
        excluded = unexplained.astype(float).iloc[::-1].rolling(horizon, min_periods=1).max().iloc[::-1].shift(-1).fillna(0) > 0
    # max of the next `horizon` sessions' highs (not today's), NaN unless the stock has a bar in every one of them (full horizon)
    future = high.iloc[::-1].rolling(horizon, min_periods=horizon).max().iloc[::-1].shift(-1)
    position = {day: i for i, day in enumerate(high.index)}
    frames = []
    for session in sessions:
        day = pd.Timestamp(session)
        if day not in position or position[day] + horizon > len(high.index) - 1:
            continue  # the label would need sessions after the data end
        frame, _note = provider.frame(day.strftime("%Y-%m-%d"), [], model_inputs=spec["model_inputs"])
        if frame.empty:
            continue
        part = frame[["stock_id", *columns]].copy()
        part.insert(1, "trade_date", day)
        gain = (future.loc[day] / close.loc[day] - 1).reindex(part.stock_id).to_numpy()
        part["label"] = (gain > target).astype(int)
        part["future_max_return"] = gain  # realised max return of the horizon; read only by the acceptance Rank IC, never a feature
        frames.append(part[~pd.isna(gain) & ~excluded.loc[day].reindex(part.stock_id).fillna(False).to_numpy(bool)])
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame(columns=["stock_id", "trade_date", *columns, "label", "future_max_return"])


def _metric(value, minimum, reason=None) -> dict:
    """Undefined metrics carry value=None and why (never 0 or infinity)."""
    if value is None:
        return {"value": None, "min": minimum, "passed": None, "reason": reason}
    return {"value": float(value), "min": minimum, "passed": bool(value >= minimum), "reason": None}


def evaluate_segment(frame: pd.DataFrame, scores, acceptance: dict, seed: int) -> dict:
    """Specification 9.1 on one segment: per-session metrics averaged over the segment, plus the permuted-label AUC guard."""
    from sklearn.metrics import roc_auc_score

    data = frame[["trade_date", "label", "future_max_return"]].assign(score=list(scores)).reset_index(drop=True)
    lifts, ics = {}, []
    for day, group in data.groupby("trade_date"):
        base = group.label.mean()
        if base > 0:  # a session without positives has no defined lift
            top = group.nlargest(max(len(group) // 10, 1), "score")
            lifts[day] = top.label.mean() / base
        if group.score.nunique() > 1 and group.future_max_return.nunique() > 1:
            ics.append(group.score.corr(group.future_max_return, method="spearman"))
    limits = {k: v["min"] for k, v in acceptance["metrics"].items()}
    two_classes = data.label.nunique() == 2
    monthly = pd.Series(lifts).groupby(lambda d: (d.year, d.month)).mean() if lifts else None
    permuted = roc_auc_score(data.label.sample(frac=1, random_state=seed).to_numpy(), data.score) if two_classes else None
    metrics = {"top_decile_lift_mean": _metric(sum(lifts.values()) / len(lifts) if lifts else None, limits["top_decile_lift_mean"], "no session has a positive label"),
               "monthly_lift_positive_share": _metric((monthly > 1.0).mean() if monthly is not None else None, limits["monthly_lift_positive_share"], "no session has a positive label"),
               "pooled_roc_auc": _metric(roc_auc_score(data.label, data.score) if two_classes else None, limits["pooled_roc_auc"], "the segment has a single label class"),
               "mean_rank_ic": _metric(sum(ics) / len(ics) if ics else None, limits["mean_rank_ic"], "no session has varying scores and returns")}
    guards = {"permuted_label_auc": {"value": None if permuted is None else float(permuted), "range": [0.48, 0.52],
                                     "passed": None if permuted is None else bool(0.48 <= permuted <= 0.52)}}
    checks = [m["passed"] for m in metrics.values()] + [guards["permuted_label_auc"]["passed"]]
    status = "NOT_EVALUABLE" if None in checks else "PASS" if all(checks) else "FAIL"
    return {"rows": len(data), "sessions": int(data.trade_date.nunique()), "metrics": metrics, "guards": guards, "status": status}


def _commit() -> str | None:
    try:
        return subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True, check=True).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return None


def future_independence(provider, sessions, spec: dict) -> list[str]:
    """Spec 9.1 guard 2 before fitting: sessions whose features change when every later bar is removed (rtol 1e-9, atol 1e-12).
    ponytail: first, middle and last session of each segment; a full scan is one frame build per session."""
    columns = ["stock_id", *spec["feature_order"]]
    failed = []
    for day in sessions:
        stamp = str(pd.Timestamp(day).date())
        full, _ = provider.frame(stamp, [], model_inputs=spec["model_inputs"])
        cut, _ = provider.truncated(stamp).frame(stamp, [], model_inputs=spec["model_inputs"])
        try:
            pd.testing.assert_frame_equal(full[columns].reset_index(drop=True), cut[columns].reset_index(drop=True), rtol=1e-9, atol=1e-12, check_dtype=False)
        except AssertionError:
            failed.append(stamp)
    return failed


def _accept(model, provider, sessions, parts, spec, strategy_key, data_end, seed, data_manifest_sha256, events) -> dict:
    """Validation and test scored once each with the fitted model, plus the split and feature-list guards (spec 9.1; guard 2 runs before fitting)."""
    columns = list(spec["feature_order"])
    segments = {}
    for name in ("validation", "test"):
        frame = build_training_frame(provider, parts[name], spec, strategy_key, data_end, events)
        segments[name] = evaluate_segment(frame, model.predict_proba(frame[columns])[:, 1], spec["acceptance"], seed) if len(frame) else {"status": "NOT_EVALUABLE", "reason": "empty segment"}
    position = {str(day): i for i, day in enumerate(sessions)}  # all sessions, including the purged and embargoed ones
    horizon = int(spec["strategies"][strategy_key]["label"]["look_ahead_sessions"])
    guards = {"sessions_disjoint": not (set(parts["train"]) & set(parts["validation"]) or set(parts["validation"]) & set(parts["test"]) or set(parts["train"]) & set(parts["test"])),
              "train_horizon_ends_before_validation": position[str(parts["train"][-1])] + horizon < position[str(parts["validation"][0])],
              "validation_horizon_ends_before_test": position[str(parts["validation"][-1])] + horizon < position[str(parts["test"][0])],
              "feature_list_equals_spec": list(model.feature_names_in_) == columns,
              # guard 6: None (undefined) until the owner registers the manifest hash in the specification
              "data_manifest_matches_registered": None if not spec.get("data_manifest_sha256") else data_manifest_sha256 == spec["data_manifest_sha256"]}
    statuses = [s["status"] for s in segments.values()]
    status = ("FAIL" if False in guards.values() or "FAIL" in statuses else "NOT_EVALUABLE" if None in guards.values() or "NOT_EVALUABLE" in statuses else "PASS")
    return {"segments": segments, "guards": guards, "test_evaluations": 1, "status": status}


def train(provider, sessions, spec: dict, strategy_key: str, approval_path, output_dir, data_manifest_sha256: str | None = None, events: pd.DataFrame | None = None) -> Path:
    """Fit one model under the approved specification. Nothing is read before the approval check passes."""
    approval = require_approval(spec, approval_path)
    if spec.get("corporate_actions", {}).get("label_policy") != VERIFIED_ONLY:
        raise OwnerApprovalError(f"label_policy must be {VERIFIED_ONLY} (OD-T1); raw-price labels are not trainable")
    if events is None:
        raise OwnerApprovalError("official events (TWT49U) are required to build the OD-T1 labels")
    from xgboost import XGBClassifier

    sessions = list(sessions)
    horizon = int(spec["strategies"][strategy_key]["label"]["look_ahead_sessions"])
    parts = split_sessions(sessions, purge=horizon, embargo=spec["split"]["embargo_sessions"], ratios=(spec["split"]["train"], spec["split"]["validation"]))
    data_end = sessions[-1]
    checked = [part[i] for part in parts.values() if part for i in (0, len(part) // 2, -1)]
    leaking = future_independence(provider, checked, spec)
    if leaking:
        raise FutureLeakageError(f"guard 2 failed: features change when later data is removed on {leaking}; nothing was fitted")
    train_frame = build_training_frame(provider, parts["train"], spec, strategy_key, data_end, events)
    features, y = train_frame[list(spec["feature_order"])], train_frame["label"]
    hp = spec["hyperparameters"]
    model = XGBClassifier(n_estimators=hp["n_estimators"], learning_rate=hp["learning_rate"], max_depth=hp["max_depth"], min_child_weight=hp["min_child_weight"],
                          subsample=hp["subsample"], colsample_bytree=hp["colsample_bytree"], random_state=hp["random_state"], eval_metric=hp["eval_metric"],
                          scale_pos_weight=(len(y) - y.sum()) / max(y.sum(), 1) * 0.5, n_jobs=-1)
    model.fit(features, y)
    name = spec["strategies"][strategy_key]["model_name"]
    path = Path(output_dir) / f"stock_ai_model_{name}.pkl"
    rejected = path.with_name(f"stock_ai_model_{name}_rejected.pkl")
    if path.exists() or rejected.exists():  # never overwrite a model (or an old one) in place
        raise OwnerApprovalError(f"model already exists: {path.name}; a new model needs a new output folder and specification version")
    key = json.dumps({"spec_sha256": spec["sha256"], "strategy": strategy_key}, sort_keys=True)
    ledger = Path(TEST_EVALUATION_LEDGER)
    if ledger.is_file() and key in ledger.read_text(encoding="utf-8").splitlines():
        raise OwnerApprovalError(f"test segment already evaluated for this specification and strategy ({ledger.name}); a new attempt needs a new specification version")
    ledger.parent.mkdir(parents=True, exist_ok=True)
    with ledger.open("a", encoding="utf-8") as handle:  # recorded before the test segment is read: a crash cannot reopen it
        handle.write(key + "\n")
    acceptance = _accept(model, provider, sessions, parts, spec, strategy_key, data_end, hp["random_state"], data_manifest_sha256, events)
    if acceptance["status"] != "PASS":
        path = rejected  # spec 9.1: rejected; kept for audit under a name no loader reads
    path.parent.mkdir(parents=True, exist_ok=True)
    joblib.dump({"model": model, "features": list(spec["feature_order"]), "strategy": name, "version": f"{name}-TS2-{spec['sha256'][:8]}",
                 "training_date": datetime.now().strftime("%Y-%m-%d %H:%M:%S"), "training_data_start": str(parts["train"][0]), "training_data_end": str(data_end),
                 "split": {k: [str(v[0]), str(v[-1]), len(v)] for k, v in parts.items() if v}, "spec_sha256": spec["sha256"], "approved_by": approval["approved_by"],
                 "data_manifest_sha256": data_manifest_sha256, "code_commit": _commit(), "look_ahead_days": horizon,
                 "target_return": spec["strategies"][strategy_key]["label"]["target_return"], "label_policy": VERIFIED_ONLY,
                 "future_independence_sessions_checked": [str(d) for d in checked], "acceptance": acceptance}, path)
    return path
