"""JSON-safe immutable records exposed by the diagnostics service."""

import json
from dataclasses import asdict, dataclass
from typing import Any


@dataclass(frozen=True)
class DataSourceStatus:
    source_id: str
    source_type: str
    provider: str
    pipeline: str
    latest_date: Any = "N/A"
    row_count: Any = "N/A"
    freshness: Any = "N/A"
    missing_count: Any = "N/A"
    duplicate_count: Any = "N/A"
    status: str = "UNKNOWN"
    source_location: str = "N/A"

@dataclass(frozen=True)
class FactorStatus:
    factor_id: str
    display_name: str
    family: str
    role: str
    data_type: str
    required_fields: tuple[str, ...]
    lookback: Any
    direction: Any
    validation_status: str
    primary_horizon: Any
    ic: Any = "N/A"
    rank_ic: Any = "N/A"
    icir: Any = "N/A"
    positive_ic_ratio: Any = "N/A"
    coverage: Any = "N/A"
    temporal_stability: Any = "N/A"
    redundancy_status: Any = "N/A"
    used_by_strategies: tuple[str, ...] = ()
    source_artifact: str = "N/A"

@dataclass(frozen=True)
class StrategyStatus:
    strategy_id: str
    display_name: str
    stage: str
    factors: tuple[str, ...]
    factor_weights: dict[str, Any]
    top_n: Any
    rebalance: Any
    portfolio_weighting: Any
    historical_metrics: dict[str, Any]
    robustness: dict[str, Any]
    fresh_oos: dict[str, Any]
    runtime_health: dict[str, Any]
    production_status: dict[str, Any]
    block_reason: Any
    fingerprint: Any
    source_artifact: str


def record(value: Any) -> dict[str, Any]:
    """Convert a diagnostics record to a detached JSON-safe dictionary."""
    return json.loads(json.dumps(asdict(value), default=str))
