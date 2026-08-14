"""D5 composite-factor weights and cross-sectional scores."""

import numpy as np
import pandas as pd


def build_factor_weights(candidates: pd.DataFrame, correlation: pd.DataFrame, method: str) -> pd.DataFrame:
    """Build auditable quality weights; UNKNOWN redundancy remains neutral."""
    ids = candidates["factor_id"].tolist()
    quality_col = {"equal": None, "ic": "mean_ic", "icir": "icir", "redundancy_adjusted": "mean_ic"}.get(method)
    if method not in {"equal", "ic", "icir", "redundancy_adjusted"}:
        raise ValueError(f"unsupported combination method: {method}")
    quality = np.ones(len(ids)) if quality_col is None else candidates[quality_col].abs().fillna(0).to_numpy(float)
    if not quality.sum() > 0:
        quality = np.ones(len(ids))
    base = quality / quality.sum()
    penalties, unknown = [], []
    for factor_id in ids:
        peers = correlation.loc[factor_id, [item for item in ids if item != factor_id]]
        is_unknown = peers.isna().any()
        unknown.append(is_unknown)
        penalties.append(1.0 if is_unknown else 1.0 / (1.0 + peers.clip(lower=0).sum()))
    penalties, raw = np.asarray(penalties), base * np.asarray(penalties)
    if method == "redundancy_adjusted" and any(unknown):
        final, known = base.copy(), ~np.asarray(unknown)
        if known.any():
            remaining = 1.0 - final[~known].sum()
            final[known] = raw[known] / raw[known].sum() * remaining if raw[known].sum() else base[known] / base[known].sum() * remaining
    else:
        final = raw / raw.sum()
    return pd.DataFrame({"combination_method": method, "factor_id": ids, "base_quality_weight": base, "redundancy_adjustment": penalties, "final_factor_weight": final, "redundancy_status": ["UNKNOWN_NEUTRAL" if item else "KNOWN" for item in unknown]})


def build_composite_scores(ranks: pd.DataFrame, weights: pd.DataFrame, method: str) -> pd.DataFrame:
    frame = ranks.merge(weights[["factor_id", "final_factor_weight"]], on="factor_id", how="inner")
    frame = frame.loc[frame["direction_adjusted_rank"].notna()].copy()
    group = frame.groupby(["asof_date", "asset_id"])["final_factor_weight"]
    frame["available_weight"] = frame["final_factor_weight"] / group.transform("sum")
    frame["part"] = frame["direction_adjusted_rank"] * frame["available_weight"]
    result = frame.groupby(["asof_date", "asset_id"], as_index=False).agg(composite_score=("part", "sum"), factor_count=("factor_id", "nunique"))
    result["combination_method"] = method
    result["composite_score"] = result["composite_score"].replace([np.inf, -np.inf], np.nan)
    if result.duplicated(["asof_date", "asset_id", "combination_method"]).any():
        raise ValueError("duplicate composite canonical key")
    return result[["asof_date", "asset_id", "combination_method", "composite_score", "factor_count"]]
