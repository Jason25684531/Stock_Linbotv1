"""Markdown/JSON reports of change 2026-11-10-historical-data-foundation-v1, generated only from stored evaluation, gap and run files.

    python -m scripts.historical_report            # data reports for the latest evaluation (+ cross-regime when runs exist)
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from core.research.history import categories as cats
from scripts import historical_foundation as hf

DOCS = ROOT / 'docs/research/2026-11-10-historical-data-foundation-v1'
PRICED_VERDICT = {'VERIFIED': 'DATA_VERIFIED', 'PARTIAL': 'PARTIAL', 'ACCESS_BLOCKED': 'BLOCKED', 'SOURCE_UNAVAILABLE': 'BLOCKED',
                  'FAILED_VALIDATION': 'FAILED_VALIDATION'}
ABBR = {'VERIFIED': 'V', 'PARTIAL': 'P', 'ACCESS_BLOCKED': 'AB', 'SOURCE_UNAVAILABLE': 'SU', 'FAILED_VALIDATION': 'FV', 'GOVERNED_ONLY': 'G',
        'REQUIRED': 'REQ', 'NOT_REQUIRED': '-', 'COMPARISON_ONLY': 'CMP'}
HEADER = '> REFERENCE_ONLY · RESEARCH_ONLY · NOT_CERTIFIED · NOT_A_TRIAL。由 `python -m scripts.historical_report` 從存檔產生，未手抄。\n\n'


def _share(present, expected):
    return None if not expected or present is None else round(present / expected, 4)


def _completeness(cell) -> dict:
    """{'value', 'reason'}: None with the cell's own status and reason when nothing local can be counted (never 0 by default)."""
    value = _share(cell['present'], cell['expected'])
    return {'value': value, 'reason': None if value is not None else f"{cell['status']}:{(cell['reasons'] or [''])[0]}"}


def spec_before(registered_at: str, created_at: str | None) -> bool:
    return bool(created_at) and pd.Timestamp(registered_at) <= pd.Timestamp(created_at)


def _pct(value):
    return '-' if value is None else f'{value * 100:.2f}%'


def _cpct(entry):
    return _pct(entry['value']) if entry['value'] is not None else f"None（{entry['reason'].split(':')[0]}）"


def coverage_rows(evaluation) -> list[dict]:
    cell = {(c['year'], c['category']): c for c in evaluation['coverage']}
    rows = []
    for year in cats.YEARS:
        get = lambda cid: cell[(year, cid)]
        priced = cats.worst(get(cid)['status'] for cid in cats.PRICED)
        checks = {f"{cid}:{k['against']}": k['rate'] for cid in ('calendar', 'prices', 'universe', 'delistings', 'dividends', 'capital_changes',
                                                                   'financial_statements') for k in get(cid)['checks'] if k['compared']}
        rows.append({
            'year': year, 'stocks': get('prices')['metrics'].get('stocks'), 'sessions': get('calendar')['metrics'].get('sessions'),
            'trading_day_completeness': _share(get('calendar')['present'], get('calendar')['expected']),
            'ohlcv_session_completeness': _share(get('prices')['present'], get('prices')['expected']),
            'ohlcv_row_completeness': get('prices')['metrics'].get('ohlcv_complete_share'),
            'corporate_actions': {'dividends': get('dividends')['status'], 'capital_changes': get('capital_changes')['status'],
                                  'unexplained_price_jumps': get('capital_changes')['metrics'].get('unexplained_price_jumps')},
            'delisting_coverage': _share(get('delistings')['present'], get('delistings')['expected']),
            'monthly_revenue_completeness': _completeness(get('monthly_revenue')),
            'financial_completeness': _completeness(get('financial_statements')),
            'financial_pit_completeness': _completeness(get('financial_publication_time')),
            'revenue_pit_completeness': _completeness(get('revenue_announcement_time')),
            'benchmark_completeness': {'price': _share(get('benchmark_price')['present'] or 0, get('benchmark_price')['expected']),
                                       'total_return': _share(get('benchmark_total_return')['present'] or 0, get('benchmark_total_return')['expected'])},
            'independent_reconciliation': checks, 'missing_sessions': get('calendar')['metrics'].get('missing_sessions', []),
            'status_by_category': {c.id: get(c.id)['status'] for c in cats.CATEGORIES},
            'price_layer_verdict': PRICED_VERDICT[priced], 'price_layer_reasons': sorted({r for cid in cats.PRICED for r in get(cid)['reasons']
                                                                                          if get(cid)['status'] != 'VERIFIED'})})
    return rows


def coverage_md(evaluation, rows) -> str:
    lines = ['# HISTORICAL_DATA_COVERAGE_REPORT（2015～2025）\n', HEADER,
             f"- Snapshot：`{evaluation['snapshot_id']}`；Evaluation：`{evaluation['evaluation_id']}`；建立 {evaluation['created_at']}；0 次 HTTP。",
             '- 範圍：TWSE 上市四碼證券（含 TDR 91xx；不含上櫃，TPEx 為 ACCESS_BLOCKED 受限來源）；原始未調整價，還原因子另存。',
             '- 判定規則（`core/research/history/categories.decide`）：完整性錯誤 → FAILED_VALIDATION；本機無資料 → ACCESS_BLOCKED（合法來源未授權）或 '
             'SOURCE_UNAVAILABLE；獨立對帳一致率 < 0.99 → FAILED_VALIDATION；不完整、無獨立來源、獨立覆蓋 < 50%、一致率 < 0.999 或任何附註 → PARTIAL；其餘 VERIFIED。\n',
             '## 逐年摘要\n',
             '| 年 | 股票數 | 交易日 | 交易日完整率 | OHLCV 日完整率 | OHLCV 列完整率 | 除權息 | 減資／面額 | 無事件跳空 | 下市覆蓋 | 月營收 | 財報 | 財報 PIT | 營收 PIT | 指數（價／報酬） | 價格層判定 |',
             '|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|']
    for r in rows:
        ca = r['corporate_actions']
        lines.append(f"| {r['year']} | {r['stocks']} | {r['sessions']} | {_pct(r['trading_day_completeness'])} | {_pct(r['ohlcv_session_completeness'])} | "
                     f"{_pct(r['ohlcv_row_completeness'])} | {ca['dividends']} | {ca['capital_changes']} | {ca['unexplained_price_jumps']} | "
                     f"{_pct(r['delisting_coverage'])} | {_cpct(r['monthly_revenue_completeness'])} | {_cpct(r['financial_completeness'])} | "
                     f"{_cpct(r['financial_pit_completeness'])} | {_cpct(r['revenue_pit_completeness'])} | "
                     f"{_pct(r['benchmark_completeness']['price'])}／{_pct(r['benchmark_completeness']['total_return'])} | {r['price_layer_verdict']} |")
    lines += ['\n價格層判定＝交易日曆、行情、股票池、下市、除權息、減資六類的最差狀態（BLOCKED＝ACCESS_BLOCKED 或 SOURCE_UNAVAILABLE）。\n',
              '## 類別 × 年份狀態\n', '| 類別 | 角色 | ' + ' | '.join(map(str, cats.YEARS)) + ' |', '|---|---|' + '---|' * len(cats.YEARS)]
    for c in cats.CATEGORIES:
        lines.append(f'| {c.label} (`{c.id}`) | {c.role} | ' + ' | '.join(ABBR[r['status_by_category'][c.id]] for r in rows) + ' |')
    lines += ['\nV＝VERIFIED、P＝PARTIAL、AB＝ACCESS_BLOCKED、SU＝SOURCE_UNAVAILABLE、FV＝FAILED_VALIDATION。\n', '## 獨立對帳（一致率）\n',
              '| 年 | ' + ' | '.join(sorted({k for r in rows for k in r['independent_reconciliation']})) + ' |']
    keys = sorted({k for r in rows for k in r['independent_reconciliation']})
    lines.append('|---|' + '---|' * len(keys))
    for r in rows:
        lines.append(f"| {r['year']} | " + ' | '.join('-' if r['independent_reconciliation'].get(k) is None else f"{r['independent_reconciliation'][k]:.6f}"
                                                      for k in keys) + ' |')
    lines += ['\n## 非 VERIFIED 的價格層原因\n']
    lines += [f"- {r['year']}：" + '；'.join(r['price_layer_reasons']) for r in rows if r['price_layer_reasons']]
    lines += ['\n## 缺漏交易日（本機無原始檔，獨立來源顯示有交易）\n']
    lines += [f"- {r['year']}：{', '.join(r['missing_sessions'])}" for r in rows if r['missing_sessions']]
    return '\n'.join(lines) + '\n'


