"""Research data provider: Daily-equivalent cross-sections built from the official snapshot.

Indicators, chip features and the z-score matrix come from core.calc_indicators.build_feature_history, the
same core recovered Daily selection uses (jobs/run_daily.load_recovered_data). Missing inputs stay NaN and the
stock is excluded with a recorded reason; nothing is zero-filled.
"""
from __future__ import annotations

import inspect
import json
import re
from pathlib import Path

import pandas as pd

from core.calc_indicators import build_feature_history, build_multi_factor_matrix

LOOKBACK_DAYS = 150  # run_daily.compute_indicators_from_history window
# Statutory filing deadlines (month, day, year offset); the latest legally public period, never later data.
QUARTER_DEADLINES = {1: (5, 15, 0), 2: (8, 14, 0), 3: (11, 14, 0), 4: (3, 31, 1)}
FRAME_INPUTS = frozenset({
    "ma5", "ma20", "ma60", "bias", "rsi", "macd_hist", "kd_k", "bb_width", "atr", "natr", "std_20", "volume_ratio",
    "foreign_ratio", "trust_ratio", "dealer_ratio", "foreign_consec_days", "trust_consec_days", "dealer_consec_days", "margin_change_pct",
    "chip_score", "foreign_buy", "trust_buy", "dealer_buy", "margin_balance", "revenue_yoy", "op_profit_margin",
    "eps", "rd_ratio", "high_60"})
PIT_RECORDS = Path(__file__).resolve().parents[2] / "data/processed/fundamental_pit_v3/fundamental_records.parquet"
CHIP_INPUTS = frozenset({"dealer_buy", "dealer_ratio", "dealer_consec_days", "margin_balance", "margin_change_pct", "chip_score"})
FLOW_INPUTS = frozenset({"foreign_buy", "trust_buy", "foreign_ratio", "trust_ratio", "foreign_consec_days", "trust_consec_days"})
FUNDAMENTAL_INPUTS = frozenset({"revenue_yoy", "op_profit_margin", "eps", "rd_ratio"})


def rule_inputs(strategy) -> set[str]:
    """Frame columns named in the strategy module (rules and its declared model features)."""
    source = inspect.getsource(inspect.getmodule(type(strategy)))
    return {t for t in re.findall(r"""['"]([a-z_0-9]+)['"]""", source) if t in FRAME_INPUTS}


def _due(year, month, day) -> pd.Series:
    return pd.to_datetime(pd.DataFrame({"year": year, "month": month, "day": day}))


MI_INDEX_DIRS = (Path(__file__).resolve().parents[2] / "artifacts/factors/d3_full_20230103_20260728/_raw/twse_rwd",
                 Path(__file__).resolve().parents[2] / "artifacts/official_data_recovery_cache/mi_index")


def listed_universe(date_str: str, directories=MI_INDEX_DIRS) -> frozenset[str]:
    """TWSE-listed four-digit common stocks of one session from the cached official MI_INDEX closing table; never guessed."""
    from core.research.adjustment.normalization import normalize_twse_closing_table
    from core.research.sources.twse import find_closing_table

    name = f"MI_INDEX_{date_str.replace('-', '')}.json"
    for directory in directories:
        path = Path(directory) / name
        if path.exists():
            payload = json.loads(path.read_text(encoding="utf-8"))
            if payload.get("stat") == "OK":
                return frozenset(normalize_twse_closing_table(find_closing_table(payload), pd.Timestamp(date_str), None).stock_id)
    raise RuntimeError(f"LISTED_UNIVERSE_UNAVAILABLE: no cached official MI_INDEX trading-day response for {date_str}")


def load_publication_dates() -> pd.DataFrame | None:
    """First MOPS publication date per (stock, year, quarter) from the PIT dataset; None when it is not present."""
    if not PIT_RECORDS.is_file():
        return None
    records = pd.read_parquet(PIT_RECORDS, columns=["ticker", "fiscal_year", "quarter", "publication_date", "data_version"])
    records = records[records.data_version == "fundamental-pit-v3"]
    first = records.groupby(["ticker", "fiscal_year", "quarter"]).publication_date.min().reset_index()
    return pd.DataFrame({"stock_id": first.ticker.astype(str), "year": first.fiscal_year.astype(int), "quarter": first.quarter.astype(int),
                         "publication_date": pd.to_datetime(first.publication_date)})


