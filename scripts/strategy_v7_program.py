"""V7: revenue momentum held as a portfolio, an ETF trend switch, and their mix, on the pinned snapshot. Research only.

python -m scripts.strategy_v7_program data | charter | admit <spec.json> | smoke <h> <v> | run <h> <v> [holdout] | finalize | report [--final]
It reuses the V6 charter, queue, verdict and report with the output folder pointed at outputs/strategy_v7. Writes only under outputs/strategy_v7.
"""
from __future__ import annotations

import hashlib
import inspect
import json
import sys
from contextlib import contextmanager
from datetime import date
from pathlib import Path

import pandas as pd

from scripts import strategy_v6_program as v6
from scripts import strategy_v6_rules as v6rules
from scripts import strategy_v7_rules as rules

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'outputs/strategy_v7'
RAW = ROOT / 'outputs/finlab_recovery/2026-10-24/raw'
SNAPSHOT_REVENUE = ROOT / 'outputs/strategy_validation/2026-10-21/snapshot_monthly_revenue.parquet'
V7_KINDS = {'etf_switch': ('etf_switch',), 'portfolio': ('first_sessions', 'portfolio_rule', 'rebalance_step', '_Weight', '_book_value',
                                                       'revenue_momentum_selection', 'revenue_momentum_ids', 'price_trend_ids', 'usable_revenue',
                                                       '_ranked_find_candidates')}
GATE_FUNCTIONS = ('market_above', 'until')  # V6 rules the A2 entry gate and the window cut run through
BUDGET = {'hypotheses': 5, 'variants': 2, 'finalists': 2, 'sourced_per_failure': 1}
_ORIGINAL = {name: getattr(v6, name) for name in ('OUT', 'BUDGET', 'PROXY_KINDS', 'bound_code', 'numbers_for', 'measurement_code')}


def _sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def verified_data():
    """The manifest's output hashes, after checking that every stored data file still has the hash recorded when it was built."""
    manifest = json.loads((rules.DATA / 'manifest.json').read_text(encoding='utf-8'))
    for name, recorded in manifest['outputs'].items():
        if _sha(rules.DATA / name) != recorded:
            raise RuntimeError(f'DATA_CHANGED_SINCE_BUILD {name}')
    return manifest['outputs']


def build_data():
    """Research inputs outside production tables: revenue by release date in dollars, and 0050 raw closes. Units are checked, not assumed."""
    finlab = pd.read_parquet(next(p for p in RAW.glob('monthly_revenue__*.parquet') if '(%)' not in p.name))
    finlab.columns = [str(c) for c in finlab.columns]
    snap = pd.read_parquet(SNAPSHOT_REVENUE)
    snap['stock_id'] = snap.stock_id.astype(str)
    overlap = snap[(snap.year == 2023) & (snap.month == 6)].set_index('stock_id').revenue
    row = finlab[(finlab.index.year == 2023) & (finlab.index.month == 7)].iloc[0].dropna()
    common = overlap.index.intersection(row.index)
    ratio = (overlap[common] / row[common]).dropna()
    within = float(((ratio / 1000 - 1).abs() < 0.01).mean())
    median = float(ratio.median())
    if abs(median - 1000) > 0.5 or within < 0.98:  # FinLab is in thousands, so small companies carry a rounding error
        raise RuntimeError(f'REVENUE_UNIT_MISMATCH median ratio {median}, share within 1 percent of a factor 1000: {within:.3f}')
    finlab = finlab * 1000
    last = finlab.index.max()
    snap['release'] = [pd.Timestamp(date(y + (m == 12), 1 if m == 12 else m + 1, 10)) for y, m in zip(snap.year, snap.month)]
    later = snap[snap.release > last].pivot_table(index='release', columns='stock_id', values='revenue', aggfunc='last')
    merged = pd.concat([finlab, later]).sort_index()
    merged = merged[~merged.index.duplicated(keep='last')]
    DATA = rules.DATA
    DATA.mkdir(parents=True, exist_ok=True)
    merged.to_parquet(DATA / 'revenue_usable.parquet')
    price = pd.read_parquet(next(RAW.glob('price__*.parquet')))
    etf = price['0050'].dropna().rename('close').rename_axis('date').reset_index()
    etf.to_parquet(DATA / 'etf_0050.parquet')
    manifest = {'revenue_rows': len(merged), 'revenue_first_release': str(merged.index.min().date()), 'revenue_last_release': str(merged.index.max().date()),
                'finlab_last_release': str(last.date()), 'unit_check_share_within_1pct': within, 'unit_check_median_ratio': median, 'etf_rows': len(etf),
                'etf_first': str(etf.date.min().date()), 'etf_last': str(etf.date.max().date()), 'etf_basis': 'RAW_CLOSE_NO_DIVIDENDS',
                'sources': {p.name: _sha(p) for p in (SNAPSHOT_REVENUE, *RAW.glob('monthly_revenue__*.parquet'), *RAW.glob('price__*.parquet'))},
                'outputs': {p.name: _sha(p) for p in (DATA / 'revenue_usable.parquet', DATA / 'etf_0050.parquet')}}
    v6.write(DATA / 'manifest.json', manifest)
    return manifest


