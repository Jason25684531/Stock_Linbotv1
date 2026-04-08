import pandas as pd


def test_apply_news_sentiment_overlay_reorders_candidates(monkeypatch):
    import app as app_module

    candidates = pd.DataFrame(
        [
            {"stock_id": "2330", "ai_score": 0.70, "close_price": 950.0},
            {"stock_id": "2317", "ai_score": 0.68, "close_price": 205.0},
        ]
    )

    monkeypatch.setattr(
        app_module,
        "get_news_sentiment",
        lambda date_str=None: {
            "bull_sectors": ["AI伺服器"],
            "bear_sectors": [],
            "bull_theme_map": {"AI伺服器": "GB200 供應鏈追單"},
            "bear_theme_map": {},
        },
    )
    monkeypatch.setattr(
        app_module,
        "_get_stock_mentions_map",
        lambda stock_ids: {},
    )
    monkeypatch.setattr(
        app_module,
        "get_stock_sector",
        lambda stock_id: {"2330": "半導體", "2317": "AI伺服器"}[str(stock_id)],
    )

    boosted = app_module._apply_news_sentiment_overlay(candidates, "2026-04-02")

    assert list(boosted["stock_id"]) == ["2317", "2330"]
    assert "GB200 供應鏈追單" in boosted.iloc[0]["news_boost_reason"]