def requirements_md(evaluation) -> str:
    ids = [c.id for c in cats.CATEGORIES]
    lines = ['# strategy_data_requirements（21 策略）\n', HEADER,
             '來源：`core.research.reference.catalog.capabilities()`（不另維護清單）＋ `native_frame.rule_inputs`（策略原始碼實際使用的欄位）。'
             'REQ＝必要、-＝NOT_REQUIRED、CMP＝只用於 Benchmark 比較。\n',
             '| 策略 | 家族 | 引擎 | 暖機（日曆天） | 治理 | ' + ' | '.join(f'`{i}`' for i in ids) + ' | 規則輸入 |',
             '|---|---|---|---|---|' + '---|' * len(ids) + '---|']
    for r in evaluation['requirements']:
        lines.append(f"| {r['strategy_id']} | {r['family']} | {r['engine'] or 'NONE'} | {r['warmup_calendar_days']} | {r['governance'] or '-'} | "
                     + ' | '.join(ABBR[r['needs'][i]] for i in ids) + f" | {', '.join(r['rule_inputs']) or '-'} |")
    return '\n'.join(lines) + '\n'


def trust_md(evaluation) -> str:
    lines = ['# strategy_data_trust_matrix（2015～2025）\n', HEADER,
             '每格＝策略必要 SIGNAL 類別在該年與暖機觸及年份的最差狀態；減資與下市為 soft（最差降為 PARTIAL，FAILED_VALIDATION 仍傳遞）；Benchmark 不計入。'
             'DATA_TRUST_VERIFIED 需在指定期間內每年皆 VERIFIED，且期間至少 3 年。\n',
             '| 策略 | ' + ' | '.join(map(str, cats.YEARS)) + ' | 最早可用日 | DATA_TRUST_VERIFIED 期間 | 全期間 | CERTIFIED |',
             '|---|' + '---|' * len(cats.YEARS) + '---|---|---|---|']
    for sid, cells in evaluation['trust'].items():
        s = evaluation['summaries'][sid]
        lines.append(f'| {sid} | ' + ' | '.join(ABBR[c['status']] for c in cells) +
                     f" | {s['earliest_usable_date'] or '-'} | {s['data_trust_verified'] or 'NO'} | {'YES' if s['data_trust_full_2015_2025'] else 'NO'} | NO |")
    lines += ['\n## 逐策略阻擋原因（每個非 VERIFIED 年份的約束類別）\n']
    for sid, cells in evaluation['trust'].items():
        bad = [c for c in cells if c['status'] != 'VERIFIED']
        if not bad:
            continue
        grouped = {}
        for c in bad:
            grouped.setdefault(c['status'] + ' ← ' + '；'.join(c['reasons'][:3]), []).append(c['year'])
        lines.append(f'### {sid}\n')
        lines += [f"- {min(y)}～{max(y)}（{len(y)} 年）：{why}" for why, y in grouped.items()]
        lines.append('')
    return '\n'.join(lines) + '\n'


def inventory_md(evaluation) -> str:
    lines = ['# historical_source_inventory\n', HEADER, '| Source ID | 類別 | 期間 | 檔案數 | 位置 | Aggregate SHA256 | 存取 | 可否重取 | 狀態 | 獨立對帳 | 預估請求 |',
             '|---|---|---|---|---|---|---|---|---|---|---|']
    for r in evaluation['inventory']:
        period = '～'.join(r['period']) if r['period'] else '-'
        lines.append(f"| {r['source_id']} | {r['category']} | {period} | {r['files']} | {r['location'] or '-'} | "
                     f"{(r['aggregate_sha256'] or '-')[:16]} | {r['access']} | {r['refetchable']} | {r['status']} | {'YES' if r['independent_reconciliation'] else 'NO'} | "
                     f"{r.get('estimated_requests') if r.get('estimated_requests') is not None else '-'} |")
    return '\n'.join(lines) + '\n'


def gaps_md(gap_report) -> str:
    lines = ['# Historical data gap report\n', HEADER, f"0 次 HTTP。每個缺口最多三輪；同一來源結果不變即停止。共 {len(gap_report['gaps'])} 個缺口、"
             f"{sum(g['recovered'] for g in gap_report['gaps'])} 個補齊。\n",
             '| 年 | 類別 | 狀態 | 精確範圍 | 輪數 | 已試來源 | 合法替代 | 受影響策略數 | 最小 Owner 動作 |', '|---|---|---|---|---|---|---|---|---|']
    for g in gap_report['gaps']:
        tried = sorted({f"{a['source']}:{(a['result'] or {}).get('denied', 'found' if a['result'] else 'none')}" for a in g['attempts']})
        rng = ', '.join(g['exact_range']) if isinstance(g['exact_range'], list) else g['exact_range']
        lines.append(f"| {g['year']} | {g['category']} | {g['status']} | {rng} | {g['rounds']} | {'; '.join(tried)} | {g['legal_alternative'] or '-'} | "
                     f"{len(g['affected_strategies'])} | {g['owner_action']} |")
    return '\n'.join(lines) + '\n'


