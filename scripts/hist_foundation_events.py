"""2023-2025 capital reductions and par-value changes from the verified t1b cache (change 2026-11-10-engine-accounting-and-history-gaps-v1, F06).

    python -m scripts.hist_foundation_events

The Owner-approved t1b round (2026-10-24) already fetched TWTAUU and TWTB8U for 2022-01-01..2026-10-23 and verified the responses; the raw
bodies sit in outputs/finlab_recovery/2026-10-24/t1b_official/*.bin. This script writes, once, the two snapshot source files
event_sources/TWTAUU_20230101_20251231.json and TWTB8U_20230101_20251231.json with the rows dated 2023-01-01..2025-12-31 and the provenance
of the body they came from (file, SHA256, request window, ledger line). No HTTP, no row is altered, 2026 rows are never copied.
"""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

T1B = ROOT / 'outputs/finlab_recovery/2026-10-24/t1b_official'
EVENTS = ROOT / 'outputs/research_mode/h4_hist_unlock_v1/event_sources'
WINDOW = ('20230101', '20251231')
DATE_FIELD = '恢復買賣日期'  # 恢復買賣日期
REQUIRED = {DATE_FIELD, '股票代號', '停止買賣前收盤價格', '恢復買賣參考價'}


def _roc_to_iso(value: str) -> str:
    year, month, day = str(value).strip().split('/')
    return f'{int(year) + 1911:04d}{int(month):02d}{int(day):02d}'


def verified_body(kind: str) -> tuple[Path, dict]:
    """The t1b body pinned by core.runtime.public_data.T1B_VERIFIED (file name and SHA256); any other body of the window is ignored."""
    from core.runtime.public_data import T1B_VERIFIED
    filename, digest, _ = T1B_VERIFIED[kind]
    path = T1B / filename
    raw = path.read_bytes()
    if hashlib.sha256(raw).hexdigest() != digest:
        raise ValueError(f'{kind}: {filename} does not match its pinned SHA256')
    payload = json.loads(raw)
    if payload.get('stat') != 'OK' or not REQUIRED <= set(payload.get('fields') or []):
        raise ValueError(f'{kind}: pinned body is not a verified reference-price table')
    return path, payload


def derive(kind: str) -> dict:
    path, payload = verified_body(kind)
    at = payload['fields'].index(DATE_FIELD)
    rows = [r for r in payload['data'] if WINDOW[0] <= _roc_to_iso(r[at]) <= WINDOW[1]]
    ledger_lines = [line for line in (T1B / 't1b_request_ledger.jsonl').read_text(encoding='utf-8').splitlines() if kind in line]
    return {'stat': 'OK', 'fields': payload['fields'], 'data': rows, 'derived_from': {
        'file': path.relative_to(ROOT).as_posix(), 'sha256': hashlib.sha256(path.read_bytes()).hexdigest(), 'request_window': ['20220101', '20261023'],
        'kept_window': list(WINDOW), 'rows_in_body': len(payload['data']), 'rows_kept': len(rows), 'budget_job': 't1b',
        'ledger_lines': ledger_lines[-2:], 'note': 'rows copied unchanged; 2022 and 2026 rows excluded; no HTTP'}}


def main() -> int:
    EVENTS.mkdir(parents=True, exist_ok=True)
    out = {}
    for kind in ('TWTAUU', 'TWTB8U'):
        target = EVENTS / f'{kind}_{WINDOW[0]}_{WINDOW[1]}.json'
        if target.exists():
            out[kind] = {'file': target.relative_to(ROOT).as_posix(), 'state': 'REUSE', 'sha256': hashlib.sha256(target.read_bytes()).hexdigest()}
            continue
        body = derive(kind)
        with open(target, 'x', encoding='utf-8') as stream:
            json.dump(body, stream, ensure_ascii=False, indent=1)
        out[kind] = {'file': target.relative_to(ROOT).as_posix(), 'state': 'WRITTEN', 'rows': body['derived_from']['rows_kept'],
                     'from': body['derived_from']['file'], 'from_sha256': body['derived_from']['sha256'], 'sha256': hashlib.sha256(target.read_bytes()).hexdigest()}
    print(json.dumps(out, ensure_ascii=False))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
