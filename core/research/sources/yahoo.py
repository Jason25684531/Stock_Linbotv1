"""yfinance reconciliation-only source adapter."""

from datetime import date, datetime

import yfinance as yf

from core.research.sources import RawResponse


def fetch_history(symbol: str, start: date, end: date) -> RawResponse:
    parameters = {"start": start.isoformat(), "end": end.isoformat(), "auto_adjust": False, "actions": True, "keepna": True, "repair": False, "interval": "1d"}
    try:
        payload = yf.Ticker(symbol).history(**parameters)
        error = None
    except Exception as caught:
        payload, error = None, str(caught)
    return RawResponse("yfinance", "history", parameters, datetime.now(), None, payload, error)
