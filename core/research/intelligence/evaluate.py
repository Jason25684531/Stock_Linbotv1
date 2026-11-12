"""Seven separate evaluation dimensions and the five-section health check (design D3, D5; spec strategy-evaluation-engine).

No composite score. A verdict uses only the frozen profile's allowed threshold sources; anything else is RESEARCH_THRESHOLD_UNSET.
Target verdicts exist only on the VERIFIED track; PROVISIONAL numbers are shown with INSUFFICIENT_DATA.
"""
from __future__ import annotations

BENCHMARK_KEY = {'max_drawdown': 'benchmark_max_drawdown_total_return'}
BENCHMARK_CONSTANT = {'excess_cagr': 0.0, 'downside_capture': 1.0}  # the benchmark against itself: zero excess, capture of exactly 1
OPS = {'>': lambda a, b: a > b, '<': lambda a, b: a < b, '<=': lambda a, b: a <= b}


def _val(source: dict | None, key: str):
    item = (source or {}).get(key)
    return item.get('value') if isinstance(item, dict) else item


def metric_value(key, summary, trades, liquidity):
    if key.startswith('forward_return') or key in ('mfe_median', 'mae_median'):
        return _val(trades, key)
    return _val(summary, key)


def edge_checks(profile_type: dict, summary, trades, liquidity, verified=True) -> list[dict]:
    out = []
    for check in profile_type['edge_checks']:
        value = metric_value(check['metric'], summary, trades, liquidity)
        if check['value'] == 'benchmark':
            reference = BENCHMARK_CONSTANT[check['metric']] if check['metric'] in BENCHMARK_CONSTANT else _val(summary, BENCHMARK_KEY[check['metric']])
        else:
            reference = check['value']
        if value is None or reference is None or not verified:  # PROVISIONAL numbers are shown, never judged
            status = 'INSUFFICIENT_DATA'
        else:
            status = 'PASS' if OPS[check['op']](value, reference) else 'FAIL'
        out.append(check | {'measured': value, 'reference': reference, 'status': status})
    return out


def target(verified, targets) -> dict:
    cagr = _val(verified, 'cagr')
    if verified is None:
        return {'status': 'INSUFFICIENT_VERIFIED_HISTORY', 'cagr': None}
    if cagr is None:
        return {'status': 'INSUFFICIENT_DATA', 'cagr': None, 'reason': (verified.get('cagr') or {}).get('reason')}
    status = 'RETURN_TARGET_PRIORITY' if cagr >= targets['cagr_priority'] else 'RETURN_TARGET_BASIC' if cagr >= targets['cagr_basic'] else 'RETURN_TARGET_NOT_MET'
    return {'status': status, 'cagr': cagr, 'basic': targets['cagr_basic'], 'priority': targets['cagr_priority']}


def defensive_value(primary_type, verified) -> dict:
    if primary_type != 'defensive' or verified is None:
        return {'status': 'NOT_APPLICABLE'}
    mdd, bmdd, capture = _val(verified, 'max_drawdown'), _val(verified, 'benchmark_max_drawdown_total_return'), _val(verified, 'downside_capture')
    if None in (mdd, bmdd, capture):
        return {'status': 'INSUFFICIENT_DATA'}
    ok = mdd > bmdd and capture < 1
    return {'status': 'DEFENSIVE_VALUE' if ok else 'NO_DEFENSIVE_VALUE', 'max_drawdown': mdd, 'benchmark_max_drawdown': bmdd, 'downside_capture': capture,
            'rule': 'shallower maximum drawdown than the benchmark over the same period and downside capture below 1'}


def thin(summary, prof) -> str | None:
    """A VERIFIED track with fewer closed trades than the frozen sample rule is shown, never judged (review r4)."""
    if not summary:
        return None
    floor = prof['sample_rules']['min_closed_trades_for_trade_statistics']
    closed = summary.get('closed_trades', 0)
    return f'平倉交易 {closed} 筆，少於樣本規則 {floor} 筆，樣本不足' if closed < floor else None


