"""Daily smart selection view (change 2026-11-12, design D5): the stored daily recommendations, read-only, with traceable reasons.

Nothing here writes a file or a table, and no research module imports it (OD-V2-2: 2026 rows never enter research artifacts).
Feasibility describes the signal date's market row only; it does not promise a fill on the next session.
"""
from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
CARDS = ROOT / 'core/research/intelligence/data/cards'
READINESS = ROOT / 'outputs/platform_v2/STRATEGY_READINESS_21.json'
V1 = ROOT / 'outputs/strategy_intelligence'
HEARTBEAT = 'NONE'
RESEARCH_BUDGET = 1_000_000 * 0.20  # one position of the research capital (runner.POSITION_SIZE)
CAP = 0.05
RANK_BASIS = ('同策略內依 ai_score 由高到低；ai_score 來自依規格為 MODEL_BLOCKED 的模型，是未驗證的排序分數，不是上漲機率')
FEASIBILITY_NOTE = '可行性只描述訊號日收盤後已知的行情，不代表次一交易日一定買得到。'
LABELS = ['RESEARCH_ONLY', 'NOT_CERTIFIED', 'NOT_INVESTMENT_ADVICE']


def canonical(strategy):
    from core.strategy_manager import StrategyManager
    try:
        return StrategyManager.resolve(StrategyManager, str(strategy), warn_legacy=False)
    except KeyError:
        return str(strategy)


def feasibility(row) -> dict:
    if row is None:
        return {'status': 'UNKNOWN', 'reason': 'NO_MARKET_ROW'}
    volume, close = row.get('volume'), row.get('close_price')
    if volume is None or pd.isna(volume) or close is None or pd.isna(close):
        return {'status': 'UNKNOWN', 'reason': 'VOLUME_OR_CLOSE_MISSING'}
    if volume <= 0:
        return {'status': 'NOT_TRADABLE', 'reason': 'NO_VOLUME'}
    if row.get('open_price') == row.get('high_price') == row.get('low_price') == close:
        return {'status': 'LIMITED', 'reason': 'ONE_PRICE_SESSION'}
    amount = float(volume) * float(close)  # the table has no traded value: volume x close, labelled as an estimate
    if RESEARCH_BUDGET > CAP * amount:
        return {'status': 'LIMITED', 'reason': 'ABOVE_PARTICIPATION_CAP', 'amount_estimate': amount}
    return {'status': 'FEASIBLE', 'reason': None, 'amount_estimate': amount, 'amount_basis': 'ESTIMATED_VOLUME_X_CLOSE'}


def _trusted(validation) -> bool:
    return bool(validation and validation.get('trusted_years') and validation.get('sample') == 'ADEQUATE')


def build(recs, market, validation, cards, next_session, read_time, authorization) -> dict:
    """recs: daily_recommendations rows (heartbeats included); market: the signal date's daily_market_data rows."""
    allowed, basis = authorization
    evidence = {'data_class': 'REAL_RESEARCH', 'source_table': 'daily_recommendations', 'read_time': read_time, 'labels': LABELS,
                'authorization': basis}
    if not allowed:
        return {'status': 'GOVERNANCE_BLOCKED', 'reason': basis, 'rows': [], 'consensus': [], 'evidence': evidence}
    if recs is None or recs.empty:
        return {'status': 'DATA_MISSING', 'reason': 'NO_RECOMMENDATIONS', 'rows': [], 'consensus': [], 'evidence': evidence}
    recs = recs.assign(stock_id=recs.stock_id.astype(str), strategy_id=recs.strategy.map(canonical))
    signal = str(recs.trade_date.iloc[0])[:10]
    beats = recs.stock_id == HEARTBEAT
    no_pick = sorted(set(recs[beats].strategy_id) - set(recs[~beats].strategy_id))
    recs = recs[~beats].sort_values(['strategy_id', 'ai_score'], ascending=[True, False], na_position='last')
    if recs.empty:
        return {'status': 'NO_SIGNALS', 'reason': '當日策略已執行，但沒有選出股票', 'signal_date': signal, 'rows': [], 'consensus': [], 'no_pick_strategies': no_pick,
                'feasibility_note': FEASIBILITY_NOTE, 'evidence': evidence | {'as_of_date': signal}}
    bars = {str(r['stock_id']): r for r in market.to_dict('records')} if market is not None else {}
    session, session_state = next_session(signal)
    rows = []
    for sid, group in recs.groupby('strategy_id', sort=True):
        card, check = cards.get(sid, {}), validation.get(sid)
        for rank, rec in enumerate(group.to_dict('records'), start=1):
            bar = bars.get(rec['stock_id'])
            feasible = feasibility(bar)
            risks = [f"可行性 {feasible['status']}：{feasible['reason']}"] if feasible['status'] != 'FEASIBLE' else []
            if not _trusted(check):
                risks.append('策略沒有 VERIFIED 軌或樣本不足：屬未驗證訊號')
            risks.append('V1 研究結果未達報酬目標' if (check or {}).get('return_target') == 'RETURN_TARGET_NOT_MET' else '報酬目標狀態見策略驗證')
            rows.append({'stock_id': rec['stock_id'], 'stock_name': (bar or {}).get('stock_name'), 'signal_date': str(rec['trade_date'])[:10],
                         'expected_session': {'date': session, 'reason': None if session else session_state, 'calendar': session_state},
                         'strategy_id': sid, 'strategy_key': rec['strategy'], 'rank': rank, 'rank_basis': RANK_BASIS,
                         'ai_score': {'value': rec.get('ai_score'), 'label': 'UNVALIDATED_RANKING_SCORE'},
                         'conditions': {k: card.get(k) for k in ('selection', 'entry', 'risk_controls')},
                         'feasibility': feasible, 'validation': check or {'status': 'NO_V1_RESULT'}, 'trusted_data_signal': _trusted(check),
                         'risks': risks, 'provenance': {'table': 'daily_recommendations', 'trade_date': signal, 'strategy_key': rec['strategy'],
                                                        'read_time': read_time}})
    consensus = {}
    for row in rows:
        entry = consensus.setdefault(row['stock_id'], {'stock_id': row['stock_id'], 'stock_name': row['stock_name'], 'trusted_data_signals': 0,
                                                       'unvalidated_signals': 0, 'strategies': []})
        entry['trusted_data_signals' if row['trusted_data_signal'] else 'unvalidated_signals'] += 1
        entry['strategies'].append(row['strategy_id'])
    ordered = sorted(consensus.values(), key=lambda c: (-c['trusted_data_signals'], -c['unvalidated_signals'], c['stock_id']))
    return {'status': 'OK', 'signal_date': signal, 'rows': rows, 'consensus': ordered, 'no_pick_strategies': no_pick,
            'feasibility_note': FEASIBILITY_NOTE, 'evidence': evidence | {'as_of_date': signal}}


