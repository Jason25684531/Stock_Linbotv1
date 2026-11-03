"""Offline HYP-COST registration and diagnostic evidence."""
import csv
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts import research_workflow as workflow

OUTPUT = ROOT / 'outputs/research_mode/report_v7_hypcost'
FAMILIES = ('RS_004_NEAR_HIGH_120D', 'A191_088')


def preflight():
    report = ROOT / 'outputs/research_mode/report_v6'
    paths = [workflow.REGISTRY, workflow.TRIAL_LEDGER, ROOT / 'core/research/repair_targets.py',
             ROOT / 'core/research/repair_runner.py', ROOT / 'app/dashboard_payloads.py',
             ROOT / 'scripts/assemble_research_snapshot.py', ROOT / 'scripts/track_b_stats.py']
    hashes = {str(p.relative_to(ROOT)): workflow.sha256(p) for p in paths}
    tree = ''.join(f'{p.relative_to(report)}:{workflow.sha256(p)}\n' for p in sorted(report.rglob('*')) if p.is_file())
    for run in ('289', '129'):
        directory = ROOT / 'outputs/research_mode/day3_runs_v6' / run
        manifest = json.loads((directory / 'manifest.json').read_text(encoding='utf-8'))
        for name, expected in manifest['output_sha256'].items():
            if workflow.sha256(directory / name) != expected:
                raise ValueError(f'ARCHIVE_HASH_MISMATCH:{run}:{name}')
            hashes[str((directory / name).relative_to(ROOT))] = expected
    if (workflow.verify_registry(), workflow.verify_trials(workflow.RESEARCH_MODE_LEDGER),
            workflow.research_trials(), workflow.verify_trials(), workflow.effective_trials()) != ('PASS', 'PASS', 500, 'PASS', 743):
        raise ValueError('PREFLIGHT_LEDGER_MISMATCH')
    OUTPUT.mkdir(parents=True, exist_ok=True)
    with (OUTPUT / 'preflight.json').open('x', encoding='utf-8') as stream:
        json.dump({'at': datetime.now(timezone.utc).isoformat(), 'hashes': hashes,
                   'report_v6_tree_sha256': hashlib.sha256(tree.encode()).hexdigest(),
                   'registry': 'PASS', 'research_ledger': 'PASS', 'research_trials': 500,
                   'formal_ledger': 'PASS', 'formal_trials': 743}, stream, indent=2)
        stream.write('\n')


def draft():
    if workflow.verify_registry() != 'PASS':
        raise ValueError('REGISTRY_UNVERIFIED')
    with workflow.REGISTRY.open(encoding='utf-8-sig', newline='') as stream:
        existing = list(csv.DictReader(stream))
    original = next(row for row in existing if row['hypothesis_id'] == 'A191_132:HYP-COST')
    addition = (
        ' V7: baseline runs 289 (RS_004, N=10) and 129 (A191_088, N=30). '
        'Use V6 PIT eligible ranks: retain actual positions or positive uncredited stock receivables in top 2N, '
        'sell others, fill vacancies by highest ranked candidates to N, and reweight with V6 score_weighted normalization. '
        'L1 requires stress_turnover from turnover_of <= V6 * 2/3, stress_total_return >= V6 after identical dates, '
        'equal-weight proxy daily returns, source SHA and benchmark_total_return, and mean target overlap >= 0.70. '
        'L2 requires L1 and paired V7 minus V6 2x net daily return Sharpe bootstrap lower > 0 at confidence 0.95; '
        'also report 0.975 Bonferroni lower. block_bootstrap_sharpe uses block length 20, 2000 draws, seed 20261023. '
        'L3 reports unchanged r1-r5 and DSR with max(research ledger count, 743); it does not gate L1 or L2. '
        'Date mismatch, zero-variance paired difference or unresolved held delisting/share-count event yields '
        'NOT_COMPUTABLE; do not truncate, fill, substitute holdings or rerun. No 2026 OOS.'
    )
    rows = []
    for family in FAMILIES:
        source = Path('outputs/research_mode/report_v6/diagnostics') / (family + '.md')
        rows.append({'hypothesis_id': family + ':HYP-COST', 'strategy': family,
                     'hypothesis': original['hypothesis'], 'falsification_test': original['falsification_test'] + addition,
                     'data_window': original['data_window'], 'trial_budget': '1',
                     'registered_at': None, 'status': 'REGISTERED_NOT_RUN',
                     'source': f'{source.as_posix()} sha256 {workflow.sha256(ROOT / source)}'})
    return rows


