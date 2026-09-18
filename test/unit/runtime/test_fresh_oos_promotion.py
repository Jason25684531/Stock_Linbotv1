from __future__ import annotations

import json

import pandas as pd

from core.runtime import fresh_oos_promotion as continuation
from core.runtime import fundamental_shadow as shadow


def test_contract_is_frozen_and_current_date_is_dynamic(monkeypatch):
    contract = continuation.load_contract()
    assert contract["minimum"] == {"months": 9, "rebalances": 3, "trading_days": 180}
    monkeypatch.setenv("CURRENT_RUN_DATE", "2026-09-17T23:00:00+08:00")
    assert continuation.resolve_current_run_date().date().isoformat() == "2026-09-17"


def test_minimum_evidence_precedes_performance(tmp_path):
    ledger = pd.DataFrame({"daily_return": [0.5], "rebalance_flag": [True], "turnover": [1.0], "cost": [0.0]})
    result = continuation.evaluate_once(tmp_path, {"status": "INSUFFICIENT_DATA"}, ledger, continuation.load_contract())
    assert result["status"] == "INSUFFICIENT_DATA"
    assert not (tmp_path / "FreshOOSResult.json").exists()


def test_evaluation_is_written_once_and_uses_frozen_fingerprint(tmp_path):
    ledger = pd.DataFrame({
        "daily_return": [0.001] * 180,
        "rebalance_flag": ([True] + [False] * 59) * 3,
        "turnover": [1.0, 0.0, 0.0] + [0.0] * 177,
        "cost": [0.0] * 180,
    })
    availability = {"status": "PASS", "trading_days": 180, "calendar_months": 9, "rebalance_count": 3}
    first = continuation.evaluate_once(tmp_path, availability, ledger, continuation.load_contract())
    before = (tmp_path / "FreshOOSResult.json").read_bytes()
    second = continuation.evaluate_once(tmp_path, availability, ledger.assign(daily_return=-0.5), continuation.load_contract())
    assert first["status"] == "PASS"
    assert second == first
    assert (tmp_path / "FreshOOSResult.json").read_bytes() == before


def test_append_is_cutoff_safe_and_idempotent(tmp_path, monkeypatch):
    source = tmp_path / "source"
    source.mkdir()
    ledger_path = source / "FreshOOSLedger.csv"
    pd.DataFrame(columns=continuation.LEDGER_COLUMNS).to_csv(ledger_path, index=False)
    monkeypatch.setattr(continuation, "PRIOR_OUTPUT_ROOT", source)

    class FakeAdapter:
        universe = pd.DataFrame({"asof_date": pd.to_datetime(["2026-07-28", "2026-07-29"]), "asset_id": ["1", "1"], "member": [True, True], "is_tradable_t1": [True, True], "execution_date": pd.to_datetime(["2026-07-29", "2026-07-30"]), "entry_price": [1.0, 1.0]})

        def __init__(self, *_args, **_kwargs):
            pass

        def health(self, day):
            return {"reason_codes": [], "data_freshness": "HEALTHY", "FACTOR_HEALTH": "PASS", "eligible_asset_count": 1, "coverage_ratio": 1.0}

        def selection(self, day):
            return pd.DataFrame({"stock_id": ["1"], "score": [1.0], "rank": [1], "selected": [True], "target_weight": [1.0], "reason": ["SELECTED"]}), "REBALANCE"

    monkeypatch.setattr(continuation.shadow, "CanonicalSelectionAdapter", FakeAdapter)
    class FakeLoader:
        def load(self):
            return object()

    monkeypatch.setattr(continuation.shadow, "FrozenStrategyLoader", FakeLoader)
    first = continuation.append_valid_observations(tmp_path / "out", tmp_path / "snapshot", pd.Timestamp("2026-07-29"))
    second = continuation.append_valid_observations(tmp_path / "out", tmp_path / "snapshot", pd.Timestamp("2026-07-29"))
    assert first["appended_rows"] == 1
    assert second["appended_rows"] == 0
    assert len(pd.read_csv(tmp_path / "out" / "FreshOOSLedger.csv")) == 1
    assert json.loads((tmp_path / "out" / "fresh_oos_lineage_audit.json").read_text())["FRESH_OOS_LINEAGE"] == "PASS"


def test_promotion_cannot_auto_enable_live():
    evidence = {key: "PASS" for key in ("FROZEN_STRATEGY_IMPORT", "RESEARCH_RUNTIME_PARITY", "RUNTIME_REPLAY", "DATA_FRESHNESS_GATE", "FACTOR_HEALTH_GATE", "SHADOW_EXECUTION", "SHADOW_ORDER_ISOLATION", "KILL_SWITCH", "ROLLBACK", "LEGACY_STRATEGY_REGRESSION")}
    evidence["INDEPENDENT_REVIEW"] = "APPROVED"
    review = continuation._promotion(evidence, "INSUFFICIENT_DATA")
    assert review["PRODUCTION_PROMOTION_STATUS"] == "SHADOW_APPROVED"
    assert review["BROKER_ORDER_SUBMISSION"] == "DISABLED"
    assert review["AUTO_LIVE_PROMOTION"] == "NO"
