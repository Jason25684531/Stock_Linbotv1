"""Strict model contract for research backtests: own model only, no fallback, no feature zero-filling."""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

import joblib
import pandas as pd

from core.model_utils import get_model_path

# Raw inputs behind features that build_multi_factor_matrix derives itself.
DERIVED_FROM = {
    "institutional_net_buy": ("foreign_buy", "trust_buy", "dealer_buy"),
    "institutional_consec_buy_days": ("foreign_consec_days", "trust_consec_days", "dealer_buy"),
    "dealer_consec_days": ("dealer_buy",),
    "chip_score": ("foreign_ratio", "trust_ratio", "dealer_ratio", "margin_change_pct"),
}


class ModelContractError(ValueError):
    """The model cannot be shown to run on research inputs; the ML backtest is BLOCKED."""


@dataclass(frozen=True)
class ModelContract:
    model: object
    features: list[str]
    required_raw: list[str]
    identity: dict


def load_model_contract(strategy_name: str, available_inputs, window_start: str, unavailable=frozenset()) -> ModelContract:
    path = Path(get_model_path(strategy_name))  # primary model only; the generic fallback is never used
    if not path.is_file():
        raise ModelContractError(f"MODEL_MISSING: {path.name} (no fallback to another model)")
    data = joblib.load(path)
    if not isinstance(data, dict) or "model" not in data or "features" not in data or "training_date" not in data:
        raise ModelContractError(f"MODEL_METADATA_INCOMPLETE: {path.name} needs model, features and training_date")
    model, features = data["model"], list(data["features"])
    importance = getattr(model, "feature_importances_", None)
    if importance is None or len(importance) != len(features):
        raise ModelContractError(f"FEATURE_IMPORTANCE_UNAVAILABLE: {path.name}")
    names = getattr(model, "feature_names_in_", None)
    if names is not None and list(names) != features:
        raise ModelContractError(f"FEATURE_ORDER_MISMATCH: {path.name}")
    # Features the model never splits on cannot influence a prediction; the rest must be computable.
    relevant = [f for f, w in zip(features, importance) if w > 0]
    raw = sorted({r for f in relevant for r in DERIVED_FROM.get(f.removesuffix("_z"), (f.removesuffix("_z"),))})
    missing = [r for r in raw if r not in available_inputs]
    if missing:
        raise ModelContractError(f"FEATURE_UNAVAILABLE: {missing} (importance > 0)")
    empty = sorted(set(raw) & set(unavailable))
    if empty:
        raise ModelContractError(f"FEATURE_UNAVAILABLE_AT_SOURCE: {empty} are NULL in the window but feed features with importance > 0; "
                                 "training and Daily may have seen different values, so no faithful ranking exists")
    trained = pd.Timestamp(data["training_date"])
    if pd.Timestamp(window_start) <= trained.normalize():
        raise ModelContractError(f"WINDOW_NOT_AFTER_TRAINING: window starts {window_start}, model trained {trained:%Y-%m-%d}; "
                                 "training-data cutoff is not recorded, so earlier dates may overlap training data")
    identity = {"path": path.as_posix(), "sha256": hashlib.sha256(path.read_bytes()).hexdigest(), "version": data.get("version"),
                "training_date": str(data["training_date"]), "feature_order": features,
                "features_ignored_by_model": [f for f, w in zip(features, importance) if w <= 0],
                "features_used": relevant, "raw_inputs_required": raw}
    return ModelContract(model, features, raw, identity)
