"""TWSE official-source adapters."""

import json
from datetime import date, datetime
from pathlib import Path

import requests

from core.research.sources import RawResponse


MI_INDEX_URL = "https://www.twse.com.tw/rwd/zh/afterTrading/MI_INDEX"


def fetch_daily_quotes(trade_date: date, cache_dir: Path) -> RawResponse:
    """Fetch and cache one official daily closing-report response."""

    day = trade_date.strftime("%Y%m%d")
    parameters = {"date": day, "type": "ALL", "response": "json"}
    cache_path = Path(cache_dir) / f"MI_INDEX_{day}.json"
    if cache_path.exists():
        payload = json.loads(cache_path.read_text(encoding="utf-8"))
    else:
        response = requests.get(MI_INDEX_URL, params=parameters, timeout=30)
        response.raise_for_status()
        payload = response.json()
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        cache_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    revision = payload.get("date") if isinstance(payload, dict) else None
    return RawResponse(
        source="twse_rwd",
        endpoint="MI_INDEX",
        request_parameters=parameters,
        retrieved_at=datetime.now(),
        source_revision=revision,
        payload=payload,
        error=None,
    )
