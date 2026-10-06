"""Immutable recommendation records shared at application boundaries."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date, datetime
from numbers import Real
from typing import Any, TypeAlias

DateValue: TypeAlias = str | date | datetime


class RecommendationValidationError(ValueError):
    """Raised when a canonical recommendation row is invalid."""


def _serialize_date(value: DateValue) -> str | DateValue:
    return value.isoformat() if isinstance(value, (date, datetime)) else value


@dataclass(frozen=True)
class Recommendation:
    """Canonical selection row with optional compatibility observations."""

    stock_id: str
    strategy_id: str
    asof_date: DateValue
    score: Real | None = None
    rank: int | None = None
    selected: bool | None = None
    target_weight: Real | None = None
    reason: str | None = None
    close_price: Real | None = None
    ai_score: Real | None = None
    rsi: Real | None = None
    volume: Real | None = None
    news_boost_reason: str | None = None

    def __post_init__(self) -> None:
        for field_name in ("stock_id", "strategy_id"):
            value = getattr(self, field_name)
            if not isinstance(value, str) or not value.strip():
                raise RecommendationValidationError(
                    f"{field_name} must be a non-empty string"
                )
            object.__setattr__(self, field_name, value.strip())
        if self.asof_date is None or (
            isinstance(self.asof_date, str) and not self.asof_date.strip()
        ):
            raise RecommendationValidationError("asof_date must be present")
        if self.rank is not None and (
            isinstance(self.rank, bool) or not isinstance(self.rank, int)
        ):
            raise RecommendationValidationError("rank must be an integer or None")
        if self.selected is not None and not isinstance(self.selected, bool):
            raise RecommendationValidationError("selected must be a boolean or None")
        if self.reason is not None and not isinstance(self.reason, str):
            raise RecommendationValidationError("reason must be a string or None")
        if self.news_boost_reason is not None and not isinstance(
            self.news_boost_reason, str
        ):
            raise RecommendationValidationError(
                "news_boost_reason must be a string or None"
            )
        for field_name in (
            "score",
            "target_weight",
            "close_price",
            "ai_score",
            "rsi",
            "volume",
        ):
            value = getattr(self, field_name)
            if value is not None and (
                isinstance(value, bool) or not isinstance(value, Real)
            ):
                raise RecommendationValidationError(
                    f"{field_name} must be numeric or None"
                )

    def to_dict(self, *, include_none: bool = True) -> dict[str, Any]:
        """Serialize without inventing values for absent source semantics."""

        payload = asdict(self)
        payload["asof_date"] = _serialize_date(self.asof_date)
        if include_none:
            return payload
        return {key: value for key, value in payload.items() if value is not None}

    as_dict = to_dict


RecommendationRow = Recommendation

__all__ = [
    "DateValue",
    "Recommendation",
    "RecommendationRow",
    "RecommendationValidationError",
]
