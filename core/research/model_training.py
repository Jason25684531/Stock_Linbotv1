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


class OwnerApprovalError(RuntimeError):
    """Training was attempted without a matching owner approval of the specification."""


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


def build_training_frame(provider, sessions, spec: dict, strategy_key: str, data_end) -> pd.DataFrame:
    """Features of every session (same frame as recovered Daily) plus the label of the strategy; rows without a full horizon are dropped."""
    label = spec["strategies"][strategy_key]["label"]
    horizon, target = int(label["look_ahead_sessions"]), float(label["target_return"])
    columns = list(spec["feature_order"])
    bars = provider.snapshot[provider.snapshot.trade_date <= pd.Timestamp(data_end)]
    high = bars.pivot(index="trade_date", columns="stock_id", values="high_price").sort_index()
    close = bars.pivot(index="trade_date", columns="stock_id", values="close_price").sort_index()
    # max of the next `horizon` sessions' highs (not today's), NaN when the horizon is incomplete or the stock has no bar in it
    future = high.iloc[::-1].rolling(horizon, min_periods=1).max().iloc[::-1].shift(-1)
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
        frames.append(part[~pd.isna(gain)])
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame(columns=["stock_id", "trade_date", *columns, "label"])


def _commit() -> str | None:
    try:
        return subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True, check=True).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return None


def train(provider, sessions, spec: dict, strategy_key: str, approval_path, output_dir, data_manifest_sha256: str | None = None) -> Path:
    """Fit one model under the approved specification. Nothing is read before the approval check passes."""
    approval = require_approval(spec, approval_path)
    from xgboost import XGBClassifier

    sessions = list(sessions)
    horizon = int(spec["strategies"][strategy_key]["label"]["look_ahead_sessions"])
    parts = split_sessions(sessions, purge=horizon, embargo=spec["split"]["embargo_sessions"], ratios=(spec["split"]["train"], spec["split"]["validation"]))
    data_end = sessions[-1]
    train_frame = build_training_frame(provider, parts["train"], spec, strategy_key, data_end)
    features, y = train_frame[list(spec["feature_order"])], train_frame["label"]
    hp = spec["hyperparameters"]
    model = XGBClassifier(n_estimators=hp["n_estimators"], learning_rate=hp["learning_rate"], max_depth=hp["max_depth"], min_child_weight=hp["min_child_weight"],
                          subsample=hp["subsample"], colsample_bytree=hp["colsample_bytree"], random_state=hp["random_state"], eval_metric=hp["eval_metric"],
                          scale_pos_weight=(len(y) - y.sum()) / max(y.sum(), 1) * 0.5, n_jobs=-1)
    model.fit(features, y)
    name = spec["strategies"][strategy_key]["model_name"]
    path = Path(output_dir) / f"stock_ai_model_{name}.pkl"
    joblib.dump({"model": model, "features": list(spec["feature_order"]), "strategy": name, "version": f"{name}-TS2-{spec['sha256'][:8]}",
                 "training_date": datetime.now().strftime("%Y-%m-%d %H:%M:%S"), "training_data_start": str(parts["train"][0]), "training_data_end": str(data_end),
                 "split": {k: [str(v[0]), str(v[-1]), len(v)] for k, v in parts.items() if v}, "spec_sha256": spec["sha256"], "approved_by": approval["approved_by"],
                 "data_manifest_sha256": data_manifest_sha256, "code_commit": _commit(), "look_ahead_days": horizon,
                 "target_return": spec["strategies"][strategy_key]["label"]["target_return"], "acceptance": "NOT_EVALUATED"}, path)
    return path
