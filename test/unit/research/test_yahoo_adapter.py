from datetime import date

from core.research.sources import yahoo


def test_fetch_history_uses_only_explicit_vendor_parameters(monkeypatch):
    received = {}

    class Ticker:
        def __init__(self, symbol):
            received["symbol"] = symbol

        def history(self, **kwargs):
            received["kwargs"] = kwargs
            return {"Close": [1]}

    monkeypatch.setattr(yahoo.yf, "Ticker", Ticker)

    response = yahoo.fetch_history("2330.TW", date(2023, 1, 1), date(2023, 1, 31))

    assert response.payload == {"Close": [1]}
    assert received == {
        "symbol": "2330.TW",
        "kwargs": {"start": "2023-01-01", "end": "2023-01-31", "auto_adjust": False, "actions": True, "keepna": True, "repair": False, "interval": "1d"},
    }


def test_fetch_history_records_runtime_source_metadata(monkeypatch):
    monkeypatch.setattr(yahoo.yf, "__version__", "0.2.test", raising=False)

    class Ticker:
        def __init__(self, symbol):
            pass

        def history(self, **kwargs):
            return {"Close": [1]}

    monkeypatch.setattr(yahoo.yf, "Ticker", Ticker)

    response = yahoo.fetch_history("2330.TW", date(2023, 1, 1), date(2023, 1, 31))

    assert response.metadata == {
        "ticker": "2330.TW",
        "package_version": "0.2.test",
        "request_parameters": response.request_parameters,
        "requested_period": {"start": "2023-01-01", "end": "2023-01-31"},
        "retrieved_at": response.retrieved_at,
        "repair_status": None,
        "source_error": None,
    }


def test_fetch_history_isolates_an_unavailable_vendor(monkeypatch):
    class Ticker:
        def __init__(self, symbol):
            pass

        def history(self, **kwargs):
            raise RuntimeError("vendor unavailable")

    monkeypatch.setattr(yahoo.yf, "Ticker", Ticker)

    response = yahoo.fetch_history("2330.TW", date(2023, 1, 1), date(2023, 1, 31))

    assert response.payload is None
    assert response.error == "vendor unavailable"
    assert response.metadata["source_error"] == "vendor unavailable"