def bound_code(kind):
    if kind not in V7_KINDS:
        return _ORIGINAL['bound_code'](kind)
    code = {f'rules7.{n}': hashlib.sha256(inspect.getsource(getattr(rules, n)).encode()).hexdigest() for n in V7_KINDS[kind]}
    code['data_manifest'] = _sha(rules.DATA / 'manifest.json')
    if kind == 'portfolio':
        code |= {name: _sha(ROOT / name) for name in v6.ENGINE_BOUND_FILES}
        code |= {f'rules6.{n}': hashlib.sha256(inspect.getsource(getattr(v6rules, n)).encode()).hexdigest() for n in GATE_FUNCTIONS}
    return code


def measurement_code():
    own = ''.join(inspect.getsource(globals()[n]) for n in ('numbers_for', '_engine_track', '_etf_numbers', 'verified_data'))
    return hashlib.sha256((_ORIGINAL['measurement_code']() + own).encode()).hexdigest()


def _etf_numbers(spec, variant, years, window_end, ctx):
    etf = pd.read_parquet(rules.DATA / 'etf_0050.parquet')
    etf = etf[etf.date <= pd.Timestamp(window_end)]
    costs = v6.charter()['etf_costs']
    track = v6._track(rules.etf_switch(etf, variant['ma'], costs, 1), rules.etf_switch(etf, variant['ma'], costs, 2))
    summary, sliced = v6._summary(track, years, ctx)
    return summary, sliced, {'note': 'ETF 0050 raw closes, price only (no dividends), fills at the next session close; no data after 2023-12-29, so no holdout',
                             'proxy': True}


def _engine_track(hid, vid, spec, window, bench):
    from core.backtest import run_store
    from core.backtest.run_store import canonical_hash
    from scripts import history_engine
    from scripts import platform_v2 as v2
    from scripts.platform_v2_engine import execute
    variant, charter = spec['variants'][vid], v6.charter()
    base = next(p for p in v2.baseline_params() if p['strategy_id'] == variant['host'])
    end = f"{charter['windows'][window][1]}-12-31"
    params = base | {'end': end, 'rule': 'LOT_ODD'}
    snapshot = charter['snapshot_id']
    rid = 'RID7-' + canonical_hash({'original_identity': v2.identity(params, snapshot), 'hypothesis': hid, 'variant': variant,
                                    'bound_code': bound_code('portfolio'), 'data': verified_data()})[:24]
    run_id = _reusable(rid)
    if not run_id:
        run_id = run_store.new_run_id()
        sessions = history_engine._prepared(snapshot, params['start'], params['end']).sessions
        cache = v6._cached_features(sessions)
        revenue = rules.usable_revenue(rules.load_revenue(), pd.Timestamp(end) + pd.Timedelta(days=1))
        with cache, rules.portfolio_rule(variant, sessions, revenue, v6rules.until(bench, end)):
            result = execute(params['strategy_id'], snapshot, params['start'], params['end'], params['capital'], params['costs'], params['rule'])
        record, frames = v2.build_record(params, rid, run_id, snapshot, result)
        record.update(v6_feature_cache_check=cache.verified | {'mismatches': result['feature_cache']['mismatches']}, strategy_type='strategy_v7_portfolio',
                      research_version='V7', labels=v6.LABELS, hypothesis=hid, variant=vid, variant_params=variant, bound_code=bound_code('portfolio'),
                      charter_sha256=v6._sha((OUT / 'charter.json').read_bytes()))
        run_store.save_run(record, frames, root=OUT / 'runs', run_id=run_id)
    return run_store.load_run(run_id, root=OUT / 'runs'), rid


