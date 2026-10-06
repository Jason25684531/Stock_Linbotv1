"""Frozen legacy inputs reconstructed from existing strategy test evidence."""

from __future__ import annotations

import math

import pandas as pd


ASOF_DATE = "2026-04-10"
EXPECTED_SELECTIONS = {
    "hybrid_trend_rank": ("2330", "2317", "2454", "2303"),
    "defensive_low_volatility": ("2303", "2454"),
    "growth_momentum_breakout": ("2303", "2454"),
    "quality_growth": ("2454", "2303"),
    "institutional_flow_confirmation": ("2303", "2454"),
    "mean_reversion": ("2330", "2317"),
    "quality_value_low_volatility": ("2303", "2454"),
}

# These are the existing Config defaults, pinned here so characterization never
# reads the mutable user_settings table.
RUNTIME_OVERRIDES = {
    "v34_revenue_yoy_min": 18.0,
    "v34_breakout_ratio": 0.93,
    "v34_volume_ratio_min": 0.9,
    "v34_volume_min": 300.0,
    "v34_relaxed_revenue_yoy_min": 10.0,
    "v34_relaxed_breakout_ratio": 0.9,
    "v34_relaxed_volume_ratio_min": 0.7,
    "v34_relaxed_volume_min": 150.0,
    "v35_op_margin_min": 0.06,
    "v35_revenue_yoy_min": 0.0,
    "v35_volume_ratio_min": 0.8,
    "v35_volume_min": 300.0,
    "v35_relaxed_op_margin_min": 0.04,
    "v35_relaxed_revenue_yoy_min": -5.0,
    "v35_relaxed_volume_ratio_min": 0.6,
    "v35_relaxed_volume_min": 150.0,
}


def frozen_legacy_frame() -> pd.DataFrame:
    """Return the minimum fixed feature rows needed by all seven strategies."""

    rows = []
    for stock_id, volume_ratio, kd_k, std_20, revenue_yoy, margin, chip_score in (
        ("2330", 0.9, 20.0, 2.0, 0.0, math.nan, 10.0),
        ("2317", 0.9, 25.0, 1.0, 0.0, math.nan, 10.0),
        ("2454", 2.0, 22.0, 2.0, 50.0, 0.2, 90.0),
        ("2303", 2.0, 24.0, 1.0, 80.0, 0.3, 95.0),
    ):
        rows.append(
            {
                "stock_id": stock_id,
                "trade_date": ASOF_DATE,
                "close_price": 100.0,
                "high_price": 100.0,
                "ma20": 90.0,
                "ma60": 80.0,
                "volume": 6_000_000.0,
                "volume_ratio": volume_ratio,
                "rsi": 55.0,
                "kd_k": kd_k,
                "bb_width": 5.0,
                "natr": 2.0,
                "std_20": std_20,
                "macd_hist": 1.0,
                "bias": 2.0,
                "revenue_yoy": revenue_yoy,
                "op_profit_margin": margin,
                "eps": 2.0,
                "chip_score": chip_score,
                "foreign_consec_days": 5,
                "trust_consec_days": 5,
                "foreign_ratio": 0.1,
                "trust_ratio": 0.1,
                "dealer_ratio": 0.1,
                "margin_change_pct": 0.0,
                "atr": 1.0,
            }
        )
    return pd.DataFrame(rows)
