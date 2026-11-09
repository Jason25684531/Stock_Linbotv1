"""Offline HTML report of round 2026-11-06-msv (design D8): one self-contained file, Plotly embedded from the local package."""
import csv
import html
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from core.backtest import run_store
from core.strategy_manager import StrategyManager

DOCS = ROOT / 'docs/research/2026-11-06-multi-strategy-validation-and-composite-v1'
LAB = ROOT / 'outputs/research_mode/combination_lab_v1'
LEGACY = ROOT / 'outputs/research_mode/legacy_validation_v1/summary.json'
E = html.escape

FEATURES = [  # (feature, status, evidence) - the five states of design D14
    ('V34／V35 基準', 'IMPLEMENTED_BUT_DATA_BLOCKED', '月營收沒有公告時間，未回測'),
    ('Run Store（不可覆寫、三種指標、SAVE_FAILED）', 'IMPLEMENTED_AND_VERIFIED', 'test_backtest_run_store.py'),
    ('Web 策略名稱（無版本號，ID/LINE 相容）', 'IMPLEMENTED_AND_VERIFIED', 'test_strategy_display_names.py'),
    ('回測頁最後成功／基準／歷史、run 檢視與同群組比較', 'IMPLEMENTED_AND_VERIFIED', 'test_platform_routes.py'),
    ('Factor Combination Lab（條件池、組合清單、凍結佇列、23 次上限）', 'IMPLEMENTED_AND_VERIFIED', 'test_combination_lab.py；queue.json SHA256'),
    ('熱力圖、avg_score、Top 3 深度分析', 'IMPLEMENTED_AND_VERIFIED', 'test_trade_analytics.py；lab_summary.json'),
    ('CompositeExecutor（交集與加權）', 'IMPLEMENTED_AND_VERIFIED', 'test_composite_strategy.py'),
    ('條件 c5 獲利品質', 'IMPLEMENTED_BUT_DATA_BLOCKED', 'BLOCKED_PIT：沒有已驗證公告日的營業利益率'),
    ('條件 c6 估值、c4m 月營收', 'IMPLEMENTED_BUT_DATA_BLOCKED', '本機沒有 PIT 估值來源；月營收沒有公告時間'),
    ('含息 Benchmark（發行量加權股價報酬指數）', 'IMPLEMENTED_AND_VERIFIED', '本機官方 MI_INDEX 2023-2025 共 724/724 日；MFI94U 本身未下載，需 Owner 授權'),
    ('市場資料更新服務（鎖、冪等、重試、檢查）', 'IMPLEMENTED_AND_VERIFIED', 'test_market_data_update.py（假來源）'),
    ('實際網路更新', 'IMPLEMENTED_BUT_AUTH_BLOCKED', '本 session 不執行對外抓取；使用者可在資料管理頁自行觸發'),
    ('每日自動排程', 'IMPLEMENTED_BUT_AUTH_BLOCKED', 'SCHEDULER_SETUP_REQUIRED；scheduler_setup.md'),
    ('資料管理頁（管理員、CSRF、限速、Session）', 'IMPLEMENTED_AND_VERIFIED', 'test_platform_routes.py；僅限本機或內網，未具 HTTPS'),
    ('最新研究選股', 'IMPLEMENTED_BUT_AUTH_BLOCKED', 'LATEST_SIGNAL_DISABLED：需 Owner 前瞻授權，2026 研究不可讀'),
    ('Notebook 清冊（41 份，含使用者 19 份）', 'IMPLEMENTED_AND_VERIFIED', 'test_notebook_inventory.py；notebook_inventory.csv'),
    ('Notebook 金鑰改寫（2 份）', 'IMPLEMENTED_AND_VERIFIED', '已備份並改為環境變數；撤銷與重發由 Owner 在 TEJ 帳戶操作'),
    ('2026 封存隔離', 'IMPLEMENTED_AND_VERIFIED', 'test_sealed_window_isolation.py'),
    ('OOS／正式認證', 'NOT_TESTED', 'OOS_NOT_AVAILABLE；不宣稱 CERTIFIED'),
]


