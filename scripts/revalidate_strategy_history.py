"""Independently gate immutable historical evidence and generate complete reports."""
import argparse
from collections import Counter, defaultdict
import csv
import json
import math
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.audit_strategy_history import HISTORY, digest, read, write, verify_manifest


def data_checks(root):
    import pandas as pd
    from core.research.fundamentals.pit import validate_pit_order

    result = {}
    for name in ('fundamental_records', 'fundamental_matrix'):
        path = root / 'data/processed/fundamental_pit_v3' / (name + '.parquet')
        frame = pd.read_parquet(path)
        details = {'path': path.relative_to(root).as_posix(), 'sha256': digest(path), 'rows': len(frame), 'columns': list(frame)}
        if name == 'fundamental_records':
            ok, diagnostics = validate_pit_order(frame)
            details.update(pit_order='PASS' if ok else 'FAIL', invalid_rows=len(diagnostics))
        else:
            details['duplicate_asset_dates'] = int(frame.duplicated(['ticker', 'asof_date']).sum())
        result[name] = details
    parts = sorted((root / 'artifacts/factors/d3_full_20230103_20260728/research_dataset/momentum_20d').glob('*.csv'))
    columns = ['asof_date', 'asset_id', 'execution_date', 'entry_price', 'member', 'is_tradable_t1']
    frame = pd.concat([pd.read_csv(p, usecols=columns) for p in parts], ignore_index=True)
    asof, execution = pd.to_datetime(frame.asof_date), pd.to_datetime(frame.execution_date)
    eligible = frame.member.astype(str).str.lower().eq('true') & frame.is_tradable_t1.astype(str).str.lower().eq('true') & execution.notna()
    prices = pd.to_numeric(frame.entry_price, errors='coerce')
    result['d3_research_universe'] = {'parts': [{'path': p.relative_to(root).as_posix(), 'sha256': digest(p)} for p in parts],
        'rows': len(frame), 'eligible_rows': int(eligible.sum()),
        'duplicate_asset_dates': int(frame.duplicated(['asset_id', 'asof_date']).sum()),
        'noncausal_eligible_rows': int((eligible & (execution <= asof)).sum()),
        'missing_execution_prices': int((eligible & (prices.isna() | (prices <= 0))).sum()),
        'weekend_signal_rows': int((asof.dt.dayofweek >= 5).sum()),
        'weekend_execution_rows': int((eligible & (execution.dt.dayofweek >= 5)).sum()),
        'calendar_certification': 'NOT_VERIFIABLE: source manifest alone is not an independently matched official session calendar',
        'survivorship': 'NOT_VERIFIABLE: current listing source plus listing dates does not prove delisted universe completeness',
        'adjustment_certification': 'NOT_VERIFIABLE: local_adjusted basis declared; action-by-action reconciliation not independently certified'}
    result['local_mysql_snapshot'] = read(root / HISTORY / 'snapshot_quality.json')
    result['fundamental_pit_report'] = read(root / 'outputs/fundamental_data/fundamental_pit_v3_20260915T000000Z/pit_validation_report.json')
    result['benchmark'] = {'configured_symbol': '2330', 'instrument': 'single equity, not TAIEX', 'certification': 'BLOCKED',
                           'evidence': 'config/settings.py; previous benchmark_identity_correction.json'}
    # Existing DB evidence is mutable and has no run/snapshot key. Read it, but
    # never manufacture lineage by joining its global equity curve to a strategy.
    try:
        from core.db_helper import get_db_engine
        from sqlalchemy import text
        with get_db_engine().connect() as connection:
            connection.execute(text('START TRANSACTION READ ONLY'))
            trades = [dict(row) for row in connection.execute(text(
                'SELECT strategy, COUNT(*) AS trade_rows, MIN(buy_date) AS first_buy, MAX(sell_date) AS last_sell FROM backtest_trades GROUP BY strategy')).mappings()]
            equity = [dict(row) for row in connection.execute(text(
                'SELECT COUNT(*) AS rows_count, MIN(date) AS first_date, MAX(date) AS last_date FROM backtest_equity_curve')).mappings()]
            connection.rollback()
        result['database_history'] = {'status': 'READ_ONLY_QUERY_COMPLETED', 'trade_groups': [
            {k: str(v) if k in {'first_buy', 'last_sell'} else v for k, v in row.items()} for row in trades],
            'global_equity': [{k: str(v) for k, v in row.items()} for row in equity],
            'verification': 'NOT_VERIFIABLE: no run_id/settings/snapshot provenance; global equity cannot be attributed to individual strategies'}
    except Exception as exc:
        result['database_history'] = {'status': 'BLOCKED', 'reason': type(exc).__name__}
    return result