def write_draft():
    OUTPUT.mkdir(parents=True, exist_ok=True)
    path = OUTPUT / 'registration_draft.json'
    with path.open('x', encoding='utf-8') as stream:
        json.dump(draft(), stream, ensure_ascii=False, indent=2)
        stream.write('\n')


def register():
    path = OUTPUT / 'registration_draft.json'
    rows = json.loads(path.read_text(encoding='utf-8'))
    if rows != draft():
        raise ValueError('REGISTRATION_DRAFT_CHANGED')
    before = workflow.REGISTRY.read_bytes()
    for row in rows:
        workflow.register(row, today=datetime.now(timezone.utc).isoformat(timespec='microseconds'))
    after = workflow.REGISTRY.read_bytes()
    if not after.startswith(before) or workflow.verify_registry() != 'PASS':
        raise ValueError('REGISTRATION_APPEND_VERIFICATION_FAILED')
    with workflow.REGISTRY.open(encoding='utf-8-sig', newline='') as stream:
        current = list(csv.DictReader(stream))
    if [r['hypothesis_id'] for r in current[-2:]] != [r['hypothesis_id'] for r in rows]:
        raise ValueError('REGISTRATION_ROWS_MISMATCH')


def decisions():
    report = ROOT / 'outputs/research_mode/report_v6'
    with (report / 'screen_results.csv').open(encoding='utf-8-sig', newline='') as stream:
        rows = list(csv.DictReader(stream))
    names = sorted({row['strategy'] for row in rows})
    if len(names) != 12:
        raise ValueError('UNEXPECTED_FAMILY_COUNT')
    lines = ['# 策略決策（實驗前凍結）', '', 'RESEARCH_UNCERTIFIED / DIAGNOSTIC_ONLY；僅開發期證據。', '',
             '| family | V6 證據與數字 | 核心弱點 | 本輪動作 | research_decision | data_status | 依據 |',
             '|---|---|---|---|---|---|---|']
    for name in names:
        group = [r for r in rows if r['strategy'] == name]
        computed = [r for r in group if r['status'] == 'COMPUTABLE']
        blocked = len(group) - len(computed)
        values = [float(r['stress_sharpe']) for r in computed if r['stress_sharpe']]
        best = f'{max(values):.3f}' if values else 'None（無可計算 Sharpe）'
        diagnostic = (report / 'diagnostics' / (name + '.md')).relative_to(ROOT).as_posix()
        if not (ROOT / diagnostic).is_file():
            raise ValueError(f'DIAGNOSTIC_MISSING:{name}')
        pending = name in FAMILIES
        decision = 'PENDING_HYP_COST' if pending else ('BLOCKED' if not computed else 'REVISE')
        status = 'BLOCKED' if not computed else ('PARTIALLY_BLOCKED' if blocked else 'COMPLETE')
        action = 'HYP-COST 一次' if pending else '本輪不實驗'
        lines.append(f'| {name} | `{diagnostic}`；{len(computed)}/{len(group)} 可計算；最佳 2x Sharpe {best} | '
                     f'篩選未通過；{blocked} 組資料阻擋 | {action} | {decision} | {status} | '
                     'V6 開發期診斷；REVISE 表示需重新研究，不是認證 |')
    path = OUTPUT / 'strategy_decisions.md'
    path.write_text('\n'.join(lines) + '\n', encoding='utf-8', newline='\n')
    with (OUTPUT / 'hypcost_experiments.json').open('x', encoding='utf-8') as stream:
        json.dump({'label': 'DIAGNOSTIC_ONLY', 'data_label': 'RESEARCH_UNCERTIFIED',
                   'pre_experiment_decisions_sha256': workflow.sha256(path), 'experiments': []}, stream, indent=2)
        stream.write('\n')


