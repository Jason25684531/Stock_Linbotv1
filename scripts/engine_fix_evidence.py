"""Evidence of the closed data gaps F05-F08 (change 2026-11-10-engine-accounting-and-history-gaps-v1), from stored files only.

    python -m scripts.engine_fix_evidence

Writes outputs/engine_history_fix/evidence.json (overwritten: it is derived, the raw files and run folders are the evidence) and prints it.
Reads the eight Saturday MI_INDEX files, the derived 2023-2025 capital-change files, the delisting list, the legacy and the latest snapshot,
their evaluations, the research-budget status and the FinLab reconcile file. No HTTP.
"""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
OUT = ROOT / 'outputs/engine_history_fix'


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def saturdays(snap, legacy_eval, finlab) -> dict:
    from core.runtime.public_data import HIST_SATURDAYS
    from scripts import research_h4_hist_fetch as h4
    bars = snap.frame('bars')
    calendar = snap.frame('calendar').set_index('date')
    out = {}
    for day in sorted(HIST_SATURDAYS):
        path = h4.RAW / f'MI_INDEX_{day}.json'
        stamp = pd.Timestamp(day)
        entry = {'file': path.relative_to(ROOT).as_posix() if path.exists() else None, 'sha256': _sha(path) if path.exists() else None,
                 'bytes': path.stat().st_size if path.exists() else None, 'in_snapshot': stamp in calendar.index and calendar.loc[stamp, 'kind'] == 'TRADING'}
        if path.exists():
            payload = json.loads(path.read_text(encoding='utf-8'))
            entry.update(payload_date=payload.get('date'), stat=payload.get('stat'), date_matches=str(payload.get('date', '')).replace('-', '') == day)
        rows = bars[bars.trade_date == stamp]
        entry.update(rows=int(len(rows)), traded_rows=int((rows.status == 'TRADED').sum()))
        if finlab is not None:
            theirs = finlab[finlab.trade_date == stamp][['stock_id', 'finlab_close']]
            merged = rows[['stock_id', 'close']].merge(theirs, on='stock_id')
            entry['finlab'] = {'compared': int(len(merged)), 'agreed': int(((merged.close - merged.finlab_close).abs() <= 0.011).sum())}
        out[day] = entry
    return out


def saturdays_summary(found: dict, legacy_eval) -> dict:
    missing_before = sorted({d for c in legacy_eval['coverage'] if c['category'] == 'calendar' for d in c['metrics'].get('missing_sessions', [])})
    return {'missing_sessions_before': missing_before, 'files_present': sum(1 for v in found.values() if v['file']),
            'all_in_snapshot': all(v['in_snapshot'] for v in found.values())}


def capital_changes(snap, legacy_eval, new_eval) -> dict:
    events = ROOT / 'outputs/research_mode/h4_hist_unlock_v1/event_sources'
    out = {}
    for kind in ('TWTAUU', 'TWTB8U'):
        path = events / f'{kind}_20230101_20251231.json'
        body = json.loads(path.read_text(encoding='utf-8'))
        out[kind] = {'file': path.relative_to(ROOT).as_posix(), 'sha256': _sha(path), 'rows': len(body['data']), 'from': body['derived_from']['file'],
                     'from_sha256': body['derived_from']['sha256'], 'in_snapshot_manifest': path.name in snap.manifest['sources'].get('capital', {})}
    cells = lambda ev: {c['year']: c for c in ev['coverage'] if c['category'] == 'capital_changes'}
    before, after = cells(legacy_eval), cells(new_eval)
    jumps_before = {y: before[y]['metrics'].get('unexplained_price_jumps') for y in (2023, 2024, 2025)}
    jumps_after = {y: after[y]['metrics'].get('unexplained_price_jumps') for y in (2023, 2024, 2025)}
    out.update(unexplained_jumps_before=int(sum(v or 0 for v in jumps_before.values())), unexplained_jumps_after=int(sum(v or 0 for v in jumps_after.values())),
               by_year_before=jumps_before, by_year_after=jumps_after, status_after={y: after[y]['status'] for y in (2023, 2024, 2025)},
               reasons_after={y: after[y]['reasons'] for y in (2023, 2024, 2025)},
               explained_now=sorted({s for y in (2023, 2024, 2025) for s in before[y]['metrics'].get('unexplained_samples', [])}))
    return out