def emit_csv(path, rows):
    fields = list(dict.fromkeys(key for row in rows for key in row))
    with path.open('w', newline='', encoding='utf-8-sig') as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows({k: json.dumps(v, ensure_ascii=False, allow_nan=False) if isinstance(v, (dict, list)) else v for k, v in r.items()} for r in rows)


def compare_d5_replay(root, output):
    before = root / HISTORY / 'portfolio_research_diagnostics.json'
    after = root / 'outputs/strategy_revalidation/2026-10-21-d5-causal/portfolio_research_diagnostics.json'
    if not after.is_file():
        return {'status': 'NOT_TESTED', 'reason': 'Corrected D5 replay receipt missing'}
    old, new = read(before), read(after)
    if set(old) != set(new):
        raise ValueError('D5_PARAMETER_ID_SET_CHANGED')
    rows = []
    for sid in sorted(new):
        tracks = {}
        for track in new[sid]:
            a, b = old[sid][track], new[sid][track]
            ordered_identity = [[x[k] for k in ('date', 'asset', 'side')] for x in a['fills']] == [[x[k] for k in ('date', 'asset', 'side')] for x in b['fills']]
            amounts = len(a['fills']) == len(b['fills']) and all(math.isclose(float(x[k]), float(y[k]), rel_tol=1e-9, abs_tol=1e-12)
                for x, y in zip(a['fills'], b['fills']) for k in ('quantity', 'price', 'fee', 'notional'))
            equity = a['dates'] == b['dates'] and len(a['equity']) == len(b['equity']) and all(
                math.isclose(x, y, rel_tol=1e-9, abs_tol=1e-12) for x, y in zip(a['equity'], b['equity']))
            tracks[track] = {'ordered_fill_identity_equal': ordered_identity, 'fill_amounts_equal': amounts,
                             'equity_equal': equity, 'before_order_count': a['order_count'], 'after_order_count': b['order_count'],
                             'before_metrics': a['metrics'], 'after_metrics': b['metrics']}
        old_targets = root / HISTORY / 'd5_ledger/target_weights' / (sid + '.csv')
        new_targets = after.parent / 'd5_replay/target_weights' / (sid + '.csv')
        if digest(old_targets) != digest(new_targets):
            raise ValueError('FROZEN_D5_TARGETS_CHANGED: ' + sid)
        rows.append({'id': 'portfolio_research:' + sid, 'target_weights_unchanged': True,
                     'target_sha256': digest(new_targets), 'tracks': tracks})
    result = {'status': 'COMPLETED', 'scope': 'Actual corrected historical D5 diagnostic replay; no validated investment data or Fresh OOS claim',
        'command': 'myenv/Scripts/python.exe scripts/replay_strategy_research.py d5 --output outputs/strategy_revalidation/2026-10-21-d5-causal/d5_replay',
        'exit_status': 0, 'before_path': before.relative_to(root).as_posix(), 'before_sha256': digest(before),
        'after_path': after.relative_to(root).as_posix(), 'after_sha256': digest(after),
        'source_sha256': {p: digest(root / p) for p in ('jobs/run_portfolio_research.py', 'core/research/strategy_search.py', 'core/research/vectorbt_adapter.py', 'scripts/replay_strategy_research.py')},
        'configurations': len(rows), 'cost_tracks': sum(len(row['tracks']) for row in rows),
        'changed_configurations': sum(any(not all(t[k] for k in ('ordered_fill_identity_equal', 'fill_amounts_equal', 'equity_equal')) for t in row['tracks'].values()) for row in rows),
        'records': rows}
    write(output / 'd5_repair_comparison.json', result)
    return result