def _reusable(rid):
    from scripts import platform_v2 as v2
    found = v2.lookup(rid, OUT / 'runs')
    return found if found and json.loads((OUT / 'runs' / found / 'run.json').read_text(encoding='utf-8')).get('publishable') else None


def numbers_for(hid, vid, window, ctx):
    spec = v6.registration(hid)['spec']
    if spec['kind'] not in V7_KINDS:
        return _ORIGINAL['numbers_for'](hid, vid, window, ctx)
    charter = v6.charter()
    variant, years = spec['variants'][vid], tuple(charter['windows'][window])
    out = {'hypothesis': hid, 'variant': vid, 'window': window, 'spec_kind': spec['kind'], 'source': spec.get('source'),
           'bound_code': bound_code(spec['kind'])}
    if spec['kind'] == 'etf_switch':
        data = verified_data()
        summary, sliced, extra = _etf_numbers(spec, variant, years, f'{years[1]}-12-31', ctx)
        out |= extra | {'kind': 'index_switch', 'publishable': summary is not None,
                        'run_identity': 'ETF-' + hashlib.sha256(json.dumps([hid, vid, variant, out['bound_code'], data], sort_keys=True).encode()).hexdigest()[:24]}
        if window == 'holdout':
            raise ValueError('NO_ETF_DATA_FOR_HOLDOUT')
    else:
        from core.research.intelligence import performance as perf
        from scripts.strategy_v4 import bundle
        loaded, rid = _engine_track(hid, vid, spec, window, ctx['bench'])
        run, trust, _ = bundle(loaded, ctx)
        verified = None
        if window == 'development':
            inside = {y: s for y, s in trust.items() if years[0] <= int(y) <= years[1]}
            verified = perf.verified_span(inside, sorted(inside))
            years = verified or years
        summary, sliced = v6._summary(run['nav'], years, ctx, run['trades'], run['record']['cost_model'])
        reasons = list(loaded['run'].get('not_publishable_reasons') or []) + ([] if verified or window != 'development' else ['NO_VERIFIED_YEAR'])
        out |= {'kind': 'engine', 'proxy': False, 'publishable': bool(loaded['run']['publishable']) and not reasons, 'not_publishable_reasons': reasons,
                'run_identity': rid, 'run_id': loaded['run']['run_id'], 'feature_cache': loaded['run'].get('feature_cache'),
                'data_trust': {str(k): v for k, v in trust.items() if years[0] <= int(k) <= years[1]},
                'note': 'LOT_ODD: odd lots at board-lot prices; before 2020-10 a diagnostic of the rule, not tradable history'}
    summary = summary or {}
    missing = {'value': None, 'reason': 'NO_SESSIONS_OR_NOT_PUBLISHABLE'}
    if spec['kind'] == 'etf_switch':
        summary['invested_session_share'] = {'value': None, 'reason': 'NOT_A_STOCK_ENGINE_RUN'}
    out |= {'measurement_code': measurement_code(), 'span': list(years),
            **{k: summary.get(k, missing) for k in ('cagr', 'cagr_stress', 'max_drawdown', 'calmar', 'worst_year', 'invested_session_share')},
            'max_drawdown_stress': v6._stress_drawdown(sliced), 'closed_trades': summary.get('closed_trades') or 0, 'years': summary.get('sessions', 0) / 252,
            'series': {'date': sliced.date.astype(str).tolist(), 'nav_base': sliced.nav_base.astype(float).tolist(),
                       'nav_stress': sliced.nav_stress.astype(float).tolist()} if len(sliced) else None}
    return out