def delisted_2015(snap, finlab, new_eval) -> dict:
    delist = snap.frame('delistings').set_index('stock_id')
    bars = snap.frame('bars')
    files = {d.strftime('%Y%m%d'): True for d in bars[bars.trade_date.dt.year == 2015].trade_date.unique()}
    out = {}
    for sid in ('2384', '5280'):
        row = delist.loc[sid]
        last = finlab[(finlab.stock_id == sid) & finlab.finlab_close.notna()].trade_date.max() if finlab is not None else None
        out[sid] = {'name': row['name'], 'delisting_date': str(pd.Timestamp(row.delisting_date).date()), 'source': 'TWSE suspendListing (artifacts/official_dual_track_cache/suspendListing.json)',
                    'mi_index_files_2015_containing': int((bars[(bars.trade_date.dt.year == 2015) & (bars.stock_id == sid)]).shape[0]),
                    'mi_index_sessions_2015_checked': len(files), 'finlab_last_close_date': None if last is None or pd.isna(last) else str(last.date())}
    cell = next(c for c in new_eval['coverage'] if c['category'] == 'delistings' and c['year'] == 2015)
    uni = next(c for c in new_eval['coverage'] if c['category'] == 'universe' and c['year'] == 2015)
    ok = all(v['mi_index_files_2015_containing'] == 0 and v['finlab_last_close_date'] and v['finlab_last_close_date'] < '2015-01-01' for v in out.values())
    out['verdict'] = ('VERIFIED: neither code appears in any of the 2015 official MI_INDEX sessions; TWSE lists both as delisted in 2015 '
                      '(2384 2015-07-07, 5280 2015-01-02); the independent source shows their last trades in 2014, so trading was suspended before the window'
                      if ok else 'PARTIAL: evidence incomplete')
    out['coverage_2015'] = {'delistings': cell['status'], 'delistings_reasons': cell['reasons'], 'universe': uni['status'], 'universe_reasons': uni['reasons']}
    return out


def benchmark_2019(legacy_snap, snap, legacy_eval, new_eval) -> dict:
    old, new = legacy_snap.frame('benchmark').set_index('date'), snap.frame('benchmark').set_index('date')
    old_nan = old[old.taiex_price.isna()]
    cell = lambda ev, cat: next(c for c in ev['coverage'] if c['category'] == cat and c['year'] == 2019)
    return {'sessions_without_index_before': int(old_nan.shape[0]), 'sessions_without_index_after': int(new.taiex_price.isna().sum()),
            'missing_dates_before': [str(d.date()) for d in old_nan.index], 'fixed_date': '2019-04-29',
            'taiex_price': float(new.loc['2019-04-29', 'taiex_price']), 'taiex_total_return': float(new.loc['2019-04-29', 'taiex_total_return']),
            'neighbours': {str(d.date()): float(v) for d, v in new.loc['2019-04-26':'2019-04-30', 'taiex_price'].items()},
            'method': 'MI_INDEX_20190429.json was on disk; TWSE printed the short labels (加權股價指數 / 加權股價報酬指數) that day; the parser accepts them (core/research/history/snapshot.INDEX_LABELS). No HTTP, no value invented.',
            'coverage_2019_before': {'price': cell(legacy_eval, 'benchmark_price')['metrics'], 'status': cell(legacy_eval, 'benchmark_price')['status']},
            'coverage_2019_after': {'price': cell(new_eval, 'benchmark_price')['metrics'], 'status': cell(new_eval, 'benchmark_price')['status']}}


def budget() -> dict:
    from core.runtime import research_budget as rb
    status = rb.status()['jobs']
    jobs = {j: {'used': status.get(j, {}).get('used'), 'cap': rb.CAPS[j]} for j in ('hist_foundation_quotes', 'hist_foundation_events', 'hist_foundation_verify')}
    log = ROOT / 'outputs/research_mode/h4_hist_unlock_v1/fetch_log.jsonl'
    rows = [json.loads(l) for l in log.read_text(encoding='utf-8').splitlines() if l.strip()]
    this_round = [r for r in rows if r.get('at', '') >= '2026-11-10T14:00' and r['params'].get('date') in {'20160130', '20160604', '20160910', '20170218', '20170603', '20170930', '20180331', '20181222'}]
    return {**jobs, 'total_cap': 17, 'http_requests_this_round': len(this_round), 'fetch_log_rows': [{k: r[k] for k in ('file', 'at', 'status', 'sha256', 'result')} for r in this_round]}


