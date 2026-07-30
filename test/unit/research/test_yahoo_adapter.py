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