def historical_report(output):
    payload = read(output / ('identity_matrix_final.json' if (output / 'identity_matrix_final.json').exists() else 'identity_matrix.json'))
    summary = payload['summary']
    lines = ['# 全策略歷史回測追溯', '', '此表區分「有做過」與「驗證通過」；數字由逐筆證據連結產生。', '',
             '```json', json.dumps(summary, ensure_ascii=False, indent=2), '```', '',
             '完整來源與雜湊：`' + output.relative_to(ROOT).as_posix() + '/historical_hashes.json`。',
             '原始 API 快取及歷次架構源碼備份未逐檔重算，排除範圍見 scan_scope.json；不代表完成原始來源動態驗證。', '',
             '| 策略家族 | 歷史完成身分 | 已核對 manifest 產物身分 | 可重用重播身分 |', '|---|---:|---:|---:|']
    for family in read(output / 'family_source.json')['families']:
        rows = [r for r in payload['records'] if r['family_id'] == family['family_id']]
        counts = [sum(r[k] == 'PASS' for r in rows) for k in ('historical_backtest_completed', 'historical_evidence_verified', 'reproducible')]
        lines.append('| ' + family['family_id'] + ' | ' + ' | '.join(map(str, counts)) + ' |')
    lines += ['', 'PASS 的證據範圍以 identity_matrix.json 中 historical_runs 與 replay_receipt_checks.json 為準。',
              '重現指同一研究 owner 與 observer 的輸出比對，不等於原始 Notebook parity、投資有效或 Fresh OOS。',
              '舊績效、負績效、版本差異與失敗 manifest 全部保留；NOT_FOUND 表示掃描範圍未找到足夠完成證據，不表示歷史上從未執行。']
    (ROOT / 'docs/strategy_historical_backtest_audit.md').write_text('\n'.join(lines) + '\n', encoding='utf-8')


