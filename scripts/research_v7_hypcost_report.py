"""Offline V7 paired comparison; never admits a trial or opens OOS prices."""
import csv
import json
import math
from pathlib import Path
import sys

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from core.research.attribution import run_attribution
from core.research.research_screen import screen
from scripts import research_mode_report as report, research_v7_hypcost as v7, research_workflow as workflow, track_b_stats as stats

OUT = v7.OUTPUT
ARCHIVE = ROOT / 'outputs/research_mode/day3_runs_v6'


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def safe(value, reason='UNDEFINED_METRIC'):
    if value is None or isinstance(value, (float, np.floating)) and not math.isfinite(value):
        return {'value': None, 'reason': reason}
    return {'value': float(value), 'reason': None}


def metrics(returns, benchmark):
    equity = (1 + returns).cumprod()
    drawdown = equity / equity.cummax() - 1
    duration = longest = 0
    for underwater in drawdown < 0:
        duration = duration + 1 if underwater else 0
        longest = max(longest, duration)
    downside = np.minimum(returns.to_numpy(), 0)
    downside_deviation = float(np.sqrt(np.mean(downside ** 2)))
    aligned = pd.concat([returns.rename('strategy'), benchmark.rename('benchmark')], axis=1).dropna()
    regression = run_attribution('diagnostic', aligned.strategy, aligned.benchmark) if len(aligned) >= 10 and aligned.benchmark.std() > 0 else None
    return {'total_return': safe(equity.iloc[-1] - 1 if len(equity) else None),
            'excess_total_return': safe((equity.iloc[-1] - 1) - ((1 + aligned.benchmark).prod() - 1) if len(aligned) == len(returns) and len(returns) else None),
            'sharpe': safe(stats.sharpe(returns)),
            'sortino': safe(float(returns.mean() / downside_deviation * math.sqrt(252)) if downside_deviation > 0 else None, 'ZERO_DOWNSIDE_DEVIATION'),
            'max_drawdown': safe(drawdown.min() if len(drawdown) else None), 'max_drawdown_duration_sessions': longest if len(drawdown) else None,
            'annual_volatility': safe(returns.std(ddof=1) * math.sqrt(252) if len(returns) > 1 else None),
            'best_5pct_day_share': safe(stats.best_days_share(returns)),
            'alpha_annual_hac': safe(regression['alpha'] * 252 if regression else None),
            'alpha_p_hac': safe(regression['alpha_p_hac'] if regression else None),
            'beta_hac': safe(regression['beta'] if regression else None),
            'annual_total_return': {str(year): safe((1 + part).prod() - 1) for year, part in returns.groupby(returns.index.year)}}


def load_result(folder):
    manifest = read(folder / 'manifest.json')
    if manifest['status'] != 'COMPUTABLE':
        return manifest, None
    for name, digest in manifest['output_sha256'].items():
        if workflow.sha256(folder / name) != digest:
            raise ValueError(f'OUTPUT_HASH_MISMATCH:{folder}:{name}')
    if manifest.get('config', {}).get('rank_buffer_multiple') is not None:
        target_hashes = manifest.get('track_targets_sha256', {})
        if set(target_hashes) != {'base', 'stress'}:
            raise ValueError(f'TRACK_TARGET_HASH_MISSING:{folder}')
        for name, digest in target_hashes.items():
            if workflow.sha256(folder / f'targets_{name}.csv') != digest:
                raise ValueError(f'TRACK_TARGET_HASH_MISMATCH:{folder}:{name}')
    tracks = {name: read(folder / f'{name}.json') for name in ('base', 'stress')}
    if any(track['reconciliation']['status'] != 'PASS' for track in tracks.values()):
        raise ValueError(f'RECONCILIATION_FAILED:{folder}')
    return manifest, tracks


def verify_proxy_source(source, manifests):
    digest = workflow.sha256(source)
    if any(manifest['inputs_sha256']['bars'] != digest for manifest in manifests):
        raise ValueError('PROXY_SOURCE_HASH_MISMATCH')
    return digest


