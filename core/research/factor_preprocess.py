"""Universe-scoped factor preprocessing for research datasets."""

from collections.abc import Mapping

import pandas as pd

from core.research.factor_operators import rank_cs, winsorize_cs


def preprocess_factors(
    factors: pd.DataFrame, membership: pd.DataFrame, directions: Mapping[str, int], *, lower: float = 0.01, upper: float = 0.99
) -> pd.DataFrame:
    """Retain raw factor values and add same-day, member-only preprocessing."""

    result = factors.copy()
    result["asof_date"] = pd.to_datetime(result["asof_date"])
    members = membership.rename(columns={"trade_date": "asof_date", "stock_id": "asset_id"}).copy()
    members["asof_date"] = pd.to_datetime(members["asof_date"])
    result = result.merge(members.loc[:, ["asof_date", "asset_id", "member"]], on=["asof_date", "asset_id"], how="left")
    result["member"] = result["member"].fillna(False)
    result["winsorized_value"] = float("nan")
    result["rank_value"] = float("nan")
    for (_, _), group in result.groupby(["asof_date", "factor_id"], sort=False):
        member_rows = group.index[group["member"]]
        values = pd.to_numeric(result.loc[member_rows, "raw_value"], errors="coerce")
        wide = pd.DataFrame([values.to_numpy()], columns=member_rows)
        clipped = winsorize_cs(wide, lower, upper).iloc[0]
        ranks = rank_cs(clipped.to_frame().T).iloc[0]
        count = int(clipped.notna().sum())
        result.loc[member_rows, "winsorized_value"] = clipped.to_numpy()
        if count >= 2:
            result.loc[member_rows, "rank_value"] = ((ranks - 0.5) / count).to_numpy()
    result["direction"] = result["factor_id"].map(directions).fillna(0).astype(int)
    result["direction_adjusted_rank"] = result["rank_value"]
    negative = result["direction"].eq(-1)
    result.loc[negative, "direction_adjusted_rank"] = 1 - result.loc[negative, "rank_value"]
    result.loc[result["direction"].eq(0), "direction_adjusted_rank"] = float("nan")
    return result
