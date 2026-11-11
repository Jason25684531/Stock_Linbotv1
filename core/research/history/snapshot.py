"""Content-addressed 2015-2025 research snapshot built only from local raw files (design D1). No HTTP, no MySQL, raw files read-only.

Parts: bars (raw OHLCV of every listed four-digit TWSE equity per session, untraded rows kept), calendar (every weekday), benchmark (price
and total-return index from the same MI_INDEX files), events (TWT49U with the previous session's MI_INDEX close), capital_changes
(TWTAUU/TWTB8U), delistings, chips (T86/MI_MARGN availability), integrity. Adjusted prices are derived on read, never stored over raw.
"""
from __future__ import annotations

import functools
import hashlib
import json
import math
import os
import re
import shutil
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[3]
OUT = ROOT / 'outputs/historical_foundation'
SNAPSHOTS = OUT / 'snapshots'
DEV_END = '2025-12-31'
STOCK = re.compile(r'[1-9]\d{3}')
PRICE_INDEX, TOTAL_RETURN_INDEX = '發行量加權股價指數', '發行量加權股價報酬指數'
PARTS = ('bars', 'calendar', 'benchmark', 'events', 'capital_changes', 'delistings', 'chips', 'integrity')


class SnapshotDrift(RuntimeError):
    """A part or pinned file no longer matches its manifest SHA256."""


@dataclass(frozen=True)
class Sources:
    mi_index_dirs: tuple  # earlier directory wins for a date
    twt49u_files: tuple
    capital_files: tuple  # TWTAUU_/TWTB8U_<start>_<end>.json
    delisting_file: Path | None
    fetch_log: Path | None  # jsonl {file, sha256} written when the raw file was fetched
    chip_dir: Path | None
    pinned: dict = field(default_factory=dict)  # name -> engine input file (financials, revenue, publications, ...)
    reconcile: dict = field(default_factory=dict)  # name -> independent second-source file (FinLab, MySQL extract)
    window: tuple = ('2015-01-01', DEV_END)


def default_sources(extra_reconcile=None) -> Sources:
    from core.research.native_frame import PIT_RECORDS
    hist, d3 = ROOT / 'outputs/research_mode/hist_raw_v1', ROOT / 'artifacts/factors/d3_full_20230103_20260728/_raw/twse_rwd'
    events = ROOT / 'outputs/research_mode/h4_hist_unlock_v1/event_sources'
    finlab = ROOT / 'outputs/finlab_recovery/2026-10-24/raw'
    validation = ROOT / 'outputs/strategy_validation/2026-10-21'
    return Sources(
        mi_index_dirs=(hist, d3), twt49u_files=(hist / 'TWT49U_20150101_20221231.json', d3 / 'TWT49U_20230103_20260728.json'),
        capital_files=(events / 'TWTAUU_20150101_20221230.json', events / 'TWTB8U_20150101_20221230.json',
                       events / 'TWTAUU_20230101_20251231.json', events / 'TWTB8U_20230101_20251231.json'),  # scripts/hist_foundation_events.py
        delisting_file=ROOT / 'artifacts/official_dual_track_cache/suspendListing.json',
        fetch_log=ROOT / 'outputs/research_mode/h4_hist_unlock_v1/fetch_log.jsonl', chip_dir=ROOT / 'artifacts/official_chip_cache/twse',
        pinned={'financial_statements': validation / 'snapshot_financial_statements.parquet',
                'monthly_revenue': validation / 'snapshot_monthly_revenue.parquet', 'publication_records': PIT_RECORDS},
        reconcile={'finlab_close': finlab / 'price__收盤價.parquet', 'finlab_capital_reduction': finlab / 'capital_reduction_tse__twse_cap_divide_ratio.parquet',
                   'finlab_par_value_change': finlab / 'par_value_change_tse__twse_par_value_change_divide_ratio.parquet',
                   'finlab_dividend_announcement': finlab / 'dividend_announcement.parquet', **(extra_reconcile or {})})


