"""Read-only research strategy payloads for the Web (h4-web-integration-v1 design D3-D5).

Only the verified snapshot outputs/research_mode/h4_web_v1 is read. Dates are checked before any file is opened;
paths are a fixed root plus fixed names. Nothing here writes, publishes or runs an engine."""
from __future__ import annotations

import csv
import hashlib
import json
from pathlib import Path
import re

import pandas as pd

from core.research import latest_signal, strategy_signal as ss
from core.research.h4_signal import SPEC, STRATEGY_ID, build_research_registry

ROOT = Path(__file__).resolve().parents[1]
SNAPSHOT = ROOT / 'outputs/research_mode/h4_web_v1'
DISPLAY_NAME = 'H4｜基本面成長＋股價動能'
DISCLAIMER = '歷史回測不保證未來報酬。H4 尚未通過完整策略驗證。'
PERIODS = {'FULL': ('2023-01-03', '2025-12-31'), '2023': ('2023-01-03', '2023-12-31'), '2024': ('2024-01-01', '2024-12-31'),
           '2025': ('2025-01-01', '2025-12-31'), '2016-2022': ('2016-01-04', '2022-12-30'), '2026': ('2026-01-02', '2026-12-31')}
PERIOD_LABELS = {'FULL': '全期 2023～2025', '2023': '2023', '2024': '2024', '2025': '2025',
                 '2016-2022': '2016～2022（DATA_BLOCKED）', '2026': '2026（SEALED）'}
CAPITAL_RANGE = (10_000, 1_000_000_000)
COMPONENT_LABELS = (('g2_score', '營業利益成長分數'), ('g3_score', 'EPS 成長分數'), ('momentum_score', '股價動能分數'))
STATUS_REASONS = {'NO_GROWTH': '缺少營業利益與 EPS 成長資料，無法評分', 'NO_MOMENTUM': '缺少 near_high_252d 動能資料，無法評分',
                  'EVENT_EXCLUDED': '訊號日有未解決的公司事件，評分後依封存規則排除', 'NOT_MEMBER': '訊號日不在研究股票池內'}
_DAY = re.compile(r'^\d{4}-\d{2}-\d{2}$')


class ResearchError(Exception):
    def __init__(self, status: int, code: str, reason: str):
        super().__init__(code)
        self.status, self.code, self.reason = status, code, reason

    def payload(self):
        return {'status': 'error', 'code': self.code, 'reason': self.reason, 'research_only': True}


def research_ids() -> tuple[str, ...]:
    return build_research_registry().canonical_ids()


def is_research(strategy_id) -> bool:
    return str(strategy_id or '').strip().upper() in research_ids()


def _strategy(sid):
    if sid not in research_ids():
        raise ResearchError(404, 'UNKNOWN_RESEARCH_STRATEGY', 'Strategy is not in the research registry')
    return sid


def check_dates(*days):
    """SEALED before DATA_BLOCKED, both before any file is read."""
    for day in days:
        if not isinstance(day, str) or not _DAY.match(day):
            raise ResearchError(400, 'INVALID_DATE', 'Dates must be YYYY-MM-DD')
    if any(day > ss.DEV_END for day in days):
        raise ResearchError(403, 'SEALED', f'2026 out-of-sample data is sealed; requests after {ss.DEV_END} are refused')
    if any(day < ss.DEV_START for day in days):
        raise ResearchError(409, 'DATA_BLOCKED', '2016-2022 is DATA_BLOCKED: point-in-time fundamentals fail (corrected filings without a first version)')


def _sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def snapshot():
    """Manifest, after checking every snapshot file and every archived source against it."""
    # ponytail: rehashes ~8 MB per request (tens of ms); cache keyed on file mtimes if this page ever gets traffic
    try:
        manifest = json.loads((SNAPSHOT / 'manifest.json').read_text(encoding='utf-8'))
        bad = [name for name, digest in manifest['files_sha256'].items() if _sha(SNAPSHOT / name) != digest]
        bad += [name for name, digest in manifest['sources_sha256'].items() if _sha(ROOT / name) != digest]
    except (OSError, ValueError, KeyError) as exc:
        raise ResearchError(503, 'DATA_NOT_AVAILABLE', f'Research snapshot unavailable: {type(exc).__name__}') from exc
    if bad:
        raise ResearchError(503, 'DATA_NOT_AVAILABLE', 'Snapshot or archive SHA256 mismatch: ' + ', '.join(sorted(bad)))
    return manifest