def item_features(items):
    """Status rows computed from the queue evidence: one per legacy baseline / sensitivity, never a hard-coded claim."""
    rows = []
    for e in items:
        kind = (e.get('item') or {}).get('kind', '')
        if not kind.startswith('legacy'):
            continue
        status = e.get('status')
        state = {'SUCCESS': 'IMPLEMENTED_BUT_DATA_BLOCKED', 'FAILED': 'FAILED'}.get(status, 'NOT_TESTED')
        note = f"{status}: {e.get('reason') or ''}"
        if status == 'SUCCESS':
            note = f"run {e.get('run_id')}; 門檻 DATA_BLOCKED（資料未認證）"
        elif e.get('failure_run_id'):
            note += f" (FAILED run {e['failure_run_id']}, trial {e.get('experiment_id')})"
        rows.append((('Legacy 敏感度 ' if kind == 'legacy_sensitivity' else 'Legacy 基準 ') + e['item_id'].split(':', 1)[1], state, note))
    return rows


def _read(path, default=None):
    try:
        return json.loads(Path(path).read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return default


def _v(metrics, name, pct=True):
    item = (metrics or {}).get(name) or {}
    value = item.get('value') if isinstance(item, dict) else None
    if value is None:
        return f'<span class=m>{E(str(item.get("reason") or "NOT_COMPUTABLE"))}</span>' if isinstance(item, dict) else '—'
    return f'{value * 100:.2f}%' if pct else f'{value:.3f}'


def _table(headers, rows):
    head = ''.join(f'<th>{E(h)}</th>' for h in headers)
    body = ''.join('<tr>' + ''.join(f'<td>{c}</td>' for c in row) + '</tr>' for row in rows)
    return f'<table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table>'


def overview(items, legacy, scores, top_ids):
    by_item = {i['item_id']: i for i in items}
    rank = {rid: n + 1 for n, rid in enumerate(top_ids)}
    score_by = {s['run_id']: s for s in scores}
    rows = []
    meta = StrategyManager.STRATEGY_METADATA
    for sid in ('hybrid_trend_rank', 'defensive_low_volatility', 'growth_momentum_breakout', 'quality_growth',
                'institutional_flow_confirmation', 'mean_reversion', 'quality_value_low_volatility'):
        base = by_item.get(f'LEGACY:{sid}', {})
        info = legacy.get(sid, {})
        m = base.get('metrics') or {}
        rows.append([E(f'{meta[sid].display_name_zh}（{sid}）'), '-', E(base.get('run_id') or '-'), E(base.get('experiment_id') or '-'),
                     '2023-01-03~2025-12-31' if base else '-', E(info.get('data_health', '-')),
                     (f"報酬 {_v(m, 'total_return')} / CAGR {_v(m, 'cagr')} / Sharpe {_v(m, 'sharpe', False)} / MDD {_v(m, 'max_drawdown')}" if m else E(base.get('status') or info.get('gate_result', '-'))),
                     '-', '-', 'OOS_NOT_AVAILABLE', E(info.get('gate_result', 'DATA_BLOCKED')), 'YES（研究展示）', 'NO（LATEST_SIGNAL_DISABLED）'])
        rows[-1][7] = E(info.get('recommendation', 'NOT_EVALUATED'))
    plan = _read(LAB / 'queue.json', {}) or {}
    for q in plan.get('queue', []):
        if q['kind'].startswith('legacy'):
            continue
        e = by_item.get(q['item_id'], {})
        m = e.get('metrics') or {}
        sc = score_by.get(e.get('run_id') or '', {})
        rows.append([E(q['item_id']), E(' AND '.join(q.get('conditions') or sorted((q.get('weights') or {}))) or '-'), E(e.get('run_id') or '-'),
                     E(e.get('experiment_id') or '-'), '2023-01-03~2025-12-31', 'RESEARCH_UNCERTIFIED（prepared_v6）',
                     (f"報酬 {_v(m, 'total_return')} / Sharpe {_v(m, 'sharpe', False)} / MDD {_v(m, 'max_drawdown')}" if m else E(e.get('status', 'PENDING') + ' ' + str(e.get('reason') or ''))),
                     E(f"{sc['avg_score']:.3f}" if sc.get('avg_score') is not None else (sc.get('reason') or '-')),
                     E(str(rank.get(e.get('run_id'), '-'))), 'OOS_NOT_AVAILABLE', 'NOT_CERTIFIED', 'YES（研究展示）' if e.get('run_id') else 'NO', 'NO'])
    return _table(['Strategy ID', 'Condition IDs', 'Run ID', 'Trial ID', '回測期間', '資料品質', '績效與風險', 'avg_score／參數建議', 'Top 3', 'OOS',
                   'OD-24 結果', '可研究展示', '授權最新研究選股'], rows)


def reference_curves():
    """TRI benchmark and archived H4 NAV scaled to 1,000,000; labelled as reference lines of other comparison groups."""
    from core.research import benchmark_tri
    lines = []
    try:
        tri = benchmark_tri.research_series(benchmark_tri.research_calendar())
        lines.append({'x': [d.strftime('%Y-%m-%d') for d in tri.index], 'y': (tri / tri.iloc[0] * 1_000_000).round(2).tolist(),
                      'name': 'Benchmark: TWSE total-return index (reference)', 'type': 'scatter', 'line': {'dash': 'dot'}})
    except Exception as exc:
        lines.append({'x': [], 'y': [], 'name': f'Benchmark unavailable ({type(exc).__name__})', 'type': 'scatter'})
    daily = (_read(ROOT / 'outputs/research_mode/h4_runs_v1/002/base.json', {}) or {}).get('result', {}).get('daily', [])
    if daily:
        lines.append({'x': [r['date'] for r in daily], 'y': [r['nav'] for r in daily],
                      'name': 'H4 archived (REB60, different comparison group)', 'type': 'scatter', 'line': {'dash': 'dash'}})
    return lines


def build(out=DOCS / 'report.html'):
    from core.research import strategy_signal as ss
    ss.check_window('2023-01-03', '2025-12-31')  # the report reads development-window evidence only
    import plotly
    plotly_js = (Path(plotly.__file__).parent / 'package_data' / 'plotly.min.js').read_text(encoding='utf-8')
    summary = _read(LAB / 'lab_summary.json', {}) or {}
    legacy = _read(LEGACY, {}) or {}
    items = summary.get('items') or [_read(p, {}) for p in sorted((LAB / 'items').glob('*.json'))]
    scores = summary.get('avg_scores') or []
    top = [t['run_id'] for t in summary.get('top') or []]
    curves = []
    for rid in top:
        try:
            nav = run_store.read_frame(run_store.load_run(rid), 'nav')
            curves.append({'x': nav['date'].tolist(), 'y': nav['nav_base'].tolist(), 'name': rid, 'type': 'scatter'})
        except Exception as exc:
            curves.append({'x': [], 'y': [], 'name': f'{rid} ({type(exc).__name__})', 'type': 'scatter'})
    curves += reference_curves()
    notebooks = list(csv.DictReader((DOCS / 'notebook_inventory.csv').open(encoding='utf-8'))) if (DOCS / 'notebook_inventory.csv').is_file() else []
    nb_rows = [[E(n['name']), E(n['in_user_list']), E(n['asset_class']), E(n['tej_dependency']), E(n['credential_finding']),
                E(n['duplicate_of']), E(n['reproducible_with_local_data'][:120]), E(n['conclusion'])] for n in notebooks]
    conditions = list(csv.DictReader((LAB / 'conditions.csv').open(encoding='utf-8'))) if (LAB / 'conditions.csv').is_file() else []
    cond_rows = [[E(c['condition_id']), E(c['name']), E(c['definition']), E(c['status']), E(c.get('reason') or '')] for c in conditions]
    feat = _table(['功能', '狀態', '證據'], [[E(f), E(s), E(e)] for f, s, e in item_features(items) + FEATURES])
    top_html = ''.join(f"<h3>#{n + 1} {E(rid)}</h3><pre>{E(json.dumps((summary.get('top_analysis') or {}).get(rid, {}), ensure_ascii=False, default=str)[:6000])}</pre>"
                       for n, rid in enumerate(top)) or '<p>沒有可排名的已測試組合。</p>'
    bench = (DOCS / 'benchmark_quality_report.md').read_text(encoding='utf-8') if (DOCS / 'benchmark_quality_report.md').is_file() else '未產生'
    page = f"""<!doctype html><html lang=zh-Hant><meta charset=utf-8><title>Stock_Linbotv1 多策略驗證與複合式選股（2026-11-06）</title>
<style>body{{font-family:'Microsoft JhengHei',system-ui,sans-serif;margin:24px;color:#1b2430}}table{{border-collapse:collapse;font-size:12px;margin:8px 0}}
td,th{{border:1px solid #c8d0da;padding:4px 6px;vertical-align:top}}th{{background:#eef2f7}}.m{{color:#7a8594}}pre{{white-space:pre-wrap;font-size:11px;background:#f6f8fa;padding:8px}}
.banner{{border:1px solid #d9a400;background:#fff8e1;padding:10px}}</style>
<script>{plotly_js}</script>
<h1>多策略驗證、條件組合研究與選股平台整合</h1>
<p class=banner>本報告全部是研究結果：沒有任何策略取得 CERTIFIED，OOS = OOS_NOT_AVAILABLE；2026 封存資料未讀取。Legacy 門檻結果因資料未認證，最高為 DATA_BLOCKED，g1～g5 數值只作比較。
avg_score 是同一比較群組內的探索性相對分數，不是認證分數或勝率。本輪 TRIAL：{summary.get('round_trials', '統計中')} / {summary.get('cap', 23)}；effective_trials：{summary.get('effective_trials', '-')}；佇列 SHA256：{E(str(summary.get('queue_sha256', '-')))}。</p>
<h2>總覽</h2>{overview(items, legacy, scores, top)}
<h2>功能狀態</h2>{feat}
<h2>條件池</h2>{_table(['ID', '條件', '定義', '狀態', '原因'], cond_rows)}
<h2>已測試組合熱力圖</h2><div id=heat style='height:520px'></div>
<h2>Top 3 淨值（研究候選，非認證）</h2><div id=nav style='height:420px'></div>{top_html}
<h2>TQuant Notebook 清冊（41 份；含使用者 19 份）</h2>{_table(['Notebook', '使用者 19 份', '類別', 'TEJ', '憑證', '重複', '本機可重現', '結論'], nb_rows)}
<p>金鑰只以 REDACTED 記錄；處置見 credential_actions.md（撤銷與重發由 Owner 在 TEJ 帳戶操作）。</p>
<h2>含息 Benchmark 品質</h2><pre>{E(bench)}</pre>
<script>
const heat = {json.dumps(summary.get('heatmap'))};
if (heat) Plotly.newPlot('heat', [{{type:'heatmap', x:heat.x, y:heat.y, z:heat.z, customdata:heat.customdata, colorscale:'Viridis', zmin:0, zmax:1}}], {{margin:{{l:200}}}});
else document.getElementById('heat').innerText = '尚無足夠的已測試組合';
Plotly.newPlot('nav', {json.dumps(curves)}, {{}});
</script></html>"""
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(page, encoding='utf-8')
    return out


if __name__ == '__main__':
    print(build())