def overlap(v6, v7, top_n):
    for frame in (v6, v7):
        if frame.duplicated(['asof_date', 'asset_id']).any():
            raise ValueError('DUPLICATE_TARGET')
    if set(v6.asof_date) != set(v7.asof_date):
        raise ValueError('TARGET_DATE_MISMATCH')
    values = []
    for day in sorted(set(v6.asof_date)):
        left = set(v6.loc[v6.asof_date == day, 'asset_id'])
        right = set(v7.loc[(v7.asof_date == day) & (v7.target_weight != 0), 'asset_id'])
        values.append(len(left & right) / top_n)
    if not values:
        raise ValueError('NO_TARGET_DATES')
    return sum(values) / len(values)


def paired_bootstrap(v6, v7):
    import numpy as np
    from scripts.track_b_stats import block_bootstrap_sharpe
    if not v6.index.equals(v7.index) or v6.index.has_duplicates:
        return {'status': 'NOT_COMPUTABLE', 'value': None, 'reason': 'DATE_MISMATCH'}
    difference = np.asarray(v7 - v6, dtype=float)
    if len(difference) < 40 or not np.isfinite(difference).all() or np.std(difference, ddof=1) <= 1e-12:
        return {'status': 'NOT_COMPUTABLE', 'value': None, 'reason': 'INSUFFICIENT_OR_ZERO_VARIANCE'}
    results = {str(conf): block_bootstrap_sharpe(difference, confidence=conf) for conf in (0.95, 0.975)}
    if not all(np.isfinite(item['lower']) for item in results.values()):
        return {'status': 'NOT_COMPUTABLE', 'value': None, 'reason': 'UNDEFINED_BOOTSTRAP'}
    return {'status': 'COMPUTABLE', 'intervals': results}


def replay():
    from scripts import research_v7_hypcost  # include the V7 orchestration in the admitted code identity
    from core.research.repair_runner import run
    from scripts.research_mode_run import FAMILIES as RUN_FAMILIES, configs
    for name in FAMILIES:
        workflow.require_registered(name + ':HYP-COST')
    spec = RUN_FAMILIES['day3']
    items = list(configs('day3', spec['prepared'], spec['signals'], spec['output'] / 'runner_evidence.json'))
    for index in (289, 129):
        config = {**items[index], 'output': str(OUTPUT / 'replay' / 'revision_4' / str(index))}
        print(f'Replay {index}: starting', flush=True)
        result = run(config)
        print(f'Replay {index}: {result["manifest"]["status"]}', flush=True)
        if result['manifest']['status'] != 'COMPUTABLE':
            raise ValueError('REPLAY_NOT_COMPUTABLE')