def _performance():
    return json.loads((SNAPSHOT / 'performance.json').read_text(encoding='utf-8'))


def _configuration():
    growth = [f for f in SPEC.required_features if f != 'near_high_252d']
    weights = SPEC.parameters['weights']
    return {'factors': [{'name': 'G2 營業利益 YoY', 'feature': growth[0], 'weight': weights['growth'] / len(growth)},
                        {'name': 'G3 EPS YoY', 'feature': growth[1], 'weight': weights['growth'] / len(growth)},
                        {'name': '動能 near_high_252d', 'feature': 'near_high_252d', 'weight': weights['momentum']}],
            'top_n': SPEC.parameters['top_n'], 'weighting': SPEC.portfolio_policy['weighting'], 'max_weight': SPEC.portfolio_policy['max_weight'],
            'rebalance_days': SPEC.parameters['rebalance_days'], 'cost': '2x 原研究交易成本（非 2 倍槓桿）', 'publish': SPEC.execution_policy['publish']}


def overview(sid=STRATEGY_ID):
    """Configuration and validation status; status comes from the snapshot, never from template constants."""
    sid = _strategy(sid)
    out = {'strategy_id': sid, 'display_name': DISPLAY_NAME, 'lifecycle': SPEC.lifecycle.value, 'configuration': _configuration(),
           'disclaimer': DISCLAIMER, 'latest': latest_signal.readiness()}
    try:
        manifest = snapshot()
        perf = _performance()
        out.update(available=True, validation=perf['status'], metrics=perf['metrics'], signal_dates=manifest['signal_dates'],
                   snapshot_created_at=manifest['created_at'])
    except ResearchError as exc:
        out.update(available=False, unavailable_reason=exc.reason, validation=None, signal_dates=[])
    return out


def catalog_entries():
    """Research strategies in the Strategy Center catalog schema; Daily is never available."""
    entries = []
    for sid in research_ids():
        info = overview(sid)
        validation = info['validation'] or {}
        entries.append({
            'strategy_id': sid, 'name': sid, 'display_name': DISPLAY_NAME, 'display_name_en': SPEC.display_name, 'display_name_zh': DISPLAY_NAME,
            'type': 'research', 'risk_level': '', 'description': '研究展示用；未認證，不進入每日推薦、LINE 或交易', 'aliases': [],
            'category_key': 'research', 'source': 'research', 'source_notebook': '', 'category': 'Research',
            'lifecycle': SPEC.lifecycle.value, 'eligibility': validation.get('certification', 'RESEARCH_UNCERTIFIED'),
            'eligibility_reason': None if info['available'] else info['unavailable_reason'],
            'available_for_daily': False, 'daily_reason': '研究策略僅供展示，不可加入每日篩選', 'daily_reason_code': 'RESEARCH_ONLY',
            'available_for_backtest': info['available'], 'backtest_reason': None if info['available'] else info['unavailable_reason'],
            'backtest_reason_code': None if info['available'] else 'DATA_NOT_AVAILABLE', 'reason': None, 'active': False,
            'research': True, 'detail_url': f'/strategies/research?id={sid}', 'validation': info['validation']})
    return entries


def resolve_period(period=None, start=None, end=None):
    if period in PERIODS:
        start, end = PERIODS[period]
    check_dates(start, end)
    key = next((k for k, v in PERIODS.items() if v == (start, end)), None)
    if key is None:
        raise ResearchError(400, 'PERIOD_NOT_ARCHIVED', 'Only the archived periods FULL, 2023, 2024 and 2025 are available')
    return key


def resolve_capital(raw):
    try:
        capital = float(raw) if raw not in (None, '') else 1_000_000.0
    except (TypeError, ValueError):
        capital = float('nan')
    if not CAPITAL_RANGE[0] <= capital <= CAPITAL_RANGE[1]:
        raise ResearchError(400, 'INVALID_CAPITAL', f'Initial capital must be between {CAPITAL_RANGE[0]:,} and {CAPITAL_RANGE[1]:,}')
    return capital


