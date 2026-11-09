"""Read-only provider for the TWSE MFI94U total-return index (發行量加權股價報酬指數).

Reads local files under data/benchmark/MFI94U/ only; it never fetches. Research reads are cut to 2025-12-31 before any
other processing and are refused unless the coverage check is READY. READY is a coverage check, not data certification.
Files: *.json in TWSE rwd form {'fields': [...], 'data': [[date, value, ...], ...]} or *.csv with a header row; in both the
first column is the date (ROC '112/01/03', ROC '1120103', ISO '2023-01-03' or '20230103') and the second the index value.
"""
from __future__ import annotations

import csv
import json
from datetime import date
from pathlib import Path

import pandas as pd

from core.research.strategy_signal import DEV_END, DEV_START, check_window

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_ROOT = ROOT / 'data' / 'benchmark' / 'MFI94U'
PREPARED_BARS = ROOT / 'outputs' / 'research_mode' / 'prepared_v6' / 'bars.json'
AUTH_REASON = 'no local MFI94U file; the download needs Owner authorization for a public research fetch'
SOURCES = ('Used: already-downloaded exchange MI_INDEX daily files, total-return index row (tri_from_mi_index.csv, per-file sha256 in '
           'tri_from_mi_index.provenance.md); same index MFI94U publishes, not cross-checked against MFI94U (not downloaded)',
           'TWSE OpenAPI /indicesReport/MFI94U', 'https://www.twse.com.tw/indicesReport/MFI94U',
           'https://data.gov.tw/dataset/11871')


class BenchmarkBlocked(ValueError):
    """The total-return benchmark is not READY; research must use no total-return value."""


def parse_date(text) -> date:
    value = str(text).strip()
    try:
        if '-' in value or '/' in value:
            year, month, day = (int(part) for part in value.replace('/', '-').split('-'))
        else:  # yyyymmdd or ROC yyymmdd
            year, month, day = int(value[:-4]), int(value[-4:-2]), int(value[-2:])
        return date(year + 1911 if year < 1911 else year, month, day)
    except ValueError:
        raise ValueError(f'UNPARSEABLE_DATE: {value!r}') from None


def _number(text) -> float:
    try:
        return float(str(text).replace(',', '').strip())
    except ValueError:
        return float('nan')


def _rows(path: Path):
    if path.suffix.lower() == '.json':
        return [row[:2] for row in json.loads(path.read_text(encoding='utf-8')).get('data') or []]
    with path.open(encoding='utf-8-sig', newline='') as handle:
        return [row[:2] for row in list(csv.reader(handle))[1:] if row]


def files(root=DEFAULT_ROOT) -> list[Path]:
    root = Path(root)
    return sorted(p for p in root.glob('*') if p.suffix.lower() in ('.json', '.csv')) if root.is_dir() else []


def load(root=DEFAULT_ROOT) -> pd.Series:
    """All local rows, sorted by date; duplicates kept so quality() can see them."""
    pairs = [(pd.Timestamp(parse_date(d)), _number(v)) for path in files(root) for d, v in _rows(path)]
    series = pd.Series([v for _, v in pairs], index=pd.DatetimeIndex([d for d, _ in pairs]), dtype=float, name='MFI94U')
    return series.sort_index(kind='stable')


def _assess(calendar, root):
    calendar = sorted(str(day)[:10] for day in calendar)
    if not calendar:
        raise ValueError('EMPTY_CALENDAR')
    check_window(calendar[0], calendar[-1])
    found = files(root)
    if not found:
        return None, {'status': 'IMPLEMENTED_BUT_AUTH_BLOCKED', 'reason': AUTH_REASON, 'files': [],
                      'calendar_days': len(calendar), 'covered_days': 0}
    raw = load(root)
    dropped = int((raw.index > pd.Timestamp(DEV_END)).sum())
    series = raw[raw.index <= pd.Timestamp(DEV_END)]  # 2026 rows leave before any other processing
    days = pd.DatetimeIndex(pd.to_datetime(calendar))
    in_range = series[(series.index >= days[0]) & (series.index <= days[-1])]
    present = set(in_range.index)
    iso = lambda values: sorted({stamp.strftime('%Y-%m-%d') for stamp in values})
    report = {
        'files': [p.name for p in found],
        'calendar_days': len(days),
        'calendar_start': calendar[0], 'calendar_end': calendar[-1],
        'covered_days': int(sum(day in present for day in days)),
        'missing_dates': iso(day for day in days if day not in present),
        'duplicate_dates': iso(in_range.index[in_range.index.duplicated()]),
        'non_positive_dates': iso(in_range.index[~(in_range > 0)]),
        'extra_dates': iso(set(in_range.index) - set(days)),
        'rows_after_2025_dropped': dropped,
    }
    problems = [key for key in ('missing_dates', 'duplicate_dates', 'non_positive_dates', 'extra_dates') if report[key]]
    report['status'] = 'DATA_BLOCKED' if problems else 'READY'
    report['reason'] = f'problems: {problems}' if problems else 'full coverage, unique positive values (not data certification)'
    return in_range, report