@contextmanager
def installed(budget=BUDGET):
    """Point the V6 program at outputs/strategy_v7 and at the V7 kinds; restored on exit so V6 behaves as before."""
    saved = {name: getattr(v6, name) for name in _ORIGINAL}
    v6.OUT, v6.BUDGET = OUT, budget
    v6.PROXY_KINDS = ('index_switch', 'etf_switch')
    v6.bound_code, v6.numbers_for, v6.measurement_code = bound_code, numbers_for, measurement_code
    try:
        yield
    finally:
        for name, value in saved.items():
            setattr(v6, name, value)


REPORT_FIXES = (
    ('# V6 年報酬目標研究計畫：驗收報表', '# V7 核心衛星組合研究：驗收報表'),
    ('- 代理＝以含息加權指數計算，不是可交易的 ETF；換手在訊號當日收盤成交，是偏樂觀的上限。',
     '- 代理＝B1 以 0050 原始收盤價計算，不含股利，訊號隔日收盤成交；2023-12-29 之後沒有資料，所以沒有驗證區間，也不能成為決選。'),
    ('- 引擎類假說的出場由宿主策略的停損、停利與最長持有天數決定，所以「低頻進場」也代表較多時間持有現金，請看持股天數比例。',
     '- A 類假說在每月 13 日當天或之後的第一個交易日換股，不設停損與停利，持有到下次換股；新買入的倉位各取換股日淨值的 1/N，留任的名稱不重新調整權重；以引擎的零股規則回測，2020-10 之前是規則診斷，不是可成交的歷史。'),
)
CLOSEST = '## 最接近目標的結果（開發區間，不含資料問題的格子）'
REPORT_FIXES += ((CLOSEST, CLOSEST + '\n\n> 這裡的數字不能直接當作達標證據：樣本不足（SAMPLE）的格子也會排進來（例如 C1 只涵蓋約 3 年）；可信年度被縮短的格子（例如 A1b 只含 2019-2021）不含被排除的年份，同一次執行在完整登錄區間的診斷回撤見「各格備註」.'),)
BLOCKED_OUTCOME =('**結果：因資料或來源阻擋而停止，未達標**（STOPPED_BY_BLOCKER）', '**結果：目標未達成**（TARGET_NOT_MET）')


MET_VERDICTS = ('PROMISING', 'HOLDOUT_PASS')


def _covered(result):
    """(first date, last date, years) of the stored series, or None when there is no series."""
    dates = (result.get('series') or {}).get('date') or []
    return (dates[0], dates[-1], (pd.Timestamp(dates[-1]) - pd.Timestamp(dates[0])).days / 365.25) if dates else None


def _short(result):
    covered = _covered(result)
    return bool(covered) and covered[2] < result['span'][1] - result['span'][0] + 1 - 0.5


def _coverage_note(result):
    """Words for a cell whose stored series covers fewer years than its registered span (a mix is cut to its shortest member)."""
    if not _short(result):
        return ''
    first, last, years = _covered(result)
    return f' 注意：實際只涵蓋 {first} 至 {last}（{years:.1f} 年），短於登錄區間，數字不能和其他格或大盤直接比較.'


def _left_out(result, windows):
    window = windows[result['window']]
    return [y for y in range(window[0], window[1] + 1) if not result['span'][0] <= y <= result['span'][1]]


def _diagnostic_drawdown(result, windows):
    """Worst drawdown of the same run over the whole registered window (a diagnostic, never adopted), or None without a stored run."""
    nav_file = OUT / 'runs' / str(result.get('run_id')) / 'nav.csv'
    if not nav_file.is_file():
        return None
    window = windows[result['window']]
    nav = pd.read_csv(nav_file, parse_dates=['date']).set_index('date').nav_base
    inside = nav[(nav.index.year >= window[0]) & (nav.index.year <= window[1])]
    return float((inside / inside.cummax() - 1).min()) if len(inside) else None