def _figure(traces, title, yaxis):
    return {'data': traces, 'layout': {'title': {'text': title}, 'yaxis': {'title': {'text': yaxis}}, 'legend': {'orientation': 'h'}}}


def _metric(value, reason=None, scale=100, digits=2):
    return {'value': None if value is None else round(value * scale, digits), 'reason': reason}


def backtest(sid=STRATEGY_ID, period='FULL', capital=None, start=None, end=None):
    """Archived 2x-cost run for one archived period. Year periods are slices of the full run, rebased at the prior session."""
    sid = _strategy(sid)
    key = resolve_period(period, start, end)
    capital = resolve_capital(capital)
    manifest = snapshot()
    perf = _performance()
    s = perf['series']
    frame = pd.DataFrame({'nav': s['nav_2x'], 'nav_1x': s['nav_1x'], 'bench': s['benchmark_v2_return']}, index=pd.to_datetime(s['dates']))
    frame['bench'] = frame['bench'].fillna(0.0)
    lo, hi = PERIODS[key]
    window = frame.loc[lo:hi]
    before = frame.loc[:pd.Timestamp(lo) - pd.Timedelta(days=1)]
    base_nav = float(before['nav'].iloc[-1]) if len(before) else float(perf['initial_capital'])
    scale = capital / perf['initial_capital']
    equity = window['nav'] / base_nav
    bench = (1 + window['bench']).cumprod()
    drawdown = equity / equity.cummax() - 1
    monthly = equity.resample('ME').last().pct_change().fillna(equity.resample('ME').last().iloc[0] - 1)
    total = float(equity.iloc[-1] - 1)
    full = key == 'FULL'
    archived_only = 'ARCHIVED_FOR_FULL_PERIOD_ONLY'
    m = perf['metrics']
    trades = []
    with (SNAPSHOT / 'trades.csv').open(encoding='utf-8') as handle:
        for row in csv.DictReader(handle):
            if lo <= row['date'] <= hi:
                trades.append({'date': row['date'], 'stock_id': row['stock_id'], 'side': row['side'], 'price': float(row['price']),
                               'shares': int(row['shares']), 'fee': float(row['fee']), 'tax': float(row['tax'])})
    labels = []
    if capital != perf['initial_capital']:
        labels.append('ILLUSTRATIVE_LINEAR')
    if capital > perf['initial_capital'] * perf['capacity_multiple']:
        labels.append('CAPACITY_WARNING')
    dates = [d.strftime('%Y-%m-%d') for d in window.index]
    return {
        'metrics': {'total_return': _metric(total), 'max_drawdown': _metric(float(drawdown.min())),
                    'sharpe_ratio': _metric(m['sharpe_2x'], scale=1) if full else _metric(None, archived_only),
                    'win_rate': _metric(None, 'NOT_DEFINED_FOR_ARCHIVED_RESEARCH_RUN')},
        'strategy_performance': {sid: {'roi': _metric(total), 'win_rate': _metric(None, 'NOT_DEFINED_FOR_ARCHIVED_RESEARCH_RUN'),
                                       'trade_count': {'value': len(trades), 'reason': None}, 'win_count': {'value': None, 'reason': 'NOT_DEFINED_FOR_ARCHIVED_RESEARCH_RUN'}}},
        'equity_chart': _figure([{'x': dates, 'y': (equity * capital).round(2).tolist(), 'name': 'H4（2x 成本）', 'type': 'scatter'},
                                 {'x': dates, 'y': (bench * capital).round(2).tolist(), 'name': 'Benchmark V2（等權含息）', 'type': 'scatter'}],
                                f'資產曲線 · {PERIOD_LABELS[key]}', '資產（元）'),
        'drawdown_chart': _figure([{'x': dates, 'y': (drawdown * 100).round(3).tolist(), 'name': '回撤 %', 'type': 'scatter', 'fill': 'tozeroy'}],
                                  '回撤', '%'),
        'monthly_chart': _figure([{'x': [d.strftime('%Y-%m') for d in monthly.index], 'y': (monthly * 100).round(3).tolist(), 'name': '月報酬 %', 'type': 'bar'}],
                                 '月報酬', '%'),
        'trades': trades, 'selected_strategies': [sid], 'start_date': dates[0], 'end_date': dates[-1],
        'research': {
            'strategy_id': sid, 'display_name': DISPLAY_NAME, 'period': key, 'period_label': PERIOD_LABELS[key], 'disclaimer': DISCLAIMER,
            'slice_note': None if full else '本期間為 2023～2025 全期封存回測的年度切片（以前一交易日淨值為基準），不是該年獨立起始的回測。',
            'mode': 'ARCHIVED_RESULT_VIEW', 'config_id': perf['config_id'], 'cost': perf['cost'], 'benchmark': perf['benchmark'],
            'initial_capital': capital, 'archived_capital': perf['initial_capital'], 'labels': labels, 'capacity_multiple': perf['capacity_multiple'],
            'end_value': round(float(equity.iloc[-1]) * capital, 2), 'benchmark_return': _metric(float(bench.iloc[-1] - 1)),
            'market_cap_weighted': perf['market_cap_weighted_total_return'],
            'archived_metrics': m, 'yearly': perf['yearly'], 'validation': perf['status'], 'gate_values': perf['gate_values'],
            'dsr_trials': perf['dsr_trials'], 'concentration': perf['concentration'],
            'trading_cost': {'fees': sum(t['fee'] for t in trades), 'taxes': sum(t['tax'] for t in trades), 'fills': len(trades),
                             'scaled_by': scale},
            'integrity': {'status': 'ARCHIVE_VERIFIED', 'files': len(manifest['files_sha256']) + len(manifest['sources_sha256']),
                          'snapshot_created_at': manifest['created_at']},
            'data_source': 'outputs/research_mode/h4_runs_v1/002（stress = 2x 成本）、report_h4_v2/h4_result.json、prepared_v6'}}


