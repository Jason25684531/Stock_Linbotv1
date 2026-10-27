"""Shadow daily selection gate: only QUALIFIED_FOR_FORWARD strategies may record picks; every day is one append-only, hash-chained line.

Never writes MySQL, never pushes messages, never submits orders. ponytail: a local JSONL file; a production service needs the Owner's approval.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

QUALIFIED = "QUALIFIED_FOR_FORWARD"
PICK_FIELDS = ("stock_id", "stock_name", "signal_date", "strategy_version", "reason", "key_factors", "score", "rank",
               "data_asof", "data_status", "liquidity", "limit_status", "risks", "invalidation")
LEDGER = "ledger.jsonl"
HEAD = "head.json"  # record count and last sha256: a removed tail no longer verifies


def _sha(body: dict) -> str:
    return hashlib.sha256(json.dumps(body, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()


def _lines(root: Path) -> list[dict]:
    path = Path(root) / LEDGER
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line] if path.is_file() else []


def record_day(date: str, verdicts: dict[str, tuple[str, str]], picks_by_strategy: dict[str, list[dict]], root) -> dict:
    """verdicts: strategy -> (verdict, why). Picks of a strategy that is not QUALIFIED_FOR_FORWARD are rejected, never recorded."""
    from datetime import date as _date
    if _date.fromisoformat(date).isoformat() != date:
        raise ValueError(f"date must be YYYY-MM-DD: {date!r}")
    if verify(root)["status"] != "PASS":
        raise ValueError("the ledger chain does not verify; nothing is appended to a broken chain")
    previous = _lines(root)
    if any(r["date"] == date for r in previous):
        raise ValueError(f"{date} already recorded; shadow records are never rewritten")
    picks, rejected = [], {}
    for strategy, (status, why) in sorted(verdicts.items()):
        if status != QUALIFIED:
            rejected[strategy] = f"REJECTED_UNQUALIFIED: {status} ({why})"
            continue
        for pick in picks_by_strategy.get(strategy, []):
            missing = [f for f in PICK_FIELDS if pick.get(f) in (None, "")]
            if missing:
                raise ValueError(f"{strategy} pick {pick.get('stock_id')} misses {missing}")
            picks.append({"strategy": strategy, **{f: pick[f] for f in PICK_FIELDS}})
    body = {"date": date, "recorded_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "status": "SELECTION" if picks else "NO_QUALIFIED_SELECTION", "picks": picks, "rejected": rejected,
            "advice": "NOT_INVESTMENT_ADVICE", "broker": "DISABLED", "prev_sha256": previous[-1]["sha256"] if previous else None}
    record = {**body, "sha256": _sha(body)}
    Path(root).mkdir(parents=True, exist_ok=True)
    with (Path(root) / LEDGER).open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False) + "\n")
    (Path(root) / HEAD).write_text(json.dumps({"records": len(previous) + 1, "sha256": record["sha256"]}), encoding="utf-8")
    return record


def verify(root) -> dict:
    """Recompute every hash and the chain; report the first line (1-based) that does not match."""
    lines, prev = _lines(root), None
    for i, record in enumerate(lines, 1):
        body = {k: v for k, v in record.items() if k != "sha256"}
        if record["sha256"] != _sha(body) or body["prev_sha256"] != prev:
            return {"status": "FAIL", "records": len(lines), "first_bad_line": i}
        prev = record["sha256"]
    head_path = Path(root) / HEAD
    head = json.loads(head_path.read_text(encoding="utf-8")) if head_path.is_file() else {"records": 0, "sha256": None}
    if head != {"records": len(lines), "sha256": prev}:  # ponytail: the anchor sits next to the ledger; an off-host copy guards against deleting both
        return {"status": "FAIL", "records": len(lines), "first_bad_line": min(len(lines), head["records"]) + 1, "reason": "head anchor mismatch (tail removed or ledger replaced)"}
    return {"status": "PASS", "records": len(lines)}