def strategy_validation() -> dict:
    """Per canonical strategy: trusted years from the verified readiness file, sample and targets from the current V1 results."""
    readiness = json.loads(READINESS.read_text(encoding='utf-8'))['result']['strategies']
    latest = json.loads((V1 / 'LATEST.json').read_text(encoding='utf-8'))
    out = {}
    for row in readiness:
        sid, dims = row['strategy_id'], {}
        identity = latest['strategies'].get(sid)
        path = V1 / 'results' / str(identity) / f"{sid.replace(':', '__')}.json"
        if identity and path.is_file():
            dims = json.loads(path.read_text(encoding='utf-8')).get('dimensions') or {}
        out[sid] = {'state': row['state'], 'trusted_years': row['trusted_years'], 'data_trust': (dims.get('data_trust') or {}).get('status'),
                    'sample': (dims.get('sample') or {}).get('status'), 'return_target': (dims.get('return_target') or {}).get('status')}
    return out


def cards() -> dict:
    out = {}
    for path in CARDS.glob('*.json'):
        card = json.loads(path.read_text(encoding='utf-8'))
        out[card['strategy_id']] = {k: {'text': (card['fields'].get(k) or {}).get('text'), 'sources': len((card['fields'].get(k) or {}).get('sources', []))}
                                    for k in ('selection', 'entry', 'risk_controls')}
    return out


def next_session(signal_date):
    """First later day the official calendar does not know to be closed; an unverified weekday or a conflict is NEXT_SESSION_UNKNOWN."""
    from core.research import trading_calendar
    closed = trading_calendar.update_closures()
    day = date.fromisoformat(signal_date)
    for offset in range(1, 15):
        candidate = day + timedelta(days=offset)
        state = 'CLOSED_OBSERVED' if candidate.isoformat() in closed else trading_calendar.status(candidate)
        if state in ('OPEN_OBSERVED', 'EXPECTED_OPEN'):
            return candidate.isoformat(), state
        if state in ('CALENDAR_UNVERIFIED', 'CONFLICT'):
            return None, 'NEXT_SESSION_UNKNOWN'
    return None, 'NEXT_SESSION_UNKNOWN'


def authorization(app) -> tuple[bool, str]:
    """OD-V2-2: shown only where the existing production web already serves the same rows to the same audience (its public
    /api/daily-signals route). Anything else fails closed."""
    if app is not None and 'api_daily_signals' in getattr(app, 'view_functions', {}):
        return True, 'EXISTING_PUBLIC_ROUTE:/api/daily-signals'
    return False, 'EXISTING_WEB_AUTHORIZATION_NOT_VERIFIED'


def load(app) -> dict:
    """The live view; any source error is DATA_MISSING with its reason, never an empty success."""
    from core import db_helper
    read_time = datetime.now(timezone.utc).isoformat(timespec='seconds')
    allowed = authorization(app)
    if not allowed[0]:
        return build(None, None, {}, {}, next_session, read_time, allowed)
    try:
        recs = db_helper.get_daily_recommendations(include_heartbeats=True)
        market = db_helper.get_stock_data(date_str=str(recs.trade_date.iloc[0])[:10])[0] if not recs.empty else None
        return build(recs, market, strategy_validation(), cards(), next_session, read_time, allowed)
    except Exception as error:  # database down or files missing: say so
        return {'status': 'DATA_MISSING', 'reason': f'{type(error).__name__}: {str(error)[:200]}', 'rows': [], 'consensus': [],
                'evidence': {'source_table': 'daily_recommendations', 'read_time': read_time, 'labels': LABELS, 'authorization': allowed[1]}}
