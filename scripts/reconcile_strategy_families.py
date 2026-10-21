"""Add reviewed investment families without changing historical validation identities."""
import argparse
import ast
from collections import Counter
import csv
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read(path):
    return json.loads(path.read_text(encoding='utf-8'))


def write(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + '\n', encoding='utf-8')


OWNERS = {
    'strategy_research': 'core/research/strategy_search.py',
    'portfolio_research': 'core/research/portfolio_research.py',
    'fundamental_strategy_validation': 'core/research/fundamental_strategy_validation.py',
}


def reconcile(inventory, validation, review, root=ROOT):
    """Source changes or missing decisions require review, never a guessed count."""
    for path, expected in review['reviewed_source_sha256'].items():
        if sha(root / path) != expected:
            raise ValueError('SOURCE_REVIEW_STALE: ' + path)
    for n in inventory['notebooks']:
        if review['reviewed_source_sha256'].get(n['source']) != n['sha256']:
            raise ValueError('NOTEBOOK_REVIEW_MISSING: ' + n['source'])
    if sha(root / review['d5_pool_evidence']['path']) != review['d5_pool_evidence']['sha256']:
        raise ValueError('RESEARCH_POOL_REVIEW_STALE')
    old = {r['id']: r for r in validation['records']}
    if set(old) != {r['id'] for r in inventory['records']}:
        raise ValueError('HISTORICAL_ID_SET_MISMATCH')
    decisions = review['notebook_decisions']
    expected = {r['id'] for r in inventory['records'] if r['kind'] in {'STRATEGY', 'ANALYSIS_ONLY'}}
    if set(decisions) != expected:
        raise ValueError('UNREVIEWED_NOTEBOOK_IDENTITY')
    source_families = {}
    for decision in decisions.values():
        for source in decision['evidence']:
            source_families.setdefault(Path(source['path']).name, set()).add(decision['family_id'])
    families, nodes = {}, []

    def attach(record, family_id, role, name, logic, owner=None):
        family = families.setdefault(family_id, {'family_id': family_id, 'name': name,
            'investment_logic': logic, 'members': [], 'implementation_contracts': [], 'owner_sources': []})
        family['members'].append(record['id'])
        contract = owner if role == 'PARAMETER_CONFIG' else record['id']
        if contract not in family['implementation_contracts']:
            family['implementation_contracts'].append(contract)
        if owner and owner not in family['owner_sources']:
            family['owner_sources'].append(owner)
        node = {'id': record['id'], 'strategy_id': record.get('strategy_id', record['id']),
            'family_id': family_id, 'role': role, 'historical_kind': record['kind'],
            'asset_class': record.get('asset_class'),
            'historical_counts_as_strategy': record['counts_as_strategy'], 'sources': record['sources'],
            'parameters': record.get('parameters'), 'aliases': record.get('aliases', []),
            'historical_validation_ref': 'validation.json#' + record['id'],
            'historical_backtest': old[record['id']]['backtest'],
            'parameter_fingerprint': record.get('configuration_fingerprint'),
            'classification_note': 'Same family is not execution parity or validation PASS.'}
        if role == 'RESEARCH_MODEL':
            node['new_validation_status'] = 'NOT_TESTED'
            node['classification_note'] = 'Historical ANALYSIS_ONLY preserved; cell2 defines paired positions/net returns. Research-model validation and NAV tradability remain unverified; historical NOT_APPLICABLE is not a new result.'
        nodes.append(node)
        return node

    deferred = []
    for r in inventory['records']:
        kind = r['kind']
        if r['id'] in decisions:
            d = decisions[r['id']]
            if d['reviewed_role'] == 'ANALYSIS_ONLY':
                nodes.append({'id': r['id'], 'family_id': None, 'role': 'ANALYSIS_ONLY', 'historical_kind': kind,
                    'historical_counts_as_strategy': r['counts_as_strategy'], 'sources': r['sources'],
                    'historical_validation_ref': 'validation.json#' + r['id']})
            elif d['reviewed_role'] in {'ORIGINAL', 'RESEARCH_MODEL'}:
                attach(r, d['family_id'], d['reviewed_role'], d['name'], d['investment_logic'])
            else:
                raise ValueError('UNREVIEWED_ROLE: ' + r['id'])
        elif kind == 'REGISTERED':
            if r.get('original_notebook'):
                matches = source_families.get(Path(r['original_notebook']).name, set())
                if len(matches) != 1:
                    raise ValueError('AMBIGUOUS_PORT_FAMILY: ' + r['id'])
                fid = next(iter(matches))
                d = next(d for d in decisions.values() if d['family_id'] == fid)
                node = attach(r, fid, 'SELECTION_PORT', d['name'], d['investment_logic'])
                node['original_source_ids'] = [key for key, original in decisions.items() if any(
                    Path(s['path']).name == Path(r['original_notebook']).name for s in original['evidence'])]
            else:
                if r['id'] not in review['native_decisions']:
                    raise ValueError('NATIVE_REVIEW_MISSING: ' + r['id'])
                attach(r, 'family:native:' + r['id'], 'NATIVE_PYTHON', r['display_name'], review['native_decisions'][r['id']])
        elif kind == 'RESEARCH_SETTING':
            ns, p = r['namespace'], r['parameters']
            owner = OWNERS[ns]
            if ns == 'strategy_research':
                factors = sorted(p['factor_set'].split('+'))
            elif ns == 'fundamental_strategy_validation':
                factors = sorted(ast.literal_eval(p['factor_ids']))
            else:
                factors = review['d5_factor_ids']
            fid = 'family:' + ns + ':' + '+'.join(factors)
            attach(r, fid, 'PARAMETER_CONFIG', ns + ': ' + '+'.join(factors),
                'Fixed signal factor set ' + ', '.join(factors) + '; TopN, rebalance and weighting vary within the family.', owner)
        elif kind == 'FROZEN_RUNTIME':
            deferred.append(r)
        elif kind in {'BENCHMARK', 'SIGNAL_ONLY'}:
            nodes.append({'id': r['id'], 'family_id': None, 'role': kind, 'historical_kind': kind,
                'historical_counts_as_strategy': r['counts_as_strategy'], 'sources': r['sources'],
                'historical_validation_ref': 'validation.json#' + r['id']})
        else:
            raise ValueError('UNREVIEWED_RECORD: ' + r['id'])
    indexed = {n['id']: n for n in nodes}
    for r in deferred:
        candidate = indexed['fundamental_strategy_validation:' + r['research_candidate']]
        family = families[candidate['family_id']]
        node = attach(r, candidate['family_id'], 'FROZEN_RUNTIME', family['name'], family['investment_logic'])
        node['research_candidate_id'] = candidate['id']
        node['classification_note'] = 'Deployment contract of candidate; frozen identity/PIT/OOS/promotion gates remain independent.'
    by_id = {n['id']: n for n in nodes}
    for family in families.values():
        members = [by_id[k] for k in family['members']]
        family['research_parameter_combinations'] = sum(n['role'] == 'PARAMETER_CONFIG' for n in members)
        family['parameter_axes'] = {key: sorted({str(n['parameters'][key]) for n in members
            if n['role'] == 'PARAMETER_CONFIG' and key in n['parameters']})
            for key in ('top_n', 'rebalance_days', 'factor_weighting', 'portfolio_weighting', 'combination_method', 'stock_weighting')
            if any(n['role'] == 'PARAMETER_CONFIG' and key in n['parameters'] for n in members)}
        family['execution_contract_count'] = len(family['implementation_contracts'])
        family['notebook_versions'] = [n['id'] for n in members if n['role'] in {'ORIGINAL', 'RESEARCH_MODEL'}]
        family['python_ports'] = [n['id'] for n in members if n['role'] == 'SELECTION_PORT']
        covered_originals = {key for n in members if n['role'] == 'SELECTION_PORT' for key in n['original_source_ids']}
        family['unported_original_versions'] = [key for key in family['notebook_versions'] if key not in covered_originals]
        family['native_python'] = any(n['role'] in {'NATIVE_PYTHON', 'PARAMETER_CONFIG'} for n in members)
        family['migration'] = ('SELECTION_PORT_ONLY' if family['python_ports'] else
            'NATIVE_PYTHON' if family['native_python'] else 'NOT_PORTED')
        family['historical_variant_backtest_counts'] = dict(Counter(n['historical_backtest']['status'] for n in members))
        family['family_logic_validation'] = 'NOT_TESTED: semantic grouping does not certify investment logic or execution parity'
        family['parameter_optimization_validation'] = 'NOT_CERTIFIED: historical replay is not independent optimization/OOS evidence' if family['research_parameter_combinations'] else 'NOT_APPLICABLE: no materialized research search grid'
    summaries = list(families.values())
    stats = {'strategy_families': len(summaries),
        'ported_families': sum(f['migration'] == 'SELECTION_PORT_ONLY' for f in summaries),
        'native_python_families': sum(f['migration'] == 'NATIVE_PYTHON' for f in summaries),
        'unported_families': sum(f['migration'] == 'NOT_PORTED' for f in summaries),
        'partially_ported_families': sum(bool(f['python_ports']) and bool(f['unported_original_versions']) for f in summaries),
        'unported_versions_within_ported_families': sum(len(f['unported_original_versions']) for f in summaries if f['python_ports']),
        'research_parameter_combinations': sum(f['research_parameter_combinations'] for f in summaries),
        'non_strategy_tools': sum(n['family_id'] is None for n in nodes),
        'tool_categories': dict(Counter(n['role'] for n in nodes if n['family_id'] is None)),
        'historical_validation_identities': sum(r['counts_as_strategy'] for r in inventory['records']),
        'historical_source_records': len(inventory['records']),
        'historical_non_strategy_records': sum(not r['counts_as_strategy'] for r in inventory['records']),
        'family_linked_source_records': sum(n['family_id'] is not None for n in nodes),
        'current_analysis_only_tools': sum(n['role'] == 'ANALYSIS_ONLY' for n in nodes),
        'reclassified_analysis_research_models': sum(n['role'] == 'RESEARCH_MODEL' for n in nodes)}
    assert len(by_id) == len(nodes) == len(old) and set(by_id) == set(old)
    execution_cases = [{'id': n['id'], 'family_id': n['family_id'],
        'lane': 'PARAMETER_OPTIMIZATION' if n['role'] == 'PARAMETER_CONFIG' else 'IMPLEMENTATION_BACKTEST',
        'existing_evidence': n['historical_validation_ref'], 'action': 'REFERENCE_ONLY_THIS_RUN',
        'reuse_rule': 'Reuse only identical identity+source+configuration+snapshot+calendar+cost+seed+split+engine hashes; unknown provenance forbids reuse.'}
        for n in nodes if n['family_id'] is not None]
    plan = {'family_logic_checks': [{'family_id': f['family_id'], 'implementations': f['implementation_contracts']} for f in summaries],
        'port_parity_checks': [{'family_id': n['family_id'], 'port_id': n['id'],
            'original_ids': n['original_source_ids']} for n in nodes if n['role'] == 'SELECTION_PORT'],
        'execution_cases': execution_cases, 'new_backtests_executed': 0,
        'note': 'Logic checks are not an extra performance run. Cost tracks/folds belong inside their identity case. Every implementation still needs parity; one parameter winner never certifies its family.'}
    assert len({c['id'] for c in execution_cases}) == len(execution_cases)
    return {'method': review['method'], 'stats': stats, 'families': sorted(summaries, key=lambda f: f['family_id']),
        'records': nodes, 'validation_plan': plan}


