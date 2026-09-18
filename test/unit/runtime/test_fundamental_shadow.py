from __future__ import annotations

import json

import pandas as pd
import pytest

from core.runtime.fundamental_shadow import (
    CanonicalSelectionAdapter,
    FINGERPRINT,
    FrozenStrategyLoader,
    FundamentalRuntimeError,
    RUNTIME_STRATEGY_ID,
    validate_selection,
    run_shadow,
)
from core.strategy_manager import StrategyManager


@pytest.fixture(scope="module")
def adapter():
    return CanonicalSelectionAdapter(FrozenStrategyLoader().load())


def test_frozen_identity_and_unique_runtime_id():
    spec = FrozenStrategyLoader().load()
    assert spec.fingerprint == FINGERPRINT
    assert RUNTIME_STRATEGY_ID not in {"v31_hybrid", "v33_low_vol", "v34_turbo", "v35_innovation", "v36_chip_momentum", "v37_mean_reversion", "v38_value_dividend"}


def test_modified_frozen_field_rejected(tmp_path):
    payload = json.loads(FrozenStrategyLoader().path.read_text(encoding="utf-8"))
    payload["candidate"]["top_n"] = 10
    path = tmp_path / "FrozenStrategySpec.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(FundamentalRuntimeError, match="FROZEN_PARAMETER_MISMATCH"):
        FrozenStrategyLoader(path).load()


def test_top5_tie_is_deterministic_and_rows_are_canonical(adapter):
    first_target = pd.Timestamp(adapter.targets["asof_date"].min())
    left, action_left = adapter.selection(first_target)
    right, action_right = adapter.selection(first_target)
    pd.testing.assert_frame_equal(left, right)
    assert action_left == action_right
    selected = left[left["selected"]]
    assert len(selected) == 5
    assert selected["rank"].tolist() == [1, 2, 3, 4, 5]
    assert selected["stock_id"].is_unique


def test_non_rebalance_has_no_retarget(adapter):
    target_dates = set(pd.to_datetime(adapter.targets["asof_date"]).dt.date)
    non_rebalance = next(day for day in pd.to_datetime(adapter.universe["asof_date"]).drop_duplicates() if day.date() not in target_dates)
    rows, action = adapter.selection(non_rebalance)
    assert action == "NO_REBALANCE_ACTION"
    assert rows.loc[rows["selected"], "target_weight"].eq(0).all()


def test_pit_health_and_stale_gate(adapter):
    historical = adapter.health(adapter.universe["asof_date"].max())
    assert historical["data_freshness"] == "HEALTHY"
    assert historical["FACTOR_HEALTH"] == "PASS"
    current = adapter.health(pd.Timestamp("2099-01-01"))
    assert current["data_freshness"] == "STALE"
    assert "STALE_DATA" in current["reason_codes"]


def test_invalid_score_and_weight_fail_closed():
    rows = pd.DataFrame({
        "stock_id": ["1", "2"], "score": [float("nan"), 0.2], "rank": [1, 2],
        "selected": [True, True], "target_weight": [0.5, 0.5], "reason": ["SELECTED", "SELECTED"],
    })
    assert "INVALID_SCORE" in validate_selection(rows)


def test_shadow_disabled_by_default(tmp_path, monkeypatch):
    monkeypatch.delenv("FUNDAMENTAL_STRATEGY_ENABLED", raising=False)
    result = run_shadow("2026-07-27", output_root=tmp_path)
    assert result["status"] == "DISABLED"
    assert result["BROKER_ORDER_SUBMISSION"] == "DISABLED"


def test_isolated_runtime_registration_does_not_replace_legacy(monkeypatch):
    monkeypatch.setenv("FUNDAMENTAL_STRATEGY_ENABLED", "true")
    runtime = StrategyManager.runtime_strategy_registrations()
    assert RUNTIME_STRATEGY_ID in runtime
    assert runtime[RUNTIME_STRATEGY_ID]["ENABLED_FOR_LIVE"] == "NO"
    assert {"v31_hybrid", "v33_low_vol", "v34_turbo", "v35_innovation", "v36_chip_momentum", "v37_mean_reversion", "v38_value_dividend"}.issubset(StrategyManager.STRATEGY_REGISTRY)


def test_shadow_enabled_is_order_isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("FUNDAMENTAL_STRATEGY_ENABLED", "true")
    monkeypatch.setenv("FUNDAMENTAL_STRATEGY_SHADOW_ONLY", "true")
    result = run_shadow("2026-07-27", output_root=tmp_path)
    assert result["status"] == "SUCCESS"
    assert result["BROKER_ORDER_SUBMISSION"] == "DISABLED"
    assert (tmp_path / "shadow_runs" / result["run_id"] / "shadow_run_manifest.json").is_file()


def test_current_stale_shadow_is_blocked(tmp_path, monkeypatch):
    monkeypatch.setenv("FUNDAMENTAL_STRATEGY_ENABLED", "true")
    monkeypatch.setenv("FUNDAMENTAL_STRATEGY_SHADOW_ONLY", "true")
    result = run_shadow("2099-01-01", output_root=tmp_path)
    assert result["status"] == "BLOCKED"
    assert "STALE_DATA" in result["warnings"]
