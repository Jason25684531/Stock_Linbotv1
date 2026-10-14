"""Opt-in MySQL/Flask smoke checks guarded by a dedicated disposable database."""

import os

import pandas as pd
import pytest
from sqlalchemy import text
from sqlalchemy.engine import make_url


@pytest.mark.integration
def test_isolated_daily_replacement_heartbeat_and_dry_run():
    from core.db_helper import get_db_engine, RECOMMENDATION_HEARTBEAT_STOCK_ID
    from jobs.run_daily import _persist_strategy_recommendations

    requested = os.environ.get("CLEANUP_TEST_DB_URL")
    if not requested:
        pytest.skip("requires an explicitly isolated cleanup database")
    url = make_url(requested)
    assert url.database == "cleanup_probe" and url.host == "127.0.0.1"
    engine = get_db_engine()
    assert engine.url == url
    with engine.begin() as conn:
        conn.execute(text("""CREATE TABLE daily_recommendations (
            stock_id VARCHAR(10), trade_date DATE, strategy VARCHAR(80),
            close_price DOUBLE, ai_score DOUBLE, rsi DOUBLE, volume BIGINT,
            news_boost_reason TEXT
        )"""))
    candidates = pd.DataFrame([{"stock_id": "2330", "close_price": 100.0, "ai_score": 0.8}])
    for _ in range(2):
        assert _persist_strategy_recommendations(candidates, "v31_hybrid", "2026-10-14", engine) == (1, False)
    _persist_strategy_recommendations(candidates, "v33_low_vol", "2026-10-14", engine)
    with engine.connect() as conn:
        assert conn.execute(text("SELECT COUNT(*) FROM daily_recommendations")).scalar() == 2
    assert _persist_strategy_recommendations(pd.DataFrame(), "v31_hybrid", "2026-10-14", engine) == (1, True)
    with engine.connect() as conn:
        before = conn.execute(text("SELECT stock_id, strategy FROM daily_recommendations ORDER BY strategy")).all()
        assert before == [(RECOMMENDATION_HEARTBEAT_STOCK_ID, "v31_hybrid"), ("2330", "v33_low_vol")]
    assert _persist_strategy_recommendations(candidates, "v31_hybrid", "2026-10-14", engine, dry_run=True) == (1, False)
    with engine.connect() as conn:
        assert conn.execute(text("SELECT stock_id, strategy FROM daily_recommendations ORDER BY strategy")).all() == before


def test_application_health_without_network_or_line_delivery():
    from app import app
    from config import Config

    response = app.test_client().get(Config.APP_HEALTH_PATH)
    assert response.status_code == 200
    assert response.get_json()["status"] == "ok"