def render(result, output):
    s = result['stats']
    lines = ['# Stock_Linbotv1 策略家族盤點', '',
        'Strategy Family 以投資前提、主要訊號資訊／因子集合及決策機制定義；資產類別不同本身不另計家族。不是數學上唯一分類。家族數不代表已完整實作、忠實移植或可正式交易。', '',
        '| 統計 | 數量 |', '|---|---:|']
    for key, label in [('strategy_families', '獨立投資邏輯家族'), ('ported_families', '已有Notebook選股條件port的家族（非完整parity PASS）'),
        ('native_python_families', '原生Python／研究家族，不需Notebook移植'), ('unported_families', 'Notebook／研究模型尚未移植'),
        ('research_parameter_combinations', '研究網格參數組合'), ('non_strategy_tools', '非策略工具'),
        ('historical_validation_identities', '原有驗證身分（保持不變）'), ('historical_source_records', '原有來源紀錄（保持不變）')]:
        lines.append(f'| {label} | {s[key]} |')
    lines += ['', '移植＋原生Python＋未移植三類互斥相加等於家族總數。原生Python不算「已移植」；port保留universe、long-only、持股及成交差異，沒有宣稱完整移植通過。',
        f"來源對帳：舊{s['historical_validation_identities']}個驗證身分，加{s['reclassified_analysis_research_models']}筆原analysis研究模型，共{s['family_linked_source_records']}筆來源掛入family；其餘{s['non_strategy_tools']}筆為工具，合計仍為{s['historical_source_records']}筆。沒有新增或改名任何歷史ID。",
        '研究配置保留原ID。Day3按實際factor_set分家族，Fundamental按factor_ids分家族；D5同一固定handoff因子池的四種weighting不另算策略。Frozen runtime屬候選家族內的部署版本，獨立gate不變。', '',
        '股票／期貨的迴歸動量與EMA多頭回檔各自合併同家族；訊號正規化、universe、流動性、乘數及下單差異保留。已有股票port不表示期貨版本已移植：Parity只連metadata實際指定的Notebook，不擴散到整個家族。', '',
        f"已有port的{s['ported_families']}個家族中，{s['partially_ported_families']}個另含尚未移植的跨資產原始版本（共{s['unported_versions_within_ported_families']}個版本）；這些不是額外未移植家族，也不能宣稱整個家族已完整移植。", '',
        '歷史非策略8筆中，ETF折溢價Notebook cell2實際含shift(1)雙腿部位、成本及淨報酬，原ANALYSIS_ONLY是按引擎入口造成的誤分類。新層列研究模型，未增加或改寫舊453個驗證ID；仍未證明NAV可交易性、借券或真實成交。其原本NOT_APPLICABLE狀態不是本次驗證成功。',
        '工具分項：' + json.dumps(s['tool_categories'], ensure_ascii=False) + f"；Analysis Only={s['current_analysis_only_tools']}。原分類與所有記錄仍在逐ID對應表。", '',
        '同家族的參數或執行不同，不表示回測結果相同：週期投資五個配置保留；XGBoost四分支改變主要訊號／融合規則分開；期貨範例三種訊號分開；月營收兩Notebook訊號相同合併家族，但成本／排程保留三個執行身分。', '',
        '| Family ID／名稱 | 投資邏輯判定 | 原始／研究模型版本 | Python port | 研究參數組合 | 執行契約版本 | 移植狀態 |', '|---|---|---:|---:|---:|---:|---|']
    for f in result['families']:
        lines.append(f"| `{f['family_id']}`／{f['name']} | {f['investment_logic']} | {len(f['notebook_versions'])} | {len(f['python_ports'])} | {f['research_parameter_combinations']} | {f['execution_contract_count']} | {f['migration']} |")
    lines += ['', '研究參數組合欄只計已物化的搜尋網格，不把未列舉的Notebook常數當零參數。週期投資的五個cash／避險資產／50%或100%政策配置包含在原始執行版本，並不是384組網格的一部分。執行契約版本不把每個TopN或gross/net/stress成本軌跡再當一個策略。', '',
        '## 家族、來源與參數版本對應', '',
        '| Family ID | Notebook來源 | Python／port／runtime身分或owner | 參數軸（實際組合數見上表） |', '|---|---|---|---|']
    by_id = {n['id']: n for n in result['records']}
    for f in result['families']:
        members = [by_id[k] for k in f['members']]
        notebooks = sorted({s['path'] for n in members if n['role'] in {'ORIGINAL', 'RESEARCH_MODEL'} for s in n['sources']})
        implementations = [n['id'] for n in members if n['role'] in {'NATIVE_PYTHON', 'SELECTION_PORT', 'FROZEN_RUNTIME'}] + f['owner_sources']
        axes = '; '.join(k + '=' + '/'.join(values) for k, values in f['parameter_axes'].items()) or '未物化搜尋網格；固定參數及政策版本見來源／CSV'
        lines.append('| `' + f['family_id'] + '` | ' + '<br>'.join(notebooks) + ' | ' + '<br>'.join(implementations) + ' | ' + axes + ' |')
    lines += ['',
        '## 驗證與參數優化分開', '',
        '1. 家族邏輯檢核：每家族一個語義／資料需求檢核，不能取代其各實作的驗證。',
        '2. 跨實作Parity：原始與port逐快照、訊號、持股及成交比較，不因合併家族而省略差異。',
        '3. 實作回測與參數優化分成兩條lane，每個身分只在執行佇列出現一次；成本軌跡及fold為該case子項。已存在證據僅引用，本輪不重跑或重新計算指標。',
        '4. 參數勝出不代表家族通過；完整來源／配置／snapshot／日曆／成本／seed／split／引擎hash一致才可重用，缺任何項不假稱快取命中。', '',
        f"完整逐ID版本對應：`{output.as_posix()}/strategy_family_mapping.csv`；含來源、原分類、family、角色、完整參數、alias與原驗證引用。JSON的families.members列出所有對應ID，validation_plan分開三層驗證及兩個績效lane。", '',
        '歷史驗證矩陣仍見 [原驗證報告](strategy_validation_report.md)。本輪不修改任何既有Parity／Backtest／OOS結論，不啟用策略或更動broker。',
        '分類判讀及來源hash：scripts/strategy_family_review.json；工具：scripts/reconcile_strategy_families.py。', '']
    return '\n'.join(lines)