def dimensions(ctx: dict, prof: dict) -> dict:
    """ctx: verified/provisional summaries, trades, liquidity, positions, scenarios, trust, identity_status, primary_type."""
    verified, provisional = ctx.get('verified'), ctx['provisional']
    best = verified or provisional
    sample_short = thin(verified, prof)
    judged = verified if not sample_short else None  # verdicts need a VERIFIED track with enough closed trades
    ptype = prof['types'][ctx['primary_type']]
    min_trades = prof['sample_rules']['min_closed_trades_for_trade_statistics']
    closed = (best or {}).get('closed_trades', 0)
    sample = ctx.get('scenarios_sample', {})
    return {
        'data_trust': {'status': 'VERIFIED_TRACK' if verified else 'PROVISIONAL_ONLY', 'verified_span': ctx.get('verified_span'),
                       'years': ctx.get('year_status'), 'identity': ctx.get('identity_status')},
        'return_target': target(verified, prof['targets']) | {'provisional_cagr': _val(provisional, 'cagr'), 'sample_note': sample_short},
        'cost_tolerance': {'status': ('PASS' if _val(judged, 'cagr_stress') > 0 else 'FAIL') if _val(judged, 'cagr_stress') is not None else 'INSUFFICIENT_DATA',
                           'track': 'VERIFIED' if verified else 'PROVISIONAL', 'cagr_base': _val(best, 'cagr'), 'cagr_stress': _val(best, 'cagr_stress'),
                           'cost_drag': _val(best, 'cost_drag'), 'rule': '2x cost CAGR above 0 (DEFINITIONAL)',
                           'auxiliary_positive_year_share_stress': _val(best, 'positive_year_share_stress')},
        'risk_budget': {'status': prof['risk_budget']['status'], 'source': prof['risk_budget']['source'], 'max_drawdown': _val(best, 'max_drawdown'),
                        'benchmark_max_drawdown': _val(best, 'benchmark_max_drawdown_total_return'), 'longest_recovery_sessions': _val(best, 'longest_recovery_sessions')},
        'type_edge': {'type': ctx['primary_type'], 'label': ptype['label'], 'questions': ptype['questions'], 'track': 'VERIFIED' if verified else 'PROVISIONAL',
                      'checks': edge_checks(ptype, best, ctx.get('trades'), ctx.get('liquidity'), judged is not None), 'sample_note': sample_short,
                      'defensive_value': defensive_value(ctx['primary_type'], verified)},
        'benchmark': {'status': ('ABOVE' if _val(judged, 'excess_cagr') > 0 else 'BELOW') if _val(judged, 'excess_cagr') is not None else 'INSUFFICIENT_DATA',
                      'excess_cagr': _val(best, 'excess_cagr'), 'benchmark_cagr_total_return': _val(best, 'benchmark_cagr_total_return'),
                      'benchmark_cagr_price': _val(best, 'benchmark_cagr_price'), 'label': (best or {}).get('benchmark_label')},
        'activity': {'invested_session_share': _val(best, 'invested_session_share'), 'zero_return_session_share': _val(best, 'zero_return_session_share'),
                     'last_exit_in_track': (best or {}).get('last_exit_in_track'), 'note': sample_short},
        'sample': {'status': 'ADEQUATE' if verified and closed >= min_trades and sample.get('status') == 'SUFFICIENT' else 'INSUFFICIENT',
                   'closed_trades': closed, 'min_closed_trades': min_trades, 'scenario_rule': sample.get('status'), 'complete_years_span': sample.get('span')},
    }


def _item(name, value, status, threshold, source, explanation, detail=None):
    return {'name': name, 'value': value, 'status': status, 'threshold': threshold, 'source': source, 'explanation': explanation, 'detail': detail}