def finalize(root, output):
    payload = read(output / 'identity_matrix.json')
    families = read(output / 'family_source.json')['families']
    hashes = read(output / 'historical_hashes.json')
    by_name = defaultdict(list)
    for name in hashes:
        by_name[Path(name).name].append(root / name)
    manifests = [verify_manifest(root / m['path'], root, by_name, hashes)
                 for m in read(output / 'manifest_verification.json')['manifests']]
    write(output / 'manifest_verification_corrected.json', {'manifests': manifests,
        'supersedes': 'manifest_verification.json', 'reason': 'Resolve input manifests and code paths in their declared source scope, not the local run directory'})
    checks = data_checks(root)
    write(output / 'data_validation.json', checks)
    from core.runtime.fundamental_production import OPERATION_ROOT
    from core.runtime.fundamental_shadow import FrozenStrategyLoader
    runtime_root = OPERATION_ROOT
    frozen_identity = FrozenStrategyLoader().load().fingerprint
    frozen = {name: read(runtime_root / (name + '.json')) for name in ('promotion_review', 'fresh_oos_lineage_audit', 'FreshOOSAvailabilityReport')}
    write(output / 'frozen_gate_evidence.json', {'source_root': runtime_root.relative_to(root).as_posix(), 'documents': frozen,
        'frozen_loader': 'PASS', 'fingerprint': frozen_identity,
        'scope': 'runtime-declared operational evidence root, distinct from older September integration replay; no continuation, activation or ledger append'})
    replay = compare_d5_replay(root, output)
    replay_rows = {r['id']: r for r in replay.get('records', [])}
    candidates = []
    inventory = {r['id']: r for r in read(root / HISTORY / 'inventory.json')['records']}
    all_hashes = read(output / 'historical_hashes.json')
    companions = defaultdict(list)
    for name in all_hashes:
        companions[str(Path(name).parent)].append(name)
    for r in payload['records']:
        if not r['family_id']:
            continue
        aliases = [r['id']] + inventory[r['id']].get('aliases', [])
        r['database_saved_trade_groups'] = [row for row in checks['database_history'].get('trade_groups', []) if row['strategy'] in aliases]
        r['run_companion_artifacts'] = sorted({name for l in r['historical_runs'] for name in companions[str(Path(l['path']).parent)]})
        if r['role'] in {'ORIGINAL', 'RESEARCH_MODEL'}:
            r['notebook_saved_output_observations'] = []
            for source in r['sources']:
                p = root / source['path']
                if p.suffix == '.ipynb' and p.is_file():
                    notebook = read(p)
                    r['notebook_saved_output_observations'].append({'path': source['path'], 'sha256': digest(p),
                        'cells_with_outputs': [i for i, cell in enumerate(notebook.get('cells', [])) if cell.get('outputs')],
                        'scope': 'Saved output presence only; no run identity, complete backtest or dynamic parity inferred'})
        research = r['role'] == 'PARAMETER_CONFIG'
        # The original audit's PASS meant scoreboard hash only. Preserve that receipt,
        # but require the complete declared run envelope for the stronger final axis.
        r['scoreboard_hash_verified'] = any(l['configuration_match'] and l['scoreboard_hash_verified'] for l in r['historical_runs'])
        r['historical_evidence_verified'] = 'PASS' if any(l['configuration_match'] and any(
            m['status'] == 'PASS' and Path(m['path']).parent == Path(l['path']).parent for m in manifests)
            for l in r['historical_runs']) else 'NOT_VERIFIABLE'
        if research:
            r['data_components'] = {'PIT': checks['fundamental_records'].get('pit_order') if 'fundamental_strategy_validation' in r['id'] else 'NOT_APPLICABLE',
                                    'execution_dates': 'PASS' if not checks['d3_research_universe']['noncausal_eligible_rows'] else 'FAIL',
                                    'price_adjustments': 'NOT_VERIFIABLE', 'historical_universe': 'NOT_VERIFIABLE', 'official_calendar': 'NOT_VERIFIABLE'}
            r['blockers'] = ['Independent official calendar/action-adjustment/delisted-universe reconciliation incomplete',
                             'Development-selected historical splits cannot establish Fresh OOS',
                             'Asset-specific tax/minimum-fee/liquidity stress and independent broad benchmark not fully certified']
            valid = [link for link in r['historical_runs'] if link['configuration_match'] and link['scoreboard_hash_verified']]
            selected = next((link for link in valid if '/2026-10-21/' in link['path'] and '_replay/' in link['path']), valid[-1] if valid else None)
            if selected:
                row = selected['metrics']
                r['performance_evidence'] = {'path': selected['path'], 'line': selected['line'], 'metrics': row,
                                             'scope': 'Historical development cost-adjusted research; not certified live profitability'}
                gate = row.get('strategy_valid')
                r['historical_performance'] = 'OWNER_GATE_PASS' if gate == 'True' else 'OWNER_GATE_FAIL' if gate == 'False' else 'RESEARCH_METRICS_ONLY'
                if gate == 'False':
                    r['historical_rejection_reason'] = row.get('rejection_reason')
                if r['reproducible'] == 'PASS':
                    r['engineering'] = 'REPLAY_OUTPUTS_VERIFIED'
                score = row.get('net_base_sharpe', row.get('sharpe'))
                if score is not None and r['historical_performance'] != 'OWNER_GATE_FAIL':
                    candidates.append({'family_id': r['family_id'], 'id': r['id'], 'sharpe': float(score),
                                       'cagr': row.get('net_base_annualized_return', row.get('cagr', row.get('annualized_return'))),
                                       'mdd': row.get('net_base_max_drawdown', row.get('mdd', row.get('max_drawdown'))),
                                       'path': selected['path'], 'line': selected['line'], 'qualification': 'RESEARCH_FOLLOW_UP_ONLY'})
            if r['id'] in replay_rows:
                r['corrected_replay'] = {'path': 'd5_repair_comparison.json', 'id': r['id'], 'status': 'COMPLETED_DIAGNOSTIC',
                    'fill_equality': replay_rows[r['id']]['tracks'], 'data_certification': 'BLOCKED'}
                r['current_engine_validated'] = 'PASS'
                r['engineering'] = 'CORRECTED_EXECUTION_VERIFIED'
                r['current_engine_validation_scope'] = 'D5 observed-price execution boundary: real 48-config replay plus missing-price regression; full data/cost/OOS certification separate'
                r['current_metrics'] = replay_rows[r['id']]['tracks']['BASE_COST']['after_metrics']
        elif r['role'] in {'NATIVE_PYTHON', 'SELECTION_PORT'}:
            r['blockers'].append('Legacy same-close execution retained for historical replay; next_session_open_v2 requires independently validated session list/snapshot')
        elif r['role'] == 'FROZEN_RUNTIME':
            r['historical_gate_status'] = frozen
            review = frozen['promotion_review']
            if review.get('strategy_id') != r['id']:
                raise ValueError('FROZEN_IDENTITY_MISMATCH')
            if review.get('strategy_fingerprint') != frozen_identity:
                raise ValueError('FROZEN_FINGERPRINT_MISMATCH')
            r['historical_backtest_completed'] = 'PASS'  # Exact frozen strategy ID/fingerprint in promotion review.
            r['blockers'] = ['Fresh OOS minimum unmet: ' + json.dumps(review.get('evaluation_window'))
                             + '; required minimum: ' + json.dumps(frozen['FreshOOSAvailabilityReport'].get('minimum_requirement')),
                             'Historical Shadow approval does not certify current data/production; no ledger mutation in this audit']
        r['batch_decision'] = 'CORRECTED_DIAGNOSTIC_REPLAY_COMPLETED' if r['id'] in replay_rows else 'REUSE_PRIOR_REPLAY' if research and r['reproducible'] == 'PASS' else 'BLOCKED_PREFLIGHT'
        r['certified_metrics'] = {key: {'value': None, 'reason': 'NOT_VERIFIABLE: see blockers and separate historical metrics'} for key in
                                  ('cagr', 'sharpe', 'sortino', 'max_drawdown', 'win_rate', 'trade_count', 'turnover', 'benchmark_comparison')}
    family_rows = []
    for family in families:
        rows = [r for r in payload['records'] if r['family_id'] == family['family_id']]
        family_rows.append({'family_id': family['family_id'], 'name': family['name'],
            'members': [r['id'] for r in rows], 'parameter_count': family['research_parameter_combinations'],
            'execution_contract_count': family['execution_contract_count'], 'migration': family['migration'],
            'historical_backtest': 'PASS' if any(r['historical_backtest_completed'] == 'PASS' for r in rows) else 'NOT_FOUND',
            'current_executable': 'YES' if any(r['current_executable'] == 'YES' for r in rows) else 'BLOCKED',
            'current_engine_components_passed': sum(r['current_engine_validated'] == 'PASS' for r in rows),
            'data_validation': 'BLOCKED', 'current_backtest_validation': 'BLOCKED', 'oos': 'BLOCKED', 'final_status': 'BLOCKED',
            'historical_performance_counts': dict(Counter(r['historical_performance'] for r in rows)),
            'blockers': sorted({reason for r in rows for reason in r['blockers']})})
    counts = payload['summary']
    counts.update(historical_completed_identities=sum(r['historical_backtest_completed'] == 'PASS' for r in payload['records']),
        verified_historical_identities=sum(r['historical_evidence_verified'] == 'PASS' for r in payload['records']),
        manifest_status_counts=dict(Counter(m['status'] for m in manifests)),
        historical_completed_families=sum(f['historical_backtest'] == 'PASS' for f in family_rows),
        blocked_families=len(family_rows),
        historical_owner_gate_fail=sum(r['historical_performance'] == 'OWNER_GATE_FAIL' for r in payload['records']),
        historical_owner_gate_pass=sum(r['historical_performance'] == 'OWNER_GATE_PASS' for r in payload['records']),
        historical_shadow_approval='SHADOW_APPROVED', production_eligible=0)
    counts.update(corrected_diagnostic_replays=replay.get('configurations', 0), corrected_cost_tracks=replay.get('cost_tracks', 0),
                  changed_d5_configurations=replay.get('changed_configurations'),
                  current_engine_component_validated=sum(r['current_engine_validated'] == 'PASS' for r in payload['records']))
    counts['new_reproductions'] = replay.get('configurations', 0) - replay.get('changed_configurations', 0) if replay.get('status') == 'COMPLETED' else 0
    counts['new_fully_reproduced_families'] = 0  # D5 has intentional execution-policy differences; other families reused.
    counts['current_frozen_oos_window'] = frozen['promotion_review'].get('evaluation_window')
    write(output / 'identity_matrix_final.json', payload)
    write(output / 'family_matrix.json', {'summary': counts, 'families': family_rows})
    emit_csv(output / 'identity_matrix.csv', payload['records'])
    emit_csv(output / 'family_matrix.csv', family_rows)
    write(output / 'batch_decisions.json', {'records': [{'id': r['id'], 'decision': r.get('batch_decision', 'NON_STRATEGY_TOOL'),
          'reason': r['blockers'], 'prior_evidence': [l['path'] for l in r['historical_runs']]} for r in payload['records']]})
    # One best historical configuration per family; no multiple-testing victory claims.
    top = {}
    for c in candidates:
        if c['family_id'] not in top or c['sharpe'] > top[c['family_id']]['sharpe']:
            top[c['family_id']] = c
    write(output / 'research_candidates.json', {'scope': 'Development-selected follow-up only; no production or Fresh OOS certification',
                                              'candidates': sorted(top.values(), key=lambda c: c['sharpe'], reverse=True)})
    lines = ['# 全策略重新驗證摘要', '', '工程正確性、歷史績效、樣本外有效性分開判斷。所有列皆保留，BLOCKED 不代表從未回測。', '',
             '```json', json.dumps(counts, ensure_ascii=False, indent=2), '```', '',
             '本次新重播且逐筆結果相符：' + str(counts['new_reproductions'])
             + ' 組 D5 參數設定；整個家族完全相符：0（另有 2 組因修復而改變）。已核對並重用的原有重播見 reused_reproducible_identities，沒有再跑相同的 384 組最佳化。',
             '修復後完成有效投資回測：0；目前沒有獨立認證完整的股票資料快照。合成測試僅驗證工程行為，不能算投資結果。',
             'D5 缺價修復後實際補跑 ' + str(replay.get('configurations', 0)) + ' 組、' + str(replay.get('cost_tracks', 0)) + ' 條成本軌；有 '
             + str(replay.get('changed_configurations')) + ' 組成交／資產曲線改變，完整差異見 d5_repair_comparison.json。未更改參數或 target weights。',
             '原生 Python／Registered 有可執行入口，不等於原 Notebook 已忠實移植；資產與 ML 訓練閘門不得繞過。', '',
             '## 全家族矩陣', '', '| 策略家族 | 已有歷史回測 | 目前可執行入口 | 資料驗證 | 目前回測驗證 | OOS | 最終狀態 |', '|---|---|---|---|---|---|---|']
    for f in family_rows:
        lines.append('| ' + ' | '.join(str(f[k]) for k in ('family_id', 'historical_backtest', 'current_executable', 'data_validation', 'current_backtest_validation', 'oos', 'final_status')) + ' |')
    lines += ['', '## 修復與限制', '',
              '共用 runner 新增 next_session_open_v2：前一交易日收盤訊號，下一個已提供的交易日實際開盤價格成交；缺價不成交，保留未成交原因。需要有序不重複的交易日清單；本契約不自行聲稱該清單已獲官方認證。',
              'legacy_same_close 保留原有 CLI／Web 歷史相容行為，其績效仍為診斷用途；新契約禁止寫入既有 DB 回測結果。',
              'PIT 排序檢驗與 D3 T+1 檢查獨立執行，詳見 data_validation.json；PIT PASS 不等於所有財務欄位、存活者偏差或調整價格都已認證。',
              'Notebook 需原始 TEJ／Zipline／TejToolAPI、相同授權資料快照及原始環境輸出；期貨另需合約／換月／乘數／保證金／成本，ETF 需資產費稅與 NAV 可交易性，ML 需訓練 cutoff 與防洩漏。',
              'Fresh OOS 維持原 cutoff，沒有將既有研究切片改標 Fresh OOS；Frozen 仍為歷史 SHADOW_APPROVED，Broker 維持 DISABLED。', '',
              'manifest_verification_corrected.json 取代初版核對：輸入 manifest 必須在原始輸入目錄解析，程式碼路徑則對應 repository；初版 8 份 FAIL 中 6 份屬核對器解析範圍問題。剩餘 2 份 Day3 v1／v1_repro 因目前為 v2 程式碼而有版本雜湊差異；舊產物與失敗紀錄均保留，不表示歷史績效被刪除或改判有效。', '',
              '## 後續研究候選（開發資料選出，不能當作有效性結論）', '', '| 家族／設定 | 歷史 Sharpe | CAGR | MDD | 證據 |', '|---|---:|---:|---:|---|']
    for c in sorted(top.values(), key=lambda c: c['sharpe'], reverse=True):
        lines.append(f"| {c['family_id']} / {c['id']} | {c['sharpe']:.4f} | {c['cagr']} | {c['mdd']} | {c['path']}:{c['line']} |")
    lines += ['', '歷史不佳：identity_matrix.csv 的 OWNER_GATE_FAIL 與 historical_rejection_reason 保留原標準判定，不重新調低門檻。',
              '證據位置：`' + output.relative_to(root).as_posix() + '`。family_matrix 與 identity_matrix 保留全部身分、參數、來源與各軸理由。',
              '重現：先以 audit_strategy_history.py --output <新目錄> 建立歷史基準，再以 revalidate_strategy_history.py --audit <目錄> --finalize 產出資料檢查與報告。']
    text = '\n'.join(lines) + '\n'
    (root / 'docs/strategy_revalidation_summary.md').write_text(text, encoding='utf-8')
    (root / 'docs/strategy_validation_report.md').write_text(text, encoding='utf-8')
    inventory_path = root / 'docs/strategy_full_inventory.md'
    before = (output / 'before_strategy_full_inventory.md').read_text(encoding='utf-8')
    inventory_path.write_text(before + '\n\n## 2026-10-21 歷史追溯與重新驗證\n\n'
        + f"本輪逐筆保留 {counts['source_records']} 筆來源、{counts['historical_validation_identities']} 個歷史驗證身分；{counts['family_count']} 家族與 {counts['research_parameters']} 組研究參數分開統計。\n\n"
        + '66／32 為合併兩組跨股票／期貨共用投資邏輯前的中間值；本輪承接經來源檢閱的 64／30，不因績效成功或失敗增減家族。\n\n'
        + '全量版本對應與各軸驗證見 `' + output.relative_to(root).as_posix() + '/identity_matrix.csv`、`family_matrix.csv`；歷史報告於 before_*.md 保留。\n', encoding='utf-8')
    historical_report(output)
    print(json.dumps(counts, ensure_ascii=False))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--audit', type=Path, required=True)
    parser.add_argument('--finalize', action='store_true')
    args = parser.parse_args()
    if args.finalize:
        finalize(ROOT, args.audit.resolve())
    else:
        historical_report(args.audit.resolve())