def _cut_note(result, windows):
    """Years the verified span leaves out of the registered window, with the same run's drawdown over the whole window as a diagnostic."""
    window, left_out = windows[result['window']], _left_out(result, windows)
    if not left_out:
        return ''
    note = f" 只計入可信年度 {result['span'][0]}-{result['span'][1]}；未計入 {'/'.join(map(str, left_out))}，數字不含這些年份."
    drawdown = _diagnostic_drawdown(result, windows)
    if drawdown is not None:
        note += f" 同一次執行在 {window[0]}-{window[1]} 的診斷回撤 {drawdown * 100:.1f}%（不採計，只用來避免誤讀）."
    return note


def _fix_rows(text, results, windows):
    """V6 prints all targets met whenever no number is short, and labels a mix with the registered window: only a PROMISING or HOLDOUT_PASS
    verdict may say the targets were met, and a cell on fewer years shows the dates it covers and no benchmark."""
    lines = []
    for line in text.split('\n'):
        for r in results:
            row = f"| {r['hypothesis']} | {r['variant']} | {r['window']} "
            if '全部達標' in line and r['verdict'] not in MET_VERDICTS and (line.startswith(row) or f"：{r['hypothesis']}{r['variant']} " in line):
                line = line.replace('全部達標', '數字在門檻內，但判定未過，不是達標證據')
            if line.startswith(row) and _short(r):
                first, last, _ = _covered(r)
                line = line.replace(f"{r['window']} {r['span'][0]}\u2013{r['span'][1]}", f"{r['window']} {first}\u2013{last}（實際）")
                bench = r['benchmark']
                line = line.replace(f"{v6._pct(bench['cagr'])} / {v6._pct(bench['max_drawdown'])}", '—（期間不同）')
        for r in results:
            left_out = _left_out(r, windows)
            drawdown = _diagnostic_drawdown(r, windows) if left_out else None
            tag = f"（未計入 {'/'.join(map(str, left_out))}）"
            if drawdown is not None:
                tag = tag[:-1] + f'；完整區間診斷回撤 {drawdown * 100:.1f}%）'
            if r.get('closed_trades') == 0 and line.startswith(f"| {r['hypothesis']} | {r['variant']} | {r['window']} "):
                line = line.replace('| — | 0 |', '| — | — |')
            if left_out and line.startswith(f"| {r['hypothesis']} | {r['variant']} | {r['window']} "):
                line = line.replace(f"{r['window']} {r['span'][0]}–{r['span'][1]}", f"{r['window']} {r['span'][0]}–{r['span'][1]}{tag}")
            elif left_out and line.startswith('- 最佳') and f"：{r['hypothesis']}{r['variant']} " in line:
                line += tag
        lines.append(line)
    return '\n'.join(lines)


def write_report(owner_items=(), final=False):
    """V6 report with the V7 wording: title, what the proxy and the engine cells are, the stored notes, and TARGET_NOT_MET when nothing blocked."""
    end = v6.write_report(owner_items, final)
    old, new = OUT / 'V6_ACCEPTANCE_REPORT.md', OUT / 'V7_ACCEPTANCE_REPORT.md'
    text = old.read_text(encoding='utf-8')
    old.unlink()
    for before, after in REPORT_FIXES:
        text = text.replace(before, after)
    if not any(r['event'] == 'DATA_BLOCKED' for r in v6.queue()) and BLOCKED_OUTCOME[0] in text:
        text = text.replace(*BLOCKED_OUTCOME)
        end = 'TARGET_NOT_MET'
        body = json.loads((OUT / 'report.json').read_text(encoding='utf-8')) | {'outcome': end}
        v6.write(OUT / 'report.json', body)
    results = [json.loads(p.read_text(encoding='utf-8')) for p in sorted((OUT / 'results').glob('*.json'))]
    windows = v6.charter()['windows']
    text = _fix_rows(text, results, windows)
    text += '\n## 各格備註\n\n' + '\n'.join(f"- {r['hypothesis']}{r['variant']} {r['window']}：{' '.join(filter(None, (r.get('note'), _coverage_note(r).strip(), _cut_note(r, windows).strip()))) or '無'}" for r in results) + '\n'
    new.write_text(text, encoding='utf-8')
    return end