ENGINE_LIMITS = ['ENGINE_RAW_PRICE_POSITION_LEDGER：引擎以原始價成交與估值，每筆官方公司行動（TWT49U、TWTAUU、TWTB8U）在除權息／恢復買賣日記入持倉帳：'
                 '股數、現金與每股成本一起調整，除權息跳空不再是損益；現金股利於除息日以應收權益入帳',
                 'MIXED_DIVIDEND_TERMS_ASSUMED_STOCK：權息並存事件的現金／配股拆分來自 FinLab 公告（至 2023 年）並以官方參考價驗證；無法拆分者（主要 2024～2025）'
                 '全數視為配股（ASSUMED_STOCK），總值守恆但現金部位略少，持有跨此類事件的年度標為 PARTIAL',
                 'REFERENCE_DERIVED_TERMS：沒有當時已公告的精確條件時，現金股利、配股、減資與面額變更比率由官方參考價（四捨五入至升降單位）反推，'
                 '每股誤差不超過半個升降單位；有 FinLab 公告（至 2023 年）且能重現參考價者用精確條件（RECONCILED_FINLAB）',
                 'FRACTIONAL_SHARES_AT_PAR：調整後畸零股以面額 10 元折現',
                 'SIGNALS_ON_RAW_PRICES：策略訊號仍依原始價計算（策略定義與參數不變）；除權息跳空可能觸發策略自己的訊號，分錄只修正持倉會計與停損停利的每股基準',
                 'TWSE_LISTED_ONLY：股票池只含上市四碼證券（TPEx 受限來源）',
                 'NOT_OOS_PREVIOUSLY_VIEWED_STRATEGIES：同一批固定策略的 2023～2025 結果已公開，2015～2022 不是獨立樣本外',
                 'FIXED_PARAMETERS：未搜尋參數；未使用 2026 H4 封存資料']
LEGACY_ENGINE_LIMIT = 'ENGINE_RAW_PRICE_NO_DIVIDEND_CREDIT：舊引擎以原始價成交與估值，公司行動不入帳，除權息跳空計為損益（LEGACY_ACCOUNTING/PROVISIONAL）'
FINAL_STATES = {'RUN': 'NEW_RUN_COMPLETE', 'REUSE': 'REUSE_VERIFIED', 'DATA_BLOCKED': 'DATA_BLOCKED', 'ENGINE_UNSUPPORTED': 'ENGINE_UNSUPPORTED',
                'GOVERNED_ONLY': 'GOVERNED_ONLY', 'FAILED': 'FAILED'}
ENGINE_FIX = hf.ROOT / 'outputs/engine_history_fix'