def sha256(path) -> str:
    digest = hashlib.sha256()
    with open(path, 'rb') as stream:
        for block in iter(lambda: stream.read(1 << 20), b''):
            digest.update(block)
    return digest.hexdigest()


def _key(path: Path) -> str:
    path = Path(path).resolve()
    return path.relative_to(ROOT).as_posix() if path.is_relative_to(ROOT) else path.as_posix()


def _stamp(path, prefix) -> str:
    return Path(path).stem.removeprefix(prefix)


def mi_index_files(sources: Sources) -> dict[str, Path]:
    start, end = (d.replace('-', '') for d in sources.window)
    files = {}
    for directory in sources.mi_index_dirs:
        for path in sorted(Path(directory).glob('MI_INDEX_*.json')):
            day = _stamp(path, 'MI_INDEX_')
            if re.fullmatch(r'\d{8}', day) and start <= day <= end:
                files.setdefault(day, path)
    return files


def chip_files(sources: Sources) -> dict[tuple[str, str], Path]:
    if sources.chip_dir is None:
        return {}
    start, end = (d.replace('-', '') for d in sources.window)
    out = {}
    for path in Path(sources.chip_dir).glob('*.json'):
        match = re.fullmatch(r'(T86|MI_MARGN)_(\d{8})', path.stem)
        if match and start <= match.group(2) <= end:
            out[(match.group(1), match.group(2))] = path
    return out


def source_files(sources: Sources) -> dict[str, dict[str, Path]]:
    groups = {'mi_index': {f'MI_INDEX_{d}': p for d, p in mi_index_files(sources).items()},
              'twt49u': {Path(p).name: Path(p) for p in sources.twt49u_files if Path(p).exists()},
              'capital': {Path(p).name: Path(p) for p in sources.capital_files if Path(p).exists()},
              'delisting': {'suspendListing': Path(sources.delisting_file)} if sources.delisting_file and Path(sources.delisting_file).exists() else {},
              'chips': {f'{k}_{d}': p for (k, d), p in sorted(chip_files(sources).items())},
              'pinned': {k: Path(p) for k, p in sources.pinned.items() if Path(p).exists()},
              'reconcile': {k: Path(p) for k, p in sources.reconcile.items() if Path(p).exists()}}
    if sources.fetch_log and Path(sources.fetch_log).exists():
        groups['fetch_log'] = {'fetch_log': Path(sources.fetch_log)}
    return groups


def _canonical(value) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(',', ':'))


@dataclass(frozen=True)
class Snapshot:
    snapshot_id: str
    root: Path
    manifest: dict

    def frame(self, part: str) -> pd.DataFrame:
        return _read_part(self.root, part, self.manifest['parts'][part]['sha256'])

    def source(self, group: str, name: str) -> Path | None:
        """Path of a pinned or reconcile file after checking it still has the SHA256 recorded at build time."""
        entry = self.manifest['sources'].get(group, {}).get(name)
        if entry is None:
            return None
        path = Path(entry['path']) if Path(entry['path']).is_absolute() else ROOT / entry['path']
        if _cached_sha(str(path), path.stat().st_mtime_ns) != entry['sha256']:
            raise SnapshotDrift(f'{group}/{name} changed since snapshot {self.snapshot_id}')
        return path


@functools.lru_cache(maxsize=64)
def _cached_sha(path, _mtime):
    return sha256(path)


@functools.lru_cache(maxsize=16)
def _read_part(root, part, digest):
    path = Path(root) / f'{part}.parquet'
    if sha256(path) != digest:
        raise SnapshotDrift(f'{part}.parquet differs from manifest')
    return pd.read_parquet(path)


