"""Research-only candidate definitions for the Day 2 validation run.

The module deliberately keeps the adapter surface small: every candidate is a
named :class:`CandidateSpec`, and every value is calculated on a date x asset
panel.  The immutable D3 files remain the source of labels and baseline
factors; Alpha101/191 source files are only formula references.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Mapping

import numpy as np
import pandas as pd

from core.research.factor_evaluation import EvaluationPolicy, policy_config


D3_RUN_ID = "d3_full_20230103_20260728"
TRAIN_START = pd.Timestamp("2023-01-03")
TRAIN_END = pd.Timestamp("2025-06-30")
VALIDATION_START = pd.Timestamp("2025-07-01")
VALIDATION_END = pd.Timestamp("2025-12-31")
STRICT_OOS_START = pd.Timestamp("2026-01-01")
STRICT_OOS_END = pd.Timestamp("2026-07-28")

UNIV_RESEARCH_V1 = "UNIV_RESEARCH_V1"
UNIV_RUNTIME_V1 = "UNIV_RUNTIME_V1"

# These are intentionally values, not a second policy.  Alpha eligibility
# always uses the imported D4 EvaluationPolicy below.
D4_POLICY = EvaluationPolicy()
FACTOR_SCORE_WEIGHTS = {
    "predictive_strength": 0.30,
    "economic_spread": 0.25,
    "stability": 0.20,
    "coverage": 0.10,
    "turnover": 0.05,
    "implementation_confidence": 0.05,
    "redundancy_penalty": -0.05,
}


@dataclass(frozen=True)
class CandidateSpec:
    candidate_id: str
    normalized_name: str
    family: str
    role: str
    tier: str
    universe_id: str = UNIV_RESEARCH_V1
    direction: int = 0
    lookback: int = 1
    source_id: str = ""
    kind: str = "existing"
    implementation_status: str = "IMPLEMENTATION_OK"
    pit_status: str = "VERIFIED"
    vwap_definition: str = "NOT_APPLICABLE"
    validation_path: str = "Alpha"
    lineage: str = "day2_adapter"
    notes: str = ""

    def record(self) -> dict[str, object]:
        return asdict(self)


def frozen_parameters(policy: EvaluationPolicy = D4_POLICY) -> dict[str, object]:
    """Return the exact pre-run parameters that are written to the manifest."""

    return {
        "d3_run_id": D3_RUN_ID,
        "date_split": {
            "train_start": str(TRAIN_START.date()),
            "train_end": str(TRAIN_END.date()),
            "validation_start": str(VALIDATION_START.date()),
            "validation_end": str(VALIDATION_END.date()),
            "strict_oos_start": str(STRICT_OOS_START.date()),
            "strict_oos_end": str(STRICT_OOS_END.date()),
        },
        "universe_ids": {
            "primary": UNIV_RESEARCH_V1,
            "runtime_comparison": UNIV_RUNTIME_V1,
        },
        "evaluation_policy": policy_config(policy),
        "factor_score_weights": FACTOR_SCORE_WEIGHTS,
        "pit_policy": {
            "price_volume": "T+1",
            "monthly_revenue": "announcement_or_next_month_10th_plus_1",
            "financial_statements": "statutory_deadline_plus_1",
            "institutional_flow": "T+1",
        },
        "strict_oos_rule": "read once after all pre_oos decisions are frozen",
    }


def _master_rows(master: pd.DataFrame | Path | str) -> pd.DataFrame:
    if isinstance(master, (str, Path)):
        master = pd.read_csv(master)
    required = {"candidate_id", "normalized_name", "primary_family", "role", "source"}
    missing = required - set(master.columns)
    if missing:
        raise ValueError(f"candidate master missing columns: {sorted(missing)}")
    return master.copy()


def _from_master(row: Mapping[str, object], *, tier: str, kind: str = "existing", **kwargs: object) -> CandidateSpec:
    family = str(row.get("primary_family") or "Other")
    role = str(row.get("role") or "Alpha")
    candidate_id = row.get("candidate_id") or getattr(row, "name", "")
    values = {
        "candidate_id": str(candidate_id),
        "normalized_name": str(row["normalized_name"]),
        "family": family,
        "role": role,
        "tier": tier,
        "source_id": str(candidate_id),
        "kind": kind,
        "lookback": _lookback(row.get("lookback_window")),
        "direction": _direction(row.get("direction")),
        "implementation_status": "IMPLEMENTATION_OK",
        "notes": str(row.get("notes") or ""),
    }
    values.update(kwargs)
    return CandidateSpec(**values)


def _lookback(value: object) -> int:
    try:
        return max(1, int(float(str(value).split("-")[0])))
    except (TypeError, ValueError):
        return 1


def _direction(value: object) -> int:
    try:
        parsed = int(float(value))
        return parsed if parsed in {-1, 0, 1} else 0
    except (TypeError, ValueError):
        return 0


def candidate_specs(master: pd.DataFrame | Path | str) -> list[CandidateSpec]:
    """Build the frozen, unique Day 2 validation universe.

    The 252-day near-high is shared by Tier 1 and the five-factor baseline;
    keeping one row avoids double counting it in the scoreboard.
    """

    data = _master_rows(master).set_index("candidate_id")

    def existing(candidate_id: str, tier: str, **kwargs: object) -> CandidateSpec:
        if candidate_id not in data.index:
            raise ValueError(f"Day 1 candidate missing: {candidate_id}")
        return _from_master(data.loc[candidate_id], tier=tier, **kwargs)

    specs = [
        existing("RS_008", "TIER1", kind="volume_ratio", direction=0, notes="raw direction candidate"),
        CandidateSpec("RS_008_POSITIVE", "volume_ratio_20d_positive", "H", "Alpha", "TIER1", direction=1, lookback=20, source_id="RS_008", kind="volume_ratio", notes="+1 direction"),
        CandidateSpec("RS_008_NEGATIVE", "volume_ratio_20d_negative", "H", "Alpha", "TIER1", direction=-1, lookback=20, source_id="RS_008", kind="volume_ratio", notes="-1 direction"),
        existing("RS_012", "TIER1", kind="natr"),
        existing("RS_011", "TIER1", kind="realized_vol"),
        existing("RT_001", "TIER1", role="Filter", kind="universe_filter", validation_path="Filter", universe_id=UNIV_RUNTIME_V1, pit_status="VERIFIED"),
        CandidateSpec("RS_004_NEAR_HIGH_60D", "near_high_60d", "D", "Alpha", "TIER1", direction=1, lookback=60, source_id="RS_004", kind="near_high"),
        CandidateSpec("RS_004_NEAR_HIGH_120D", "near_high_120d", "D", "Alpha", "TIER1", direction=1, lookback=120, source_id="RS_004", kind="near_high"),
        CandidateSpec("RS_004_NEAR_HIGH_252D", "near_high_252d", "D", "Alpha", "TIER1|BASELINE", direction=1, lookback=252, source_id="RS_004", kind="existing"),
        CandidateSpec("T2_REVENUE_YOY", "revenue_yoy", "A", "Alpha", "TIER2", lookback=12, source_id="RT_030/RT_041", kind="revenue_yoy", pit_status="PIT_RISK", notes="monthly data; announcement date unavailable unless DB provides it"),
        CandidateSpec("T2_OPERATING_PROFIT_MARGIN", "operating_profit_margin", "C", "Alpha", "TIER2", lookback=4, source_id="RT_040/RT_072", kind="operating_profit_margin", pit_status="PIT_RISK"),
        CandidateSpec("T2_EPS_LEVEL", "eps_level", "C", "Alpha", "TIER2", lookback=4, source_id="financial_statements", kind="eps_level", pit_status="PIT_RISK"),
        CandidateSpec("T2_EPS_POSITIVE", "eps_positive", "C", "Filter", "TIER2", lookback=4, source_id="financial_statements", kind="eps_positive", pit_status="PIT_RISK", validation_path="Filter"),
        CandidateSpec("T2_FOREIGN_NET_BUY", "foreign_net_buy", "I", "Alpha", "TIER2", direction=1, source_id="chip", kind="foreign_net_buy", pit_status="PIT_RISK"),
        CandidateSpec("T2_FOREIGN_BUY_STREAK", "foreign_buy_streak", "I", "Alpha", "TIER2", direction=1, source_id="chip", kind="foreign_buy_streak", pit_status="PIT_RISK"),
        CandidateSpec("T2_TRUST_NET_BUY", "trust_net_buy", "I", "Alpha", "TIER2", direction=1, source_id="chip", kind="trust_net_buy", pit_status="PIT_RISK"),
        CandidateSpec("T2_TRUST_BUY_STREAK", "trust_buy_streak", "I", "Alpha", "TIER2", direction=1, source_id="chip", kind="trust_buy_streak", pit_status="PIT_RISK"),
        existing("RS_001", "BASELINE"),
        existing("RS_002", "BASELINE"),
        existing("RS_003", "BASELINE"),
        existing("RS_013", "BASELINE"),
    ]

    shortlist = data.loc[
        data["source"].isin(["Alpha101", "Alpha191"]) & data["research_priority"].eq("P2")
    ]
    for _, row in shortlist.iterrows():
        candidate_id = str(row.name)
        normalized_name = str(row["normalized_name"])
        vwap = "SOURCE_APPROX_VWAP" if {"vwap", "amount"} & set(str(row.get("required_fields") or "").split(";")) or "vwap" in normalized_name else "NOT_APPLICABLE"
        if vwap == "SOURCE_APPROX_VWAP" and not normalized_name.endswith("_approx_vwap"):
            normalized_name += "_approx_vwap"
        known_issue = str(row.get("known_issue") or "")
        implementation = "IMPLEMENTATION_APPROXIMATE" if vwap == "SOURCE_APPROX_VWAP" else "IMPLEMENTATION_OK"
        note = known_issue
        if "REJECT_CANDIDATE" in known_issue or candidate_id in {"A191_132"}:
            # A191_132 is recalculated on the canonical panel as the 20d rolling
            # mean of the official D3 trading amount (verified non-NaN in the
            # run); it stays APPROXIMATE only because amount stands in for the
            # source's vwap*volume, not because the field is unusable.
            implementation = "IMPLEMENTATION_APPROXIMATE" if candidate_id == "A191_132" else "REJECT_IMPLEMENTATION"
            if candidate_id == "A191_132":
                note = "canonical adapter: 20d rolling mean of official D3 trading amount (non-NaN); SOURCE_APPROX_VWAP because canonical amount approximates the source vwap*volume term"
        if candidate_id == "A101_003":
            # Freeze the rank orientation the source left ambiguous.
            note = "canonical rank convention frozen: cross-sectional rank across assets per date (pandas axis=1); -corr(rank(open), rank(volume), 10d)"
        specs.append(
            _from_master(
                row,
                tier="TIER3",
                kind="alpha",
                normalized_name=normalized_name,
                implementation_status=implementation,
                vwap_definition=vwap,
                notes=note,
            )
        )
    ids = [spec.candidate_id for spec in specs]
    if len(ids) != len(set(ids)):
        raise ValueError("Day 2 candidate ids are not unique")
    if len(shortlist) != 37:
        raise ValueError(f"Day 1 P2 shortlist must contain 37 rows, found {len(shortlist)}")
    return specs


def _safe_div(left: pd.DataFrame, right: pd.DataFrame) -> pd.DataFrame:
    return left.div(right.where(right.ne(0))).replace([np.inf, -np.inf], np.nan)


def _rolling(values: pd.DataFrame, window: int, operation: str) -> pd.DataFrame:
    return getattr(values.rolling(window, min_periods=window), operation)()


def _cs_rank(values: pd.DataFrame) -> pd.DataFrame:
    return values.rank(axis=1, pct=True, method="average")


def _ts_rank(values: pd.DataFrame, window: int) -> pd.DataFrame:
    # pandas' native implementation is materially faster than a Python
    # callback for the 1k+ asset D3 panel and has the same last-observation
    # rank semantics.
    return values.rolling(window, min_periods=window).rank(pct=True)


def _ts_corr(left: pd.DataFrame, right: pd.DataFrame, window: int) -> pd.DataFrame:
    result = left.rolling(window, min_periods=window).corr(right)
    return result.replace([np.inf, -np.inf], np.nan)


def _decay_linear(values: pd.DataFrame, window: int) -> pd.DataFrame:
    weights = np.arange(1, window + 1, dtype=float)
    weighted = sum(values.shift(window - offset - 1) * weight for offset, weight in enumerate(weights))
    return weighted / weights.sum()


def _ts_argmax(values: pd.DataFrame, window: int) -> pd.DataFrame:
    return values.rolling(window, min_periods=window).apply(lambda x: float(np.argmax(x) + 1), raw=True)


def _vwap(frames: Mapping[str, pd.DataFrame]) -> pd.DataFrame:
    amount = frames.get("amount")
    if amount is not None:
        return _safe_div(amount, frames["volume"])
    return (frames["open"] + frames["high"] + frames["low"] + frames["close"]) / 4


def _wilder_atr(high: pd.DataFrame, low: pd.DataFrame, close: pd.DataFrame, window: int) -> pd.DataFrame:
    previous = close.shift(1)
    true_range = pd.concat([high - low, (high - previous).abs(), (low - previous).abs()], axis=1)
    true_range = true_range.T.groupby(level=0).max().T
    result = pd.DataFrame(np.nan, index=true_range.index, columns=true_range.columns)
    for column in true_range:
        values = true_range[column]
        if len(values) < window:
            continue
        result.loc[result.index[window - 1], column] = values.iloc[:window].mean()
        for position in range(window, len(values)):
            prior = result.iloc[position - 1, result.columns.get_loc(column)]
            result.iloc[position, result.columns.get_loc(column)] = (prior * (window - 1) + values.iloc[position]) / window
    return result


def _alpha101(candidate_id: str, f: Mapping[str, pd.DataFrame]) -> pd.DataFrame:
    close, open_, high, low, volume = (f[x] for x in ("close", "open", "high", "low", "volume"))
    returns = close.pct_change(fill_method=None)
    vwap = _vwap(f)
    rank, ts_rank = _cs_rank, _ts_rank
    if candidate_id == "A101_001":
        inner = close.copy()
        inner[returns < 0] = _rolling(returns, 20, "std")
        return rank(_rolling(inner.pow(2), 5, "max"))
    if candidate_id == "A101_003":
        return -_ts_corr(rank(open_), rank(volume), 10)
    if candidate_id == "A101_004":
        return -ts_rank(rank(low), 9)
    if candidate_id == "A101_005":
        return rank((open_ - _rolling(vwap, 10, "sum") / 10)) * -abs(rank(close - vwap))
    if candidate_id == "A101_007":
        result = -ts_rank((close.diff(7)).abs(), 60) * np.sign(close.diff(7))
        return result.mask(_rolling(volume, 20, "mean") >= volume, -1)
    if candidate_id == "A101_011":
        spread = vwap - close
        return (rank(_rolling(spread, 3, "max")) + rank(_rolling(spread, 3, "min"))) * rank(volume.diff(3))
    if candidate_id == "A101_012":
        return np.sign(volume.diff()) * -close.diff()
    if candidate_id == "A101_017":
        adv20 = _rolling(volume, 20, "mean")
        return -rank(ts_rank(close, 10)) * rank(close.diff().diff()) * rank(ts_rank(_safe_div(volume, adv20), 5))
    if candidate_id == "A101_019":
        return -np.sign((close - close.shift(7)) + close.diff(7)) * (1 + rank(1 + _rolling(returns, 250, "sum")))
    if candidate_id == "A101_022":
        corr = _ts_corr(high, volume, 5).fillna(0)
        return -corr.diff(5) * rank(_rolling(close, 20, "std"))
    if candidate_id == "A101_030":
        delta = close.diff()
        inner = np.sign(delta) + np.sign(delta.shift(1)) + np.sign(delta.shift(2))
        return ((1 - rank(inner)) * _rolling(volume, 5, "sum")) / _rolling(volume, 20, "sum")
    if candidate_id == "A101_033":
        return rank(-1 + _safe_div(open_, close))
    if candidate_id == "A101_034":
        inner = _safe_div(_rolling(returns, 2, "std"), _rolling(returns, 5, "std")).replace([np.inf, -np.inf], 1).fillna(1)
        return rank(2 - rank(inner) - rank(close.diff()))
    if candidate_id == "A101_035":
        return ts_rank(volume, 32) * (1 - ts_rank(close + high - low, 16)) * (1 - ts_rank(returns, 32))
    if candidate_id == "A101_039":
        adv20 = _rolling(volume, 20, "mean")
        return -rank(close.diff(7) * (1 - rank(_decay_linear(_safe_div(volume, adv20), 9)))) * (1 + rank(_rolling(returns, 250, "mean")))
    if candidate_id == "A101_041":
        return np.sqrt(high * low) - vwap
    if candidate_id == "A101_043":
        adv20 = _rolling(volume, 20, "mean")
        return ts_rank(_safe_div(volume, adv20), 20) * ts_rank(-close.diff(7), 8)
    if candidate_id == "A101_045":
        return -rank(_rolling(close.shift(5), 20, "mean")) * _ts_corr(close, volume, 2).fillna(0) * rank(_ts_corr(_rolling(close, 5, "sum"), _rolling(close, 20, "sum"), 2))
    if candidate_id == "A101_052":
        return -_rolling(low, 5, "min").diff(5) * rank((_rolling(returns, 240, "sum") - _rolling(returns, 20, "sum")) / 220) * ts_rank(volume, 5)
    if candidate_id == "A101_057":
        return -(close - vwap) / _decay_linear(rank(_rolling(close, 30, "max")), 2)
    if candidate_id == "A101_060":
        divisor = (high - low).replace(0, 0.0001)
        inner = ((close - low) - (high - close)) * volume / divisor
        return -((2 * _scale(rank(inner))) - _scale(rank(_ts_argmax(close, 10))))
    if candidate_id == "A101_061":
        adv180 = _rolling(volume, 180, "mean")
        return (rank(vwap - _rolling(vwap, 16, "min")) < rank(_ts_corr(vwap, adv180, 18))).astype(float)
    if candidate_id == "A101_064":
        adv120 = _rolling(volume, 120, "mean")
        left = (open_ * 0.178404 + low * (1 - 0.178404)).rolling(13, min_periods=13).mean()
        right = adv120.rolling(13, min_periods=13).mean()
        first = rank(_ts_corr(left, right, 17))
        second = rank((((high + low) / 2) * 0.178404 + vwap * (1 - 0.178404)).diff(4))
        return (first < second).astype(float) * -1
    if candidate_id == "A101_065":
        adv60 = _rolling(volume, 60, "mean")
        left = open_ * 0.00817205 + vwap * (1 - 0.00817205)
        first = rank(_ts_corr(left, adv60.rolling(9, min_periods=9).mean(), 6))
        return (first < rank(open_ - _rolling(open_, 14, "min"))).astype(float) * -1
    if candidate_id == "A101_073":
        p1 = rank(_decay_linear(vwap.diff(5), 3))
        base = open_ * 0.147155 + low * (1 - 0.147155)
        p2 = ts_rank(_decay_linear(-_safe_div(base.diff(2), base), 3), 17)
        return -p1.where(p1 >= p2, p2)
    if candidate_id == "A101_077":
        adv40 = _rolling(volume, 40, "mean")
        p1 = rank(_decay_linear(((high + low) / 2 + high) - (vwap + high), 20))
        p2 = rank(_decay_linear(_ts_corr((high + low) / 2, adv40, 3), 6))
        return p1.where(p1 <= p2, p2)
    if candidate_id == "A101_088":
        adv60 = _rolling(volume, 60, "mean")
        p1 = rank(_decay_linear((rank(open_) + rank(low)) - (rank(high) + rank(close)), 8))
        p2 = ts_rank(_decay_linear(_ts_corr(ts_rank(close, 8), ts_rank(adv60, 21), 8), 7), 3)
        return p1.where(p1 <= p2, p2)
    if candidate_id == "A101_096":
        return rank(_ts_corr(close, volume, 5)) * rank(vwap - close)
    if candidate_id == "A101_099":
        adv60 = _rolling(volume, 60, "mean")
        return (rank(_ts_corr(_rolling((high + low) / 2, 20, "sum"), _rolling(adv60, 20, "sum"), 9)) < rank(_ts_corr(low, volume, 6))).astype(float) * -1
    if candidate_id == "A101_101":
        return _safe_div(close - open_, high - low + 0.001)
    raise KeyError(candidate_id)


def _scale(values: pd.DataFrame) -> pd.DataFrame:
    denominator = values.abs().sum(axis=1).replace(0, np.nan)
    return values.div(denominator, axis=0)


def _alpha191(candidate_id: str, f: Mapping[str, pd.DataFrame]) -> pd.DataFrame:
    close, high, low, volume = (f[x] for x in ("close", "high", "low", "volume"))
    if candidate_id == "A191_019":
        prior = close.shift(5)
        return ((close - prior) / prior).where(close < prior, (close - prior) / close).where(close.ne(prior), 0)
    if candidate_id == "A191_076":
        value = _safe_div(close.pct_change(fill_method=None).abs(), volume)
        return _safe_div(_rolling(value, 20, "std"), _rolling(value, 20, "mean"))
    if candidate_id == "A191_088":
        return _safe_div(close - close.shift(20), close.shift(20)) * 100
    if candidate_id == "A191_097":
        return _rolling(volume, 10, "std")
    if candidate_id == "A191_109":
        value = _rolling(high - low, 10, "mean").ewm(alpha=2 / 10, adjust=False).mean()
        return _safe_div(value, value.ewm(alpha=2 / 10, adjust=False).mean())
    if candidate_id == "A191_132":
        return _rolling(f["amount"], 20, "mean")
    if candidate_id == "A191_168":
        return -_safe_div(volume, _rolling(volume, 20, "mean"))
    raise KeyError(candidate_id)


def compute_candidate(spec: CandidateSpec, frames: Mapping[str, pd.DataFrame], *, existing: pd.DataFrame | None = None, fundamentals: Mapping[str, pd.DataFrame] | None = None) -> pd.DataFrame:
    """Calculate one candidate on a canonical date x asset panel."""

    if existing is not None and spec.kind == "existing":
        return existing.reindex(index=frames["close"].index, columns=frames["close"].columns)
    if spec.kind == "volume_ratio":
        values = _safe_div(frames["volume"], _rolling(frames["volume"].where(frames["volume"] > 0), 20, "mean"))
        # Direction is applied by the evaluator, so +1 and -1 remain two
        # comparable views of the same raw signal.
        return values
    if spec.kind == "near_high":
        return _safe_div(frames["close"], _rolling(frames["close"], spec.lookback, "max")) - 1
    if spec.kind == "realized_vol":
        return _rolling(frames["close"].pct_change(fill_method=None), 20, "std") * np.sqrt(252)
    if spec.kind == "natr":
        return _safe_div(_wilder_atr(frames["high"], frames["low"], frames["close"], 14), frames["close"])
    if spec.kind == "universe_filter":
        return pd.DataFrame(True, index=frames["close"].index, columns=frames["close"].columns)
    if spec.kind == "alpha":
        if spec.candidate_id.startswith("A101_"):
            return _alpha101(spec.candidate_id, frames)
        if spec.candidate_id.startswith("A191_"):
            return _alpha191(spec.candidate_id, frames)
    if fundamentals and spec.kind in fundamentals:
        return fundamentals[spec.kind].reindex(index=frames["close"].index, columns=frames["close"].columns)
    return pd.DataFrame(np.nan, index=frames["close"].index, columns=frames["close"].columns)


def panel_to_long(values: pd.DataFrame, *, factor_id: str, direction: int, member: pd.DataFrame | None = None) -> pd.DataFrame:
    """Convert a candidate panel into the common evaluator input shape."""

    result = values.rename_axis(index="asof_date", columns="asset_id").stack(future_stack=True).rename("raw_value").reset_index()
    result["factor_id"] = factor_id
    result["direction"] = direction
    result["member"] = True if member is None else member.rename_axis(index="asof_date", columns="asset_id").stack(dropna=False).to_numpy()
    result["asof_date"] = pd.to_datetime(result["asof_date"])
    result["rank_value"] = result.groupby("asof_date")["raw_value"].rank(pct=True)
    result["direction_adjusted_rank"] = (
        result["rank_value"] if direction >= 0
        else result.groupby("asof_date")["raw_value"].rank(pct=True, ascending=False)
    )
    return result


def implementation_confidence(status: str) -> float:
    return {
        "IMPLEMENTATION_OK": 1.0,
        "IMPLEMENTATION_APPROXIMATE": 0.6,
        "IMPLEMENTATION_AMBIGUOUS": 0.3,
        "REJECT_IMPLEMENTATION": 0.0,
    }.get(status, 0.0)