def _legacy_baseline() -> dict:
    try:
        return json.loads((ENGINE_FIX / 'legacy_baseline.json').read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return {}


def _year_returns(nav: pd.DataFrame, column='nav_base') -> dict:
    nav = nav.assign(date=pd.to_datetime(nav.date)).sort_values('date')
    out, previous = {}, None
    for year, group in nav.groupby(nav.date.dt.year):
        start = previous if previous is not None else float(group[column].iloc[0])
        out[str(year)] = float(group[column].iloc[-1]) / start - 1 if start else None
        previous = float(group[column].iloc[-1])
    return out


def _run_metrics(folder: Path) -> dict:
    from core.research.reference import runner
    nav, trades = pd.read_csv(folder / 'nav.csv'), read_trades(folder / 'trades.csv')
    m = runner.metrics(nav)
    return {'base_total_return': _v(m['base']['total_return']), 'x2_total_return': _v(m['x2']['total_return']), 'max_drawdown_base': _v(m['base']['max_drawdown']),
            'sharpe_base': _v(m['base']['sharpe']), 'trades': len(trades), 'years': _year_returns(nav), 'nav_dates': set(pd.to_datetime(nav.date))}


def legacy_comparison(strategy_id, folder: Path, actions: pd.DataFrame, root: Path, legacy_root: Path | None = None) -> dict | None:
    """The frozen legacy run of the same strategy (LEGACY_RAW_PRICE) against the new run, with the difference attributed to what changed."""
    runs = [r for r in _legacy_baseline().get('historical_runs', []) if r['strategy_id'] == strategy_id and r['status'] == 'SUCCESS']
    if not runs:
        return None
    old = sorted(runs, key=lambda r: (r['period'] or ['9'])[0])[0]
    old_folder = Path(legacy_root or hf.RUNS) / old['run_id']
    if not (old_folder / 'nav.csv').exists():
        return None
    legacy, new = _run_metrics(old_folder), _run_metrics(folder)
    booked = actions[actions.status == 'BOOKED'] if len(actions) else actions
    common_years = sorted(set(legacy['years']) & set(new['years']))
    delta_years = {y: (None if legacy['years'][y] is None or new['years'][y] is None else new['years'][y] - legacy['years'][y]) for y in common_years}
    both = [y for y in common_years if delta_years[y] is not None]
    chain = lambda years: float(pd.Series([1 + years[y] for y in both]).prod() - 1) if both else None
    legacy['common_years_return'], new['common_years_return'] = chain(legacy['years']), chain(new['years'])  # same years for both engines
    saturdays = sorted(d.strftime('%Y-%m-%d') for d in new['nav_dates'] - legacy['nav_dates'] if d.weekday() >= 5)
    reasons = []
    if len(booked):
        reasons.append(f"公司行動入帳 {len(booked)} 筆，現金與畸零股折現 {float(booked.cash_credited.sum()):,.0f} 元")
    if saturdays:
        reasons.append(f'新增週六交易日 {len(saturdays)} 個')
    if old['period'] != [min(new['nav_dates']).strftime('%Y-%m-%d'), max(new['nav_dates']).strftime('%Y-%m-%d')]:
        reasons.append(f"視窗不同：舊 {old['period']} / 新 {[min(new['nav_dates']).strftime('%Y-%m-%d'), max(new['nav_dates']).strftime('%Y-%m-%d')]}")
    reasons.append('新引擎的持倉路徑（股數、現金、出場條件）自第一個事件起與舊引擎分岔，差異不是單一事件的加總')
    strip = lambda m: {k: v for k, v in m.items() if k != 'nav_dates'}
    return {'legacy_run_id': old['run_id'], 'legacy_snapshot_id': old.get('snapshot_id'), 'legacy_accounting': old.get('accounting', 'LEGACY_RAW_PRICE'),
            'window_legacy': old['period'], 'window_new': [min(new['nav_dates']).strftime('%Y-%m-%d'), max(new['nav_dates']).strftime('%Y-%m-%d')],
            'legacy': strip(legacy), 'new': strip(new), 'common_years': both,
            # the comparable difference: both engines chained over the same calendar years (whole-window totals differ in window length)
            'delta': {'common_years_return': None if not both else new['common_years_return'] - legacy['common_years_return'],
                      'base_total_return': None if None in (legacy['base_total_return'], new['base_total_return']) else new['base_total_return'] - legacy['base_total_return'],
                      'x2_total_return': None if None in (legacy['x2_total_return'], new['x2_total_return']) else new['x2_total_return'] - legacy['x2_total_return'],
                      'years': delta_years},
            'attribution': {'cash_credited': float(booked.cash_credited.sum()) if len(booked) else 0.0, 'events_booked': int(len(booked)),
                            'assumed_stock_events': int((booked.terms_status == 'ASSUMED_STOCK').sum()) if len(booked) else 0,
                            'unresolved_events': int((actions.status == 'UNRESOLVED_NOT_BOOKED').sum()) if len(actions) else 0,
                            'saturday_sessions_added': len(saturdays), 'saturday_sessions': saturdays, 'reasons': reasons}}


def corporate_action_impact(actions: pd.DataFrame) -> dict:
    if actions is None or actions.empty:
        return {'events_booked': 0, 'cash_credited': 0.0, 'assumed_stock_events': 0, 'unresolved_events': 0, 'duplicate_suppressed': 0, 'years_with_assumed_terms': []}
    booked = actions[actions.status == 'BOOKED']
    assumed = booked[booked.terms_status == 'ASSUMED_STOCK']
    return {'events_booked': int(len(booked)), 'cash_credited': float(booked.cash_credited.sum()), 'assumed_stock_events': int(len(assumed)),
            'unresolved_events': int((actions.status == 'UNRESOLVED_NOT_BOOKED').sum()), 'duplicate_suppressed': int((actions.status == 'DUPLICATE_SUPPRESSED').sum()),
            'years_with_assumed_terms': sorted({int(pd.Timestamp(d).year) for d in assumed.date})}


def _open_positions_line(s) -> str:
    value = s['open_positions']['value']
    text = '-' if value is None else f'{value:,.0f}'
    return f"- 期末未平倉：{s['open_positions']['count']} 檔，市值 {text} 元（最後可靠收盤價 × 已調整股數；已計入期末 NAV）。"


def _legacy_lines(s) -> str:
    c = s.get('legacy_comparison')
    if not c:
        return '- 舊版比較：無同策略的舊 Run。'
    pct = lambda v: '-' if v is None else f'{v * 100:.2f}%'
    years = '；'.join(f"{y} {pct(c['legacy']['years'].get(y))}→{pct(c['new']['years'].get(y))}" for y in sorted(c['delta']['years']))
    span = f"{c['common_years'][0]}～{c['common_years'][-1]}" if c.get('common_years') else '-'
    return (f"- 舊版比較（舊 Run `{c['legacy_run_id']}`，{c['legacy_accounting']}，視窗 {'～'.join(c['window_legacy'])}）：共同年份 {span} 連乘報酬（基礎）"
            f"{pct(c['legacy']['common_years_return'])}→{pct(c['new']['common_years_return'])}（差 {pct(c['delta']['common_years_return'])}）；"
            f"各自視窗總報酬 基礎 {pct(c['legacy']['base_total_return'])}→{pct(c['new']['base_total_return'])}，2x {pct(c['legacy']['x2_total_return'])}→{pct(c['new']['x2_total_return'])}；"
            f"交易數 {c['legacy']['trades']}→{c['new']['trades']}；"
            f"逐年 {years}；原因：{'；'.join(c['attribution']['reasons'])}。")


def read_actions(folder: Path) -> pd.DataFrame:
    path = folder / 'corporate_actions.csv'
    if not path.exists():
        return pd.DataFrame(columns=['date', 'stock_id', 'event_id', 'kind', 'status', 'terms_status', 'cash_credited'])
    try:
        return pd.read_csv(path, dtype={'stock_id': str})
    except pd.errors.EmptyDataError:
        return pd.DataFrame(columns=['date', 'stock_id', 'event_id', 'kind', 'status', 'terms_status', 'cash_credited'])


PRIORITY_RULE = ('Per strategy, from its own VERIFIED years only (never compared across strategies, shown in catalog order): '
                 'A_CONTINUE = 2x-cost return positive in every VERIFIED year and in every VERIFIED segment; '
                 'B_INVESTIGATE = positive in at least half of the VERIFIED years; C_DEPRIORITIZE = otherwise; '
                 'INSUFFICIENT_VERIFIED_YEARS = no VERIFIED year. A suggestion for research order, not evidence of validity or a certification.')


TRADE_COLUMNS = ['stock_id', 'buy_date', 'buy_price', 'sell_date', 'sell_price', 'shares', 'cost', 'profit_pct', 'reason']


def read_trades(path) -> pd.DataFrame:
    """A run without any trade stores an empty file: zero trades, not an error."""
    try:
        return pd.read_csv(path, dtype={'stock_id': str})
    except pd.errors.EmptyDataError:
        return pd.DataFrame(columns=TRADE_COLUMNS)


def uncredited_distributions(trades: pd.DataFrame, events: pd.DataFrame, nav: pd.DataFrame) -> dict:
    """Per year: the TWT49U value (cash plus stock, per share, times shares held) of ex-dates inside a closed trade's holding period.
    The raw-price engine books it as a price loss and never credits it; the share of the year's starting NAV bounds that understatement."""
    if trades.empty or events.empty:
        return {}
    nav = nav.assign(date=pd.to_datetime(nav.date)).sort_values('date')
    start = nav.groupby(nav.date.dt.year).nav_base.first()
    by_stock = {sid: g for sid, g in events.assign(ex_date=pd.to_datetime(events.ex_date)).groupby('stock_id')}
    out = {}
    for row in trades.itertuples():
        group = by_stock.get(str(row.stock_id))
        if group is None or pd.isna(row.buy_date) or pd.isna(row.sell_date) or pd.isna(getattr(row, 'shares', None)):
            continue
        inside = group[(group.ex_date > pd.Timestamp(row.buy_date)) & (group.ex_date <= pd.Timestamp(row.sell_date))]
        for event in inside.itertuples():
            if pd.notna(event.value):
                out[event.ex_date.year] = out.get(event.ex_date.year, 0.0) + float(event.value) * float(row.shares)
    return {str(y): {'value': round(v, 2), 'share_of_start_nav': round(v / float(start[y]), 6) if y in start else None} for y, v in sorted(out.items())}


def action_exposure(trades: pd.DataFrame, marks: dict, jumps: dict | None = None) -> dict:
    """Trades held across an ex-date or capital change (the raw-price engine books those gaps as gains or losses), and trades held across a
    price jump no known event explains (a missing capital change: the year is excluded from every aggregate)."""
    if trades.empty:
        return {'trades': 0, 'exposed': 0, 'share': None, 'unexplained_jump_trades': 0, 'unexplained_jump_years': []}
    exposed, jumped, years = 0, 0, set()
    for row in trades.itertuples():
        if pd.isna(row.buy_date) or pd.isna(row.sell_date):
            continue
        buy, sell = pd.Timestamp(row.buy_date), pd.Timestamp(row.sell_date)
        days = marks.get(str(row.stock_id))
        exposed += days is not None and bool(((days > buy) & (days <= sell)).any())
        hits = (jumps or {}).get(str(row.stock_id))
        inside = hits[(hits > buy) & (hits <= sell)] if hits is not None else []
        if len(inside):
            jumped += 1
            years |= {int(d.year) for d in inside}
    return {'trades': len(trades), 'exposed': int(exposed), 'share': exposed / len(trades), 'unexplained_jump_trades': jumped,
            'unexplained_jump_years': sorted(years)}


def _v(metric):
    return None if metric is None else metric.get('value')


def decision(row, summary, analysis) -> dict:
    """D9 state plus a rule-based research priority (not a certification, not a ranking)."""
    if row['state'] in ('GOVERNED_ONLY', 'ENGINE_UNSUPPORTED', 'DATA_BLOCKED', 'FAILED'):
        return {'state': row['state'], 'priority': 'NOT_RUN', 'reason': row['reason']}
    if row.get('no_trades'):  # complete data and no trade: the signal condition, not performance, is the open question
        return {'state': 'REFERENCE_READY', 'data_trust_verified': summary['data_trust_verified'], 'certified': False,
                'priority': 'NO_TRADES_INVESTIGATE_SIGNAL', 'reason': 'zero trades over the whole window with the required data present'}
    state = 'REFERENCE_READY'
    if row.get('reproducible'):
        state = 'REPRODUCIBLE'
    if summary['data_trust_verified']:
        state = 'GOVERNED_VALIDATION_REQUIRED'  # data trust verified for a named period: eligible to apply for governed validation
    verified = analysis['segments']['VERIFIED_ONLY']
    years = [y for y in analysis['years'].values() if y['trust'] == 'VERIFIED' and _v(y['x2'].get('cumulative_return')) is not None]
    positive_x2 = sum(_v(y['x2']['cumulative_return']) > 0 for y in years)
    total_x2 = [_v(s['x2']['cumulative_return']) for s in verified.values() if s['years_used']]
    if not years:
        priority, why = 'INSUFFICIENT_VERIFIED_YEARS', 'no VERIFIED year with a result'
    elif positive_x2 == len(years) and all(t is not None and t > 0 for t in total_x2):
        priority, why = 'A_CONTINUE', f'2x-cost return positive in {positive_x2}/{len(years)} VERIFIED years'
    elif positive_x2 * 2 >= len(years):
        priority, why = 'B_INVESTIGATE', f'2x-cost return positive in {positive_x2}/{len(years)} VERIFIED years'
    else:
        priority, why = 'C_DEPRIORITIZE', f'2x-cost return positive in {positive_x2}/{len(years)} VERIFIED years'
    return {'state': state, 'data_trust_verified': summary['data_trust_verified'], 'certified': False, 'priority': priority, 'reason': why}


def cross_regime(evaluation, rows, root=None) -> dict:
    """One entry per catalog strategy in catalog order; executed rows get regime.analyze on their stored NAV and trades."""
    from core.research.history import regime
    root = Path(root or hf.RUNS)
    bench = hf.snapshot(evaluation['snapshot_id']).frame('benchmark')
    spec = json.loads((hf.OUT / 'regime_specs' / f'{regime.REGIME_SPEC_ID}.json').read_text(encoding='utf-8'))
    snap = hf.snapshot(evaluation['snapshot_id'])
    marks, jumps = event_marks(snap)
    out = {'evaluation_id': evaluation['evaluation_id'], 'snapshot_id': evaluation['snapshot_id'], 'regime_spec': spec, 'labels': regime.LABELS,
           'engine_limits': ENGINE_LIMITS, 'legacy_engine_limit': LEGACY_ENGINE_LIMIT, 'accounting': hf.ACCOUNTING, 'benchmark': hf.BENCHMARK,
           'priority_rule': PRIORITY_RULE, 'legacy_baseline': {k: _legacy_baseline().get(k) for k in ('snapshot_id', 'engine_version', 'frozen_at')},
           'strategies': [], 'legacy_comparison_table': []}
    for row in rows:
        summary = evaluation['summaries'][row['strategy_id']]
        entry = {**row, 'trust_years': {str(c['year']): c['status'] for c in evaluation['trust'][row['strategy_id']]}, 'analysis': None,
                 'final_state': FINAL_STATES.get(row['state'], row['state']), 'accounting': None, 'legacy_comparison': None,
                 'open_positions': {'count': 0, 'value': None, 'rows': []}, 'corporate_action_impact': corporate_action_impact(None)}
        if row.get('run_id') and row['state'] in ('RUN', 'REUSE'):
            folder = root / row['run_id']
            record = json.loads((folder / 'run.json').read_text(encoding='utf-8'))
            nav, trades = pd.read_csv(folder / 'nav.csv'), read_trades(folder / 'trades.csv')
            actions = read_actions(folder)
            entry['accounting'] = record.get('accounting') or hf.LEGACY_LABEL
            if entry['accounting'] != hf.ACCOUNTING:  # a legacy-accounting run can never be a verified reuse under the new identity
                entry['final_state'] = 'FAILED' if row['state'] == 'REUSE' else entry['final_state']
            entry['ledger_version'] = record.get('ledger_version')
            trust, exposure, impact = run_trust(evaluation['trust'][row['strategy_id']], nav, trades, actions, row['window'][1], marks, jumps)
            entry['analysis'] = regime.analyze(nav, trades, bench, trust)
            entry['created_at'] = record.get('created_at')
            entry['spec_before_run'] = spec_before(spec['registered_at'], record.get('created_at'))
            entry['no_trades'] = len(trades) == 0
            entry['corporate_action_exposure'] = exposure
            entry['corporate_action_impact'] = impact
            entry['uncredited_distributions'] = {} if entry['accounting'] == hf.ACCOUNTING else uncredited_distributions(trades, snap.frame('events'), nav)
            open_rows = record.get('open_positions') or []
            values = [r.get('last_mark_value') for r in open_rows if r.get('last_mark_value') is not None]
            entry['open_positions'] = {'count': len(open_rows), 'value': float(sum(values)) if values else (0.0 if not open_rows else None), 'rows': open_rows}
            entry['legacy_comparison'] = legacy_comparison(row['strategy_id'], folder, actions, root)
            entry['limitations'] = sorted({r for c in evaluation['trust'][row['strategy_id']] if c['status'] != 'VERIFIED' for r in c['reasons'][:2]})
            if impact['years_with_assumed_terms']:
                entry['limitations'].append(f"MIXED_DIVIDEND_TERMS_ASSUMED_STOCK:{','.join(map(str, impact['years_with_assumed_terms']))}")
        entry['decision'] = decision(row | {'no_trades': entry.get('no_trades')}, summary, entry['analysis']) if entry['analysis'] else decision(row | {'state': row['state'] if row['state'] not in ('RUN', 'REUSE') else 'FAILED'}, summary, None)
        comparison = entry['legacy_comparison'] or {}
        out['legacy_comparison_table'].append({
            'strategy_id': row['strategy_id'], 'final_state': entry['final_state'], 'accounting': entry['accounting'],
            'common_years': comparison.get('common_years'),
            'legacy_base_total_return': (comparison.get('legacy') or {}).get('common_years_return'), 'new_base_total_return': (comparison.get('new') or {}).get('common_years_return'),
            'delta': (comparison.get('delta') or {}).get('common_years_return'),
            'reason': '；'.join((comparison.get('attribution') or {}).get('reasons', [])) or row.get('reason')})
        out['strategies'].append(entry)
    return out


def _fmt(metric):
    value = _v(metric)
    return f"None（{metric.get('reason')}）" if value is None else f'{value * 100:.2f}%'


def _num(metric):
    value = _v(metric)
    return f"None（{metric.get('reason')}）" if value is None else f'{value:.3f}'


def cross_regime_md(report) -> str:
    spec = report['regime_spec']
    lines = ['# 跨市場環境策略驗證（2015～2025 參考研究）\n', HEADER.replace('NOT_A_TRIAL', 'NOT_A_TRIAL · NOT_OOS_PREVIOUSLY_VIEWED_STRATEGIES'),
             f"- Snapshot `{report['snapshot_id']}`；Evaluation `{report['evaluation_id']}`；市場環境規格 `{spec['regime_spec_id']}`（登記於 {spec['registered_at']}，早於所有歷史 Run）。",
             f"- 規則：趨勢＝{spec['spec']['trend']['measure']}（≥+10% 多頭、≤−10% 空頭、其餘盤整）；波動＝{spec['spec']['volatility']['measure']}（≥20% 高波動）；"
             f"{spec['spec']['scoring']}；樣本 < 60 個交易日為 INSUFFICIENT_SAMPLE。",
             f"- Benchmark：{report['benchmark']}。",
             '- 彙總口徑：`VERIFIED_ONLY`（主要，只用 VERIFIED 年份）與 `VERIFIED_AND_PARTIAL`（敏感度，含資料附註年份）。BLOCKED 年份不產生數字。',
             '- 表格依 catalog 固定順序，不排名、不打分；研究優先順序是規則化建議，不是認證。',
             f"- 優先順序規則：{report['priority_rule']}\n", '## 共同限制\n']
    lines += [f'- {x}' for x in report['engine_limits']]
    if report.get('legacy_comparison_table'):
        pct = lambda v: '-' if v is None else f'{v * 100:.2f}%'
        lines += [f"\n## 新版（{report.get('accounting')}）與舊版（{report.get('legacy_baseline', {}).get('engine_version')}，LEGACY_ACCOUNTING/PROVISIONAL）比較\n",
                  f"- 舊版基準：Snapshot `{report.get('legacy_baseline', {}).get('snapshot_id')}`，凍結於 {report.get('legacy_baseline', {}).get('frozen_at')}；舊 Run 未修改。",
                  f"- {report.get('legacy_engine_limit')}\n",
                  '| 策略 | 最終狀態 | 會計 | 共同年份 | 舊版（基礎，共同年份連乘） | 新版（基礎，共同年份連乘） | 差異 | 差異原因 |', '|---|---|---|---|---|---|---|---|']
        for c in report['legacy_comparison_table']:
            years = c.get('common_years') or []
            lines.append(f"| {c['strategy_id']} | {c['final_state']} | {c['accounting'] or '-'} | {f'{years[0]}～{years[-1]}' if years else '-'} | "
                         f"{pct(c['legacy_base_total_return'])} | {pct(c['new_base_total_return'])} | "
                         f"{pct(c['delta'])} | {c['reason'] or '-'} |")
    lines += ['\n## 總覽（catalog 順序）\n', '| 策略 | 狀態 | 視窗 | VERIFIED 年 | 2019-2021 基礎 / 2x（VERIFIED） | 2022-2025 基礎 / 2x（VERIFIED） | 研究判定 | 優先順序 | 理由 |',
              '|---|---|---|---|---|---|---|---|---|']
    for s in report['strategies']:
        a, d = s['analysis'], s['decision']
        seg = lambda name, track: _fmt(a['segments']['VERIFIED_ONLY'][name][track]['cumulative_return']) if a else '-'
        verified = [y for y, v in a['years'].items() if v['trust'] == 'VERIFIED'] if a else []
        lines.append(f"| {s['strategy_id']} | {s.get('final_state', s['state'])}{'（整段無交易）' if s.get('no_trades') else ''} | {'～'.join(s['window']) if s['window'] else '-'} | {','.join(verified) or '-'} | "
                     f"{seg('2019-2021', 'base')} / {seg('2019-2021', 'x2')} | {seg('2022-2025', 'base')} / {seg('2022-2025', 'x2')} | {d['state']} | {d['priority']} | {d['reason']} |")
    for s in report['strategies']:
        a = s['analysis']
        lines.append(f"\n## {s['strategy_id']}\n")
        if not a:
            lines.append(f"- 未執行：{s['state']}（{s['reason']}）。\n")
            continue
        lines += [f"- Run `{s['run_id']}`；Run Identity `{s['run_identity']}`；視窗 {s['window'][0]}～{s['window'][1]}；規格早於 Run：{'YES' if s['spec_before_run'] else 'NO（POST_HOC_SPEC）'}。",
                  f"- 資料限制：{'；'.join(s['limitations']) or '無'}" + ('；**整段無交易**' if s['no_trades'] else '')
                  + f"；未標市場環境的交易日 {a.get('unlabelled_sessions', '-')} 天（歷史不足或指數缺值，不計入環境表） ",
                  f"- 會計：{s.get('accounting') or '-'}；分錄版本 `{s.get('ledger_version') or '-'}`；公司行動入帳 {s['corporate_action_impact']['events_booked']} 筆，"
                  f"現金與畸零股折現 {s['corporate_action_impact']['cash_credited']:,.0f} 元，ASSUMED_STOCK {s['corporate_action_impact']['assumed_stock_events']} 筆"
                  f"（年份 {s['corporate_action_impact']['years_with_assumed_terms'] or '無'}，該年標 PARTIAL），未解析 {s['corporate_action_impact']['unresolved_events']} 筆，"
                  f"重複抑制 {s['corporate_action_impact']['duplicate_suppressed']} 筆。",
                  _open_positions_line(s),
                  f"- 公司行動暴露：{s['corporate_action_exposure']['exposed']}／{s['corporate_action_exposure']['trades']} 筆交易的持有期間跨越除權息或減資；"
                  f"{s['corporate_action_exposure']['unexplained_jump_trades']} 筆跨越無事件解釋的價格跳空，年份 "
                  f"{s['corporate_action_exposure']['unexplained_jump_years'] or '無'} 已從所有彙總排除；"
                  f"最後一年有無事件跳空時排除：{s['corporate_action_exposure'].get('untracked_open_position_years') or '無'}。",
                  _legacy_lines(s),
                  '| 年 | Data Trust | 基礎報酬 | 2x 報酬 | Sharpe（基礎） | MDD（基礎） | 報酬指數 | 交易數 | 勝率 | 平均持有（交易日） | 交易日 |', '|---|---|---|---|---|---|---|---|---|---|---|']
        for year, y in a['years'].items():
            t = y.get('trades', {})
            lines.append(f"| {year} | {y['trust']} | {_fmt(y['base']['cumulative_return'])} | {_fmt(y['x2']['cumulative_return'])} | {_num(y['base']['sharpe'])} | "
                         f"{_fmt(y['base']['max_drawdown'])} | {_fmt(y['benchmark_total_return']['cumulative_return']) if 'benchmark_total_return' in y else '-'} | "
                         f"{t.get('trade_count', '-')} | {_fmt(t['win_rate']) if t else '-'} | {_num(t['avg_holding_sessions']) if t else '-'} | {y['base'].get('sessions', y.get('sessions', '-'))} |")
        for basis in ('VERIFIED_ONLY', 'VERIFIED_AND_PARTIAL'):
            lines += [f'\n分段與市場環境（{basis}）\n', '| 切片 | 交易日 | 基礎報酬 | 2x 報酬 | 年化（基礎） | Sharpe | MDD | 報酬指數 | 超額（基礎） | t 值 | 狀態 | 排除年份 |',
                      '|---|---|---|---|---|---|---|---|---|---|---|---|']
            slices = {**a['segments'][basis], **a['regimes'][basis]}
            for name, sl in slices.items():
                lines.append(f"| {name} | {sl['base']['sessions']} | {_fmt(sl['base']['cumulative_return'])} | {_fmt(sl['x2']['cumulative_return'])} | "
                             f"{_fmt(sl['base']['annualized_return'])} | {_num(sl['base']['sharpe'])} | {_fmt(sl['base']['max_drawdown'])} | "
                             f"{_fmt(sl['benchmark_total_return']['cumulative_return'])} | {_fmt(sl['base']['excess_vs_benchmark'])} | {_num(sl['base']['t_stat'])} | "
                             f"{sl['base']['status']} | {json.dumps(sl.get('excluded_years', {}), ensure_ascii=False) if 'excluded_years' in sl else '-'} |")
            r = a['rolling_252'][basis]
            lines.append(f"\n滾動 252 日（{basis}）：視窗 {r['windows']}；最小 {_fmt(r['min'])}；中位 {_fmt(r['median'])}；最大 {_fmt(r['max'])}；正報酬比例 {_fmt(r['share_positive'])}。")
        lines.append(f"\n研究判定：{s['decision']['state']}；優先順序建議 {s['decision']['priority']}（{s['decision']['reason']}）；CERTIFIED：NO。")
    return '\n'.join(lines) + '\n'


def event_marks(snap):
    """Ex-dates and capital-change dates per stock, and unexplained price jumps per stock, from the snapshot."""
    marks = pd.concat([snap.frame('events')[['stock_id', 'ex_date']].rename(columns={'ex_date': 'date'}), snap.frame('capital_changes')[['stock_id', 'date']]])
    marks = {sid: pd.DatetimeIndex(sorted(g.date)) for sid, g in marks.groupby('stock_id')}
    from core.research.history.coverage import Facts
    found = Facts(snap).jumps
    found = found[~found.explained] if len(found) else found
    jumps = {sid: pd.DatetimeIndex(sorted(g.trade_date)) for sid, g in found.groupby('stock_id')} if len(found) else {}
    return marks, jumps


def run_trust(cells, nav, trades, actions, window_end, marks, jumps):
    """Per-year trust of one run: the coverage matrix, downgraded where a held position crossed an unexplained price jump,
    where open positions cannot be tracked, and to PARTIAL where a held position crossed an ASSUMED_STOCK event."""
    exposure = action_exposure(trades, marks, jumps)
    trust = {c['year']: c['status'] for c in cells}
    trust |= {y: 'EXCLUDED_UNEXPLAINED_PRICE_JUMP' for y in exposure['unexplained_jump_years']}  # a held position crossed one
    held = (pd.to_datetime(trades.sell_date) - pd.to_datetime(trades.buy_date)).dt.days.max() if len(trades) else None
    flat = trades.empty and nav.nav_base.nunique() == 1  # never traded: no position can be open at the end
    untracked = [] if flat else untracked_open_position_years(window_end, jumps, held)
    trust |= {y: 'EXCLUDED_UNTRACKED_OPEN_POSITIONS' for y in untracked}
    exposure['untracked_open_position_years'] = untracked
    impact = corporate_action_impact(actions)
    trust |= {y: 'PARTIAL' for y in impact['years_with_assumed_terms'] if trust.get(y) == 'VERIFIED'}  # a held position crossed an ASSUMED_STOCK event
    return trust, exposure, impact


def regime_reports(rows, evaluation=None, root=None) -> Path:
    evaluation = evaluation or hf.latest_evaluation()
    report = cross_regime(evaluation, rows, root)
    out = hf.report_folder(evaluation['evaluation_id'])
    # one report per set of run outcomes: a failed batch keeps its own report, the next batch writes a new one beside it
    runs = hf.hs.hashlib.sha256(json.dumps([(r['strategy_id'], r['state'], r.get('run_id')) for r in rows]).encode()).hexdigest()[:12]
    write_once(f'cross_regime_report_{runs}.json', json.dumps(report, ensure_ascii=False, indent=1, default=str), out)
    write_once(f'CROSS_REGIME_VALIDATION_REPORT_{runs}.md', cross_regime_md(report), out)
    write('CROSS_REGIME_VALIDATION_REPORT.md', cross_regime_md(report), DOCS)
    point(evaluation, out, cross=runs)
    return out


def final_tables(evaluation, cross, repro=None) -> str:
    """Tables A, B and C of HISTORICAL_DATA_FOUNDATION_FINAL_REPORT.md, generated from stored files only."""
    gap_path = hf.report_folder(evaluation['evaluation_id']) / 'gap_report.json'
    gaps = json.loads(gap_path.read_text(encoding='utf-8'))['gaps']  # missing gap file raises: never reported as zero gaps
    action = {(g['year'], g['category']): g['owner_action'] for g in gaps}
    rows = coverage_rows(evaluation)
    lines = ['## A. 歷史資料庫（逐年）\n', '| 年份 | 股票數 | 行情完整率 | 除權息 / 減資 | 財報 PIT 完整率 | 營收 PIT | 歷史股票池（下市覆蓋） | Benchmark 價／報酬 | 判定 |',
             '|---|---|---|---|---|---|---|---|---|']
    for r in rows:
        ca = r['corporate_actions']
        lines.append(f"| {r['year']} | {r['stocks']} | {_pct(r['ohlcv_session_completeness'])} | {ca['dividends']} / {ca['capital_changes']} | "
                     f"{_cpct(r['financial_pit_completeness'])} | {_cpct(r['revenue_pit_completeness'])} | {r['status_by_category']['universe']}（{_pct(r['delisting_coverage'])}） | "
                     f"{_pct(r['benchmark_completeness']['price'])}／{_pct(r['benchmark_completeness']['total_return'])} | {r['price_layer_verdict']} |")
    lines += ['\n判定欄為價格層（日曆、行情、股票池、下市、除權息、減資）的最差狀態；法人、融資、財報、月營收逐年狀態見 HISTORICAL_DATA_COVERAGE_REPORT.md。\n',
              '## B. 21 策略資料可信度\n', '| 策略 | 可用歷史期間 | 必要資料 | VERIFIED 年 | BLOCKED 年 | Data Trust | Reproducibility | Certification | 缺口原因 | 最小修復動作 |',
              '|---|---|---|---|---|---|---|---|---|---|']
    by_sid = {s['strategy_id']: s for s in (cross or {}).get('strategies', [])}
    for req in evaluation['requirements']:
        sid, s, cells = req['strategy_id'], evaluation['summaries'][req['strategy_id']], evaluation['trust'][req['strategy_id']]
        needed = [cid for cid, need in req['needs'].items() if need == 'REQUIRED' and cid not in cats.PRICED]
        binding = sorted({b for c in cells if c['status'] != 'VERIFIED' for b in c['binding'] if b != 'warmup'})
        fixes = sorted({action[(c['year'], b)] for c in cells for b in c['binding'] if (c['year'], b) in action})
        x = by_sid.get(sid) or {}
        repro_state = (repro or {}).get(sid, 'NOT_RUN' if not x.get('analysis') else 'RUN_ONCE')
        period = f"{s['earliest_usable_date']}～2025-12-31" if s['earliest_usable_date'] else '無'
        lines.append(f"| {sid} | {period} | 價格類{(' + ' + ', '.join(needed)) if needed else ''} | {','.join(map(str, s['verified_years'])) or '-'} | "
                     f"{','.join(map(str, s['blocked_years'])) or '-'} | {s['data_trust_verified'] or 'NO'} | {repro_state} | NO | "
                     f"{', '.join(binding) or '-'}{('；' + req['governance']) if req['governance'] else ''} | {'；'.join(fixes) or ('依治理流程' if req['governance'] else '-')} |")
    lines += ['\n價格類＝交易日曆、行情、股票池、下市、除權息、減資（每個會交易的策略都必要）。\n', '## C. 跨市場環境驗證（可執行策略）\n',
              '| 策略 | 視窗 | 年度報酬（基礎，VERIFIED/PARTIAL 年） | 多頭／空頭／盤整（基礎，VERIFIED_ONLY） | 高／低波動 | VERIFIED 期間 基礎／2x | MDD（VERIFIED） | 報酬指數同期 | 有效交易日 | 限制 | 進一步研究 |',
              '|---|---|---|---|---|---|---|---|---|---|---|']
    for s in (cross or {}).get('strategies', []):
        a = s.get('analysis')
        if not a:
            continue
        years = '；'.join(f"{y}:{_fmt(v['base']['cumulative_return'])}" for y, v in a['years'].items() if _v(v['base']['cumulative_return']) is not None)
        reg = a['regimes']['VERIFIED_ONLY']
        trend = '／'.join(_fmt(reg[k]['base']['cumulative_return']) for k in ('BULL', 'BEAR', 'SIDEWAYS'))
        vol = '／'.join(_fmt(reg[k]['base']['cumulative_return']) for k in ('HIGH_VOL', 'LOW_VOL'))
        verified_years = [int(y) for y, v in a['years'].items() if v['trust'] == 'VERIFIED']
        whole = regime_slice(a, verified_years)
        ex = s['corporate_action_exposure']
        limits = f"跨除權息交易 {ex['exposed']}/{ex['trades']}；無事件跳空排除年 {ex['unexplained_jump_years'] or '無'}"
        lines.append(f"| {s['strategy_id']} | {'～'.join(s['window'])} | {years} | {trend} | {vol} | {whole['base']} ／ {whole['x2']} | {whole['mdd']} | "
                     f"{whole['bench']} | {whole['sessions']} | {limits} | {s['decision']['priority']} |")
    return '\n'.join(lines) + '\n'


def regime_slice(analysis, years) -> dict:
    """Compounded result over the given VERIFIED years from the stored per-year slices (each year's own metrics, chained)."""
    if not years:
        return {'base': '-', 'x2': '-', 'mdd': '-', 'bench': '-', 'sessions': 0}
    chain = lambda track: float(pd.Series([1 + _v(analysis['years'][str(y)][track]['cumulative_return']) for y in years]).prod() - 1)
    bench = float(pd.Series([1 + _v(analysis['years'][str(y)]['benchmark_total_return']['cumulative_return']) for y in years]).prod() - 1)
    mdd = min(_v(analysis['years'][str(y)]['base']['max_drawdown']) for y in years)
    sessions = sum(analysis['years'][str(y)]['base']['sessions'] for y in years)
    return {'base': f'{chain("base") * 100:.2f}%', 'x2': f'{chain("x2") * 100:.2f}%', 'mdd': f'{mdd * 100:.2f}%（年內最差）',
            'bench': f'{bench * 100:.2f}%', 'sessions': sessions}


def write_once(name: str, text: str, folder: Path):
    """Stored evidence is never rewritten: identical content is a no-op, different content raises."""
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / name
    if path.exists():
        if path.read_text(encoding='utf-8') != text:
            raise FileExistsError(f'EVIDENCE_EXISTS_WITH_OTHER_CONTENT: {path}')
        return
    with open(path, 'x', encoding='utf-8') as stream:
        stream.write(text)


def point(evaluation, folder: Path, cross: str | None):
    pointer = hf.REPORTS / 'LATEST.json'
    old = json.loads(pointer.read_text(encoding='utf-8')) if pointer.exists() else {}
    same = old.get('folder') == folder.relative_to(hf.REPORTS).as_posix()
    rel = lambda name: (folder / name).relative_to(hf.REPORTS).as_posix()
    body = {'evaluation_id': evaluation['evaluation_id'], 'folder': folder.relative_to(hf.REPORTS).as_posix(),
            'cross_regime': rel(f'cross_regime_report_{cross}.json') if cross else (old.get('cross_regime') if same else None),
            'cross_regime_md': rel(f'CROSS_REGIME_VALIDATION_REPORT_{cross}.md') if cross else (old.get('cross_regime_md') if same else None)}
    tmp = pointer.with_suffix('.tmp')
    tmp.write_text(json.dumps(body), encoding='utf-8')
    tmp.replace(pointer)


def untracked_open_position_years(window_end: str, jumps: dict, max_hold_days: float | None = None) -> list[int]:
    """The engine stores closed trades only, so positions still open at the window end are invisible. A position open at the end was
    bought at most max(365, 2 x the longest observed holding) calendar days earlier (a bound, not a proof); every year with an
    unexplained jump in that span is excluded."""
    end = pd.Timestamp(window_end)
    earliest = end - pd.Timedelta(days=max(365.0, 2 * (max_hold_days or 0)))
    return sorted({int(d.year) for dates in jumps.values() for d in dates if earliest <= d <= end})


def write(name: str, text: str, folder: Path):
    folder.mkdir(parents=True, exist_ok=True)
    (folder / name).write_text(text, encoding='utf-8')


def data_reports(evaluation=None) -> Path:
    evaluation = evaluation or hf.latest_evaluation()
    out = hf.report_folder(evaluation['evaluation_id'])
    rows = coverage_rows(evaluation)
    gap_path = out / 'gap_report.json'
    gap_report = json.loads(gap_path.read_text(encoding='utf-8'))  # missing gap file raises: never reported as zero gaps
    files = {'HISTORICAL_DATA_COVERAGE_REPORT.md': coverage_md(evaluation, rows),
             'HISTORICAL_DATA_COVERAGE_REPORT.json': json.dumps({'snapshot_id': evaluation['snapshot_id'], 'evaluation_id': evaluation['evaluation_id'],
                                                                 'years': rows, 'cells': evaluation['coverage']}, ensure_ascii=False, indent=1, default=str),
             'strategy_data_requirements.md': requirements_md(evaluation),
             'strategy_data_requirements.json': json.dumps(evaluation['requirements'], ensure_ascii=False, indent=1),
             'strategy_data_trust_matrix.md': trust_md(evaluation),
             'strategy_data_trust_matrix.json': json.dumps({'trust': evaluation['trust'], 'summaries': evaluation['summaries']}, ensure_ascii=False, indent=1, default=str),
             'historical_source_inventory.md': inventory_md(evaluation),
             'historical_source_inventory.json': json.dumps(evaluation['inventory'], ensure_ascii=False, indent=1, default=str),
             'gap_report.md': gaps_md(gap_report)}
    for name, text in files.items():
        write_once(name, text, out)
        write(name, text, DOCS)  # docs/ holds convenience copies; the evidence is the write-once folder
    point(evaluation, out, cross=None)
    print(json.dumps({'reports': hf.hs._key(out), 'files': sorted(files)}))
    return out


if __name__ == '__main__':
    data_reports()