def trial_variance(benchmark, additions):
    values = []
    for folder in (ARCHIVE, ROOT / 'outputs/research_mode/d5_runs_v5'):
        for path in sorted(p for p in folder.iterdir() if p.is_dir() and p.name.isdigit()):
            manifest_path = path / 'manifest.json'
            if not manifest_path.is_file():
                attempts = sorted(path.glob('attempt_*/manifest.json'))
                if not attempts:
                    continue
                manifest_path = attempts[-1]
                path = manifest_path.parent
            record = read(manifest_path)
            if record['status'] != 'COMPUTABLE':
                continue
            stress_path = path / 'stress.json'
            if workflow.sha256(stress_path) != record['output_sha256']['stress.json']:
                raise ValueError(f'OUTPUT_HASH_MISMATCH:{stress_path}')
            returns, _ = report.returns_of(read(stress_path), record['config']['initial_cash'])
            aligned = pd.concat([returns, benchmark], axis=1).dropna()
            value = stats.sharpe(aligned.iloc[:, 0] - aligned.iloc[:, 1], periods=1)
            if math.isfinite(value):
                values.append(value)
    for returns in additions:
        aligned = pd.concat([returns, benchmark], axis=1).dropna()
        value = stats.sharpe(aligned.iloc[:, 0] - aligned.iloc[:, 1], periods=1)
        if math.isfinite(value):
            values.append(value)
    if len(values) < 2:
        raise ValueError('UNDEFINED_TRIAL_SHARPE_VARIANCE')
    return float(np.var(values, ddof=1))


def run():
    source = ROOT / 'outputs/research_mode/prepared_v6/bars.json'
    items = []
    for family, index, n in ((v7.FAMILIES[0], 289, 10), (v7.FAMILIES[1], 129, 30)):
        old, old_tracks = load_result(ARCHIVE / str(index))
        new, new_tracks = load_result(OUT / 'experiments' / family)
        items.append({'family': family, 'index': index, 'n': n, 'old': old, 'old_tracks': old_tracks, 'new': new, 'new_tracks': new_tracks})
    source_sha = verify_proxy_source(source, [item[name] for item in items for name in ('old', 'new')])
    benchmark = report.benchmark_returns(read(source)['bars'])
    if workflow.sha256(source) != source_sha:
        raise ValueError('PROXY_SOURCE_CHANGED_DURING_READ')
    regime = stats.regimes((1 + benchmark).cumprod())
    additions = [report.returns_of(item['new_tracks']['stress'], item['new']['config']['initial_cash'])[0]
                 for item in items if item['new_tracks']]
    variance = trial_variance(benchmark, additions)
    evidence = read(OUT / 'hypcost_experiments.json')
    evidence.update(registrations=read(OUT / 'registration_draft.json'), replay=read(OUT / 'replay_comparison.json'),
                    research_trials=workflow.research_trials(), formal_trials=workflow.effective_trials(),
                    dsr_trials=max(workflow.research_trials(), workflow.effective_trials()), trial_sharpe_variance=variance,
                    proxy_source_sha256=source_sha, experiments=[])
    ledger = {row['experiment_id']: row for row in workflow._trial_rows(workflow.RESEARCH_MODE_LEDGER)}
    for item in items:
        family, n, old, old_tracks, new, new_tracks = (item[k] for k in ('family', 'n', 'old', 'old_tracks', 'new', 'new_tracks'))
        old_record = {**old, '_tracks': old_tracks}
        old_result = report.build_result(old_record, benchmark, regime)
        old_returns = {name: report.returns_of(old_tracks[name], old['config']['initial_cash'])[0] for name in ('base', 'stress')}
        proxy = benchmark.reindex(old_returns['stress'].index)
        row = {'family': family, 'status': new['status'], 'old': {}, 'new': None,
               'proxy': metrics(proxy.dropna(), proxy.dropna()), 'l1': 'NOT_COMPUTABLE', 'l2': 'NOT_COMPUTABLE',
               'l3': {'status': 'NOT_COMPUTABLE', 'reason': new.get('reason')}, 'new_manifest': new,
               'old_l3': screen(old_result, trial_sharpe_variance=variance),
               'ledger_sha256': ledger[new['experiment_id']]['sha256'], 'reason': new.get('reason')}
        row['proxy']['turnover'] = {'value': None, 'reason': 'PROXY_NOT_TRADED'}
        row['proxy']['top5_asset_profit_share'] = {'value': None, 'reason': 'PROXY_NOT_TRADED'}
        for track in ('base', 'stress'):
            row['old'][track] = metrics(old_returns[track], benchmark)
            row['old'][track]['turnover'] = safe(old_result[track]['turnover'])
        row['old']['top5_asset_profit_share'] = safe(old_result['top5_assets_profit_share'])
        if new_tracks:
            new_result = report.build_result({**new, '_tracks': new_tracks}, benchmark, regime)
            new_returns = {name: report.returns_of(new_tracks[name], new['config']['initial_cash'])[0] for name in ('base', 'stress')}
            row['l3'] = screen(new_result, trial_sharpe_variance=variance)
            row['new'] = {track: metrics(new_returns[track], benchmark) for track in ('base', 'stress')}
            for track in ('base', 'stress'):
                row['new'][track]['turnover'] = safe(new_result[track]['turnover'])
            row['new']['top5_asset_profit_share'] = safe(new_result['top5_assets_profit_share'])
            if not old_returns['stress'].index.equals(new_returns['stress'].index):
                row['reason'] = 'DATE_MISMATCH'
            elif (benchmark.reindex(old_returns['stress'].index).isna().any()
                  or benchmark.reindex(new_returns['stress'].index).isna().any()
                  or not benchmark.reindex(old_returns['stress'].index).equals(benchmark.reindex(new_returns['stress'].index))
                  or old_result['benchmark_total_return'] != new_result['benchmark_total_return']):
                row['reason'] = 'PROXY_MISMATCH'
            else:
                old_targets = pd.read_csv(ARCHIVE / str(item['index']) / 'targets.csv', dtype={'asset_id': str})
                new_targets = pd.read_csv(OUT / 'experiments' / family / 'targets_stress.csv', dtype={'asset_id': str})
                try:
                    overlap = v7.overlap(old_targets, new_targets, n)
                except ValueError as exc:
                    row['reason'] = str(exc)
                else:
                    row['overlap'] = overlap
                    paired = v7.paired_bootstrap(old_returns['stress'], new_returns['stress'])
                    row['paired_bootstrap'] = paired
                    if paired['status'] == 'COMPUTABLE':
                        checks = {'turnover': new_result['stress']['turnover'] <= old_result['stress']['turnover'] * 2 / 3,
                                  'return': new_result['stress']['total_return'] >= old_result['stress']['total_return'], 'overlap': overlap >= .70}
                        row['l1_checks'] = checks
                        row['l1'] = 'PASS' if all(checks.values()) else 'FAIL'
                        row['l2'] = 'PASS' if row['l1'] == 'PASS' and paired['intervals']['0.95']['lower'] > 0 else 'FAIL'
                        row['difference_sharpe'] = safe(stats.sharpe(new_returns['stress'] - old_returns['stress']))
                        row['sharpe_difference'] = safe(stats.sharpe(new_returns['stress']) - stats.sharpe(old_returns['stress']))
                    else:
                        row['reason'] = paired['reason']
        evidence['experiments'].append(row)
    return evidence