def prepare_fundamentals(financials: pd.DataFrame, revenue: pd.DataFrame, publications: pd.DataFrame | None = None):
    """Availability-dated fundamentals with Daily's definitions (owner decision OD-4).

    A filing is usable after its actual MOPS publication date when known; otherwise after the statutory deadline,
    a conservative estimate flagged as such (a late filer could still be early-dated). Monthly revenue has no
    publication source and always uses the deadline (10th of the next month).
    """
    fin = financials.assign(stock_id=financials.stock_id.astype(str))
    deadline = fin.quarter.map(QUARTER_DEADLINES)
    fin["due"] = _due(fin.year + deadline.str[2], deadline.str[0], deadline.str[1])
    fin["availability_source"] = "statutory_deadline"
    if publications is not None:
        fin = fin.merge(publications, on=["stock_id", "year", "quarter"], how="left")
        known = fin.publication_date.notna()
        fin.loc[known, "due"] = fin.loc[known, "publication_date"]
        fin.loc[known, "availability_source"] = "mops_publication"
    revenue_ok = fin.revenue.where(fin.revenue > 0)
    fin["op_profit_margin"] = fin.operating_profit / revenue_ok  # Daily: operating_profit / revenue
    fin["rd_ratio"] = fin.rd_expense / revenue_ok
    fin = fin.sort_values(["stock_id", "year", "quarter"])[["stock_id", "due", "availability_source", "op_profit_margin", "rd_ratio", "eps"]]
    rev = revenue.assign(stock_id=revenue.stock_id.astype(str))
    rev["due"] = _due(rev.year + (rev.month == 12), rev.month % 12 + 1, 10)  # monthly revenue is due on the 10th
    rev["availability_source"] = "statutory_deadline"
    return fin, rev.sort_values(["stock_id", "year", "month"])[["stock_id", "due", "availability_source", "revenue_yoy"]]


def exclusion_reason(missing) -> str:
    missing = set(missing)
    if missing & CHIP_INPUTS:
        return "CHIP_SOURCE_MISSING"
    if missing & FLOW_INPUTS:
        return "FLOW_SOURCE_MISSING"
    if missing & FUNDAMENTAL_INPUTS:
        return "FUNDAMENTAL_NOT_PUBLISHED_OR_MISSING"
    return "INSUFFICIENT_HISTORY_OR_MISSING_BAR"


