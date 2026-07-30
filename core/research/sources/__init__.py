"""Research data-source adapters."""

from dataclasses import dataclass
from datetime import datetime
from typing import Mapping


@dataclass(frozen=True)
class RawResponse:
    """An immutable source response, successful or failed."""

    source: str
    endpoint: str
    request_parameters: Mapping[str, object]
    retrieved_at: datetime
    source_revision: str | None
    payload: object | None
    error: str | None

    def __post_init__(self) -> None:
        if self.error is not None and self.payload is not None:
            raise ValueError("payload must be None when error is set")
