from __future__ import annotations

import json

import pandas as pd

from core.runtime import fundamental_advancement as advancement


def test_current_run_date_is_dynamic_and_normalized(monkeypatch):
    monkeypatch.setenv("CURRENT_RUN_DATE", "2026-09-17T22:00:00+08:00")
    assert advancement.resolve_current_run_date().date().isoformat() == "2026-09-17"


def test_frozen_contract_and_archived_dataset_are_unchanged():
    contract = advancement.load_frozen_contract()
    assert contract["frozen"]["strategy_fingerprint"] == advancement.FINGERPRINT
    assert contract["minimum"]["trading_days"] == 180
    assert advancement.verify_archived_immutability()["ARCHIVED_RESEARCH_DATASET_IMMUTABLE"] == "PASS"


def test_cache_only_without_captured_market_data_fails_closed(tmp_path):
    quotes, report = advancement.refresh_market_data(
        tmp_path,
        pd.Timestamp("2026-07-29"),
        pd.Timestamp("2026-07-30"),
        ("1101",),
        cache_only=True,
    )
    assert quotes.empty
    assert report["refresh_status"] == "BLOCKED"
    assert report["missing_requests"] == 2


def test_ledger_identity_is_deterministic():
    frame = pd.DataFrame({"stock_id": ["2", "1"], "target_weight": [0.4, 0.6]})
    assert advancement._hash_frame(frame) == advancement._hash_frame(frame)

