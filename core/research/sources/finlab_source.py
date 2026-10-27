"""FinLab source adapter (2026-10-24-recover-data-gaps-with-finlab): one approved `data.get` per field key, raw frame saved with its SHA-256,
every download in its own ledger (never the TWSE request ledger). Quota or permission errors stop the run; nothing is filled or guessed.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

STOP_WORDS = ("quota", "額度", "limit", "vip", "permission", "權限", "usage", "exceed", "login", "token")


class FinlabStop(RuntimeError):
    """A quota, permission or login problem: the Owner must be told before anything else is downloaded."""


def _log(ledger: Path, **entry) -> None:
    ledger.parent.mkdir(parents=True, exist_ok=True)
    with ledger.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps({"ts": datetime.now(timezone.utc).isoformat(timespec="seconds"), **entry}, ensure_ascii=False) + "\n")


def fetch(key: str, raw_dir: Path, ledger: Path):
    """Download one FinLab key (cached file reused, never overwritten); returns the frame. Raises FinlabStop on quota/permission/login trouble."""
    import pandas as pd
    path = Path(raw_dir) / (key.replace(":", "__").replace("/", "_") + ".parquet")
    if path.exists():
        return pd.read_parquet(path)
    import os
    if not os.environ.get("FINLAB_API_TOKEN"):  # finlab 1.5.3 reads only this variable; without it, it opens an interactive login and hangs
        from dotenv import load_dotenv
        load_dotenv(Path(__file__).resolve().parents[3] / ".env")
    if not os.environ.get("FINLAB_API_TOKEN"):
        raise FinlabStop("login: FINLAB_API_TOKEN is not set (environment or .env)")
    from finlab import data
    _log(ledger, event="REQUEST", key=key)
    try:
        frame = pd.DataFrame(data.get(key))
    except Exception as exc:  # the message may name the account limit; never print credentials (finlab does not echo the token)
        text = f"{type(exc).__name__}: {exc}"[:300]
        _log(ledger, event="FAILED", key=key, error=text)
        if any(w in text.lower() for w in STOP_WORDS):
            raise FinlabStop(text) from exc
        raise
    path.parent.mkdir(parents=True, exist_ok=True)
    frame.columns = frame.columns.astype(str)
    frame.to_parquet(path)
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    _log(ledger, event="RECEIVED", key=key, rows=len(frame), columns=frame.shape[1], first=str(frame.index.min()), last=str(frame.index.max()), sha256=digest)
    return frame
