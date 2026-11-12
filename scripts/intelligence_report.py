"""STRATEGY_INTELLIGENCE_FINAL_ACCEPTANCE_REPORT.md, per strategy, from stored results and acceptance evidence only.

Formatting only: every number is read from outputs/strategy_intelligence (LATEST.json, results, acceptance files). Missing evidence is
written as its own status, never assumed. Run from the repo root: python -m scripts.intelligence_report
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from scripts import strategy_intelligence as si  # noqa: E402

OUT = si.OUT
ACC = OUT / 'acceptance'
NOTES = ROOT / 'core/research/intelligence/data/implementation_notes.json'
REGIMES = ('BULL', 'BEAR', 'SIDEWAYS', 'HIGH_VOL', 'LOW_VOL')
REGIME_ZH = {'BULL': '多頭', 'BEAR': '空頭', 'SIDEWAYS': '盤整', 'HIGH_VOL': '高波動', 'LOW_VOL': '低波動'}
TARGET_ZH = {'RETURN_TARGET_PRIORITY': '達 10% 優先目標', 'RETURN_TARGET_BASIC': '達 5% 基本目標，未達 10%', 'RETURN_TARGET_NOT_MET': '未達 5% 目標',
             'INSUFFICIENT_VERIFIED_HISTORY': '不判定：沒有可信（VERIFIED）期間', 'INSUFFICIENT_DATA': '不判定：資料不足'}


def p(m, signed=False):
    value = m.get('value') if isinstance(m, dict) else m
    return '—' if value is None else (f'{value:+.1%}' if signed else f'{value:.1%}')


def n(m, digits=2):
    value = m.get('value') if isinstance(m, dict) else m
    return '—' if value is None else f'{value:,.{digits}f}'


def why(m):
    return f"（{m.get('reason')}）" if isinstance(m, dict) and m.get('value') is None and m.get('reason') else ''


def read(path):
    return si.read_json(path) if Path(path).suffix == '.json' else (Path(path).read_text(encoding='utf-8') if Path(path).is_file() else None)


def regime_lines(perf, note=None) -> list[str]:
    regimes = perf['regime']['regimes']
    lines = ['| 環境 | 僅 VERIFIED 年份：日數／年化／MDD | 含 PARTIAL（PROVISIONAL）：日數／年化／MDD | 兩倍成本年化（PROVISIONAL） |', '|---|---|---|---|']
    for k in REGIMES:
        v, a = regimes.get('VERIFIED_ONLY', {}).get(k), regimes.get('VERIFIED_AND_PARTIAL', {}).get(k)
        cell = lambda g: '—' if not g or not g['sessions'] else f"{g['sessions']}／{p(g['base']['annualized_return'])}{why(g['base']['annualized_return'])}／{p(g['base']['max_drawdown'])}"
        lines.append(f"| {REGIME_ZH[k]} {k} | {cell(v)} | {cell(a)} | {p(a['x2']['annualized_return']) if a and a['sessions'] else '—'} |")
    basis = 'VERIFIED_ONLY' if any(g['sessions'] for g in regimes.get('VERIFIED_ONLY', {}).values()) else 'VERIFIED_AND_PARTIAL'
    usable = {k: g['base']['annualized_return']['value'] for k, g in regimes.get(basis, {}).items() if g['sessions'] and g['base']['annualized_return']['value'] is not None}
    trades = {k: g['trades']['trade_count'] for k, g in regimes.get(basis, {}).items()}
    idle = [REGIME_ZH[k] for k in usable if not trades.get(k)]
    win = [REGIME_ZH[k] for k, x in usable.items() if x > 0 and trades.get(k)]
    lose = [REGIME_ZH[k] for k, x in usable.items() if x < 0 and trades.get(k)]
    label = '僅 VERIFIED 年份' if basis == 'VERIFIED_ONLY' else '含 PARTIAL 年份，PROVISIONAL'
    lines.append('')
    if note:
        lines.append(f'- {note}：環境表只是期間報酬的拆分，不據此判斷策略在哪種環境有優勢。')
        return lines
    lines.append(f"- 依{label}：年化為正的環境 {('、'.join(win) or '無')}；年化為負的環境 {('、'.join(lose) or '無')}；該環境沒有平倉交易（無證據）{('、'.join(idle) or '無')}。"
                 f"日數少於 60 的環境年化值為不可計算（INSUFFICIENT_SAMPLE）。環境規則 {perf['regime']['regime_spec_id']}，事先固定，不因結果調整。")
    return lines


def scenario_lines(r) -> list[str]:
    sc = r.get('scenarios')
    if not sc:
        return ['- 沒有歷史 Run，無法建立情境。']
    if sc['status'] == 'SUFFICIENT':
        sim, st = sc['simulation'], sc['stress']
        ww, bw = st.get('worst_window_max_drawdown', {}), st.get('benchmark_worst_252', {})
        under = '／'.join(p(v) for v in sim['underwater']['share_underwater_at'].values())
        return [f"- 樣本：VERIFIED 完整交易年度 {sc['check']['span'][0]}～{sc['check']['span'][1]}（{sim['sample_sessions']} 日），區塊 {sim['block']} 日，{sim['paths']} 條路徑 × {sim['horizon']} 日，種子 {sim['seed']}，規則 {sc['check']['rule']['id']} v{sc['check']['rule']['version']}。",
                f"- 一年內最大回撤：中位數 {p(sim['drawdown']['median'])}、較差 5% {p(sim['drawdown']['p05'])}；歷史任一 252 日最深回撤 {p(ww.get('value'))}（{ww.get('start')}～{ww.get('end')}）。",
                f"- 回不到高點：一年後仍在水下的路徑 {p(sim['underwater']['censored_share'])}（CENSORED，水下日數中位數至少 {n(sim['underwater']['censored_longest_median_lower_bound'], 0)} 日）；第 3／6／9／12 個月仍在水下 {under}。",
                f"- 運氣檢驗：重抽樣 CAGR 中位數 {p(sim['cagr'].get('median'))}，95% 區間 {p(sim['cagr'].get('interval_95', [None, None])[0])}～{p(sim['cagr'].get('interval_95', [None, None])[1])}；低於 0 的比例 {p(sim['cagr'].get('share_below_zero'))}，低於 5% 的比例 {p(sim['cagr'].get('share_below_5pct'))}。",
                f"- 壓力情境：大盤最差 252 日（{bw.get('start')}～{bw.get('end')}，大盤 {p(bw.get('benchmark_return'))}）期間策略 {p(bw.get('strategy_return'))}。",
                f"- {sc.get('notice') or sim['notice']}"]
    years = '、'.join(f"{y['year']} {y['trust']}{'（完整）' if y['complete'] else ''}" for y in sc['check']['years'])
    worst = sorted(sc['historical']['drawdown_episodes'], key=lambda e: e['depth'])[:3]
    events = '；'.join(f"{e['peak_date']}→{e['trough_date']} {p(e['depth'])}（{'回復於 ' + e['recovery_date'] if e['recovery_date'] else '未回復'}）" for e in worst) or '無'
    reason = ('Run 身份與目前程式不一致' if sc['status'] == 'IDENTITY_STALE' else
              f"完整可信年度內只有 {sc['check'].get('closed_trades_in_span')} 筆平倉交易，少於樣本規則 {sc['check'].get('min_closed_trades')} 筆，交易樣本不足以代表策略" if sc['status'] == 'INSUFFICIENT_SAMPLE' else
              f"VERIFIED 軌沒有三個相連的完整可信交易年度（{sc['check']['rule']['id']}）")
    return [f"- **{sc['status']}**：{reason}，不輸出模擬機率。逐年：{years}。",
            f"- 改列真實歷史風險事件（{sc['historical']['track']}）最深三次：{events}。"]


def feasibility_lines(r, folio) -> list[str]:
    f = r['feasibility']
    rec, liq, cap, pos = f['reconciliation'], f['liquidity'], f['capacity'], f['positions']
    entry = liq.get('entry_participation_prev', {})
    lines = [f"- 持倉重建：**{rec['status']}**" + (f"，第一個不一致日 {rec.get('first_mismatch_date')}，{rec.get('mismatch_sessions')}／{rec['sessions']} 日不一致，原因 {'、'.join(rec['reasons'])}；持股集中度、持股重疊與資金容量為不可驗證。"
                                                     if rec['status'] != 'RECONCILED' else f"，逐日對帳 NAV（{rec['tolerance']}）；平均持股 {n(pos['avg_holdings'])} 檔、平均單檔最高權重 {p(pos['avg_max_weight'])}、平均現金比例 {p(pos['avg_cash_share'])}。"),
             f"- 流動性：進場金額占前一日成交金額中位數 {p(entry.get('p50'))}、P90 {p(entry.get('p90'))}、受 5% 上限限制 {p(liq.get('cap_binding_share'))}；持有期間無成交 {liq.get('held_no_trade_sessions')} 日、一價到底 {liq.get('held_one_price_sessions')} 日（共 {liq.get('held_sessions')} 個持有日）。",
             f"- 研究用資金容量：**{cap['label']}** {n(cap['value'], 0)} 元。{cap['assumption']}",
             f"- 成本：手續費 {f['cost_model']['fee_rate']}、證交稅 {f['cost_model']['tax_rate']}、滑價上限 {f['cost_model'].get('slippage_max')}（含在成交價內）；年化換手 {n(r['performance'].get('turnover_annual'))}；全期手續費與稅 {n((r['performance'].get('cost_summary') or {}).get('fees_and_tax_paid'), 0)} 元。"]
    sid = r['strategy_id']
    if folio and sid in folio.get('eligible', []) and folio.get('status') == 'OK':
        corr = {b: c for b, c in folio['correlation'][sid].items() if b != sid and c is not None}
        lo, hi = min(corr, key=corr.get), max(corr, key=corr.get)
        lines.append(f"- 組合：納入比較（共同 VERIFIED {folio['common_sessions']} 日）。相關最低 {lo} {corr[lo]:.2f}、最高 {hi} {corr[hi]:.2f}；"
                     f"與 {lo} 同時重大損失 {p(folio['joint_tail_loss'][sid][lo])}（獨立參考 {p(folio['joint_tail_independent_reference'])}）。")
    else:
        lines.append(f"- 組合：未納入比較，原因 {(folio or {}).get('excluded', {}).get(sid) or (folio or {}).get('status') or '無組合結果'}。")
    return lines


def strategy_section(i, r, notes, folio) -> list[str]:
    sid = r['strategy_id']
    lines = [f"### {i}. {r['display_name']}（`{sid}`）", '']
    note = notes.get('strategies', {}).get(sid, [])
    impl = [f"- {x['id']}：{x['text']}（{'、'.join(x['refs'])}）" for x in note] or ['- 撰寫規格卡時沒有發現程式與說明不一致。']
    impl += [f"- 引擎共通 {x['id']}：{x['text']}" for x in notes.get('engine', [])]
    card = r['definition']['fields']
    traced = sum(f['status'] == 'TRACED' for f in card.values())
    head = [f"- 類型：{r['profile']['type']['label']}；狀態：**{r['status']}**；規格卡 {traced}／{len(card)} 欄已追溯到程式（Definition `{r['definition']['definition_id']}`）。"]
    if not r.get('run'):
        return lines + head + [f"- 資料可信：無可用 Run；研究評估：不評估；正式認證：{r['certification']}。",
                               f"- 原因：{r.get('reason') or r['status']}。所以沒有報酬、回撤、情境、可行性與組合數字。", '', '**實作與說明差異**', ''] + impl + ['']
    run, perf, d = r['run'], r['performance'], r['dimensions']
    v, prov = perf.get('verified'), perf['provisional']
    s = v or prov
    track = 'VERIFIED' if v else 'PROVISIONAL'
    years = '、'.join(f'{y} {t}' for y, t in r['year_status'].items())
    lines += head + [
        f"- 三種狀態分開：資料可信 **{d['data_trust']['status']}**（可信期間 {'～'.join(map(str, perf['verified_span'])) if perf['verified_span'] else '無'}）；研究評估 **{d['return_target']['status']}**；正式認證 **{r['certification']}**。",
        f"- 出處：Run `{run['run_id']}`（{run['identity_status']}，`{run['run_identity']}`）；Snapshot `{run['snapshot_id']}`；Evaluation `{r['versions']['evaluation_id']}`；Engine {run['engine']['contract']}、帳務 {run['accounting']}、分錄 `{(run['ledger_version'] or '')[:16]}`；Regime `{r['versions']['regime_spec_id']}`；Profile `{r['versions']['profile_id']}`；分析結果 `{r['identity']}`。",
        f"- 逐年資料可信度（含 Run 層級降級）：{years}。",
        f"- 活動度：投入交易日 {p(d['activity']['invested_session_share'])}、零報酬交易日 {p(d['activity']['zero_return_session_share'])}、期間內最後一次出場 {d['activity']['last_exit_in_track'] or '無'}" +
        (f"；**{d['activity']['note']}：成本承受、相對大盤、優勢檢查與體檢不做判定，不估資金容量，不輸出情境機率；CAGR 仍與 5%／10% 目標比較（報酬事實）**。" if d['activity'].get('note') else '。'), '',
        '**1. 年化淨報酬目標**', '',
        f"- 判定：**{TARGET_ZH.get(d['return_target']['status'], d['return_target']['status'])}**{'（' + d['return_target']['sample_note'] + '）' if d['return_target'].get('sample_note') else ''}。VERIFIED 期間 CAGR {p(v['cagr']) if v else '—（無 VERIFIED 期間）'}；完整 Run（PROVISIONAL）CAGR {p(prov['cagr'])}（{prov['period'][0]}～{prov['period'][1]}，日曆時間 {p(prov['cagr_calendar'])}）。",
        f"- 兩倍成本 CAGR {p(s['cagr_stress'])}（成本承受 {d['cost_tolerance']['status']}）；同期加權報酬指數 CAGR {p(s['benchmark_cagr_total_return'])}、價格指數 {p(s['benchmark_cagr_price'])}；超額 {p(s['excess_cagr'], True)}（{d['benchmark']['status']}）。以 {track} 期間計。", '',
        f'**2. 最大回撤與回本時間（{track}）**', '',
        f"- 最大回撤 {p(s['max_drawdown'])}（同期大盤 {p(s['benchmark_max_drawdown_total_return'])}）；最長回本 {n(s['longest_recovery_sessions'], 0)} 交易日{why(s['longest_recovery_sessions'])}；期末仍未回本 {n(s['current_underwater_sessions'], 0)} 交易日；最差年度 {p(s['worst_year'])}（大盤同期最差 {p(s['benchmark_worst_year_total_return'])}）。風險預算：{d['risk_budget']['status']}（Owner OD-1：不設固定回撤門檻）。",
        '- 最深三次回撤：' + ('；'.join(f"{e['peak_date']}→{e['trough_date']} {p(e['depth'])}（{'回復於 ' + e['recovery_date'] if e['recovery_date'] else '未回復'}）" for e in s['drawdown_episodes'][:3]) or '無'), '',
        '**3. 市場環境**', ''] + regime_lines(perf, d['activity'].get('note')) + ['', '**4. 未來一年歷史情境**', ''] + scenario_lines(r) + ['', '**5. 交易可行性與組合分散**', ''] + feasibility_lines(r, folio) + [
        '', f"**6. {d['type_edge']['label']}專屬優勢（{d['type_edge']['track']}）**", '',
        '- ' + '；'.join(f"{c['metric']} {n(c['measured'], 4)} {c['op']} {c['reference']}（{c['source']}）→ {c['status']}" for c in d['type_edge']['checks']),
        f"- 樣本：平倉 {d['sample']['closed_trades']} 筆（最低 {d['sample']['min_closed_trades']}）→ {d['sample']['status']}。" +
        (f" 防禦價值：{d['type_edge']['defensive_value']['status']}。" if d['type_edge']['defensive_value']['status'] != 'NOT_APPLICABLE' else ''), '',
        '**7. 實作與說明差異**', ''] + impl + ['']
    return lines


def evidence_status() -> dict:
    p0 = read(ACC / 'p0_verification.json') or {}
    gov = read(ACC / 'governance.json') or {}
    pytest_text = read(ACC / 'pytest_final.txt') or ''
    summary = re.findall(r'=+ (.*?) in [\d.]+s', pytest_text)
    web = read(ACC / 'playwright.json') or {}
    review = read(OUT / 'review' / 'final_verdict.json') or {}
    return {'p0': p0, 'governance': gov, 'pytest': summary[-1] if summary else '未執行', 'web': web, 'review': review}


def build() -> Path:
    latest = si.read_json(OUT / 'LATEST.json')
    if not latest:
        raise FileNotFoundError('LATEST.json: no finished intelligence job')
    problems = si.latest_problems(latest)
    results = {sid: si.read_json(si.RESULTS / ident / f"{sid.replace(':', '__')}.json") for sid, ident in latest['strategies'].items()}
    folio = si.read_json(si.RESULTS / latest['portfolio'] / 'PORTFOLIO.portfolio.json') if latest.get('portfolio') else None
    notes = si.read_json(NOTES) or {}
    ev = evidence_status()
    order = si.catalog_ids()
    lines = ['# STRATEGY_INTELLIGENCE_FINAL_ACCEPTANCE_REPORT', '',
             f"> 變更 `2026-11-11-strategy-intelligence-platform-v1`。REFERENCE_ONLY · RESEARCH_ONLY · NOT_CERTIFIED · NOT_A_TRIAL · 已看過的歷史結果，不是樣本外證據。",
             f"> 由 `python -m scripts.intelligence_report` 從結果檔產生：分析工作 `{latest['job_id']}`（{latest['status']}，{latest['finished_at']}），評估設定 `{latest['profile_id']}`，Snapshot `{latest['snapshot_id']}`。"
             + ('' if not problems else f" **警告：結果與目前程式不一致 {problems[:3]}**"), '',
             '## 總覽', '',
             '| 策略 | 類型 | 狀態 | Run 身份 | 可信期間 | CAGR（軌） | 5%／10% | MDD | 情境 | 持倉對帳 | 組合 |', '|---|---|---|---|---|---|---|---|---|---|---|']
    for sid in order:
        r = results.get(sid)
        if not r:
            lines.append(f"| {sid} | — | 分析失敗：{latest.get('failed', {}).get(sid)} | — | — | — | — | — | — | — | — |")
            continue
        if not r.get('run'):
            lines.append(f"| {sid} | {r['profile']['type']['label']} | {r['status']} | — | — | — | 不評估 | — | — | — | 未納入 |")
            continue
        perf = r['performance']
        s = perf.get('verified') or perf['provisional']
        lines.append(f"| {sid} | {r['profile']['type']['label']} | {r['status']} | {r['run']['identity_status']} | {'～'.join(map(str, perf['verified_span'])) if perf['verified_span'] else '無'} | "
                     f"{p(s['cagr'])}（{'V' if perf.get('verified') else 'P'}） | {r['dimensions']['return_target']['status']} | {p(s['max_drawdown'])} | {r['scenarios']['status']} | "
                     f"{r['feasibility']['reconciliation']['status']} | {'納入' if folio and sid in folio.get('eligible', []) else '未納入'} |")
    lines += ['', '（V＝VERIFIED 期間，P＝PROVISIONAL 全期。5%／10% 目標只在 VERIFIED 期間判定。）', '', '## 逐策略結果', '']
    for i, sid in enumerate(order, 1):
        if results.get(sid):
            lines += strategy_section(i, results[sid], notes, folio)
        else:
            lines += [f"### {i}. `{sid}`", '', f"- 最近一次分析失敗：{latest.get('failed', {}).get(sid)}；失敗分類與錯誤見 `outputs/strategy_intelligence/jobs/{latest['job_id']}.json`。", '']
    lines += ['## 策略組合', '']
    if folio:
        lines.append(f"- 狀態 {folio['status']}；納入 {len(folio['eligible'])} 個：{'、'.join(folio['eligible']) or '無'}；共同 VERIFIED 期間 {'～'.join(folio.get('common_period') or []) or '—'}（{folio['common_sessions']} 日）。")
        lines.append('- 未納入：' + '；'.join(f'{k} {v}' for k, v in folio['excluded'].items()))
        if folio['status'] == 'OK':
            demo = folio['equal_weight_demo']
            lines.append(f"- 等權示範（RESEARCH_DEMO_NOT_A_STRATEGY）：CAGR {p(demo['cagr'])}、MDD {p(demo['max_drawdown'])}（成員平均 {p(demo['average_member_max_drawdown'])}）、波動 {p(demo['volatility'])}（成員平均 {p(demo['average_member_volatility'])}）、分散比 {n(demo['diversification_ratio'])}。分散比大於 1 表示合併後波動小於成員平均。")
            lines.append(f"- 持股重疊：{'、'.join(f'{k} {v}' for k, v in folio['holding_overlap_reason'].items() if v) or '全部可計算'}；產業集中度 {folio['industry_concentration']['reason']}。")
    lines += ['', '## 驗收證據', '',
              f"- P0 重算：{json.dumps(ev['p0'].get('summary'), ensure_ascii=False) if ev['p0'] else '未執行'}；與前一輪數值完全相同 {len(ev['p0'].get('numerically_identical_to_previous', [])) if ev['p0'] else '—'} 個；舊 Run 檔案變動 {len(ev['p0'].get('old_run_files_changed', [])) if ev['p0'] else '—'}；晚於 2025-12-31 的新 Run {ev['p0'].get('new_runs_after_2025') if ev['p0'] else '—'}。",
              f"- 全量 pytest（最後一次修改後）：{ev['pytest']}（`acceptance/pytest_final.txt`）。",
              f"- 治理對帳：{ev['governance'].get('status', '未執行')}，差異 {ev['governance'].get('differences', '—')}（Ledger、Registry、effective_trials() 770、H4 91 檔、Run 身份檔、舊 Run 檔案）。",
              f"- Playwright：{ev['web'].get('summary', '未執行')}（`{ev['web'].get('run_dir', '—')}`）。",
              f"- 獨立審查：{ev['review'].get('summary', '未執行')}。Codex CLI：BLOCKED_WITH_EVIDENCE（workspace 額度用盡，`review/design_r1.log`）。", '',
              '## 限制與狀態', '', '每一項都有明確狀態；細節見 `DEFERRED_FINDINGS.md`。', '',
              '| 項目 | 狀態 | 說明 |', '|---|---|---|',
              '| 2024～2025 權息並存事件拆分 | PARTIAL（外部阻擋） | 以 ASSUMED_STOCK 入帳的年份降為 PARTIAL，不進入 VERIFIED 判定 |',
              '| 部分出場成交未存檔（E2） | POSITIONS_UNVERIFIABLE | 受影響策略的集中度、持股重疊、資金容量不可驗證 |',
              '| 每日持倉未存檔（E1） | DEFERRED | 需修改 Run 身份檔，另開變更 |',
              '| 前瞻觀察 | GOVERNANCE_BLOCKED | Owner OD-2：不解除 2026 研究資料限制 |',
              '| 風險預算門檻 | RESEARCH_THRESHOLD_UNSET | Owner OD-1：不設固定回撤門檻 |',
              '| 產業集中度 | NOT_AVAILABLE | 快照沒有產業分類 |',
              '| 每檔 1 張、20% 預算：股價約 200 元以上買不到（E9） | DEFERRED（引擎屬 Run 身份） | 所有引擎策略都買不到高價股；macd_cross 只選 2330，2017-05 後無成交 |',
              '| 平倉交易少於 30 筆 | INSUFFICIENT_SAMPLE | 成本承受、相對大盤、優勢檢查、體檢與資金容量不判定，不輸出情境機率；CAGR 仍與目標比較並加註 |',
              '| Sharpe、Sortino > 0 | 事實標籤（平均日報酬為正／為負） | 只表示算術平均日報酬為正，有波動拖累時總報酬仍可能為負；不代表表現良好 |',
              '| Profit Factor > 1、期望值 > 0 | 事實標籤（每筆等權損益兩平：成立／不成立） | 每筆報酬率等權計算，不代表表現良好 |',
              '| 引擎 BULL 進場條件用 2330 而非加權指數（E4） | DEFERRED（引擎屬 Run 身份） | 規格卡依程式描述；跨環境分析另用加權指數的版本化規則 |',
              '| 持倉重建在 2 個策略有部分出場以外的不一致（E3） | POSITIONS_UNVERIFIABLE | 兩者已因部分出場不可驗證 |',
              '| metrics.py 的 CAGR 無防護、持有天數與換手欄位（E5） | 已在本平台繞開 | metrics.py 屬 Run 身份，不修改 |',
              '| 評估設定凍結紀錄沒有雜湊鏈（E8） | DEFERRED | 只追加，兩個版本與原因已記錄 |',
              '| 策略程式與說明不一致（I01～I20、E9、E10） | DEFERRED（範圍凍結） | 唯一來源 implementation_notes.json；Web 規格卡分頁逐策略顯示 |',
              '| Codex CLI 獨立審查 | BLOCKED_WITH_EVIDENCE | 以獨立審查子代理代替 |', '']
    path = OUT / 'STRATEGY_INTELLIGENCE_FINAL_ACCEPTANCE_REPORT.md'
    path.write_text('\n'.join(lines) + '\n', encoding='utf-8')
    return path


def _p(m, signed=False):  # REPORT_TABLES.md formatters (moved from strategy_intelligence so formatting never changes a result identity)
    value = m.get('value') if isinstance(m, dict) else m
    return '—' if value is None else (f'{value:+.1%}' if signed else f'{value:.1%}')


def _n(m, digits=2):
    value = m.get('value') if isinstance(m, dict) else m
    return '—' if value is None else f'{value:.{digits}f}'


def tables() -> Path:
    """REPORT_TABLES.md from the latest stored results only (every number traceable to a result file)."""
    latest = si.read_json(OUT / 'LATEST.json')
    if not latest:
        raise FileNotFoundError('LATEST.json: no finished intelligence job')
    if si.latest_problems(latest):
        raise ValueError(f'LATEST_NOT_CURRENT: {si.latest_problems(latest)[:3]}; rerun the job after the last code change')
    rows = {sid: si.read_json(si.RESULTS / ident / f"{sid.replace(':', '__')}.json") for sid, ident in latest['strategies'].items()}
    lines = [f"# REPORT_TABLES（job {latest['job_id']}，{latest['status']}，profile {latest['profile_id']}，snapshot {latest['snapshot_id']}）", '',
             '> 由 `python -m scripts.intelligence_report` 從 `outputs/strategy_intelligence/LATEST.json` 指向的結果檔產生。REFERENCE_ONLY · NOT_CERTIFIED · NOT_A_TRIAL。', '',
             '## A. 規格卡與狀態', '', '| 策略 | 類型 | 狀態 | Run 身份 | 卡片欄位 TRACED／STALE／UNVERIFIED | Definition | 結果身份 |', '|---|---|---|---|---|---|---|']
    for sid, r in rows.items():
        fields = [f['status'] for f in r['definition']['fields'].values()]
        lines.append(f"| {sid} | {r['profile']['type']['label']} | {r['status']} | {(r.get('run') or {}).get('identity_status', '—')} | "
                     f"{fields.count('TRACED')}／{fields.count('STALE_DEFINITION')}／{fields.count('UNVERIFIED_DEFINITION')} | {r['definition']['definition_id']} | {r['identity']} |")
    lines += ['', '## B. 績效與風險（成本後；VERIFIED 期間優先，否則 PROVISIONAL）', '',
              '| 策略 | 軌 | 期間 | CAGR | 兩倍成本 CAGR | 大盤 CAGR（含息） | 超額 | MDD | 大盤 MDD | 最差年度 | 最長回本（日） | Sharpe | PF | 5%／10% 目標 | 成本承受 | 類型優勢檢查 |',
              '|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|']
    for sid, r in rows.items():
        perf = r.get('performance')
        if not perf:
            lines.append(f"| {sid} | — | — | {r['status']}：{r.get('reason')} |||||||||||||")
            continue
        s, track = (perf['verified'], 'VERIFIED') if perf.get('verified') else (perf['provisional'], 'PROVISIONAL')
        d = r['dimensions']
        checks = ' '.join(f"{c['metric']}:{c['status']}" for c in d['type_edge']['checks'])
        lines.append(f"| {sid} | {track} | {'～'.join(s['period'])} | {_p(s['cagr'])} | {_p(s['cagr_stress'])} | {_p(s['benchmark_cagr_total_return'])} | "
                     f"{_p(s['excess_cagr'], True)} | {_p(s['max_drawdown'])} | {_p(s['benchmark_max_drawdown_total_return'])} | {_p(s['worst_year'])} | "
                     f"{_n(s['longest_recovery_sessions'], 0)} | {_n(s['sharpe'])} | {_n(s['profit_factor'])} | {d['return_target']['status']} | {d['cost_tolerance']['status']} | {checks} |")
    lines += ['', '### 市場環境（僅 VERIFIED 年份；年化報酬，基礎成本）', '', '| 策略 | BULL | BEAR | SIDEWAYS | HIGH_VOL | LOW_VOL |', '|---|---|---|---|---|---|']
    for sid, r in rows.items():
        reg = ((r.get('performance') or {}).get('regime') or {}).get('regimes', {}).get('VERIFIED_ONLY')
        if reg:
            lines.append(f"| {sid} | " + ' | '.join(f"{_p(reg[k]['base']['annualized_return'])}（{reg[k]['sessions']} 日）" for k in ('BULL', 'BEAR', 'SIDEWAYS', 'HIGH_VOL', 'LOW_VOL')) + ' |')
    lines += ['', '## C. 未來一年風險情境', '', '| 策略 | 狀態 | 樣本期間 | 一年 MDD 中位數 | 較差 5% | 一年後仍在水下 | 重抽樣 CAGR 95% 區間 | 低於 0 | 低於 5% |', '|---|---|---|---|---|---|---|---|---|']
    for sid, r in rows.items():
        sc = r.get('scenarios')
        if not sc:
            continue
        if sc['status'] == 'SUFFICIENT':
            sim = sc['simulation']
            lines.append(f"| {sid} | SUFFICIENT | {'～'.join(sc['period'])} | {_p(sim['drawdown']['median'])} | {_p(sim['drawdown']['p05'])} | {_p(sim['underwater']['censored_share'])} | "
                         f"{_p(sim['cagr']['interval_95'][0])}～{_p(sim['cagr']['interval_95'][1])} | {_p(sim['cagr']['share_below_zero'])} | {_p(sim['cagr']['share_below_5pct'])} |")
        else:
            complete = [y['year'] for y in sc['check']['years'] if y['complete']]
            lines.append(f"| {sid} | {sc['status']} | 完整可信年度：{complete or '無'} | — | — | — | — | — | — |")
    lines += ['', '## D. 交易可行性', '', '| 策略 | 持倉對帳 | 第一個不一致日 | 原因 | 進場參與率 P90 | 資金容量（元） | 容量標記 |', '|---|---|---|---|---|---|---|']
    for sid, r in rows.items():
        f = r.get('feasibility')
        if not f:
            continue
        rec, cap = f['reconciliation'], f['capacity']
        capacity = '—' if cap['value'] is None else format(cap['value'], ',.0f')
        lines.append(f"| {sid} | {rec['status']} | {rec.get('first_mismatch_date') or '—'} | {'、'.join(rec['reasons']) or '—'} | "
                     f"{_p(f['liquidity'].get('entry_participation_prev', {}).get('p90'))} | {capacity} | {cap['label']} |")
    folio = si.read_json(si.RESULTS / latest['portfolio'] / 'PORTFOLIO.portfolio.json') if latest.get('portfolio') else None
    lines += ['', '## E. 策略組合', '']
    if folio:
        lines.append(f"狀態 {folio['status']}；納入 {len(folio['eligible'])} 個；共同 VERIFIED 期間 {'～'.join(folio.get('common_period') or [])}（{folio['common_sessions']} 日）。")
        lines.append('未納入：' + '；'.join(f'{k} {v}' for k, v in folio['excluded'].items()))
        if folio['status'] == 'OK':
            demo = folio['equal_weight_demo']
            lines.append(f"等權示範（RESEARCH_DEMO_NOT_A_STRATEGY）：CAGR {_p(demo['cagr'])}、MDD {_p(demo['max_drawdown'])}（成員平均 {_p(demo['average_member_max_drawdown'])}）、"
                         f"波動 {_p(demo['volatility'])}（成員平均 {_p(demo['average_member_volatility'])}）、分散比 {_n(demo['diversification_ratio'])}。")
            ids = folio['eligible']
            pairs = sorted(((folio['correlation'][a][b], a, b) for i, a in enumerate(ids) for b in ids[i + 1:] if folio['correlation'][a][b] is not None), reverse=True)
            lines += ['', '| 配對 | 相關係數 | 同時回撤 | 同時重大損失 |', '|---|---|---|---|']
            lines += [f"| {a} × {b} | {c:.2f} | {_p(folio['joint_drawdown_share'][a][b])} | {_p(folio['joint_tail_loss'][a][b])} |" for c, a, b in pairs]
    path = OUT / 'REPORT_TABLES.md'
    path.write_text('\n'.join(lines) + '\n', encoding='utf-8')
    return path


if __name__ == '__main__':
    print(tables())
    print(build())