def signal_dates(sid=STRATEGY_ID):
    sid = _strategy(sid)
    return {'strategy_id': sid, 'signal_dates': snapshot()['signal_dates'], 'research_only': True}


def _level(value):
    return '較高' if value >= .8 else '較低' if value <= .2 else '中等'


def reason(row):
    """Fixed template over the row's own values; no other financial claim."""
    if row['eligibility_status'] != 'ELIGIBLE':
        return STATUS_REASONS.get(row['eligibility_status'], row['eligibility_status'])
    parts = [f'{label} {row[col]:.2f}（{_level(row[col])}）' for col, label in COMPONENT_LABELS if pd.notna(row.get(col))]
    tail = '進入 Top 10' if row['target_weight'] > 0 else '未進入 Top 10'
    return '、'.join(parts) + f'，綜合分數 {row["final_score"]:.4f}，排名第 {int(row["rank"])}，{tail}。'


def _records(frame):
    return json.loads(frame.to_json(orient='records', force_ascii=False))


def signals(sid=STRATEGY_ID, day=None):
    sid = _strategy(sid)
    check_dates(day)
    manifest = snapshot()
    if day not in manifest['signal_dates']:
        raise ResearchError(404, 'NOT_AN_APPROVED_SIGNAL_DATE', 'Choose one of the archived 60-day rebalance signal dates')
    frame = pd.read_csv(SNAPSHOT / 'signals' / f'{day}.csv', dtype={'ticker': str})
    frame['reason'] = [reason(r) for r in frame.to_dict('records')]
    change = pd.read_csv(SNAPSHOT / 'rebalance' / f'{day}.csv', dtype={'ticker': str})
    index = manifest['signal_dates'].index(day)
    top = frame.loc[frame['target_weight'] > 0]
    return {'strategy_id': sid, 'signal_date': day, 'previous_signal_date': manifest['signal_dates'][index - 1] if index else None,
            'research_only': True, 'notice': '歷史研究訊號，不是今日推薦', 'top': _records(top), 'rows': _records(frame),
            'changes': _records(change), 'status_counts': frame['eligibility_status'].value_counts().to_dict()}


def latest(sid=STRATEGY_ID):
    """(http status, payload). No provider is configured, so this is 403 LATEST_SIGNAL_DISABLED and carries no ticker."""
    sid = _strategy(sid)
    state = latest_signal.readiness()
    return (200 if state['status'] == 'READY' else 403), {'strategy_id': sid, 'research_only': True, **state}
