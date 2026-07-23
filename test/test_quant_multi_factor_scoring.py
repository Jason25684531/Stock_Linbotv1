import math
from types import SimpleNamespace

import numpy as np
import pandas as pd


def test_cross_sectional_zscore_normalizes_by_trade_date():
    from core.calc_indicators import calculate_cross_sectional_zscore

    df = pd.DataFrame(
        [
            {"stock_id": "A", "trade_date": "2026-05-01", "rsi": 10},
            {"stock_id": "B", "trade_date": "2026-05-01", "rsi": 20},
            {"stock_id": "C", "trade_date": "2026-05-01", "rsi": 30},
            {"stock_id": "A", "trade_date": "2026-05-02", "rsi": 40},
            {"stock_id": "B", "trade_date": "2026-05-02", "rsi": 60},
        ]
    )

    result = calculate_cross_sectional_zscore(df, ["rsi"])

    day1 = result[result["trade_date"] == "2026-05-01"].sort_values("stock_id")
    values = day1["rsi_z"].tolist()
    assert values[0] < 0
    assert values[1] == 0
    assert values[2] > 0
    assert math.isclose(day1["rsi_z"].mean(), 0.0, abs_tol=1e-12)

    day2 = result[result["trade_date"] == "2026-05-02"].sort_values("stock_id")
    assert day2["rsi_z"].tolist() == [-1.0, 1.0]


def test_cross_sectional_zscore_falls_back_to_zero_for_unusable_values():
    from core.calc_indicators import calculate_cross_sectional_zscore

    df = pd.DataFrame(
        [
            {"stock_id": "A", "trade_date": "2026-05-01", "constant": 5, "dirty": np.nan},
            {"stock_id": "B", "trade_date": "2026-05-01", "constant": 5, "dirty": np.inf},
            {"stock_id": "C", "trade_date": "2026-05-01", "constant": 5, "dirty": "bad"},
        ]
    )

    result = calculate_cross_sectional_zscore(df, ["constant", "dirty", "missing_factor"])

    assert result["constant_z"].tolist() == [0.0, 0.0, 0.0]
    assert result["dirty_z"].tolist() == [0.0, 0.0, 0.0]
    assert result["missing_factor_z"].tolist() == [0.0, 0.0, 0.0]


def test_news_sentiment_encoding_handles_known_and_invalid_values():
    from core.calc_indicators import encode_news_sentiment

    assert encode_news_sentiment("bullish") == 1
    assert encode_news_sentiment("positive") == 1
    assert encode_news_sentiment("neutral") == 0
    assert encode_news_sentiment(None) == 0
    assert encode_news_sentiment("unknown") == 0
    assert encode_news_sentiment("bearish") == -1
    assert encode_news_sentiment("negative") == -1


def test_multi_factor_matrix_preserves_rows_and_adds_neutral_optional_factors():
    from core.calc_indicators import (
        MULTI_FACTOR_COLUMNS,
        Z_SCORE_FEATURE_COLUMNS,
        build_multi_factor_matrix,
    )

    df = pd.DataFrame(
        [
            {
                "stock_id": "A",
                "trade_date": "2026-05-01",
                "rsi": 45,
                "bias": 1.5,
                "volume": 1000,
                "foreign_buy": 20,
                "trust_buy": 5,
                "dealer_buy": -3,
                "foreign_consec_days": 2,
                "trust_consec_days": 1,
                "large_holder_ratio": 42.0,
                "large_holder_ratio_change": 0.5,
            },
            {
                "stock_id": "B",
                "trade_date": "2026-05-01",
                "rsi": 55,
                "bias": -0.5,
                "volume": 2000,
                "foreign_buy": -10,
                "trust_buy": 0,
                "dealer_buy": 8,
            },
        ]
    )

    result = build_multi_factor_matrix(df, news_sentiment="bullish")

    assert len(result) == len(df)
    assert set(MULTI_FACTOR_COLUMNS).issubset(result.columns)
    assert set(Z_SCORE_FEATURE_COLUMNS).issubset(result.columns)
    assert result["institutional_net_buy"].tolist() == [22, -2]
    assert result["institutional_consec_buy_days"].tolist() == [3, 0]
    assert result["news_sentiment_score"].tolist() == [1, 1]
    assert result.loc[result["stock_id"] == "B", "large_holder_ratio"].iloc[0] == 0
    assert result[Z_SCORE_FEATURE_COLUMNS].isna().sum().sum() == 0


def test_train_model_uses_canonical_zscore_features_for_strategy():
    from core.calc_indicators import Z_SCORE_FEATURE_COLUMNS
    from jobs.train_model import resolve_model_feature_columns

    data = pd.DataFrame({name: [0.0, 1.0] for name in Z_SCORE_FEATURE_COLUMNS})
    data["rsi"] = [40, 60]
    strategy = SimpleNamespace(features=["rsi"])

    selected, missing = resolve_model_feature_columns(strategy, data)

    assert selected == Z_SCORE_FEATURE_COLUMNS
    assert missing == []


def test_run_strategy_backfills_missing_model_zscore_features(monkeypatch):
    from jobs import run_daily

    class FakeModel:
        def __init__(self):
            self.seen_columns = None

        def predict_proba(self, frame):
            self.seen_columns = list(frame.columns)
            return np.array([[0.2, 0.8], [0.7, 0.3]])

    class FakeStrategy:
        name = "v31_hybrid"
        display_name = "V31"
        target_return = 0.08
        look_ahead_days = 7
        features = ["rsi"]

        def filter_candidates(self, df):
            return df.copy()

    model = FakeModel()
    persisted = []
    monkeypatch.setattr(run_daily, "load_strategy_model", lambda strategy_name: (model, ["rsi_z", "missing_z"]))
    monkeypatch.setattr(run_daily.Config, "NEWS_BOOST_ENABLED", False)
    monkeypatch.setattr(
        run_daily,
        "_persist_strategy_recommendations",
        lambda candidates, strategy_name, date_str, engine: persisted.append(candidates.copy()) or (len(candidates), False),
    )

    market_df = pd.DataFrame(
        [
            {"stock_id": "A", "close_price": 10.0, "rsi_z": -1.0, "rsi": 40, "volume": 1000},
            {"stock_id": "B", "close_price": 20.0, "rsi_z": 1.0, "rsi": 60, "volume": 2000},
        ]
    )

    result = run_daily.run_strategy(FakeStrategy(), market_df, "2026-05-01", object())

    assert model.seen_columns == ["rsi_z", "missing_z"]
    assert result["missing_z"].tolist() == [0, 0]
    assert result["ai_score"].tolist() == [0.8, 0.3]
    assert persisted[0]["ai_score"].tolist() == [0.8, 0.3]


def test_v31_hybrid_model_path_uses_strategy_specific_artifact():
    from core import model_utils

    assert model_utils.get_model_path("v31_hybrid").replace("\\", "/").endswith(
        "ML_Data/pkl/stock_ai_model_v31_hybrid.pkl"
    )