MAX_CELLS, FIXED_HYPOTHESES = 6, 4


def admit(spec):
    """V6 admission plus the V7 limits: at most six cells in all, and the free slot (the fifth hypothesis) always names a failed parent."""
    registered = [r for r in v6.queue() if r['event'] == 'REGISTERED']
    if sum(len(r['spec']['variants']) for r in registered) + len(spec['variants']) > MAX_CELLS:
        raise ValueError('SEVENTH_CELL')
    if len(registered) >= FIXED_HYPOTHESES and not spec.get('parent'):
        raise ValueError('FREE_SLOT_NEEDS_A_FAILED_PARENT')
    return v6.admit(spec)


def main(argv):
    command = argv[0] if argv else ''
    with installed():
        if command == 'admit':
            print(json.dumps(admit(json.loads(Path(argv[1]).read_text(encoding='utf-8'))), ensure_ascii=False)[:300])
            return 0
        if command == 'data':
            print(json.dumps(build_data(), ensure_ascii=False)[:600])
        elif command == 'smoke':
            print(json.dumps(smoke(argv[1], argv[2]), ensure_ascii=False, default=str)[:600])
        elif command == 'report':
            items = json.loads((OUT / 'owner_items.json').read_text(encoding='utf-8')) if (OUT / 'owner_items.json').exists() else []
            print(write_report(items, final='--final' in argv))
        elif command in ('charter', 'rebind', 'run', 'finalize', 'status'):
            return v6.main(argv)
        else:
            print(__doc__)
            return 1
    return 0


def smoke(hid, vid):
    """A short engine run (2023-01-03..2023-03-31) outside the queue: timing, selection and cache verification before the batch."""
    import time
    v6.require_runnable(hid, vid, 'development')
    started, ctx = time.time(), v6._context()
    spec = v6.registration(hid)['spec']
    charter = v6.charter()
    from scripts import history_engine
    from scripts import platform_v2 as v2
    from scripts.platform_v2_engine import execute
    variant = spec['variants'][vid]
    base = next(p for p in v2.baseline_params() if p['strategy_id'] == variant['host'])
    params = base | {'start': '2023-01-03', 'end': '2023-03-31', 'rule': 'LOT_ODD'}
    sessions = history_engine._prepared(charter['snapshot_id'], params['start'], params['end']).sessions
    cache = v6._cached_features(sessions)
    revenue = rules.usable_revenue(rules.load_revenue(), pd.Timestamp('2023-04-01'))
    with cache, rules.portfolio_rule(variant, sessions, revenue, v6rules.until(ctx['bench'], params['end'])):
        result = execute(params['strategy_id'], charter['snapshot_id'], params['start'], params['end'], params['capital'], params['costs'], params['rule'])
    record, frames = v2.build_record(params, 'SMOKE', 'SMOKE', charter['snapshot_id'], result)
    fills = frames['fills']
    out = {'hypothesis': hid, 'variant': vid, 'seconds': round(time.time() - started, 1), 'publishable': record['publishable'],
           'reasons': record['not_publishable_reasons'], 'cache_mismatches': result['feature_cache']['mismatches'],
           'buy_fills': int((fills.side == 'BUY').sum()) if len(fills) else 0, 'distinct_names': int(fills.stock_id.nunique()) if len(fills) else 0}
    v6.write(OUT / 'smoke' / f'{hid}__{vid}.json', out)
    v6.deviation(f'smoke run {hid}:{vid} on 2023-01-03..2023-03-31 outside the queue (task 4.1); not a result')
    return out


if __name__ == '__main__':
    raise SystemExit(main(sys.argv[1:]))