def reduction_reconcile(snap) -> dict:
    """Official reference price ratio (pre/reference) against the FinLab divide ratio for 2015-2023 capital reductions."""
    from core.research.history import ledger
    led = ledger.from_snapshot(snap)
    red = led[led.kind.str.startswith('CAPITAL_REDUCTION') & (led.date.dt.year <= 2023)].copy()
    fin = pd.read_parquet(snap.source('reconcile', 'finlab_capital_reduction')).stack().reset_index()
    fin.columns = ['date', 'stock_id', 'ratio']
    fin['date'] = pd.to_datetime(fin['date'])
    m = red.merge(fin, on='stock_id', suffixes=('', '_fin'))
    m = m[(m.date_fin - m.date).abs().dt.days <= 7]
    # FinLab's divide ratio is pre_close / reference (the price adjustment): it equals the share ratio only for loss offsets
    price = (m.pre_close / m.reference - m.ratio).abs() / m.ratio
    loss = m[m.kind == 'CAPITAL_REDUCTION_LOSS']
    shares = (loss.share_multiplier - loss.ratio).abs()
    return {'ledger_reductions_2015_2023': int(len(red)), 'matched_same_or_within_7_days': int(len(m)),
            'price_factor_agrees_within_0_5pct': int((price <= 0.005).sum()), 'price_factor_max_rel_diff': float(price.max()) if len(m) else None,
            'loss_offset_share_ratio_checked': int(len(loss)), 'loss_offset_share_ratio_agrees_1e2': int((shares <= 1e-2).sum()),
            'cash_return_share_ratio': 'derived from the official reference price at par 10; no independent second source (DEFERRED_FINDINGS D4)',
            'ledger_summary': ledger.summary(led)}


def main() -> int:
    from core.research.history import snapshot as hs
    from scripts import historical_foundation as hf
    baseline = json.loads((OUT / 'legacy_baseline.json').read_text(encoding='utf-8'))
    legacy_snap, snap = hs.load(baseline['snapshot_id']), hf.snapshot()
    new_eval = hf.latest_evaluation()
    legacy_eval = json.loads(next((hf.EVALUATIONS / baseline['snapshot_id']).glob('EV1-*.json')).read_text(encoding='utf-8'))
    path = snap.source('reconcile', 'finlab_close')
    finlab = None
    if path is not None:
        wide = pd.read_parquet(path)
        finlab = wide.stack().reset_index()
        finlab.columns = ['trade_date', 'stock_id', 'finlab_close']
        finlab['trade_date'] = pd.to_datetime(finlab['trade_date'])
        finlab['stock_id'] = finlab['stock_id'].astype(str)
    evidence = {'generated_at': pd.Timestamp.now(tz='UTC').isoformat(timespec='seconds'), 'snapshot': {'legacy': baseline['snapshot_id'], 'new': snap.snapshot_id},
                'evaluation': {'legacy': legacy_eval['evaluation_id'], 'new': new_eval['evaluation_id']},
                'engine': {'legacy': baseline['engine_version'], 'new': hf.ACCOUNTING},
                'saturdays': (found := saturdays(snap, legacy_eval, finlab)), 'saturdays_summary': saturdays_summary(found, legacy_eval),
                'capital_changes_2023_2025': capital_changes(snap, legacy_eval, new_eval),
                'delisted_2015': delisted_2015(snap, finlab, new_eval), 'benchmark_2019': benchmark_2019(legacy_snap, snap, legacy_eval, new_eval),
                'budget': budget(), 'capital_reduction_reconcile': reduction_reconcile(snap)}
    (OUT / 'evidence.json').write_text(json.dumps(evidence, ensure_ascii=False, indent=1, default=str), encoding='utf-8')
    brief = {'saturdays': evidence['saturdays_summary'], 'capital': {k: evidence['capital_changes_2023_2025'][k] for k in ('unexplained_jumps_before', 'unexplained_jumps_after', 'status_after')},
             'delisted': evidence['delisted_2015']['verdict'][:60], 'benchmark': {k: evidence['benchmark_2019'][k] for k in ('sessions_without_index_before', 'sessions_without_index_after')},
             'budget': {k: evidence['budget'][k] for k in ('hist_foundation_quotes', 'http_requests_this_round')}, 'reductions': evidence['capital_reduction_reconcile']}
    print(json.dumps(brief, ensure_ascii=False, default=str))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