def compare_replays():
    import math
    import pandas as pd
    from pandas.testing import assert_frame_equal

    def difference(left, right, path='root'):
        if isinstance(left, dict) and isinstance(right, dict):
            if left.keys() != right.keys():
                return f'{path}: keys differ'
            for key in left:
                error = difference(left[key], right[key], f'{path}.{key}')
                if error:
                    return error
        elif isinstance(left, list) and isinstance(right, list):
            if len(left) != len(right):
                return f'{path}: lengths {len(left)} != {len(right)}'
            for index, (a, b) in enumerate(zip(left, right)):
                error = difference(a, b, f'{path}[{index}]')
                if error:
                    return error
        elif isinstance(left, (int, float)) and not isinstance(left, bool) and isinstance(right, (int, float)) and not isinstance(right, bool):
            if isinstance(left, int) and isinstance(right, int):
                if left != right:
                    return f'{path}: {left} != {right}'
            elif not math.isclose(left, right, rel_tol=1e-9, abs_tol=1e-12):
                return f'{path}: {left} != {right}'
        elif left != right:
            return f'{path}: {left!r} != {right!r}'
        return None

    comparisons = {}
    for index in (289, 129):
        archived = ROOT / 'outputs/research_mode/day3_runs_v6' / str(index)
        current = OUTPUT / 'replay' / 'revision_4' / str(index)
        comparisons[str(index)] = {}
        try:
            assert_frame_equal(pd.read_csv(archived / 'targets.csv', dtype={'asset_id': str}),
                               pd.read_csv(current / 'targets.csv', dtype={'asset_id': str}),
                               check_dtype=False, rtol=1e-9, atol=1e-12)
            comparisons[str(index)]['targets.csv'] = {'status': 'PASS', 'first_difference': None}
        except AssertionError as exc:
            comparisons[str(index)]['targets.csv'] = {'status': 'FAIL', 'first_difference': str(exc)}
        for track in ('base', 'stress'):
            old = json.loads((archived / f'{track}.json').read_text(encoding='utf-8'))
            new = json.loads((current / f'{track}.json').read_text(encoding='utf-8'))
            error = difference(old, new)
            comparisons[str(index)][track] = {'status': 'FAIL' if error else 'PASS', 'first_difference': error}
    passed = all(item['status'] == 'PASS' for group in comparisons.values() for item in group.values())
    (OUTPUT / 'replay_comparison.json').write_text(json.dumps({'status': 'PASS' if passed else 'FAIL',
        'label': 'DIAGNOSTIC_ONLY', 'comparisons': comparisons}, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    if not passed:
        raise ValueError('REPLAY_MISMATCH_STOP')


def experiments():
    from scripts import research_v7_hypcost  # same code identity as the replay
    from core.research.repair_runner import run
    from scripts.research_mode_run import FAMILIES as RUN_FAMILIES, configs
    comparison_path = OUTPUT / 'replay_comparison.json'
    comparison = json.loads(comparison_path.read_text(encoding='utf-8'))
    review = json.loads((OUTPUT / 'review/review_1_gate.json').read_text(encoding='utf-8'))
    if (comparison['status'] != 'PASS' or review['status'] != 'PASS' or review['major'] != 0
            or review['comparison_sha256'] != workflow.sha256(comparison_path)):
        raise ValueError('REVIEW_1_GATE_BLOCKED')
    for index in (289, 129):
        manifest = json.loads((OUTPUT / 'replay' / 'revision_4' / str(index) / 'manifest.json').read_text(encoding='utf-8'))
        if any(workflow.sha256(path) != digest for path, digest in manifest['code_sha256'].items()):
            raise ValueError('REPLAY_CODE_CHANGED')
    spec = RUN_FAMILIES['day3']
    items = list(configs('day3', spec['prepared'], spec['signals'], spec['output'] / 'runner_evidence.json'))
    for index in (289, 129):
        original = items[index]
        identity = f"{original['strategy']}__TOP{original['top_n']}__REB20__SCORE_WEIGHTED__BUF2"
        config = {**original, 'source_config_id': original['config_id'], 'config_id': identity,
                  'rank_buffer_multiple': 2, 'output': str(OUTPUT / 'experiments' / original['strategy'])}
        print(f'Experiment {original["strategy"]}: starting', flush=True)
        result = run(config)
        print(f'Experiment {original["strategy"]}: {result["manifest"]["status"]}', flush=True)


if __name__ == '__main__':
    {'preflight': preflight, 'draft': write_draft, 'register': register, 'decisions': decisions,
     'replay': replay, 'compare-replays': compare_replays, 'experiments': experiments}[sys.argv[1]]()