NEUTRAL = {'Sharpe': ('平均日報酬為正', '平均日報酬為負', '只表示算術平均日報酬為正；有波動拖累時總報酬仍可能為負，不代表表現良好。'),
           'Sortino': ('平均日報酬為正', '平均日報酬為負', '只表示算術平均日報酬為正；有波動拖累時總報酬仍可能為負，不代表表現良好。'),
           '兩倍成本 CAGR': ('兩倍成本下年化為正', '兩倍成本下年化為負', '只表示成本加倍後仍未虧損，不代表表現良好。'),
           'Profit Factor': ('每筆等權損益兩平：成立', '每筆等權損益兩平：不成立', '每筆交易報酬率等權計算的損益兩平檢查，不代表表現良好。'),
           '相對 Benchmark 年化超額': ('高於大盤', '低於大盤', ''),
           '最大回撤': ('淺於大盤', '深於大盤', '')}


def _two(value, good, rule, source, name, explanation, verified, missing=None):
    labels = NEUTRAL.get(name)
    if labels and labels[2]:
        explanation += labels[2]
    if value is None:
        return _item(name, None, 'INSUFFICIENT_DATA', rule, source, explanation)
    if missing:
        return _item(name, value, 'INSUFFICIENT_DATA', rule, source, explanation + f'（{missing}，不做判定）')
    if not verified:
        return _item(name, value, 'INSUFFICIENT_DATA', rule, source, explanation + '（僅有 PROVISIONAL 數字或平倉交易少於樣本規則，不做判定）')
    item = _item(name, value, 'GOOD' if good(value) else 'RISK', rule, source, explanation)
    if labels:
        item['neutral'] = labels[0] if good(value) else labels[1]  # shown instead of 良好／風險: these are facts, not quality grades
    return item


def _info(name, value, explanation, reference=None):
    return _item(name, value, 'NOT_APPLICABLE' if value is not None else 'INSUFFICIENT_DATA', 'RESEARCH_THRESHOLD_UNSET', None, explanation, {'reference': reference})