def quality(calendar, root=DEFAULT_ROOT) -> dict:
    return _assess(calendar, root)[1]


def research_series(calendar, root=DEFAULT_ROOT) -> pd.Series:
    """MFI94U aligned to the research calendar; raises BenchmarkBlocked unless quality is READY."""
    series, report = _assess(calendar, root)
    if report['status'] != 'READY':
        raise BenchmarkBlocked(f"{report['status']}: {report['reason']}")
    return series.reindex(pd.to_datetime(sorted(str(day)[:10] for day in calendar)))


MI_INDEX_RAW = ROOT / 'artifacts' / 'factors' / 'd3_full_20230103_20260728' / '_raw' / 'twse_rwd'
TRI_ROW = '發行量加權股價報酬指數 '.strip()


def extract_from_mi_index(raw=MI_INDEX_RAW, root=DEFAULT_ROOT, first='20221230', last='20251231') -> dict:
    """Write the official total-return index closes found in already-downloaded exchange MI_INDEX daily files (the same index MFI94U
    publishes) as tri_from_mi_index.csv. Files are selected by the date in their name before any is opened, so no 2026 file is read."""
    import hashlib
    chosen = [p for p in sorted(Path(raw).glob('MI_INDEX_*.json')) if first <= p.name[9:17] <= min(last, DEV_END.replace('-', ''))]
    rows, digests = [], {}
    for path in chosen:
        payload = path.read_bytes()
        digests[path.name] = hashlib.sha256(payload).hexdigest()
        for table in json.loads(payload.decode('utf-8')).get('tables') or []:
            hit = next((row for row in table.get('data') or [] if row and row[0] == TRI_ROW), None)
            if hit:
                rows.append((f'{path.name[9:13]}-{path.name[13:15]}-{path.name[15:17]}', str(hit[1]).replace(',', '')))
                break
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    with (root / 'tri_from_mi_index.csv').open('w', encoding='utf-8', newline='') as handle:
        writer = csv.writer(handle)
        writer.writerow(('date', 'total_return_index'))
        writer.writerows(rows)
    (root / 'tri_from_mi_index.provenance.md').write_text(
        '# Provenance\n\nSource: local exchange MI_INDEX daily files (row 發行量加權股價報酬指數 ), '
        f'{len(chosen)} files {first}..{last}; sha256 per file below. MFI94U itself was not downloaded (needs Owner authorization), '
        'so no cross-check against MFI94U was possible.\n\n' + '\n'.join(f'- {n} {d}' for n, d in digests.items()) + '\n', encoding='utf-8')
    return {'files_read': len(chosen), 'rows': len(rows)}


def research_calendar(path=PREPARED_BARS) -> list[str]:
    """Sorted prepared_v6 trading dates inside the development window (loads the large bars.json once)."""
    bars = json.loads(Path(path).read_text(encoding='utf-8'))['bars']
    return sorted(day for day in bars if DEV_START <= day <= DEV_END)


def write_quality_report(calendar, path, root=DEFAULT_ROOT) -> dict:
    report = quality(calendar, root)
    lines = [
        '# MFI94U total-return benchmark quality report', '',
        f"- Status: **{report['status']}**",
        f"- Reason: {report['reason']}",
        f"- Research calendar: {report.get('calendar_start', min(calendar))} .. {report.get('calendar_end', max(calendar))}, "
        f"{report['calendar_days']} trading days (prepared_v6 bars, development window only)",
        f"- Covered days: {report['covered_days']}",
        f"- Local files: {', '.join(report['files']) or 'none'} (expected under `data/benchmark/MFI94U/`)",
    ]
    for key in ('missing_dates', 'duplicate_dates', 'non_positive_dates', 'extra_dates'):
        if key in report:
            values = report[key]
            lines.append(f"- {key}: {len(values)}" + (f" ({', '.join(values[:50])}{' ...' if len(values) > 50 else ''})" if values else ''))
    if 'rows_after_2025_dropped' in report:
        lines.append(f"- Rows after 2025-12-31 dropped before processing: {report['rows_after_2025_dropped']}")
    lines += [
        '', '## Sources', '', *[f'- {source}' for source in SOURCES], '',
        '## License', '', '- To be confirmed by the Owner; not verified in this session.', '',
        '## Boundaries', '',
        '- The provider is read-only over local files and never fetches; no download was performed in this session.',
        '- Every research read drops rows after 2025-12-31 before any other processing; 2026 rows never reach research.',
        '- Benchmark READY is a coverage check, not data certification.',
        '- The H4 archive is untouched.', '',
    ]
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text('\n'.join(lines), encoding='utf-8')
    return report
