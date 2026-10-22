"""Official-source research snapshot for the native strategies (TWSE-listed, price-return basis).

Prices, sessions, TAIEX and corporate actions come from hash-recorded official TWSE responses.
MySQL rows only supply institutional flows (and are reconciled against the official bars);
no source row is modified and no missing price is invented.
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

from core.research.adjustment.normalization import normalize_corporate_actions, normalize_twse_closing_table
from core.research.sources import RawResponse
from core.research.sources.twse import ResponseKind, classify, find_closing_table

RAW_DIR = Path("artifacts/factors/d3_full_20230103_20260728/_raw/twse_rwd")
FLOW_COLUMNS = ("foreign_buy", "trust_buy", "dealer_buy", "margin_balance", "short_balance")
PRICE_COLUMNS = ("open_price", "high_price", "low_price", "close_price")
_STAMP = datetime(2026, 7, 28)  # RawResponse needs a timestamp; classification ignores it


@dataclass
class Official:
    sessions: pd.DatetimeIndex
    holidays: list[str]
    bars: pd.DataFrame
    taiex: pd.Series
    actions: pd.DataFrame
    raw_sha256: str
    file_count: int


def load_official(raw_dir: Path = RAW_DIR, start: str = "2025-01-01", end: str = "2026-07-28") -> Official:
    lo, hi = pd.Timestamp(start), pd.Timestamp(end)
    sessions, holidays, bars, taiex, hashes = [], [], [], {}, []
    for path in sorted(raw_dir.glob("MI_INDEX_*.json")):
        day = pd.Timestamp(path.stem[-8:])
        if not lo <= day <= hi:
            continue
        raw = path.read_bytes()
        hashes.append(f"{path.name}:{hashlib.sha256(raw).hexdigest()}")
        payload = json.loads(raw)
        kind = classify(RawResponse("twse", "MI_INDEX", {}, _STAMP, None, payload, None)).kind
        if kind == ResponseKind.NON_TRADING_DAY:
            if day.dayofweek < 5:
                holidays.append(day.strftime("%Y-%m-%d"))
            continue
        if kind != ResponseKind.TRADING_DAY:
            raise ValueError(f"{path.name}: unexpected source state {kind.value}")
        sessions.append(day)
        bars.append(normalize_twse_closing_table(find_closing_table(payload), day, _STAMP))
        index_rows = [r for t in payload["tables"] for r in t.get("data", []) if r and r[0] == "發行量加權股價指數"]
        if len(index_rows) != 1:
            raise ValueError(f"{path.name}: expected one TAIEX row, found {len(index_rows)}")
        taiex[day] = float(index_rows[0][1].replace(",", ""))
    actions_path = next(raw_dir.glob("TWT49U_*.json"))
    raw = actions_path.read_bytes()
    hashes.append(f"{actions_path.name}:{hashlib.sha256(raw).hexdigest()}")
    actions = normalize_corporate_actions(json.loads(raw), _STAMP)
    actions = actions[(pd.to_datetime(actions.ex_date) >= lo) & (pd.to_datetime(actions.ex_date) <= hi)]
    frame = pd.concat(bars, ignore_index=True).rename(columns={
        "raw_open": "open_price", "raw_high": "high_price", "raw_low": "low_price", "raw_close": "close_price"})
    return Official(pd.DatetimeIndex(sessions), holidays, frame[["trade_date", "stock_id", *PRICE_COLUMNS, "volume", "amount"]],
                    pd.Series(taiex, name="taiex_close"), actions.reset_index(drop=True),
                    hashlib.sha256("\n".join(hashes).encode()).hexdigest(), len(hashes))


def _same_bar(db: pd.DataFrame, off: pd.DataFrame) -> pd.Series:
    return pd.Series(np.all([np.isclose(db[c], off[c], atol=1e-6) for c in PRICE_COLUMNS], axis=0), index=db.index)


def build_snapshot(official: Official, daily: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    """Official bars + MySQL flows. Returns (snapshot, audit with before/after counts per rule)."""
    d = daily.copy()
    d["stock_id"] = d["stock_id"].astype(str).str.strip()
    d["trade_date"] = pd.to_datetime(d["trade_date"])
    end = official.sessions.max()
    twse = set(official.bars.stock_id)
    sessions = set(official.sessions)
    stock_like = d.stock_id.str.fullmatch(r"[1-9]\d{3}")
    is_twse = stock_like & d.stock_id.isin(twse)
    in_window = d.trade_date <= end
    on_session = d.trade_date.isin(sessions)
    keys = ["stock_id", "trade_date"]
    audit: dict = {"rows_total": len(d), "rows_not_four_digit_stock": int((~stock_like).sum()),
                   "rows_four_digit_not_in_official_twse_list(tpex)": int((stock_like & ~is_twse).sum()),
                   "rows_after_official_end": int((is_twse & ~in_window).sum()),
                   "rows_twse_weekend": int((is_twse & in_window & (d.trade_date.dt.dayofweek >= 5)).sum()),
                   "rows_twse_weekday_not_official_session": int((is_twse & in_window & ~on_session & (d.trade_date.dt.dayofweek < 5)).sum())}
    x = d[is_twse & in_window & on_session].copy()
    audit["rows_twse_on_official_sessions"] = len(x)
    dup = x.duplicated(keys, keep=False)
    audit["duplicate_rows"] = int(dup.sum())
    # A source row is trusted only when its bar equals the official bar for that stock/session.
    x = x.merge(official.bars, on=keys, how="left", suffixes=("", "_off"))
    x["trusted"] = _same_bar(x[list(PRICE_COLUMNS)], x[[c + "_off" for c in PRICE_COLUMNS]].set_axis(PRICE_COLUMNS, axis=1)) & x.close_price_off.notna()
    per_key = x.groupby(keys).trusted.agg(["size", "any"])
    audit.update(duplicate_keys=int((per_key["size"] > 1).sum()),
                 duplicate_keys_resolved_by_official_bar=int(((per_key["size"] > 1) & per_key["any"]).sum()),
                 price_keys_compared=int(x.close_price_off.notna().groupby([x.stock_id, x.trade_date]).any().sum()),
                 price_keys_matching_official=int(per_key["any"].sum()),
                 db_only_keys_without_official_bar=int((x.close_price_off.isna().groupby([x.stock_id, x.trade_date]).all()).sum()),
                 volume_match_rate=round(float(np.isclose(x.volume[x.trusted], x.volume_off[x.trusted]).mean()), 6))
    audit["price_match_rate"] = round(audit["price_keys_matching_official"] / audit["price_keys_compared"], 6)
    bad_dates = x[~x.trusted & x.close_price_off.notna()].groupby("trade_date").size().sort_values(ascending=False)
    audit["price_mismatch_rows_by_date_top10"] = {k.strftime("%Y-%m-%d"): int(v) for k, v in bad_dates.head(10).items()}
    first_db = x.trade_date.min()
    official_keys = official.bars.loc[official.bars.trade_date >= first_db, keys].drop_duplicates()
    seen = official_keys.merge(x[keys].drop_duplicates(), on=keys, how="left", indicator=True)
    audit["official_bars_missing_in_mysql_since_first_mysql_date"] = int((seen._merge == "left_only").sum())
    per_session = x.groupby("trade_date").size().reindex(official.sessions[official.sessions >= first_db]).fillna(0)
    audit["official_sessions_missing_in_mysql"] = [s.strftime("%Y-%m-%d") for s, n in per_session.items()
                                                   if n < 0.5 * (official.bars.trade_date == s).sum()]
    audit["official_stock_ids_never_in_mysql"] = sorted(twse - set(d.stock_id))
    # Flows come only from trusted rows; trusted rows that still disagree on flows become unavailable (NaN).
    ok = x[x.trusted]
    flows = ok.groupby(keys)[list(FLOW_COLUMNS)].first()
    disagree = ok.groupby(keys)[list(FLOW_COLUMNS)].nunique(dropna=False).gt(1).any(axis=1)
    flows.loc[disagree[disagree].index, list(FLOW_COLUMNS)] = np.nan
    snapshot = official.bars.merge(flows.reset_index(), on=keys, how="left").sort_values(keys).reset_index(drop=True)
    in_mysql_span = snapshot.trade_date >= first_db
    audit.update(snapshot_rows=len(snapshot), snapshot_stocks=int(snapshot.stock_id.nunique()), mysql_first_date=first_db.strftime("%Y-%m-%d"),
                 snapshot_rows_in_mysql_span=int(in_mysql_span.sum()),
                 snapshot_rows_in_mysql_span_without_trusted_flows=int((in_mysql_span & snapshot.foreign_buy.isna()).sum()),
                 dealer_buy_nonzero_rows=int((snapshot.dealer_buy.fillna(0) != 0).sum()),
                 margin_balance_nonzero_rows=int((snapshot.margin_balance.fillna(0) != 0).sum()),
                 short_balance_notnull_rows=int(snapshot.short_balance.notna().sum()))
    return snapshot, audit


def corporate_action_check(official: Official) -> dict:
    """Official ex-rights events versus the official previous close (evidence the events are consistent)."""
    bars = official.bars.sort_values(["stock_id", "trade_date"])
    bars["prev_close"] = bars.groupby("stock_id").close_price.shift()
    a = official.actions.assign(ex_date=pd.to_datetime(official.actions.ex_date))
    j = a.merge(bars[["stock_id", "trade_date", "prev_close", "close_price"]], left_on=["stock_id", "ex_date"],
                right_on=["stock_id", "trade_date"], how="left")
    known = j.prev_close.notna()
    return {"events": len(a), "events_for_non_stock_ids_or_without_official_bar": int((~known).sum()), "events_on_official_sessions": int(known.sum()),
            "pre_ex_close_equals_previous_official_close": int(np.isclose(j.pre_ex_close[known], j.prev_close[known], atol=1e-6).sum()),
            "basis": "price_return: raw prices, cash dividends not credited; total return unavailable"}


def reconcile_fundamentals(financials: pd.DataFrame, revenue: pd.DataFrame, pit: pd.DataFrame) -> dict:
    """MySQL financial rows against MOPS-derived PIT v3 (both reporting bases) plus revenue_yoy self-consistency."""
    raw = pit[pit.data_version == "fundamental-pit-v3"].sort_values(["publication_date", "revision_id"])
    last = raw.drop_duplicates(["ticker", "fiscal_year", "quarter", "metric", "REPORTING_BASIS"], keep="last")
    wide = last.pivot_table(index=["ticker", "fiscal_year", "quarter"], columns=["metric", "REPORTING_BASIS"], values="value", aggfunc="last")
    wide.columns = [f"pit_{m}_{b}" for m, b in wide.columns]
    wide.index = wide.index.set_names(["ticker", "year", "quarter"])
    fin = financials.assign(ticker=financials.stock_id.astype(str)).set_index(["ticker", "year", "quarter"])
    joined = fin.join(wide, how="inner")
    out = {"mysql_rows": len(financials), "rows_with_pit_counterpart": len(joined),
           "pit_fiscal_year_max": int(raw.fiscal_year.max()), "mysql_year_max": int(financials.year.max())}
    for metric in ("eps", "operating_profit", "revenue"):
        for basis in ("CUMULATIVE", "STANDALONE"):
            ref = joined.get(f"pit_{metric}_{basis}")
            if ref is not None and ref.notna().any():
                ok = ref.notna()
                out[f"{metric}_matches_{basis.lower()}"] = round(float(np.isclose(joined[metric][ok].astype(float), ref[ok].astype(float), rtol=1e-6).mean()), 4)
    prev = revenue.assign(year=revenue.year + 1)[["stock_id", "year", "month", "revenue"]].rename(columns={"revenue": "prev_revenue"})
    r = revenue.merge(prev, on=["stock_id", "year", "month"])
    recomputed = (r.revenue / r.prev_revenue.replace(0, np.nan) - 1) * 100
    out["monthly_revenue_yoy_rows_checkable"] = len(r)
    out["monthly_revenue_yoy_self_consistent"] = round(float(np.isclose(recomputed, r.revenue_yoy, atol=0.02).mean()), 4) if len(r) else None
    return out
