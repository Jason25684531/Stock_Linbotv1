"""Domain recommendation contracts and explicit compatibility mappings."""

from .contracts import (
    DateValue,
    Recommendation,
    RecommendationRow,
    RecommendationValidationError,
)
from .normalizer import (
    normalize_legacy_row,
    serialize_recommendation,
    to_legacy_mapping,
)

__all__ = [
    "DateValue",
    "Recommendation",
    "RecommendationRow",
    "RecommendationValidationError",
    "normalize_legacy_row",
    "serialize_recommendation",
    "to_legacy_mapping",
]