def deliver(evidence):
    if workflow.verify_trials(workflow.RESEARCH_MODE_LEDGER) != 'PASS' or workflow.effective_trials() != 743:
        raise ValueError('LEDGER_UNVERIFIED')
    trial_rows = {row['experiment_id']: row for row in workflow._trial_rows(workflow.RESEARCH_MODE_LEDGER)}
    replays = [read(OUT / 'replay' / 'revision_4' / str(index) / 'manifest.json') for index in (289, 129)]
    evidence['replay_ledger_sha256'] = [trial_rows[m['experiment_id']]['sha256'] for m in replays]
    evidence['all_v7_ledger_sha256'] = [row['sha256'] for row in workflow._trial_rows(workflow.RESEARCH_MODE_LEDGER)[500:]]
    evidence['review_1_sha256'] = workflow.sha256(OUT / 'review/review_1.md')
    evidence['research_ledger_sha256'] = workflow.sha256(workflow.RESEARCH_MODE_LEDGER)
    evidence['formal_ledger_sha256'] = workflow.sha256(workflow.TRIAL_LEDGER)
    evidence['notes'] = ('開發期內配對研究，非 OOS 或正式認證。B 回撤控制及 C 權重上限本輪不執行：'
                         'equal TOP10 恰為 10%，上限不綁；集中度主要來自淨利小而高換手。'
                         '額外 replay 因 Review 1 修正而如實計入 ledger。')
    rows = []
    for item in evidence['experiments']:
        for track in ('base', 'stress'):
            old, new, proxy = item['old'][track], item['new'][track] if item['new'] else {}, item['proxy']
            for key in old:
                if key == 'annual_total_return':
                    for year in sorted(set(old[key]) | set(new.get(key, {}))):
                        a, b, c = old[key].get(year), new.get(key, {}).get(year), proxy[key].get(year)
                        av, bv, cv = (value.get('value') if value else None for value in (a, b, c))
                        rows.append({'family': item['family'], 'track': track, 'metric': f'annual_total_return_{year}',
                                     'v6': av, 'v7': bv, 'proxy': cv, 'difference': bv - av if None not in (av, bv) else None})
                    continue
                a, b = old[key], new.get(key)
                c = proxy.get(key)
                av = a.get('value') if isinstance(a, dict) else a
                bv = b.get('value') if isinstance(b, dict) else b
                cv = c.get('value') if isinstance(c, dict) else c
                rows.append({'family': item['family'], 'track': track, 'metric': key,
                             'v6': av, 'v7': bv, 'proxy': cv, 'difference': bv - av if None not in (av, bv) else None})
        a = item['old']['top5_asset_profit_share']['value']
        b = item['new']['top5_asset_profit_share']['value'] if item['new'] else None
        rows.append({'family': item['family'], 'track': 'stress', 'metric': 'top5_asset_profit_share',
                     'v6': a, 'v7': b, 'proxy': None, 'difference': b - a if None not in (a, b) else None})
    for row in rows:
        for key in ('v6', 'v7', 'proxy', 'difference'):
            row[key + '_reason'] = None if row[key] is not None else ('PROXY_NOT_TRADED' if key == 'proxy' else
                next((item.get('reason') for item in evidence['experiments'] if item['family'] == row['family'] and item.get('reason')), 'METRIC_NOT_COMPUTABLE'))
    evidence['comparison_rows'] = rows
    with (OUT / 'hypcost_experiments.json').open('w', encoding='utf-8') as stream:
        json.dump(evidence, stream, ensure_ascii=False, indent=2, allow_nan=False)
        stream.write('\n')
    with (OUT / 'hypcost_comparison.csv').open('w', newline='', encoding='utf-8-sig') as stream:
        writer = csv.DictWriter(stream, fieldnames=('family', 'track', 'metric', 'v6', 'v6_reason', 'v7', 'v7_reason', 'proxy', 'proxy_reason', 'difference', 'difference_reason'))
        writer.writeheader()
        writer.writerows(rows)
    lines = ['# HYP-COST 比較', '', '**RESEARCH_UNCERTIFIED / DIAGNOSTIC_ONLY；開發期內研究，非 OOS 或認證。**', '',
             '| 家族 | L1 | L2 | L3 | 換手 V6 → V7 | 2x 報酬 V6 → V7 | 重疊率 |', '|---|---|---|---|---|---|---|']
    for item in evidence['experiments']:
        if item['new'] is None or item['l1'] == 'NOT_COMPUTABLE':
            lines.append(f"| {item['family']} | NOT_COMPUTABLE | NOT_COMPUTABLE | {item['l3']['status']} | — | — | — |")
            continue
        old, new = item['old']['stress'], item['new']['stress']
        lines.append(f"| {item['family']} | {item['l1']} | {item['l2']} | {item['l3']['status']} | "
                     f"{old['turnover']['value']:.4f} → {new['turnover']['value']:.4f} | "
                     f"{old['total_return']['value']:.4f} → {new['total_return']['value']:.4f} | {item['overlap']:.4f} |")
    lines += ['', '門檻以 JSON／CSV 未四捨五入值判定。配對差 Sharpe 與 Sharpe 差分分開列示；Sortino 分母採每日負報酬平方平均的平方根。', '',
              '| 分層結果 | 判定 |', '|---|---|', '| L1 FAIL | HYP-COST 目標未達成 |',
              '| L1 PASS、L2 FAIL | 目標達成，但無充分報酬改善統計證據 |',
              '| L1 PASS、L2 PASS、L3 未通過 | 開發期支持改善；未通過原研究篩選 |',
              '| L1 PASS、L2 PASS、L3 通過 | 開發期支持改善並通過研究篩選；仍非認證 |',
              '| NOT_COMPUTABLE | 日期、變異數或事件邊界不允許判斷 |', '']
    for item in evidence['experiments']:
        lines += [f"## {item['family']}", '', f"- L1 三條：`{item.get('l1_checks', {})}`；原因：{item.get('reason') or '無'}。",
                  f"- L2 配對差 bootstrap：`{item.get('paired_bootstrap', {})}`；差分 Sharpe `{item.get('difference_sharpe')}`；Sharpe 差 `{item.get('sharpe_difference')}`。",
                  f"- L3：`{item['l3']}`。", f"- 實驗 ledger SHA：`{item['ledger_sha256']}`。", '']
    lines += [f"Replay ledger SHA：`{evidence['replay_ledger_sha256']}`；研究 {evidence['research_trials']}、正式 {evidence['formal_trials']}、DSR 採 {evidence['dsr_trials']}。",
              '', evidence['notes'], '', '完整原值、逐年、代理與差異見 `hypcost_comparison.csv`；登記、來源 SHA、事件與重放證據見 `hypcost_experiments.json`。', '']
    (OUT / 'hypcost_comparison.md').write_text('\n'.join(lines), encoding='utf-8')
    decisions_path = OUT / 'strategy_decisions.md'
    frozen_path = OUT / 'strategy_decisions_pre_experiment.md'
    if not frozen_path.exists():
        if workflow.sha256(decisions_path) != evidence['pre_experiment_decisions_sha256']:
            raise ValueError('FROZEN_DECISIONS_CHANGED')
        frozen_path.write_bytes(decisions_path.read_bytes())
    if workflow.sha256(frozen_path) != evidence['pre_experiment_decisions_sha256']:
        raise ValueError('FROZEN_DECISIONS_CHANGED')
    before = frozen_path.read_text(encoding='utf-8').splitlines()
    current = decisions_path.read_text(encoding='utf-8').splitlines()
    if [line for line in before if not any(line.startswith('| ' + item['family'] + ' |') for item in evidence['experiments'])] != [line for line in current if not any(line.startswith('| ' + item['family'] + ' |') for item in evidence['experiments'])]:
        raise ValueError('OTHER_DECISION_ROWS_CHANGED')
    after = []
    for line in before:
        for item in evidence['experiments']:
            if line.startswith('| ' + item['family'] + ' |'):
                decision = 'BLOCKED' if item['l1'] == 'NOT_COMPUTABLE' else ('CONTINUE' if item['l1'] == 'PASS' else 'REVISE')
                action = f"HYP-COST 已執行；L1 {item['l1']}"
                line = line.replace('| HYP-COST 一次 | PENDING_HYP_COST |', f'| {action} | {decision} |')
        after.append(line)
    if sum(a != b for a, b in zip(before, after)) != 2:
        raise ValueError('DECISION_ROWS_MISMATCH')
    decisions_path.write_text('\n'.join(after) + '\n', encoding='utf-8')
    source_track = ROOT / 'outputs/research_mode/report_v6/research_track.json'
    track = read(source_track)
    track.update(research_trials=evidence['research_trials'], dsr_trials=evidence['dsr_trials'], trial_sharpe_variance=evidence['trial_sharpe_variance'],
                 report_dir=OUT.relative_to(ROOT).as_posix(), v6_source_sha256=workflow.sha256(source_track),
                 v7_experiments=[{'strategy': item['family'], 'status': item['status'], 'l1': item['l1'], 'l2': item['l2'],
                                  'experiment_id': item['new_manifest']['experiment_id'], 'ledger_sha256': item['ledger_sha256']}
                                 for item in evidence['experiments']])
    (OUT / 'research_track.json').write_text(json.dumps(track, ensure_ascii=False, indent=2, allow_nan=False) + '\n', encoding='utf-8')
    readme = ['# HYP-COST 結論', '', '**RESEARCH_UNCERTIFIED / DIAGNOSTIC_ONLY；僅開發期，非 OOS 或正式認證。**', '']
    for item in evidence['experiments']:
        if item['new'] is None or item['l1'] == 'NOT_COMPUTABLE':
            readme += [f"- {item['family']}：L1／L2 NOT_COMPUTABLE；原因 {item['reason']}；L3 {item['l3']['status']}。", '']
            continue
        checks = item['l1_checks']
        readme += [f"- **{item['family']}**：換手降至少三分之一：{'是' if checks['turnover'] else '否'}；"
                   f"訊號重疊至少 70%：{'是' if checks['overlap'] else '否'}；"
                   f"2x 成本後超額維持或提升：{'是' if checks['return'] else '否'}。"
                   f"L1 {item['l1']}、L2 {item['l2']}、L3 {item['l3']['status']}。"]
    counts = {name: sum(f'| {name} |' in line for line in after) for name in ('CONTINUE', 'REVISE', 'STOP', 'BLOCKED')}
    outcomes = [item['l1'] for item in evidence['experiments']]
    recommendation = ('本輪兩家族均未達目標，此緩衝機制目前不值得直接續投。' if all(x == 'FAIL' for x in outcomes)
                      else '至少一項無法判斷，無法判定是否值得續投。' if 'NOT_COMPUTABLE' in outcomes
                      else '至少一項達到 L1，可依 L2 和 L3 繼續研究，不能視為正式認證。')
    readme += ['', f"12 家族決策：{counts}；兩家族 HYP-COST 的後續判斷依上述 L1 結果，不據此執行新參數。", '',
               recommendation, '', '詳細依據見 `strategy_decisions.md` 與 `hypcost_comparison.md`。', '']
    (OUT / 'README.md').write_text('\n'.join(readme), encoding='utf-8')


if __name__ == '__main__':
    result = run()
    print(json.dumps(result, ensure_ascii=False, default=str, allow_nan=False))