def run(args):
    args.output.mkdir(parents=True, exist_ok=True)
    inputs = {str(p): sha(p) for p in (args.inventory, args.validation, args.review)}
    baseline_path = args.output / 'baseline.json'
    if baseline_path.exists():
        baseline = read(baseline_path)
        if baseline['inputs'] != inputs:
            raise ValueError('Use a new output directory for changed evidence')
    else:
        baseline = {'inputs': inputs, 'report_backups': {}}
        for name in ('strategy_full_inventory.md', 'strategy_validation_report.md'):
            source = ROOT / 'docs' / name
            target = args.output / ('before_' + name)
            target.write_bytes(source.read_bytes())
            baseline['report_backups'][name] = {'path': target.as_posix(), 'sha256': sha(target)}
        write(baseline_path, baseline)
    result = reconcile(read(args.inventory), read(args.validation), read(args.review))
    result['provenance'] = baseline
    write(args.output / 'strategy_families.json', result)
    with (args.output / 'strategy_family_mapping.csv').open('w', encoding='utf-8-sig', newline='') as handle:
        keys = ['id', 'family_id', 'role', 'historical_kind', 'historical_counts_as_strategy', 'sources', 'parameters', 'aliases', 'historical_validation_ref', 'classification_note']
        writer = csv.DictWriter(handle, fieldnames=keys, extrasaction='ignore')
        writer.writeheader()
        for node in result['records']:
            writer.writerow({k: json.dumps(v, ensure_ascii=False, sort_keys=True) if isinstance(v, (list, dict)) else v for k, v in node.items() if k in keys})
    doc = render(result, args.output)
    (ROOT / 'docs/strategy_family_inventory.md').write_text(doc, encoding='utf-8')
    archive = baseline['report_backups']['strategy_full_inventory.md']
    (ROOT / 'docs/strategy_full_inventory.md').write_text(doc + f"\n歷史盤點原表封存於 `{archive['path']}`，SHA-256 `{archive['sha256']}`。其驗證身分分母不能當作獨立策略數。\n", encoding='utf-8')
    assert all(sha(Path(p)) == expected for p, expected in inputs.items())
    assert all(sha(Path(v['path'])) == v['sha256'] for v in baseline['report_backups'].values())
    print(json.dumps(result['stats'], ensure_ascii=False))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--inventory', type=Path, default=Path('outputs/strategy_validation/2026-10-21/inventory.json'))
    parser.add_argument('--validation', type=Path, default=Path('outputs/strategy_validation/2026-10-21/validation.json'))
    parser.add_argument('--review', type=Path, default=Path('scripts/strategy_family_review.json'))
    parser.add_argument('--output', type=Path, default=Path('outputs/strategy_family_inventory/2026-10-21-final'))
    run(parser.parse_args())
