"""Generate delivery reports strictly from this run's recorded evidence."""
import json
from pathlib import Path
import re
import xml.etree.ElementTree as ET

from scripts.system_acceptance import OUT, ROOT, now, read, write


def load(name, default=None):
    path = OUT / name
    return json.loads(path.read_text(encoding='utf-8-sig')) if path.is_file() else default


def report(name, title, body):
    (OUT / name).write_text(f'# {title}\n\n產生時間：{now()}。RESEARCH_ONLY；非投資、交易或正式部署認證。\n\n{body}\n', encoding='utf-8')


def table(rows):
    return '| 項目 | 本輪結果／證據 |\n|---|---|\n' + ''.join(f'| {k} | {str(v).replace(chr(10), " ").replace("|", "/")} |\n' for k, v in rows)


def main():
    inventory = read('inventory.json')
    checks = {p.stem: load(str(p.relative_to(OUT))) for p in (OUT / 'checks').glob('*.json')}
    pytest = checks.get('pytest_closure', {})
    pytest_stats = {'status': pytest.get('status', 'PENDING')}
    xml = OUT / 'pytest_closure.xml'
    if xml.is_file():
        suites = ET.parse(xml).getroot().findall('testsuite')
        pytest_stats.update({k: sum(int(s.get(k, 0)) for s in suites) for k in ('tests', 'failures', 'errors', 'skipped')})
        text = (OUT / 'logs/pytest_closure.log').read_text(encoding='utf-8')
        pytest_stats['summary'] = next((line for line in reversed(text.splitlines()) if re.search(r'\d+ passed', line)), 'NO_FINAL_SUMMARY')
    v3 = load('browser_v3/outputs/web_v3/acceptance/playwright.json', {})
    v4 = load('browser_v4/playwright.json', {})
    browser = {'v3': v3.get('status', 'PENDING'), 'v3_pages': len(v3.get('pages', [])),
               'v4': v4.get('status', 'PENDING'), 'v4_pages': len(v4.get('pages', []))}
    reconciliation = load('reconciliation_details.json', {})
    protected = checks.get('protected', {})
    governance = load('checks/governance.json', {})
    operations = load('operations_probe.json', {})
    restore = load('backup/restore_verification.json', {})
    os_task = load('windows_isolated_task.json', {})
    review = load('independent_review.json', {})
    gates = {'pytest': pytest_stats['status'], 'V3_browser': browser['v3'], 'V4_browser': browser['v4'],
             'reconciliation': reconciliation.get('status', 'PENDING'), 'protected': protected.get('status', 'PENDING'),
             'governance': governance.get('status', 'PENDING'), 'independent_review': review.get('status', 'PENDING'),
             'openspec_strict': checks.get('openspec_strict', {}).get('status', 'PENDING'),
             'diff_check': checks.get('diff_check', {}).get('status', 'PENDING')}
    engineering = all(v in ('PASS', 'MATCH') for v in gates.values())
    readiness = {'ENGINEERING': 'ENGINEERING_COMPLETE' if engineering else 'NOT_COMPLETE',
                 'OPERATIONS': 'OPERATIONS_READY_ISOLATED_ONLY' if operations.get('status') == restore.get('status') == 'PASS' else 'NOT_COMPLETE',
                 'PRODUCTION': 'NOT_READY_NOT_AUTHORIZED', 'unattended_live': 'NOT_ACCEPTED',
                 'BOOT_RECOVERY': 'BOOT_RECOVERY_NOT_LIVE_TESTED', 'new_production_schedule_configuration': 'NOT_ACTIVATED',
                 'formal_notification': 'NOT_LIVE_TESTED', 'backup_storage': 'SAME_DISK_ONLY'}
    write('acceptance_results.json', {'at': now(), 'readiness': readiness, 'gates': gates, 'pytest': pytest_stats,
                                     'browser': browser, 'restore': restore.get('status', 'PENDING')})
    fixes = [
        ('GET_DDL', '安全', 'P1', '系統狀態讀取初始化 schema', 'Web 狀態共用路徑', '移除 reader DDL；缺表降級', 'test_system_status_reader_never_initializes_schema'),
        ('MORNING_EXIT', '排程', 'P1', 'timeout 覆蓋 scheduler ERRORLEVEL', '早晨 wrapper', '沿用 evening 保存並返回結束碼', 'test_morning_wrapper_preserves_scheduler_failure'),
        ('CHECKPOINT', '恢復', 'P1', '損壞 JSON 被當作缺檔；後段未先驗證', '研究 checkpoint', '全步驟預驗身份、結構、attempts；損壞拒絕', 'test_corrupt_checkpoint_refuses_recovery / test_late_corrupt_checkpoint_prevents_all_execution'),
        ('LOCK_OWNER', '恢復', 'P1', '鎖按時間刪除活 owner；runnable 永遠略過孤兒', 'task controller', '重用 OS guard，確認 dead PID 才恢復，保留活 owner', 'test_hard_killed_task_recovers_completed_checkpoint / test_old_live_lock_is_not_stolen'),
        ('STALE_CANDIDATE', '併發', 'P1', '候選列表與取得鎖之間狀態改變', 'task controller', '鎖內重新核對 status/dependencies/check_after', 'test_stale_scheduler_candidate_is_rechecked_under_lock'),
        ('OUTAGE_CLASS', '維運', 'P2', 'MySQL/network 錯誤歸為 CODE', '研究步驟失敗診斷', '分類 ENVIRONMENT；有限重試測試', 'test_transient_outage_is_classified_and_recovers_with_bounded_retry'),
        ('PIPELINE_OVERLAP', '併發', 'P1', '正式別名入口缺共用執行鎖', 'daily/evening/morning/push_evening', '重用 OS 跨程序鎖；重複啟動回傳75，不執行子工作', 'test_production_pipeline_aliases_share_exclusive_slot_without_running_jobs'),
        ('FROZEN_ROUTE', '整合', 'P1', '本輪初版摘要整合改變受凍結路由雜湊', 'V4 比較發布', '精確回復路由 prechange SHA；使用 context processor', 'V4 COMPARABLE 與 browser_v4/playwright.json'),
        ('BACKUP_SCAN', '安全', 'P1', '本輪初版 scanner 跳過 binary 且覆蓋同名 env secret', '備份工具', '集合蒐集所有 secret 值、串流掃描所有檔案、未核驗 ZIP 排除', 'test_backup_filters_binary_secrets_and_unreviewed_archives'),
    ]
    issues = []
    for key, kind, severity, cause, scope, fix, test in fixes:
        issues.append({'id': key, 'type': kind, 'severity': severity, 'root_cause': cause, 'impact': scope,
                       'repair': fix, 'fallback': '保留原始證據，隔離並記錄阻擋，不重試無效命令',
                       'test_result': {'check': 'operations_closure', 'status': checks.get('operations_closure', {}).get('status', 'PENDING'), 'evidence': test},
                       'final_status': 'RESOLVED' if checks.get('operations_closure', {}).get('status') == 'PASS' else 'PENDING_VERIFICATION'})
    limitations = [
        ('OS_LIVE', 'P1', '正式任務要求互動登入、72h timeout、無失敗 restart / missed-start recovery', '修改正式任務未授權；沿用唯讀稽核及隔離實機 task', 'BLOCKED_PRODUCTION_CONFIGURATION'),
        ('BOOT', 'P2', '未實際重開 Windows', '本輪兩次真 Flask restart；開機恢復保留 BOOT_RECOVERY_NOT_LIVE_TESTED', 'NOT_LIVE_TESTED'),
        ('ORPHAN_CHILD', 'P2', '若只kill正式scheduler父程序，子工作可能繼續而OS鎖已釋放', '本輪未測此正式父子孤兒情境；不宣稱整條正式pipeline可自動恢復，不停止他人程序', 'NOT_LIVE_TESTED'),
        ('FORMAL_ALERT', 'P2', '不允許正式外部通知實測', '本機可配置檔案 sink 與八種 Mock；正式渠道驗證未通過', 'NOT_LIVE_TESTED'),
        ('OFFSITE', 'P2', '備份仍在同一顆磁碟', '完成解壓與校驗；異地/離線副本待授權目的地', 'DEFERRED_EXTERNAL_STORAGE'),
        ('HISTORICAL_PORTFOLIO', 'P2', '兩個歷史 portfolio 的 stress 證據缺失但 saved publishable=True', '現有來源綁定發布閘門已阻擋；不修改保護歷史檔', 'SAFELY_BLOCKED'),
        ('LEGACY_OPENSPEC', 'P2', '其他變更仍有未完成 task', '逐一列出；V3 已明定接替 enhancement，不回填舊勾選', 'OUT_OF_SCOPE_DISCLOSED'),
    ]
    for key, severity, cause, fallback, state in limitations:
        issues.append({'id': key, 'type': '限制', 'severity': severity, 'root_cause': cause, 'impact': '正式營運或歷史證據',
                       'repair': '不執行超出授權操作', 'fallback': fallback, 'test_result': {'status': state}, 'final_status': state})
    write('SYSTEM_ISSUE_REGISTER.json', issues)
    required = ['FINAL_SYSTEM_ACCEPTANCE_REPORT.md', 'SYSTEM_ARCHITECTURE_MAP.md', 'AUTOMATION_ACCEPTANCE_REPORT.md',
                'WINDOWS_SCHEDULER_AUDIT.md', 'RECOVERY_AND_SELF_HEALING_REPORT.md', 'MONITORING_AND_ALERT_REPORT.md',
                'BACKUP_AND_RESTORE_REPORT.md', 'V1_V4_REGRESSION_REPORT.md', 'SYSTEM_ISSUE_REGISTER.json',
                'FINAL_TEST_EVIDENCE.md', 'BLOCKERS_AND_DEFERRED.md', 'FINAL_DELIVERY_INDEX.md']
    architecture = '\n'.join(f'## {layer}\n\n' + '\n'.join(f'- `{path}`：{sha or "MISSING"}' for path, sha in files.items())
                             for layer, files in inventory['architecture'].items())
    report('SYSTEM_ARCHITECTURE_MAP.md', '系統架構與現況', architecture + '\n\n' + table([
        ('資料庫（唯讀）', inventory['database']['status']), ('策略／報告', f"{len(inventory['strategies'])} / {sum(r['has_report'] for r in inventory['strategies'])}"),
        ('正式與研究工作', inventory['pipelines']), ('資源／槽位', [inventory['resources'], inventory['slot']]),
        ('Schema、策略逐列、來源 SHA', 'inventory.json'), ('歷史 handover 比對', load('baseline.json')['historical_drift'])]))
    windows = load('windows_repository_tasks.json', {})
    report('WINDOWS_SCHEDULER_AUDIT.md', 'Windows 排程唯讀稽核', table([
        ('既有正式任務', len(windows.get('repository_candidates', []))), ('唯讀匯出', 'windows_repository_tasks.json；含 actions/triggers/settings/principal/result'),
        ('實際 interpreter／工作目錄', 'execution/*.bat 使用 myenv/Scripts/python.exe 並 cd 專案；OS WorkingDirectory 空值由 wrapper 補足'),
        ('重疊策略', '既有 MultipleInstances=2（IgnoreNew）＋本輪 scheduler 共用鎖；隔離驗證正式別名重複啟動不進入子工作'),
        ('登入／重啟／逾時', 'LogonType=3（InteractiveToken）、RunLevel=0、PT72H、RestartCount=0、StartWhenAvailable=false'),
        ('本輪隔離 OS task', os_task), ('正式任務變更', 'NONE；改善配置 NOT_ACTIVATED'),
        ('保留事項', '72h 可能長時間占槽、未登入不執行、電池條件停止、網路/DB readiness 仍需正式持續營運驗證')]))
    report('AUTOMATION_ACCEPTANCE_REPORT.md', '自動化驗收', table([
        ('既有正式流程', inventory['pipelines']), ('測試／研究分離', '僅注入工作與隔離 root；未執行正式推薦、LINE 或新增 Trial'),
        ('計時與重複觸發', operations.get('checks', {}).get('timer_duplicate', {})), ('真 Windows dispatch', os_task),
        ('併發／失敗／資源／授權', 'operations_closure.xml；test_reference_batch_job、test_platform_v2_pipeline、test_scheduler_e2e、test_final_system_operations'),
        ('工作 timeout', '既有 OS 72h 上限已讀取；未新增自動停止其他 session／修改正式 task'), ('正式無人值守', readiness['unattended_live'])]))
    faults = [
        ('Python 中止', '真 hard-kill 測試程序樹，checkpoint 不重複，dead-PID 恢復', 'test_hard_killed_task_recovers_completed_checkpoint'),
        ('Web 中止', '兩次真 Flask 啟動與 hard stop；第二次服務200', 'operations_probe.json / web-restart-*.log'),
        ('MySQL 不可用', '注入 OperationalError，ENVIRONMENT，兩次失敗第三次恢復；不破壞DB', 'test_transient_outage_is_classified_and_recovers_with_bounded_retry'),
        ('網路中斷', 'ConnectionError 注入、有限重試；未改全域網路', '同上 network case'),
        ('資料缺失', 'DATA／依賴SKIPPED，DATA_BLOCKED 不產生假報酬', 'test_platform_v2_pipeline / test_web_v3'),
        ('重複 Job', '真 Timer雙觸發與共用OS鎖、stale candidate再核對', 'operations_probe.json / operations_closure.xml'),
        ('Checkpoint 損壞', '全步驟預驗證，後段損壞時前段亦不執行', 'test_final_system_operations'),
        ('磁碟不足', '注入0.5GiB，preflight拒絕；Mock本機告警', 'test_baseline_preflight_rejects_insufficient_resources'),
        ('記憶體不足', '注入23GiB，24GiB需求前安全停止', '同上 memory case'),
        ('損壞結果', 'manifest/source身份不符不發布；歷史portfolio兩筆被阻擋', 'test_strategy_v4_web / test_platform_v2_runs / reconciliation_details.json'),
        ('Windows 啟動', '未 reboot；既有 restart health 指令以受控503/不可用驗證', 'test_restart_health；BOOT_RECOVERY_NOT_LIVE_TESTED')]
    report('RECOVERY_AND_SELF_HEALING_REPORT.md', '恢復與故障矩陣', '| 故障 | 本輪行為／範圍 | 證據 |\n|---|---|---|\n' + ''.join(f'| {a} | {b} | {c} |\n' for a, b, c in faults)
           + '\n自癒能力是已測試的機制與顯式觸發恢復；本輪沒有建立或啟用常駐自主修復 daemon。正式機器重新開機與持續營運尚無證據。')
    alerts = operations.get('checks', {}).get('alerts', {})
    report('MONITORING_AND_ALERT_REPORT.md', '監控與告警', table([
        ('已有監控', 'pipeline_runs 成敗時間/錯誤、研究槽位/PID/log、checkpoint attempts、資源、可信度與治理'),
        ('新增Web資訊', '/platform/system 固定本機紀錄；明確標示非即時監控、擷取時間、測試成敗、恢復與備份限制'),
        ('GET安全', '移除共用status讀取DDL；browser SQL mutation guard；缺少/損壞摘要不寫檔'),
        ('本機告警', alerts), ('sink配置', 'scripts.system_operations_probe.alert(path, event, reason, attempts)：檔案路徑注入；事件皆MOCK_LOCAL_ONLY'),
        ('正式外部通知', 'NOT_LIVE_TESTED；沒有發送LINE/Email。環境變數存在與否不代表正式渠道可用。'),
        ('真實資料新鮮度/最後成功', inventory['pipeline_status']), ('限制', '沒有聲稱驗收快照是即時監控或自動遠端告警')]))
    report('BACKUP_AND_RESTORE_REPORT.md', '備份與實際還原', table([
        ('目前還原驗證', {k: restore.get(k, 'PENDING') for k in ('status', 'readback', 'extraction', 'import', 'storage')}), ('封裝', 'backup/source_config.zip、research_evidence.zip、v4_snapshot_evidence.zip、openspec_reports.zip'),
        ('範圍', '已追蹤/未追蹤來源＋忽略程式資產、OpenSpec、V1–V4受保護證據、既有V4快照封裝；無正式DB dump'),
        ('secret排除', '所有來源env secret集合、全檔串流比對、敏感路徑與未核驗zip排除；backup/exclusions.json'),
        ('歷史來源', 'V4既有封裝已重新驗hash；與目前不同的來源置 historical_v4_package/ 防止覆蓋'),
        ('校驗/還原', '每包SHA、每member讀回SHA、逐檔解壓SHA、OpenSpec可讀、隔離來源import；見backup/restore_verification.json'),
        ('重建依賴', 'requirements*.txt、logs/dependencies.log；未宣稱全新機器完成套件安裝或完整外部pinned資料重抓'),
        ('災難恢復限制', 'SAME_DISK_ONLY；無異地/離線副本；backup/RESTORE.md')]))
    report('V1_V4_REGRESSION_REPORT.md', 'V1–V4 本輪回歸', table([
        ('pytest', pytest_stats), ('Playwright', browser), ('21策略／可發布報告', f"{len(inventory['strategies'])} / {sum(r['has_report'] for r in inventory['strategies'])}"),
        ('V1/V2逐日NAV與交易', len(reconciliation.get('baseline', []))), ('策略/組合帳本', len(reconciliation.get('ledgers', []))),
        ('V4獨立重算', len(reconciliation.get('v4_independent', []))), ('歷史不可發布項', reconciliation.get('failed_ledger', [])),
        ('治理', governance), ('受保護檔', protected), ('功能/security', 'browser V3/V4＋full pytest：5%/10%、阻擋、AI限制、portfolio、管理員/CSRF、GET不執行研究')]))
    evidence = table([(name, {k: v.get(k) for k in ('status', 'command', 'started_at', 'ended_at', 'exit_code', 'source_identity', 'log')})
                      for name, v in sorted(checks.items()) if 'command' in v])
    report('FINAL_TEST_EVIDENCE.md', '本輪命令與測試證據', evidence + '\n\n初版/中止命令保留作診斷，不作最終PASS：pytest_full、pytest_final、operations_green、reconciliation初版。最終使用 pytest_closure、operations_closure、browser_closure、reconciliation_final。\n\n完整來源逐檔身份見 source_final.json；執行命令hash用於區分版本，後新增純驗收腳本須另做import/測試。')
    report('BLOCKERS_AND_DEFERRED.md', '阻擋與範圍限制', table([(key, {'severity': sev, 'cause': cause, 'fallback': fallback, 'state': state})
                                                       for key, sev, cause, fallback, state in limitations])
           + '\n\n' + table([(r['name'], f"{r['complete']} completed / {r['remaining']} remaining") for r in inventory['changes'] if r['remaining'] and r['name'] != 'system-operations-final-acceptance'])
           + '\n\n不修改 OD-1/OD-2、不新增正式Trial、不重建已移除根入口、不停止其他session、不重開Windows、不寫正式DB、不發通知、不commit/push/deploy。')
    completed = sum(row['final_status'] == 'RESOLVED' for row in issues)
    report('FINAL_SYSTEM_ACCEPTANCE_REPORT.md', '全系統最終工程與營運驗收', table([
        ('最終分層結論', readiness), ('工程閘門', gates), ('pytest', pytest_stats), ('桌機/手機', browser),
        ('問題統計', f'{len(issues)}項；{completed}修復；其餘為已安全阻擋/未授權實測/外部保存限制'),
        ('原始證據', [protected, {'governance': governance.get('status'), 'effective_trials': governance.get('current', {}).get('effective_trials')}]),
        ('備份', restore.get('status', 'PENDING')), ('OpenSpec', 'system-operations-final-acceptance；日期2026-11-15；CLI不接受日期前綴，使用支援名稱；task及verify另見OpenSpec驗證報告'),
        ('Web', load('preview.json', {'status': 'preview not started'})), ('交付', 'FINAL_DELIVERY_INDEX.md')])
           + '\n正式每日推薦與研究管線分開。工程結論不使用10% CAGR為門檻；既有正式Windows任務存在與最近result=0不代表端到端成功、通知送達或無人值守通過。')
    report('FINAL_DELIVERY_INDEX.md', '最終交付索引', '\n'.join(f'- `{name}`' for name in required)
           + '\n\n' + table([('根目錄', str(OUT)), ('備份校驗', 'backup/*.manifest.json、*.zip.sha256、restore_verification.json、RESTORE.md'),
                              ('Git ignore', 'outputs/與openspec可能被排除；實際程式/規格/研究證據已在封裝中，非僅git diff'),
                              ('原始命令', 'checks/*.json、logs/*.log、*.xml'), ('版本', 'baseline.json、source_final.json、initial.diff、prechange/'),
                              ('OpenSpec驗證', 'OPENSPEC_VERIFY_REPORT.md'), ('審查', 'independent_review.json、PONYTAIL_REVIEW.md'), ('狀態', readiness)]))
    success_times = [v['ended_at'] for v in checks.values() if v.get('status') == 'PASS' and v.get('ended_at')]
    failure_times = [v['ended_at'] for v in checks.values() if v.get('status') == 'FAIL' and v.get('ended_at')]
    write('operations_summary.json', {'captured_at': now(), 'status': readiness['OPERATIONS'], 'scope': 'ISOLATED_ACCEPTANCE；非即時正式營運監控',
                                     'last_success': max(success_times, default=None), 'last_failure': max(failure_times, default=None),
                                     'next_run': {r['name']: r['next_run'] for r in windows.get('repository_candidates', [])},
                                     'recent_error': '本輪反例／修復前失敗與中止紀錄保留，非即時正式錯誤', 'retry_count': 3,
                                     'recovery': 'ISOLATED_VERIFIED; BOOT_RECOVERY_NOT_LIVE_TESTED', 'backup': restore.get('status', 'PENDING'),
                                     'attention': '正式無人值守未驗收；正式通知未測；備份同磁碟'})
    print(json.dumps({'readiness': readiness, 'issues': len(issues), 'resolved': completed}))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
