"""Long-only D5 target-weight contract."""

import pandas as pd


def build_target_weights(scores: pd.DataFrame, universe: pd.DataFrame, *, config_id: str, top_n: int, stock_weighting: str, combination_method: str | None = None, rebalance_days: int | None = None, source_handoff_id: str | None = None) -> pd.DataFrame:
    frame = scores.merge(universe[["asof_date", "asset_id", "member", "is_tradable_t1", "execution_date"]], on=["asof_date", "asset_id"], how="inner")
    frame = frame.loc[frame.member.fillna(False) & frame.is_tradable_t1.fillna(False) & frame.composite_score.notna()].copy()
    frame["asof_date"], frame["execution_date"] = pd.to_datetime(frame["asof_date"]), pd.to_datetime(frame["execution_date"])
    selected = frame.sort_values(["asof_date", "composite_score", "asset_id"], ascending=[True, False, True], kind="stable").groupby("asof_date", group_keys=False).head(top_n).copy()
    if stock_weighting == "equal":
        selected["target_weight"] = 1 / selected.groupby("asof_date")["asset_id"].transform("size")
    elif stock_weighting == "score_weighted":
        selected["target_weight"] = selected["composite_score"] / selected.groupby("asof_date")["composite_score"].transform("sum")
    else:
        raise ValueError(f"unsupported stock weighting: {stock_weighting}")
    selected["config_id"], selected["combination_method"] = config_id, combination_method
    selected["top_n"], selected["rebalance_days"] = top_n, rebalance_days
    selected["stock_weighting"], selected["source_handoff_id"] = stock_weighting, source_handoff_id
    selected["selected_count"] = selected.groupby("asof_date")["asset_id"].transform("size")
    if (selected["target_weight"] < 0).any() or (selected.groupby("asof_date")["target_weight"].sum() > 1.0 + 1e-12).any():
        raise ValueError("target weights violate long-only exposure contract")
    return selected[["asof_date", "execution_date", "asset_id", "config_id", "target_weight", "combination_method", "top_n", "rebalance_days", "stock_weighting", "selected_count", "source_handoff_id"]]
