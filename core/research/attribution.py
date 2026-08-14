"""Day 3 benchmark contract and Statsmodels alpha/beta/HAC attribution.

Sole import point for statsmodels within the D5/D6 research code. The HAC lag
rule (Newey-West 1994) is frozen here and must not be tuned after viewing
significance (design.md decision E).
"""
from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path

import pandas as pd
import statsmodels.api as sm

from core.research.sources.twse import find_closing_table


class BenchmarkUnavailableError(ValueError):
    """Raised when the canonical benchmark cannot be rebuilt offline from the frozen lineage."""


def load_benchmark_returns(raw_cache_dir: Path, symbol: str = "2330") -> tuple[pd.Series, dict]:
    """Rebuild the canonical single-stock benchmark's daily simple returns offline.

    ponytail: reuses twse.find_closing_table's schema-correct column lookup rather
    than re-deriving the MI_INDEX field ordering.
    """
    raw_cache_dir = Path(raw_cache_dir)
    files = sorted(raw_cache_dir.glob("MI_INDEX_*.json"))
    if not files:
        raise BenchmarkUnavailableError(f"no cached MI_INDEX files under {raw_cache_dir}")
    closes: dict[pd.Timestamp, float] = {}
    for file in files:
        trade_date = pd.Timestamp(file.stem.removeprefix("MI_INDEX_"))
        payload = json.loads(file.read_text(encoding="utf-8"))
        if payload.get("stat") != "OK":
            continue
        table = find_closing_table(payload)
        fields = table["fields"]
        code_index, close_index = fields.index("證券代號"), fields.index("收盤價")
        for row in table["data"]:
            if row[code_index].strip() == symbol:
                try:
                    closes[trade_date] = float(str(row[close_index]).replace(",", ""))
                except ValueError:
                    pass
                break
    if len(closes) < 2:
        raise BenchmarkUnavailableError(f"insufficient cached closing prices for benchmark symbol {symbol}")
    price_series = pd.Series(closes).sort_index()
    returns = price_series.pct_change().dropna()
    provenance = {
        "benchmark_id": symbol,
        "source": "frozen D3 raw cache (twse_rwd MI_INDEX, offline)",
        "start_date": str(returns.index.min().date()),
        "end_date": str(returns.index.max().date()),
        "frequency": "daily",
        "return_calculation": "simple daily return of closing price",
        "sha256": hashlib.sha256(pd.util.hash_pandas_object(returns).values.tobytes()).hexdigest(),
    }
    return returns, provenance


def hac_maxlags(nobs: int) -> int:
    """Newey-West (1994) automatic lag-selection rule, frozen before viewing significance."""
    if nobs <= 0:
        raise ValueError("nobs must be positive")
    return max(0, math.floor(4 * (nobs / 100) ** (2 / 9)))


def run_attribution(config_id: str, strategy_returns: pd.Series, benchmark_returns: pd.Series) -> dict:
    """Minimal r_t = alpha + beta * r_bench,t + eps_t regression with OLS and HAC inference."""
    aligned = pd.concat([strategy_returns.rename("strategy"), benchmark_returns.rename("benchmark")], axis=1).dropna()
    if len(aligned) < 10:
        raise ValueError(f"insufficient aligned observations for attribution: {len(aligned)}")
    design = sm.add_constant(aligned["benchmark"].to_numpy())
    ols = sm.OLS(aligned["strategy"].to_numpy(), design).fit()
    lag = hac_maxlags(len(aligned))
    hac = ols.get_robustcov_results(cov_type="HAC", maxlags=lag)
    ci = hac.conf_int(alpha=0.05)
    return {
        "config_id": config_id,
        "alpha": float(ols.params[0]), "beta": float(ols.params[1]),
        "alpha_se_ols": float(ols.bse[0]), "beta_se_ols": float(ols.bse[1]),
        "alpha_t_ols": float(ols.tvalues[0]), "beta_t_ols": float(ols.tvalues[1]),
        "alpha_p_ols": float(ols.pvalues[0]), "beta_p_ols": float(ols.pvalues[1]),
        "alpha_se_hac": float(hac.bse[0]), "beta_se_hac": float(hac.bse[1]),
        "alpha_t_hac": float(hac.tvalues[0]), "beta_t_hac": float(hac.tvalues[1]),
        "alpha_p_hac": float(hac.pvalues[0]), "beta_p_hac": float(hac.pvalues[1]),
        "alpha_ci_low_hac": float(ci[0][0]), "alpha_ci_high_hac": float(ci[0][1]),
        "beta_ci_low_hac": float(ci[1][0]), "beta_ci_high_hac": float(ci[1][1]),
        "r_squared": float(ols.rsquared), "n_obs": int(len(aligned)), "hac_maxlags": lag,
    }