def build(sources: Sources | None = None, out: Path = SNAPSHOTS) -> tuple[Snapshot, str]:
    """BUILT or REUSE. The id hashes every source file, this builder and the window; an existing id is verified, never rewritten."""
    sources = sources or default_sources()
    if sources.window[1] > DEV_END:
        raise ValueError(f'RESEARCH_WINDOW_AFTER_{DEV_END}: {sources.window}')
    groups = source_files(sources)
    hashes = {g: {n: sha256(p) for n, p in files.items()} for g, files in groups.items()}
    builder = sha256(Path(__file__))
    snapshot_id = 'HS1-' + hashlib.sha256(_canonical({'window': sources.window, 'builder': builder, 'sources': hashes}).encode()).hexdigest()[:16]
    out = Path(out)
    target = out / snapshot_id
    if (target / 'manifest.json').exists():
        snap = load(snapshot_id, out)
        if problems := verify(snap):
            raise SnapshotDrift(f'{snapshot_id}: {problems}')
        _point(out, snap)
        return snap, 'REUSE'
    out.mkdir(parents=True, exist_ok=True)
    tmp = out / f'.{snapshot_id}.tmp-{os.getpid()}'
    shutil.rmtree(tmp, ignore_errors=True)
    tmp.mkdir()
    frames = _parts(sources, groups, hashes)
    parts = {}
    for name in PARTS:
        path = tmp / f'{name}.parquet'
        frames[name].to_parquet(path, index=False)
        parts[name] = {'sha256': sha256(path), 'rows': len(frames[name])}
    manifest = {'snapshot_id': snapshot_id, 'schema': 1, 'created_at': datetime.now().astimezone().isoformat(timespec='seconds'),
                'window': list(sources.window), 'builder_sha256': builder, 'http_requests': 0, 'parts': parts,
                'capital_spans': _capital_spans(groups['capital']),
                'sources': {g: {n: {'path': _key(groups[g][n]), 'sha256': h} for n, h in files.items()} for g, files in hashes.items()},
                'price_basis': 'RAW (adjustment factors in events; adjusted prices derived on read)', 'scope': 'TWSE_LISTED_FOUR_DIGIT_EQUITIES'}
    (tmp / 'manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=1), encoding='utf-8')
    try:
        os.replace(tmp, target)
    except OSError:
        shutil.rmtree(tmp, ignore_errors=True)  # another builder won the race with the same content
    snap = load(snapshot_id, out)
    _point(out, snap)
    return snap, 'BUILT'


def _point(out, snap):
    pointer = Path(out) / 'LATEST.json'
    body = json.dumps({'snapshot_id': snap.snapshot_id, 'manifest_sha256': sha256(snap.root / 'manifest.json')})
    if pointer.exists() and pointer.read_text(encoding='utf-8') == body:
        return
    tmp = pointer.with_suffix(f'.tmp-{os.getpid()}')
    tmp.write_text(body, encoding='utf-8')
    os.replace(tmp, pointer)


def load(snapshot_id: str | None = None, out: Path = SNAPSHOTS) -> Snapshot | None:
    out = Path(out)
    if snapshot_id is None:
        pointer = out / 'LATEST.json'
        if not pointer.exists():
            return None
        snapshot_id = json.loads(pointer.read_text(encoding='utf-8'))['snapshot_id']
    root = out / snapshot_id
    return Snapshot(snapshot_id, root, json.loads((root / 'manifest.json').read_text(encoding='utf-8')))


def verify(snap: Snapshot, deep: bool = False) -> list[str]:
    """[] when every part (and with deep=True every source file) still has its recorded SHA256."""
    problems = [f'PART_SHA_MISMATCH:{n}' for n, e in snap.manifest['parts'].items() if sha256(snap.root / f'{n}.parquet') != e['sha256']]
    if deep:
        for group, files in snap.manifest['sources'].items():
            for name, entry in files.items():
                path = Path(entry['path']) if Path(entry['path']).is_absolute() else ROOT / entry['path']
                if not path.exists() or sha256(path) != entry['sha256']:
                    problems.append(f'SOURCE_SHA_MISMATCH:{group}/{name}')
    return problems


def universe(snap: Snapshot, day) -> frozenset[str]:
    """Listed four-digit TWSE equities on `day`, read only from that day's MI_INDEX (never from a later listing snapshot)."""
    bars = snap.frame('bars')
    return frozenset(bars.loc[bars.trade_date == pd.Timestamp(day), 'stock_id'])


def adjusted_return(pre_close: float, close_after: float, factor: float) -> float:
    """Return across an ex-date on back-adjusted prices: equals the total return with cash and new shares at the ex reference price."""
    return close_after / (pre_close * factor) - 1


def adjusted_close(bars: pd.DataFrame, events: pd.DataFrame) -> pd.Series:
    """Back-adjusted close (raw close times the product of event factors with ex_date after the bar), aligned to `bars`."""
    factor = pd.Series(1.0, index=bars.index)
    valid = events[(events.event_factor > 0) & events.event_factor.notna()]
    for stock, group in valid.groupby('stock_id'):
        rows = bars.index[bars.stock_id == stock]
        if len(rows) == 0:
            continue
        group = group.sort_values('ex_date')
        suffix = list(pd.Series(group.event_factor.to_numpy()[::-1]).cumprod()[::-1]) + [1.0]
        position = group.ex_date.searchsorted(bars.loc[rows, 'trade_date'], side='right')
        factor.loc[rows] = [suffix[p] for p in position]
    return bars.close * factor


def _roc(text) -> pd.Timestamp:
    parts = re.findall(r'\d+', str(text))
    return pd.Timestamp(int(parts[0]) + 1911, int(parts[1]), int(parts[2]))


def _number(value):
    try:
        number = float(str(value).replace(',', '').strip())
        return number if math.isfinite(number) else math.nan
    except ValueError:
        return math.nan


INDEX_LABELS = {PRICE_INDEX: (PRICE_INDEX, '加權股價指數'),  # 加權股價指數: TWSE's short label (seen 2019-04-29)
                TOTAL_RETURN_INDEX: (TOTAL_RETURN_INDEX, '加權股價報酬指數')}  # 加權股價報酬指數


def _index_value(payload, name):
    for label in INDEX_LABELS.get(name, (name,)):  # the full label first, then the short label TWSE used on some sessions
        for table in payload.get('tables', []):
            for row in table.get('data') or []:
                if row and str(row[0]).strip() == label:
                    return _number(row[1])
    return math.nan


def _capital_spans(files) -> dict:
    spans = {}
    for name in files:
        kind, start, end = Path(name).stem.split('_')
        spans.setdefault(kind, []).append([f'{start[:4]}-{start[4:6]}-{start[6:]}', f'{end[:4]}-{end[4:6]}-{end[6:]}'])
    return spans


def _parts(sources: Sources, groups, hashes) -> dict[str, pd.DataFrame]:
    from core.research.adjustment.normalization import normalize_corporate_actions, normalize_twse_closing_table
    from core.research.sources import RawResponse
    from core.research.sources.twse import ResponseKind, classify, find_closing_table

    logged = {}
    if 'fetch_log' in groups:
        for line in groups['fetch_log']['fetch_log'].read_text(encoding='utf-8').splitlines():
            if line.strip():
                row = json.loads(line)
                logged[row['file']] = row.get('sha256')
    stamp = datetime(2026, 1, 1)
    files = mi_index_files(sources)
    calendar, bars, bench, integrity = [], [], [], []
    # every weekday plus any dated file (TWSE has Saturday make-up sessions; a weekend file is read like any other)
    for day in sorted(set(pd.bdate_range(*sources.window)) | {pd.Timestamp(d) for d in files}):
        key = day.strftime('%Y%m%d')
        path = files.get(key)
        if path is None:
            calendar.append({'date': day, 'kind': 'NO_FILE', 'source': None, 'sha256': None, 'logged_sha256': None})
            continue
        digest = hashes['mi_index'][f'MI_INDEX_{key}']
        payload = json.loads(path.read_text(encoding='utf-8'))
        kind = classify(RawResponse('twse', 'MI_INDEX', {}, stamp, None, payload, None)).kind
        label = {ResponseKind.TRADING_DAY: 'TRADING', ResponseKind.NON_TRADING_DAY: 'NON_TRADING'}.get(kind, 'UNCLASSIFIED')
        calendar.append({'date': day, 'kind': label, 'source': _key(path), 'sha256': digest, 'logged_sha256': logged.get(path.name)})
        if label != 'TRADING':
            continue
        table = find_closing_table(payload)
        names = {str(r[0]).strip(): str(r[1]).strip() for r in table.get('data', []) if len(r) >= 9}
        frame = normalize_twse_closing_table(table, day, stamp)
        if frame.empty:
            continue
        frame['name'] = frame.stock_id.map(names)
        bars.append(frame[['trade_date', 'stock_id', 'name', 'raw_open', 'raw_high', 'raw_low', 'raw_close', 'volume', 'amount']])
        bench.append({'date': day, 'taiex_price': _index_value(payload, PRICE_INDEX), 'taiex_total_return': _index_value(payload, TOTAL_RETURN_INDEX)})
    bars = pd.concat(bars, ignore_index=True) if bars else pd.DataFrame(
        columns=['trade_date', 'stock_id', 'name', 'raw_open', 'raw_high', 'raw_low', 'raw_close', 'volume', 'amount'])
    bars = bars.rename(columns={'raw_open': 'open', 'raw_high': 'high', 'raw_low': 'low', 'raw_close': 'close'})
    prices = bars[['open', 'high', 'low', 'close']].astype(float)
    traded = prices.notna().all(axis=1) & (bars.volume.astype(float) > 0)
    bars['status'] = traded.map({True: 'TRADED', False: 'NO_TRADE'})
    for row in bars[bars.duplicated(['trade_date', 'stock_id'], keep=False)].itertuples():
        integrity.append({'date': row.trade_date, 'stock_id': row.stock_id, 'issue': 'DUPLICATE_KEY'})
    outside = traded & ((prices.close > prices.high) | (prices.close < prices.low) | (prices.low > prices.high))
    for row in bars[outside].itertuples():
        integrity.append({'date': row.trade_date, 'stock_id': row.stock_id, 'issue': 'CLOSE_OUTSIDE_RANGE'})
    calendar = pd.DataFrame(calendar)
    for row in calendar[calendar.logged_sha256.notna() & (calendar.logged_sha256 != calendar.sha256)].itertuples():
        integrity.append({'date': row.date, 'stock_id': None, 'issue': 'RAW_SHA_DIFFERS_FROM_FETCH_LOG'})
    return {'bars': bars, 'calendar': calendar, 'benchmark': pd.DataFrame(bench, columns=['date', 'taiex_price', 'taiex_total_return']),
            'events': _events(sources, groups, bars, normalize_corporate_actions), 'capital_changes': _capital(groups['capital'], sources.window),
            'delistings': _delistings(groups['delisting'].get('suspendListing'), bars, sources.window),
            'chips': pd.DataFrame([{'date': pd.Timestamp(d), 'dataset': k, 'sha256': hashes['chips'][f'{k}_{d}'],
                                    'stat': json.loads(p.read_text(encoding='utf-8')).get('stat')} for (k, d), p in sorted(chip_files(sources).items())],
                                  columns=['date', 'dataset', 'sha256', 'stat']),
            'integrity': pd.DataFrame(integrity, columns=['date', 'stock_id', 'issue']).astype({'stock_id': 'string'})}


def _events(sources, groups, bars, normalize):
    frames = []
    for name, path in groups['twt49u'].items():
        payload = json.loads(path.read_text(encoding='utf-8'))
        frame = normalize(payload, datetime(2026, 1, 1))
        fields = payload['fields']
        frame['value'] = [_number(dict(zip(fields, r)).get('權值+息值')) for r in payload['data']]
        frames.append(frame.assign(source_file=name))
    columns = ['ex_date', 'stock_id', 'action_type', 'pre_ex_close', 'ex_reference_price', 'event_factor', 'value', 'source_file', 'prior_session_close']
    if not frames:
        return pd.DataFrame(columns=columns)
    events = pd.concat(frames, ignore_index=True)
    events['ex_date'] = pd.to_datetime(events.ex_date)
    start, end = (pd.Timestamp(d) for d in sources.window)
    events = events[(events.ex_date >= start) & (events.ex_date <= end) & events.stock_id.str.fullmatch(STOCK)]
    # only identical official rows collapse, keeping every file in source_file (';'-joined); distinct rows of one stock and day stay and the
    # ledger decides (same kind with other terms -> CONFLICTING_EVENT_TERMS)
    key = ['ex_date', 'stock_id', 'action_type', 'pre_ex_close', 'ex_reference_price']
    files = events.groupby(key, dropna=False).source_file.agg(lambda s: ';'.join(sorted(set(s))))
    events = events.drop_duplicates(key).drop(columns='source_file').merge(files.reset_index(), on=key, how='left')
    events = events.sort_values(['ex_date', 'stock_id']).reset_index(drop=True)
    traded = bars.loc[bars.status == 'TRADED', ['trade_date', 'stock_id', 'close']].sort_values('trade_date')
    prior = pd.merge_asof(events.sort_values('ex_date'), traded.rename(columns={'trade_date': 'ex_date', 'close': 'prior_session_close'}),
                          on='ex_date', by='stock_id', allow_exact_matches=False)
    return prior.sort_values(['ex_date', 'stock_id']).reset_index(drop=True)[columns]


REASON_FIELD = '減資原因'  # 減資原因 (TWTAUU only)


def _capital(files, window):
    rows = []
    for name, path in files.items():
        kind = 'CAPITAL_REDUCTION' if name.startswith('TWTAUU') else 'PAR_VALUE_CHANGE'
        payload = json.loads(path.read_text(encoding='utf-8'))
        fields = payload.get('fields') or []
        reason_at = fields.index(REASON_FIELD) if REASON_FIELD in fields else None
        for r in payload.get('data', []):
            if len(r) > 1 and STOCK.fullmatch(str(r[1]).strip()):
                rows.append({'date': _roc(r[0]), 'stock_id': str(r[1]).strip(), 'kind': kind,
                             'pre_close': _number(r[3]) if len(r) > 3 else math.nan, 'reference': _number(r[4]) if len(r) > 4 else math.nan,
                             'reason': str(r[reason_at]).strip() if reason_at is not None and len(r) > reason_at else '', 'source_file': name})
    frame = pd.DataFrame(rows, columns=['date', 'stock_id', 'kind', 'pre_close', 'reference', 'reason', 'source_file'])
    return frame[(frame.date >= pd.Timestamp(window[0])) & (frame.date <= pd.Timestamp(window[1]))].reset_index(drop=True)


def _delistings(path, bars, window):
    columns = ['stock_id', 'name', 'delisting_date', 'first_listed', 'last_listed', 'last_traded']
    if path is None:
        return pd.DataFrame(columns=columns)
    rows = [{'stock_id': str(r['Code']).strip(), 'name': r.get('Company'), 'delisting_date': _roc(r['DelistingDate'])}
            for r in json.loads(Path(path).read_text(encoding='utf-8')) if STOCK.fullmatch(str(r.get('Code', '')).strip())]
    frame = pd.DataFrame(rows, columns=['stock_id', 'name', 'delisting_date'])
    frame = frame[(frame.delisting_date >= pd.Timestamp(window[0])) & (frame.delisting_date <= pd.Timestamp(window[1]))]
    seen = bars.groupby('stock_id').trade_date.agg(['min', 'max']).rename(columns={'min': 'first_listed', 'max': 'last_listed'})
    last = bars[bars.status == 'TRADED'].groupby('stock_id').trade_date.max().rename('last_traded')
    return frame.merge(seen, left_on='stock_id', right_index=True, how='left').merge(last, left_on='stock_id', right_index=True, how='left')[columns]
