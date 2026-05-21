from __future__ import annotations

from functools import lru_cache
from typing import Any, List, Optional, Tuple
import os

import joblib

from config import Config


DEFAULT_STRATEGY_ALIASES = {"v31"}


def normalize_strategy_name(strategy_name: Optional[str]) -> Optional[str]:
    if not strategy_name:
        return None
    return strategy_name.strip().lower()


def get_model_dir() -> str:
    return os.path.dirname(Config.MODEL_PATH) or os.path.join("ML_Data", "pkl")


def get_model_path(strategy_name: Optional[str] = None) -> str:
    name = normalize_strategy_name(strategy_name)
    if not name or name in DEFAULT_STRATEGY_ALIASES:
        return Config.MODEL_PATH
    return os.path.join(get_model_dir(), f"stock_ai_model_{name}.pkl")


def _dedupe_paths(paths: List[str]) -> List[str]:
    seen = set()
    result = []
    for path in paths:
        if not path:
            continue
        if path in seen:
            continue
        seen.add(path)
        result.append(path)
    return result


@lru_cache(maxsize=16)
def load_model(
    strategy_name: Optional[str] = None,
    allow_fallback: bool = True,
    require_predict_proba: bool = False,
) -> Tuple[Optional[Any], Optional[List[str]], Optional[str], bool]:
    primary_path = get_model_path(strategy_name)

    fallback_paths = []
    if allow_fallback:
        fallback_paths = [
            Config.MODEL_PATH,
            "stock_ai_model.pkl",
            os.path.join(get_model_dir(), "stock_ai_model.pkl"),
        ]

    paths_to_try = _dedupe_paths([primary_path] + fallback_paths)

    for path in paths_to_try:
        if not os.path.exists(path):
            continue
        try:
            data = joblib.load(path)
            if isinstance(data, dict) and "model" in data:
                model = data["model"]
                features = data.get("features")
            else:
                model = data
                features = None

            if require_predict_proba and not hasattr(model, "predict_proba"):
                continue

            used_fallback = path != primary_path
            return model, features, path, used_fallback
        except Exception:
            continue

    return None, None, None, False
