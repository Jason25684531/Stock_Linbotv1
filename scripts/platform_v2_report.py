"""Platform V2 reports, generated from the stored result files only (change 2026-11-12, task 9.4). Every number below is read from a file."""
from __future__ import annotations

import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
OUT = ROOT / 'outputs/platform_v2'
TASK_STATUS = OUT / 'acceptance/final_task_status.json'
LABEL = '> 資料類別 REAL_RESEARCH（2015～2025 快照的研究結果）・RESEARCH_ONLY・NOT_CERTIFIED・已看過的歷史結果，非樣本外證據；本報告沒有 DEMO 資料'


def _read(path):
    try:
        return json.loads(Path(path).read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return None


def pct(v, signed=False):
    if v is None:
        return '-'
    if isinstance(v, dict):
        return pct(v.get('value'), signed) if v.get('value') is not None else '- (' + str(v.get('reason')) + ')'
    return ('%+.2f%%' if signed else '%.2f%%') % (v * 100)


def _runs(root):
    from core.backtest import run_store
    return run_store.history(root=root) if Path(root).is_dir() else []


def _publishable_portfolios():
    return [r for r in _runs(OUT / 'portfolios') if r.get('status') == 'SUCCESS' and r.get('publishable') and r.get('analysis')]


def _page_status(route):
    smoke = _read(OUT / 'acceptance/phase_f/playwright_smoke.json') or {}
    rows = [r for r in smoke.get('pages', []) if r.get('route') == route]
    if not rows:
        return 'NOT_VERIFIED'
    passed = all(r.get('status') == 200 and not r.get('page_error_count') and not r.get('console_error_count')
                 and not r.get('external_requests') and not (r.get('viewport') == 'mobile' and r.get('horizontal_overflow')) for r in rows)
    return 'PASS' if passed else 'FAIL'


def _current_model_versions():
    from core.research.platform import prediction as pr
    from scripts import historical_foundation, platform_v2, research_workflow
    snapshot_id = historical_foundation.latest_evaluation()['snapshot_id']
    frozen = pr.check_frozen(platform_v2.MODEL_FREEZE)
    code = {name: research_workflow.sha256(ROOT / name) for name in ('core/research/platform/prediction.py', 'scripts/platform_v2.py')}
    return [platform_v2.model_version(h, frozen, snapshot_id, code) for h in pr.SPEC['labels']['horizons']]


def _write(name, lines):
    stamp = datetime.now(timezone.utc).isoformat(timespec='seconds')
    (OUT / name).write_text('\n'.join(lines + ['', '_generated ' + stamp + ' by scripts/platform_v2_report.py_', '']), encoding='utf-8')
    return name


def reconciliation_report():
    lines = ['# BACKTEST_RECONCILIATION_REPORT', '', LABEL, '', '## Characterization（短視窗，V1 引擎路徑與 V2 預設規則，兩個獨立程序）', '',
             '| 策略 | 視窗 | 交易日 | 交易 | 成交列 | 結果 | 差異 | 對帳 | 快取抽樣 |', '|---|---|---|---|---|---|---|---|---|']
    for path in sorted((OUT / 'acceptance/phase_b').glob('characterization_*.json')):
        c = _read(path)
        if not c:
            continue
        recon = ', '.join(k + ':' + v['status'] for k, v in c['reconciliation'].items())
        cache = 'MISMATCH' if c['feature_cache']['mismatches'] else str(len(c['feature_cache']['verified_dates'])) + ' dates equal'
        cells = [c['strategy_id'], ' ~ '.join(c['window']), c['sessions'], c['trades'], c['fills'], c['status'], ', '.join(c['differences']) or 'none', recon, cache]
        lines.append('| ' + ' | '.join(str(x) for x in cells) + ' |')
    verify = _read(OUT / 'acceptance/phase_b/baseline_verification.json')
    lines += ['', '## V2 基準 Run 對 V1 Run（全期間、預設規則、V1 資金與成本）', '']
    if not verify:
        lines.append('尚未執行 `python -m scripts.platform_v2 verify`')
    else:
        lines += ['checked ' + verify['checked_at'] + ' / snapshot `' + verify['snapshot_id'] + '` / overall **' + verify['status'] + '**', '',
                  '| 策略 | 期間 | V1 Run | V2 Run | 交易日 | 交易 | 成交列 | 與 V1 逐日相同 | 對帳 | 結果 |', '|---|---|---|---|---|---|---|---|---|---|']
        for r in verify['rows']:
            cells = [r['strategy_id'], ' ~ '.join(r['window']), '`' + str(r.get('v1_run')) + '`', '`' + str(r.get('v2_run')) + '`', r.get('sessions', '-'),
                     r.get('trades', '-'), r.get('fills', '-'), r.get('identical_to_v1', '-'), r.get('reconciliation', '-'), r['status']]
            lines.append('| ' + ' | '.join(str(x) for x in cells) + ' |')
        g = verify['governance']
        lines += ['', '舊 Run 與身份檔對帳 **' + g['status'] + '** / checked ' + json.dumps(g['checked']) + ' / differences ' + str(len(g['differences']))]
    lines += ['', '## 對帳方法', '',
              '- 由 `fills.csv` 與 BOOKED 公司行動逐日重算現金與持股；容許誤差 0.01 + 1e-9 x NAV（`core/research/platform/ledger_v2.py`）',
              '- 任何一日不符，或期末持股與 `open_positions` 不同，即 NOT_PUBLISHABLE，Web 只顯示原因',
              '- 比對容許 rtol 1e-9、atol 1e-12；交易日期、代碼、順序與數量必須完全相同（`scripts/platform_v2.compare`）']
    return _write('BACKTEST_RECONCILIATION_REPORT.md', lines)


def model_report():
    from core.research.platform import prediction as pr
    freeze = _read(OUT / 'model_profile_freeze.json') or []
    spec = pr.SPEC
    lines = ['# MODEL_VALIDATION_REPORT', '', LABEL, '', '## 1. 既有模型稽核（D.1）', '',
             '- `jobs/train_model.py` 的標的為「未來 N 日最高價是否超過 X%」（`calculate_future_target`，各策略 N、X 不同），不是 5／20 日漲跌；未來資料不足的列以 0 補值後成為負標籤；以日期 80／20 切分（`time_series_split`），沒有 purge；只記錄 accuracy 與 precision',
             '- `core/research/model_training.py` 有 purge 與 embargo，但標的同樣是最高價門檻，且需另行核准，未產生 5／20 日機率模型',
             '- `ML_Data/pkl/` 的 8 個模型檔供每日流程排序（`daily_recommendations.ai_score`），依規格為 MODEL_BLOCKED；本平台不沿用、不修改、不載入',
             '', '## 2. 凍結規格（D.2，在任何評估之前）', '']
    lines += ['- `' + e['spec_hash'] + '` frozen at ' + e['frozen_at'] + '（只追加）' for e in freeze]
    lines += ['', '- 特徵 ' + ', '.join(spec['features']) + '；' + spec['feature_note'],
              '- 標籤 ' + spec['labels']['definition'] + '；排除 ' + spec['labels']['exclude'],
              '- 模型 B0 = ' + spec['models']['B0'] + '；M1 = ' + spec['models']['M1'],
              '- 切分 ' + spec['split']['scheme'] + '，測試年 ' + str(spec['split']['test_years'][0]) + '-' + str(spec['split']['test_years'][-1]) + '，purge ' + str(spec['split']['purge_sessions']) + ' sessions',
              '- 通過條件 ' + json.dumps(spec['pass']) + '；全部成立為 DEV_WINDOW_SKILL，否則 NOT_VALIDATED；沒有 VALIDATED 或 CERTIFIED 的路徑', '',
              '## 3. 評估結果（D.4、D.5）', '']
    if OUT == ROOT / 'outputs/platform_v2':
        paths = [OUT / 'models' / version / 'model.json' for version in _current_model_versions()]
    else:  # isolated report fixtures may use synthetic identities
        paths = sorted((OUT / 'models').glob('PRED1-*/model.json'))
    for path in paths:
        m = _read(path)
        if not m:
            lines += ['### `' + path.parent.name + '` 尚未執行（目前身份的結果檔不存在）', '']
            continue
        e = m['evaluation']
        if not e.get('rows'):
            lines += ['### ' + str(m['horizon']) + ' sessions `' + m['model_version'] + '` **' + m['status'] + '**', '',
                      '- 不可計算：' + (', '.join(e.get('reasons') or []) or 'NO_PREDICTIONS'), '']
            continue
        undefined = e.get('undefined_reasons') or {}
        def number(value, reason, fmt):
            return fmt % value if value is not None else '不可計算：' + str(reason or '未提供原因')
        yearly_reasons = undefined.get('yearly_skill') or {}
        yearly = ', '.join(str(y) + ' ' + number(v, yearly_reasons.get(str(y), yearly_reasons.get(y)), '%+.4f')
                           for y, v in e['yearly_skill'].items())
        skill = number(e['brier_skill'], undefined.get('brier_skill'), '%.4f')
        interval = ' ~ '.join(number(v, undefined.get('brier_skill_ci95'), '%.4f') for v in e['brier_skill_ci95'])
        positive = pct(e['positive_skill_year_share']) if e['positive_skill_year_share'] is not None else number(None, undefined.get('positive_skill_year_share'), '')
        slope = number(e['calibration_slope'], undefined.get('calibration_slope'), '%.3f')
        lines += ['### ' + str(m['horizon']) + ' sessions `' + m['model_version'] + '` **' + m['status'] + '**', '',
                  '- evaluation `' + m['evidence']['evaluation_id'] + '` / snapshot `' + m['snapshot_id'] + '` / test rows ' + format(e['rows'], ',') + ' / test dates ' + str(e['dates']) + ' / excluded event windows ' + format(m['excluded_event_windows'], ','),
                  '- Brier M1 %.5f, B0 %.5f；Brier skill %s（95%% 日期區塊 bootstrap %s）' % (e['brier_m1'], e['brier_b0'], skill, interval),
                  '- 各年 skill ' + yearly + '；為正的年份比例 ' + positive,
                  '- 校準斜率 %s（1 為完美；小於 1 代表機率過度自信）；80%% 區間覆蓋 %s；報酬中位數絕對誤差 %s' % (slope, pct(e['coverage_80']), pct(e['median_abs_error'])),
                  '- 機率最高十分位等權、扣一次來回成本的平均報酬 ' + pct(e['net_top_decile_mean'], True) + '（不重疊 ' + str(e['net_top_decile_periods']) + ' 期）；測試期上漲比率 ' + pct(e['up_rate_test']),
                  '- 未通過的門檻 ' + (', '.join(e['reasons']) or 'none'), '']
    lines += ['## 4. 解讀限制', '',
              '- 這是 2019～2025 開發視窗內的 walk-forward；這段資料在專案中已被看過，不是獨立樣本外驗證',
              '- Brier skill 只有千分之幾、校準斜率遠小於 1；即使通過凍結門檻，也只代表比無條件上漲比率略好，不代表可交易',
              '- 今日預測一律 GOVERNANCE_BLOCKED（OD-2，研究模組不讀 2025-12-31 以後的資料）；NOT_VALIDATED 的模型不儲存也不顯示個股預測值']
    return _write('MODEL_VALIDATION_REPORT.md', lines)


def portfolio_report():
    lines = ['# PORTFOLIO_VALIDATION_REPORT', '', LABEL, '', '規則 ALLOC_V1、CONTEND_V1、REBAL_NONE／REBAL_ANNUAL_V1（`core/research/platform/portfolio_v2.py`）；組合是研究情境，不是策略', '']
    for row in _runs(OUT / 'portfolios'):
        if row.get('status') != 'SUCCESS':
            continue
        p, a = row['parameters'], row.get('analysis') or {}
        members = ', '.join(s + ' ' + '%.1f%%' % (w * 100) for s, w in zip(p['members'], p['weights']))
        recon = ', '.join(k + ' ' + v['status'] for k, v in row['reconciliation'].items())
        lines += ['## `' + row['run_id'] + '`', '', '- members ' + members + ' / period ' + ' ~ '.join(row['period']) + ' / rebalance ' + p['rebalance'] + ' / rule ' + p['rule'],
                  '- reconciliation ' + recon + ' / publishable ' + str(row['publishable'])]
        if a:
            b, s = a['portfolio']['base'], a['portfolio']['stress']
            turnover = '%.2f 倍' % a['costs']['turnover_annual'] if a['costs']['turnover_annual'] is not None else '不可計算：NO_SESSIONS'
            lines += ['- 組合（基礎成本）累積 ' + pct(b['total_return']) + '、CAGR ' + pct(b['cagr']) + '、最大回撤 ' + pct(b['max_drawdown']) + '；兩倍成本 CAGR ' + pct(s['cagr']),
                      '- 同期大盤含息累積報酬 ' + pct(a['benchmark_total_return']['total_return']),
                      '- 資金競爭 ' + (json.dumps(a['contention']) if a['contention'] else 'none') + '；成員單獨執行時同日持有同一股票 ' + str(a['overlap']['standalone_overlap_days']) + ' 日；共同重大損失 ' + str(a['common_large_losses']['count']) + ' 日',
                      '- 費用與稅 ' + format(round(a['costs']['fees_and_tax_paid']), ',') + ' 元；年化換手 ' + turnover + '；產業集中度 ' + a['industry_concentration']['reason']]
            lines += ['  - member ' + sid + ' CAGR ' + pct(m['cagr']) + '、MDD ' + pct(m['max_drawdown']) + '（單獨 V2 Run）' for sid, m in a['members'].items()]
        lines.append('')
    check = _read(OUT / 'acceptance/phase_e/single_member_equality.json')
    lines += ['## 單一成員權重 1 的組合等於該策略的 V2 Run', '', (check['status'] + ' / ' + check['detail']) if check else '尚未執行實際資料比對']
    return _write('PORTFOLIO_VALIDATION_REPORT.md', lines)


def health_report():
    pipeline, gov = _read(OUT / 'pipeline/LATEST.json'), _read(OUT / 'governance/LATEST.json')
    lines = ['# DAILY_PIPELINE_HEALTH_REPORT', '', LABEL, '', '## 研究管線（jobs/run_research_platform.py，scheduler 的 research 管線）', '']
    if pipeline:
        lines += ['job `' + pipeline['job_id'] + '` / status **' + pipeline['status'] + '** / exit ' + str(pipeline['exit_code']) + ' / updated ' + pipeline['updated_at'], '',
                  '| 步驟 | 狀態 | 失敗分類 | 嘗試 | 原因 |', '|---|---|---|---|---|']
        for n, s in pipeline['steps'].items():
            lines.append('| ' + ' | '.join([n, s['status'], s.get('failure_class') or '', str(s.get('attempts')), (s.get('reason') or '').replace('|', '/')[:160]]) + ' |')
    else:
        lines.append('研究管線尚未執行')
    lines += ['', '## 治理對帳', '']
    if gov:
        c = gov['current']
        lines.append(gov['checked_at'] + ' **' + gov['status'] + '** / effective_trials ' + str(c['effective_trials']) + ' / Trial Ledger ' + c['trial_ledger_verify'] +
                     ' / Registry ' + c['registry_verify'] + ' / H4 files ' + str(c['h4_files']) + ' / checked ' + json.dumps(gov['checked']) + ' / differences ' + str(len(gov['differences'])))
        lines += ['- ' + d for d in gov['differences']]
    lines += ['', '## 正式每日管線', '', '本變更不修改 daily、evening、morning、push_evening 四條管線、`jobs/run_daily.py` 與 `jobs/push_to_line.py`；正式管線狀態在 `/platform/system` 唯讀顯示']
    return _write('DAILY_PIPELINE_HEALTH_REPORT.md', lines)


def matrix():
    readiness = (_read(OUT / 'STRATEGY_READINESS_21.json') or {}).get('result', {}).get('strategies', [])
    runs = [r for r in _runs(OUT / 'runs') if r.get('status') == 'SUCCESS']
    versions = _current_model_versions()
    models = [_read(OUT / 'models' / version / 'model.json') for version in versions]
    models = [m for m in models if m]
    folios = [r for r in _runs(OUT / 'portfolios') if r.get('status') == 'SUCCESS']
    accepted_folios = _publishable_portfolios()
    verify = _read(OUT / 'acceptance/phase_b/baseline_verification.json') or {}
    model_text = ', '.join(str(m['horizon']) + 'd ' + m['status'] for m in models) or 'none'
    admin = _read(OUT / 'acceptance/phase_f/web_admin_actions.json') or {}
    guest = _read(OUT / 'acceptance/phase_f/web_guest_posts.json') or {}
    guest_status = 'guest POST 403 PASS' if guest.get('status') == 'PASS' else 'guest POST not verified'
    action_status = 'PASS' if admin.get('status') == 'PASS' else 'BLOCKED: no authorized-action evidence; ' + guest_status
    rows = [['每日智慧選股', '`/platform/selection`', _page_status('/platform/selection') + '；唯讀展示', 'daily_recommendations；可行性、共識欄；test_platform_v2_selection', '2026 推薦僅在既有公開路由可驗證時顯示（OD-V2-2）；ai_score 未驗證'],
            ['AI 預測與追蹤', '`/platform/predictions`', _page_status('/platform/predictions') + '；' + model_text, '當日預測 GOVERNANCE_BLOCKED；凍結規格；test_platform_v2_prediction', '開發視窗 walk-forward 非獨立樣本外；未通過模型不顯示機率'],
            ['策略回測研究', '`/platform/backtest`', _page_status('/platform/backtest') + '；自訂回測 ' + action_status, 'V2 runs ' + str(len(runs)) + '；baseline verification ' + str(verify.get('status', 'not run')) + '；engine/ledger/web evidence', '只開放有 VERIFIED 軌的策略；期間不晚於 2025-12-31'],
            ['策略組合分析', '`/platform/portfolio`', _page_status('/platform/portfolio') + '；可發布實際組合 ' + ('PASS' if accepted_folios else 'BLOCKED: no publishable portfolio') + '；Web 管理提交 ' + action_status, 'portfolio runs ' + str(len(folios)) + '；test_platform_v2_portfolio', '產業集中度 NOT_AVAILABLE；組合不是策略'],
            ['系統排程與驗證中心', '`/platform/system`', _page_status('/platform/system') + '；啟動／取消／重試 ' + action_status, '研究管線、槽位、治理對帳、正式管線唯讀；test_platform_v2_pipeline', '不建立 Windows 排程，不寫 MySQL，不發 LINE']]
    lines = ['# FEATURE_COMPLETION_MATRIX', '', LABEL, '', '| 功能 | Web 入口 | 驗收狀態 | 資料與證據 | 限制 |', '|---|---|---|---|---|']
    lines += ['| ' + ' | '.join(r) + ' |' for r in rows]
    lines += ['', '21 strategies: ' + str(len(readiness)) + ' rows / web runnable ' + str(sum(bool(r.get('web_runnable')) for r in readiness)) +
              ' / DATA_BLOCKED ' + str(sum(r['state'] == 'DATA_BLOCKED' for r in readiness)) + ' / GOVERNED_ONLY ' + str(sum(r['state'] == 'GOVERNED_ONLY' for r in readiness)) +
              '（STRATEGY_READINESS_21.json）']
    return _write('FEATURE_COMPLETION_MATRIX.md', lines)


def _task_rows():
    override = _read(TASK_STATUS) or {}
    by_id = {r['id']: r for r in override.get('tasks', []) if r.get('id')}
    rows = []
    pattern = re.compile(r'- \[([ xX])\] \[[^]]+\] ([A-Z0-9]+\.[0-9]+)\s+(.*)')
    for line in (ROOT / 'openspec/changes/2026-11-12-integrated-research-platform-v2/tasks.md').read_text(encoding='utf-8').splitlines():
        match = pattern.match(line)
        if not match:
            continue
        checked, task_id, title = match.groups()
        record = by_id.get(task_id, {})
        rows.append({'id': task_id, 'title': title, 'status': record.get('status', 'PASS' if checked.lower() == 'x' else 'PENDING'),
                     'reason': record.get('reason', ''), 'evidence': record.get('evidence', '')})
    return rows


def acceptance_report():
    tasks = _task_rows()
    counts = {s: sum(t['status'] == s for t in tasks) for s in ('PASS', 'BLOCKED', 'FAIL', 'PENDING', 'RUNNING')}
    smoke = _read(OUT / 'acceptance/phase_f/playwright_smoke.json') or {}
    smoke_rows = smoke.get('pages', [])
    smoke_pass = sum(r.get('status') == 200 and not r.get('page_error_count') and not r.get('console_error_count')
                     and not r.get('external_requests') and not (r.get('viewport') == 'mobile' and r.get('horizontal_overflow')) for r in smoke_rows)
    dynamic = _read(OUT / 'acceptance/phase_f/playwright_dynamic_report.json') or {}
    dynamic_rows = dynamic.get('pages', [])
    dynamic_pass = sum(r.get('status') == 200 and r.get('values_match') and not r.get('horizontal_overflow')
                       and not r.get('page_errors') and not r.get('console_errors') and not r.get('external_requests') for r in dynamic_rows)
    legacy = _read(OUT / 'acceptance/phase_final/legacy_intelligence_playwright_escalated.json') or {}
    review_path = OUT / 'acceptance/independent_review_20261113.md'
    tests = _read(OUT / 'acceptance/phase_final/pytest.json') or {}
    openspec = _read(OUT / 'acceptance/phase_final/openspec_validation.json') or {}
    gov = _read(OUT / 'governance/LATEST.json') or {}
    verify = _read(OUT / 'acceptance/phase_b/baseline_verification.json') or {}
    rows = [
        ('每日智慧選股', '/platform/selection', _page_status('/platform/selection') + '（唯讀）'),
        ('AI 預測與追蹤', '/platform/predictions', _page_status('/platform/predictions') + '；每日預測 GOVERNANCE_BLOCKED'),
        ('策略回測研究', '/platform/backtest', _page_status('/platform/backtest') + '；自訂管理操作見 F.8'),
        ('策略組合分析', '/platform/portfolio', _page_status('/platform/portfolio') + '；可發布實際組合 ' + ('PASS' if _publishable_portfolios() else 'BLOCKED') + '；Web 管理提交見 F.8'),
        ('系統排程與驗證中心', '/platform/system', _page_status('/platform/system') + '；管理操作見 F.8'),
    ]
    lines = ['# PLATFORM_V2_FINAL_ACCEPTANCE_REPORT', '', LABEL, '',
             '## 五個 Web 入口', '', '| 功能 | 測試網址 | 實際狀態 |', '|---|---|---|']
    lines += ['| ' + ' | '.join((name, '`http://127.0.0.1:18770' + route + '`', status)) + ' |' for name, route, status in rows]
    lines += ['', '## 37 項任務', '', '| 狀態 | 數量 |', '|---|---:|']
    lines += ['| ' + status + ' | ' + str(counts[status]) + ' |' for status in ('PASS', 'BLOCKED', 'FAIL', 'RUNNING', 'PENDING')]
    lines += ['', '| 任務 | 狀態 | 原因／證據 |', '|---|---|---|']
    lines += ['| ' + t['id'] + ' ' + t['title'] + ' | ' + t['status'] + ' | ' + (t['reason'] + ('；' if t['reason'] and t['evidence'] else '') + t['evidence']) + ' |' for t in tasks]
    lines += ['', '## 驗證證據', '',
              '- pytest：' + (f"{tests.get('passed', '?')} passed、{tests.get('skipped', '?')} skipped、{tests.get('xfailed', '?')} xfailed" if tests else '本次最終 pytest 摘要尚未記錄'),
              '- V2 Playwright：' + (f"{smoke_pass + dynamic_pass}/{len(smoke_rows) + len(dynamic_rows)} 桌機／手機頁面與數值檢查通過；靜態入口零外部請求" if smoke_rows else '尚無有效證據'),
              '- 既有 `/intelligence` Playwright：' + str(legacy.get('status', '尚未記錄')) + '（允許既有 CDN；獨立證據 `legacy_intelligence_playwright_escalated.json`）',
              '- 未授權管理 POST：' + str((_read(OUT / 'acceptance/phase_f/web_guest_posts.json') or {}).get('status', '尚未記錄')) + '（此證據只驗證 fail-closed，不代表管理員操作完成）',
              '- 獨立唯讀程式審查：' + ('已記錄' if review_path.is_file() else '尚未記錄'),
              '- OpenSpec strict：' + str(openspec.get('status', '尚未記錄')),
              '- 治理對帳：' + str(gov.get('status', '尚未記錄')) + '；effective_trials=' + str((gov.get('current') or {}).get('effective_trials', '未記錄')),
              '- V2 對帳：' + str(verify.get('status', '尚未執行')) + '；策略列 ' + str(len(verify.get('rows', []))) + '/16；snapshot `' + str(verify.get('snapshot_id', '未記錄')) + '`，研究資料截止 2025-12-31']
    lines += ['', '## 模型、組合與 5～15% 年化研究目標', '',
              '- 模型結果見 `MODEL_VALIDATION_REPORT.md`；DEV_WINDOW_SKILL 只代表已看過的 2019～2025 walk-forward 開發視窗，不是獨立樣本外或交易認證。',
              '- 組合結果見 `PORTFOLIO_VALIDATION_REPORT.md`；必須有合格 VERIFIED 成員且帳本可發布，否則不提供績效數字。',
              '- 以下為已發布研究情境的歷史 CAGR 與數學差距，不代表目標達成，也不是獨立樣本外證據：']
    folios = _publishable_portfolios()
    if folios:
        for row in folios:
            metric = (row.get('analysis') or {}).get('portfolio', {}).get('base', {}).get('cagr')
            value = metric.get('value') if isinstance(metric, dict) else metric
            if value is None:
                reason = metric.get('reason') if isinstance(metric, dict) else None
                lines.append('- `' + row['run_id'] + '` CAGR 不可計算：' + str(reason or '未提供原因'))
            else:
                low_gap, high_gap = 5 - value * 100, 15 - value * 100
                lines.append('- `' + row['run_id'] + '` 成員 ' + ', '.join(row['parameters']['members']) + '／權重 ' + ','.join(f"{w:.0%}" for w in row['parameters']['weights']) +
                             f"：歷史 CAGR {value * 100:.2f}%；距 5% 下界 {low_gap:+.2f} 個百分點、距 15% 上界 {high_gap:+.2f} 個百分點。")
    else:
        lines.append('- NOT_ASSESSED：目前沒有可發布的實際組合結果；無法量化與目標的差距。')
    lines.append('- 投資研究目標的有效差距仍為 NOT_ASSESSED，直到取得獨立樣本外且治理允許的策略或組合驗證；任何不可計算值保留原因，不以 0 代替。')
    return _write('PLATFORM_V2_FINAL_ACCEPTANCE_REPORT.md', lines)


def blockers_report():
    tasks = _task_rows()
    blocked = [t for t in tasks if t['status'] in ('BLOCKED', 'FAIL', 'RUNNING', 'PENDING')]
    lines = ['# BLOCKERS_AND_DEFERRED', '', LABEL, '',
             '只有有實際驗收證據的功能標為 PASS。背景工作仍在執行時標 RUNNING；缺少治理授權或合格資料時標 BLOCKED。', '']
    if blocked:
        lines += ['| 任務 | 狀態 | 原因 | 證據 |', '|---|---|---|---|']
        lines += ['| ' + t['id'] + ' ' + t['title'] + ' | ' + t['status'] + ' | ' + t['reason'] + ' | ' + t['evidence'] + ' |' for t in blocked]
    else:
        lines.append('目前沒有未解決的任務阻擋。')
    lines += ['', '## 年化研究目標', '',
              '年化 5～15% 的差距維持 NOT_ASSESSED，直到存在可發布、治理允許且獨立樣本外驗證的策略或組合結果。現有歷史開發視窗結果不能當成目標達成證據。',
              '', '## 明確禁止事項', '',
              '- 不新增正式 TRIAL，不修改 V1 歷史結果，不解除既有治理。',
              '- 不把 2026 每日推薦寫入研究 Run、模型訓練、評估或研究快取。',
              '- 不正式下單、不 commit、不 push。']
    return _write('BLOCKERS_AND_DEFERRED.md', lines)


def operations_guide():
    lines = ['# Stock Linbot V2 研究平台操作說明', '',
             '本機驗收網址：`http://127.0.0.1:18770/platform`。各功能頁面只讀取已保存結果或正式唯讀選股服務；GET 不啟動計算。', '',
             '| 入口 | 路徑 | 可見內容 |', '|---|---|---|',
             '| 每日智慧選股 | `/platform/selection` | 正式唯讀推薦、來源、可行性與治理狀態 |',
             '| AI 預測與追蹤 | `/platform/predictions` | 凍結模型評估；未驗證模型不提供個股機率；今日資料受治理封鎖 |',
             '| 策略回測研究 | `/platform/backtest` | 已保存 V2 Run、對帳、成交與帳本 |',
             '| 策略組合分析 | `/platform/portfolio` | 已保存組合與資格原因 |',
             '| 系統排程與驗證中心 | `/platform/system` | 研究排程、工作槽、治理及正式管線唯讀狀態 |', '',
             '管理 POST 依既有 admin 與 CSRF 驗證；未提供有效授權時應回 403，並且不啟動工作。請只從部署環境的既有祕密管理設定 `STOCK_LINBOT_ADMIN_TOKEN`，不得將 token 寫進文件、命令輸出或程式碼。', '',
             '自訂回測、組合、研究管線啟動、取消與重試都使用單一工作槽。日期不得晚於 2025-12-31。研究結果標記 REAL_RESEARCH、RESEARCH_ONLY、NOT_CERTIFIED；無資料、治理阻擋或無法對帳時顯示原因。', '',
             '目前每個入口與管理操作的驗收狀態以 `FEATURE_COMPLETION_MATRIX.md` 及 `PLATFORM_V2_FINAL_ACCEPTANCE_REPORT.md` 為準。']
    return _write('OPERATION_GUIDE.md', lines)


def main():
    names = [reconciliation_report(), model_report(), portfolio_report(), health_report(), matrix(),
             acceptance_report(), blockers_report(), operations_guide()]
    print(json.dumps({'reports': names}), flush=True)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
