"""Tests for source adapter response envelopes."""

from dataclasses import FrozenInstanceError
from datetime import datetime

import pytest

from core.research.sources import RawResponse


def test_raw_response_is_frozen_and_preserves_success_payload():
    response = RawResponse(
        source="twse",
        endpoint="MI_INDEX",
        request_parameters={"date": "20230103"},
        retrieved_at=datetime(2026, 7, 30),
        source_revision="20230103",
        payload={"stat": "OK"},
        error=None,
    )

    assert response.payload == {"stat": "OK"}
    with pytest.raises(FrozenInstanceError):
        response.source = "other"


def test_raw_response_rejects_payload_when_request_failed():
    with pytest.raises(ValueError, match="payload"):
        RawResponse(
            source="twse",
            endpoint="MI_INDEX",
            request_parameters={},
            retrieved_at=datetime(2026, 7, 30),
            source_revision=None,
            payload={"stat": "OK"},
            error="timeout",
        )