def health(ctx: dict, prof: dict) -> dict:
    """Five sections; every status is text with its threshold and source."""
    verified, provisional = ctx.get('verified'), ctx['provisional']
    s = verified or provisional
    sample_short = thin(verified, prof)
    is_v = verified is not None and not sample_short
    t = prof['targets']
    g = lambda k: _val(s, k)
    cagr = g('cagr')
    if cagr is None or verified is None:
        cagr_item = _item('CAGR（成本後）', cagr, 'INSUFFICIENT_DATA', '5% 基本／10% 優先', 'OWNER_TARGET', '只在完整可信期間判定；目前沒有 VERIFIED 軌或 CAGR 無法計算。')
    else:
        note = f'（{sample_short}；這是報酬事實與目標的比較，不代表策略優勢已驗證）' if sample_short else ''
        cagr_item = _item('CAGR（成本後）', cagr, 'GOOD' if cagr >= t['cagr_priority'] else 'CAUTION' if cagr >= t['cagr_basic'] else 'RISK',
                          '≥10% GOOD，5～10% CAUTION，<5% RISK', 'OWNER_TARGET', 'Owner 的年化淨報酬目標，基礎成本、VERIFIED 期間。' + note)
    bmdd = g('benchmark_max_drawdown_total_return')
    liq = ctx.get('liquidity') or {}
    p90 = liq.get('entry_participation_prev', {}).get('p90')
    pos = ctx.get('positions') or {}
    cap = ctx.get('capacity') or {}
    return {
        'earn': [cagr_item, _info('累積淨報酬', g('total_return'), '期間內扣成本後的總報酬。'),
                 _two(g('excess_cagr'), lambda x: x > 0, '> 0', 'BENCHMARK_SAME_PERIOD', '相對 Benchmark 年化超額', '同期間加權報酬指數（含息）的 CAGR 差。', is_v),
                 _info('最差年度', g('worst_year'), '期間內報酬最低的年度。', g('benchmark_worst_year_total_return'))],
        'drawdown': [_two(g('max_drawdown'), lambda x: bmdd is not None and x > bmdd, f'淺於 Benchmark（{bmdd:.1%}）' if bmdd is not None else '淺於 Benchmark',
                          'BENCHMARK_SAME_PERIOD', '最大回撤', '從前高到谷底的最大跌幅，與同期間大盤比較' +
                          (f'（差距 {(g("max_drawdown") - bmdd) * 100:+.1f} 個百分點）。' if g('max_drawdown') is not None and bmdd is not None else '。'), is_v,
                          'BENCHMARK_MISSING：同期間大盤資料不完整' if bmdd is None and is_v else None),
                     _info('最長回本時間（交易日）', g('longest_recovery_sessions'), '已回復的回撤中，從前高到再創新高的交易日數。'),
                     _info('目前未回本交易日', g('current_underwater_sessions'), '期末仍在水下的交易日數（未回本者不算入最長回本時間）。'),
                     _info('平均回撤', g('average_drawdown'), '每日距前高跌幅的平均。')],
        'efficiency': [_two(g('sharpe'), lambda x: x > 0, '> 0', 'DEFINITIONAL', 'Sharpe', '年化平均日報酬 / 年化波動，無風險利率 0。', is_v),
                       _two(g('sortino'), lambda x: x > 0, '> 0', 'DEFINITIONAL', 'Sortino', '年化平均日報酬 / 年化下檔標準差。', is_v),
                       _info('Calmar', g('calmar'), 'CAGR / |最大回撤|。'),
                       _two(g('cagr_stress'), lambda x: x > 0, '> 0', 'DEFINITIONAL', '兩倍成本 CAGR', '手續費、稅與滑價上限加倍後的年化報酬。', is_v),
                       _info('成本拖累（年化）', g('cost_drag'), '基礎成本 CAGR 減兩倍成本 CAGR。')],
        'consistency': [_info('兩倍成本正報酬年份比例', g('positive_year_share_stress'), '原「50% 年份」規則，僅作輔助觀察。'),
                        _info('勝率', g('win_rate'), '獲利交易筆數 / 已平倉交易筆數。'),
                        _two(g('profit_factor'), lambda x: x > 1, '> 1', 'DEFINITIONAL', 'Profit Factor', '獲利交易報酬率總和 / 虧損交易報酬率總和（每筆等權）。', is_v),
                        _info('盈虧比', g('payoff_ratio'), '平均獲利 / 平均虧損（報酬率）。'),
                        _info('滾動 12 個月正報酬比例', (s or {}).get('rolling_252', {}).get('share_positive', {}).get('value'), '每個 252 交易日視窗的報酬為正的比例。')],
        'tradability': [_item('進場參與率 P90', p90, 'NOT_APPLICABLE' if p90 is not None else 'INSUFFICIENT_DATA', 'ENGINE_ENFORCED（引擎強制 ≤ 5%）', None,
                              '每筆進場金額占前一交易日成交金額的第 90 百分位。引擎本身把每筆成交限制在 5% 以內，所以這一項不做判定。'),
                        _info('進場受 5% 上限限制的比例', liq.get('cap_binding_share'), '進場參與率達上限（≥4.9%）的比例；越高代表越常買不到預定數量。'),
                        _info('持有期間無成交日', liq.get('held_no_trade_sessions'), '持有期間成交量為 0 或非正常交易的日數。', liq.get('held_sessions')),
                        _info('持有期間一價到底日', liq.get('held_one_price_sessions'), '最高等於最低（通常為漲跌停鎖死）的日數。', liq.get('held_sessions')),
                        _item('平均單檔最高權重', _val(pos, 'avg_max_weight'), 'NOT_APPLICABLE' if _val(pos, 'avg_max_weight') is not None else 'INSUFFICIENT_DATA',
                              'RESEARCH_THRESHOLD_UNSET', None, '每日最大持股市值 / NAV 的平均；持倉無法對帳時不可驗證。', {'status': pos.get('status')}),
                        _item('研究用資金容量', cap.get('value'), 'NOT_APPLICABLE' if cap.get('value') is not None else 'INSUFFICIENT_DATA', 'RESEARCH_THRESHOLD_UNSET', None,
                              cap.get('assumption') or '以引擎 5% 成交金額上限與線性放大假設估計；資料不足時不可計算。',
                              {'label': cap.get('label'), 'reason': cap.get('reason')})],
    }