class NativeResearchData:
    available_inputs = FRAME_INPUTS

    # Sources that are NULL for most of the history; Daily and the z-score builder silently turn them into 0.
    NEVER_POPULATED = ("dealer_buy", "dealer_ratio", "margin_balance", "margin_change_pct", "chip_score")

    def __init__(self, snapshot: pd.DataFrame, financials: pd.DataFrame, revenue: pd.DataFrame, daily_equivalent_fill: bool = False,
                 publications: pd.DataFrame | None = None):
        """daily_equivalent_fill=True reproduces Daily's NULL -> 0 for NEVER_POPULATED inputs (diagnostic only, never certified)."""
        self.daily_equivalent_fill = daily_equivalent_fill
        self.snapshot = snapshot.sort_values(["stock_id", "trade_date"]).reset_index(drop=True)
        self._bars = self.snapshot.set_index(["stock_id", "trade_date"])
        self._closes = {sid: g.set_index("trade_date").close_price for sid, g in self.snapshot.groupby("stock_id")}
        self._fin, self._rev = prepare_fundamentals(financials, revenue, publications)
        self._cache: dict[pd.Timestamp, pd.DataFrame] = {}

    def truncated(self, day) -> "NativeResearchData":
        """The same data with every bar after `day` removed (spec 9.1 guard 2: features must not depend on later data)."""
        import copy

        clone = copy.copy(self)
        clone.snapshot = self.snapshot[self.snapshot.trade_date <= pd.Timestamp(day)].reset_index(drop=True)
        clone._bars = clone.snapshot.set_index(["stock_id", "trade_date"])
        clone._closes = {sid: g.set_index("trade_date").close_price for sid, g in clone.snapshot.groupby("stock_id")}
        clone._cache = {}
        return clone

    def _published(self, table: pd.DataFrame, date: pd.Timestamp) -> pd.DataFrame:
        return table[table.due < date].drop_duplicates("stock_id", keep="last").drop(columns="due")

    def cross_section(self, date: pd.Timestamp) -> pd.DataFrame:
        if date in self._cache:
            return self._cache[date]
        df = self.snapshot[(self.snapshot.trade_date >= date - pd.Timedelta(days=LOOKBACK_DAYS)) & (self.snapshot.trade_date <= date)].copy()
        if df.empty or df.trade_date.max() != date:
            raise ValueError(f"NO_OFFICIAL_SESSION_DATA: {date:%Y-%m-%d}")
        df = build_feature_history(df)
        if self.daily_equivalent_fill:
            df[list(self.NEVER_POPULATED)] = df[list(self.NEVER_POPULATED)].fillna(0)
        fin = self._published(self._fin, date).rename(columns={"availability_source": "fundamental_availability_source"})
        rev = self._published(self._rev, date).rename(columns={"availability_source": "revenue_availability_source"})
        latest = df[df.trade_date == date].merge(fin, on="stock_id", how="left").merge(rev, on="stock_id", how="left")
        self._cache[date] = latest  # shared by every strategy replayed on this provider
        return latest

    def unavailable_inputs(self, start: str, end: str) -> frozenset:
        """Inputs whose source is entirely NULL in the window (strict mode); empty when diagnostic filling is on."""
        if self.daily_equivalent_fill:
            return frozenset()
        window = self.snapshot[(self.snapshot.trade_date >= start) & (self.snapshot.trade_date <= end)]
        empty = {"dealer_buy", "dealer_ratio", "dealer_consec_days"} if window.dealer_buy.isna().all() else set()
        empty |= {"margin_balance", "margin_change_pct"} if window.margin_balance.isna().all() else set()
        return frozenset(empty | ({"chip_score"} if empty else set()))

    def frame(self, date_str: str, required, model_inputs=None) -> tuple[pd.DataFrame, dict]:
        """Daily-equivalent candidate frame; stocks missing a required input are excluded and counted.

        model_inputs=None (legacy research): the z-score population is the stocks that pass every required input.
        model_inputs given (recovered Daily, training): the population is every stock with all model inputs present, z-scores are
        computed on it first, and only then are stocks missing a rule input dropped, so rule inputs never change the population.
        """
        full = self.cross_section(pd.Timestamp(date_str))
        if model_inputs is not None:
            return self._model_population_frame(full, date_str, sorted(required), sorted(model_inputs))
        absent = full[sorted(required)].isna()
        keep = ~absent.any(axis=1)
        dropped = absent[~keep]
        rows = [{"stock_id": str(sid), "missing_columns": cols, "reason": exclusion_reason(cols)}
                for sid, cols in zip(full.stock_id[~keep], (list(dropped.columns[r]) for r in dropped.to_numpy()))]
        note = {"date": date_str, "rows": len(full), "excluded": int((~keep).sum()),
                "missing_input_counts": {c: int(absent[c].sum()) for c in absent if absent[c].any()}, "excluded_rows": rows}
        kept = full[keep]
        if kept.empty:
            return kept, note
        return build_multi_factor_matrix(kept, trade_date=date_str), note

    @staticmethod
    def _missing_rows(frame: pd.DataFrame, columns: list[str]) -> tuple[pd.Series, list[dict]]:
        absent = frame[columns].isna()
        bad = absent.any(axis=1)
        rows = [{"stock_id": str(sid), "missing_columns": list(absent.columns[r]), "reason": exclusion_reason(absent.columns[r])}
                for sid, r in zip(frame.stock_id[bad], absent[bad].to_numpy())]
        return bad, rows

    def _model_population_frame(self, full, date_str, rule_columns, model_columns):
        incomplete, model_rows = self._missing_rows(full, model_columns)
        population = full[~incomplete]
        rule_only = [c for c in rule_columns if c not in model_columns]
        rule_missing, rule_rows = self._missing_rows(population, rule_only) if rule_only and not population.empty else (pd.Series(False, index=population.index), [])
        note = {"date": date_str, "rows": len(full), "population_rows": len(population), "excluded": int(incomplete.sum() + rule_missing.sum()),
                "excluded_model_incomplete": model_rows, "excluded_rule_missing": rule_rows, "excluded_rows": model_rows + rule_rows,
                "missing_input_counts": {c: int(full[c].isna().sum()) for c in model_columns + rule_only if c in full and full[c].isna().any()}}
        if population.empty:
            return population, note
        matrix = build_multi_factor_matrix(population, trade_date=date_str)  # z-scores over the whole model-complete population
        return matrix[~rule_missing.to_numpy()], note

    def bar(self, stock_id: str, date_str: str):
        try:
            return self._bars.loc[(str(stock_id), pd.Timestamp(date_str))].to_dict()
        except KeyError:
            return None

    def last_close(self, stock_id: str, date_str: str):
        closes = self._closes.get(str(stock_id))
        closes = None if closes is None else closes.loc[:pd.Timestamp(date_str)]
        return None if closes is None or closes.empty else float(closes.iloc[-1])

    def stock_data(self, symbol: str, date_str: str):
        """Stand-in for core.db_helper.get_stock_data (one row with indicators)."""
        full = self.cross_section(pd.Timestamp(date_str))
        return full[full.stock_id == str(symbol)].reset_index(drop=True), date_str
