"""Batch orchestrator for the 21 web strategies (design D7): six states, content-addressed REUSE, reference runs outside formal research.

Every row ends in exactly one of RUN, REUSE, DATA_BLOCKED, ENGINE_UNSUPPORTED, GOVERNED_ONLY, FAILED with a reason. Executable rows run the
governed engine path of scripts/legacy_baseline_run.py (unchanged) on the pinned native7 research snapshot, base and 2x cost, 2023-01-03 to
2025-12-31; TQuant ports read the same official bars through an adapter (no database). Runs are saved with run_store.save_run under
outputs/reference_mode/runs (immutable, manifest, DEV_END guard) and never touch outputs/backtest_runs, the trial ledger, the registry or
effective_trials(). Identical run identity (data + code + parameters + costs + calendar) is REUSE: the engine is not called.

CLI: python -m core.research.reference.runner [--plan-only] [--workers N]
"""
from __future__ import annotations

import argparse
import json
import os
import secrets
import sys
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
OUT = ROOT / 'outputs/reference_mode'
STATES = ('RUN', 'REUSE', 'DATA_BLOCKED', 'ENGINE_UNSUPPORTED', 'GOVERNED_ONLY', 'FAILED')
LABELS = ['REFERENCE_ONLY', 'NOT_CERTIFIED', 'NOT_A_TRIAL']
INITIAL_CAPITAL = 1_000_000
DATA_TRUST_MISSING = 'DATA_CERTIFICATION_RECORDS_MISSING:prices,corporate_actions,pit_fundamentals,universe,calendar'
DISCLOSURE = '21 fixed configurations shown in catalog order; choosing among them is a selection over 21 trials and is not evidence.'


@dataclass(frozen=True)
class Verdict:
    state: str
    reason: str


def classify(cap, probe) -> Verdict:
    """Pure. Precedence GOVERNED_ONLY > ENGINE_UNSUPPORTED > DATA_BLOCKED > REUSE > RUN; FAILED only comes from execution."""
    if cap.governance:
        return Verdict('GOVERNED_ONLY', cap.governance)
    if cap.engine is None:
        return Verdict('ENGINE_UNSUPPORTED', probe.get('unsupported') or 'NO_ENGINE_ADAPTER')
    if cap.static_block:
        return Verdict('DATA_BLOCKED', cap.static_block)
    if probe.get('missing_inputs'):
        return Verdict('DATA_BLOCKED', 'INPUT_MISSING:' + ','.join(probe['missing_inputs']))
    if probe.get('reuse_run'):
        return Verdict('REUSE', f"IDENTICAL_IDENTITY:{probe['reuse_run']}")
    return Verdict('RUN', 'NO_IDENTICAL_RUN')


def check_reference_window(start, end):
    from core.research.strategy_signal import check_window
    try:
        check_window(start, end)  # before anything is read
    except Exception as exc:
        raise ValueError(f'OOS_OR_INPUT_WINDOW_BLOCKED: {exc}') from None


def run_identity(*, strategy_id, config, code_files, inputs) -> str:
    """RID1: SHA256 of the strategy, its configuration, every code file in the engine closure and every input digest."""
    from core.backtest.run_store import canonical_hash
    from scripts.research_workflow import sha256
    code = {Path(p).resolve().relative_to(ROOT).as_posix() if Path(p).resolve().is_relative_to(ROOT) else Path(p).as_posix(): sha256(Path(p))
            for p in code_files}
    return 'RID1:' + canonical_hash({'strategy_id': strategy_id, 'config': config, 'code_sha256': code, 'inputs': inputs})


def reference_group() -> str:
    from core.backtest.run_store import canonical_hash
    from scripts import legacy_baseline_run as legacy
    return 'REFERENCE_ENGINE:' + canonical_hash({
        'engine': 'legacy_next_session_open_v2', 'period': [legacy.START, legacy.END], 'costs': legacy._costs(),
        'stress_multiple': legacy.STRESS_MULTIPLE, 'valuation': 'finite_positive_stale', 'rebalance': 'daily rules',
        'universe': 'native7 official bars (TWSE listed)', 'benchmark': 'TAIEX price index; TOTAL_RETURN_INDEX_UNAVAILABLE'})[:12]


def _code_files(cap):
    from scripts import legacy_baseline_run as legacy
    extra = [ROOT / 'core/strategies/_tquant.py'] if cap.engine == 'TQUANT_SNAPSHOT' else []
    return [*legacy.code_paths(), legacy._strategy_file(cap.strategy_id), *extra, ROOT / 'scripts/reference_engine.py']  # files that shape the NAV


def identities(caps) -> dict:
    """Identity per executable strategy; the research inputs are loaded once for their digests."""
    from scripts import legacy_baseline_run as legacy
    from scripts.research_workflow import sha256
    prep = legacy._default_prepared(legacy.START, legacy.END)
    bars = sha256(legacy.input_paths()['bars'])
    out = {}
    for cap in caps:
        if cap.engine is None or cap.governance or cap.static_block:
            continue
        config = {k: v for k, v in legacy.config_for(cap.strategy_id).items() if k != 'round'} | {'group': reference_group()}
        inputs = dict(prep.digests) | ({'official_bars_file': bars} if cap.engine == 'TQUANT_SNAPSHOT' else {})
        out[cap.strategy_id] = run_identity(strategy_id=cap.strategy_id, config=config, code_files=_code_files(cap), inputs=inputs)
    return out


def _runs() -> Path:
    return OUT / 'runs'


def lookup(identity) -> str | None:
    """Newest stored reference run with this identity whose manifest verifies; never runs the engine."""
    from core.backtest import run_store
    for folder in sorted(_runs().glob('2*'), reverse=True):
        try:
            record = json.loads((folder / 'run.json').read_text(encoding='utf-8'))
            if record.get('run_identity') == identity and record.get('status') == 'SUCCESS':
                run_store.load_run(folder.name, root=_runs())
                return folder.name
        except (OSError, ValueError):
            continue
    return None


def _execute_by_id(sid, identity):
    from core.research.reference import catalog
    from scripts.reference_engine import execute_one  # engine adapters live with the governed engine scripts, outside core.research
    cap = {c.strategy_id: c for c in catalog.capabilities()}[sid]
    return execute_one(cap, identity)


def metrics(nav) -> dict:
    """Base and 2x cost tracks: total return, Sharpe and maximum drawdown as {'value', 'reason'} (None with a reason when undefined)."""
    import pandas as pd
    from core.research import trade_analytics
    out = {}
    for track, column in (('base', 'nav_base'), ('x2', 'nav_stress')):
        series = pd.Series(nav[column].astype(float).to_numpy(), index=pd.to_datetime(nav['date']))
        perf = trade_analytics.performance(series, initial_cash=INITIAL_CAPITAL)
        out[track] = {name: perf[name] for name in ('total_return', 'sharpe', 'max_drawdown')}
    return out


def save(cap, identity, result) -> str:
    import pandas as pd
    from core.backtest import run_store
    from scripts import legacy_baseline_run as legacy
    config = {k: v for k, v in legacy.config_for(cap.strategy_id).items() if k != 'round'}
    record = {'strategy_id': cap.strategy_id, 'strategy_ids': [cap.strategy_id], 'strategy_type': f'reference_{cap.family}',
              'parameters': config, 'parameter_hash': run_store.canonical_hash(config), 'run_identity': identity,
              'source_hash': identity.split(':', 1)[1], 'data_version': identity.split(':', 1)[1], 'comparison_group': reference_group(),
              'certification_status': 'NOT_CERTIFIED', 'status_class': 'EXPERIMENTAL', 'labels': LABELS, 'period': [legacy.START, legacy.END],
              'cost_model': {**legacy._costs(), 'stress_multiple': legacy.STRESS_MULTIPLE}, 'engine_error': result.get('engine_error'),
              'benchmark': 'TAIEX price index; TOTAL_RETURN_INDEX_UNAVAILABLE'}
    frames = {'nav': result['nav'], 'trades': result['trades'], 'holdings': pd.DataFrame(columns=['date', 'stock_id', 'shares']),
              'picks': pd.DataFrame(columns=['date', 'stock_id']), 'metrics': metrics(result['nav'])}
    return run_store.save_run(record, frames, root=_runs())


def _row(cap, verdict, run_id=None, error=None, trades=None):
    import pandas as pd
    row = {'strategy_id': cap.strategy_id, 'family': cap.family, 'state': verdict.state, 'reason': verdict.reason, 'run_id': run_id,
           'comparison_group': None, 'metrics': None, 'warnings': [], 'error': error}
    engineering = verdict.state in ('RUN', 'REUSE') and run_id is not None and not error
    if engineering:
        folder = _runs() / run_id
        record = json.loads((folder / 'run.json').read_text(encoding='utf-8'))
        nav = pd.read_csv(folder / 'nav.csv')
        row.update(comparison_group=record['comparison_group'], metrics=metrics(nav), run_identity=record.get('run_identity'))
        engineering = not record.get('engine_error')
        if trades == 0 or (trades is None and (folder / 'trades.csv').stat().st_size < 64):
            row['warnings'].append('NO_TRADES_IN_WINDOW')
    row['levels'] = {'ENGINEERING_PASS': engineering, 'REPRODUCIBLE': engineering and bool(row.get('run_identity')),
                     'DATA_TRUST_VERIFIED': False, 'CERTIFIED': False}
    row['level_reasons'] = {'DATA_TRUST_VERIFIED': DATA_TRUST_MISSING, 'CERTIFIED': 'NO_POSITIVE_CERTIFICATION_RECORD'}
    return row


def run_batch(execute=None, workers=6, plan_only=False) -> dict:
    """Classify all 21, reuse identical runs, execute the rest (in parallel processes by default), write an immutable report."""
    from core.research.reference import catalog
    caps = catalog.capabilities()
    ids = identities(caps)
    plan = {cap.strategy_id: classify(cap, {'reuse_run': lookup(ids[cap.strategy_id])} if cap.strategy_id in ids else {}) for cap in caps}
    results = {}
    todo = [cap for cap in caps if plan[cap.strategy_id].state == 'RUN']
    if not plan_only and todo:
        if execute is None:
            with ProcessPoolExecutor(max_workers=max(1, min(workers, len(todo)))) as pool:
                futures = {cap.strategy_id: pool.submit(_execute_by_id, cap.strategy_id, ids[cap.strategy_id]) for cap in todo}
                for sid, future in futures.items():
                    try:
                        results[sid] = future.result()
                    except Exception as exc:
                        results[sid] = {'error': f'{type(exc).__name__}: {exc}'[:300]}
        else:
            for cap in todo:
                try:
                    results[cap.strategy_id] = execute(cap, ids[cap.strategy_id])
                except Exception as exc:
                    results[cap.strategy_id] = {'error': f'{type(exc).__name__}: {exc}'[:300]}
    rows = []
    for cap in caps:
        verdict = plan[cap.strategy_id]
        if verdict.state == 'REUSE':
            rows.append(_row(cap, verdict, run_id=verdict.reason.split(':', 1)[1]))
        elif verdict.state == 'RUN' and not plan_only:
            result = results.get(cap.strategy_id, {'error': 'NOT_EXECUTED'})
            if result.get('error'):
                rows.append(_row(cap, Verdict('FAILED', 'ENGINE_ERROR'), error=result['error']))
                continue
            try:
                run_id = save(cap, ids[cap.strategy_id], result)
                rows.append(_row(cap, verdict, run_id=run_id, trades=len(result['trades'])))
            except Exception as exc:
                rows.append(_row(cap, Verdict('FAILED', 'SAVE_FAILED'), error=f'{type(exc).__name__}: {exc}'[:300]))
        else:
            rows.append(_row(cap, verdict))
    counts = {state: sum(row['state'] == state for row in rows) for state in STATES}
    if sum(counts.values()) != 21:
        raise RuntimeError(f'CLASSIFICATION_INCOMPLETE: {counts}')
    report = {'batch_id': datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ') + '-' + secrets.token_hex(4), 'plan_only': plan_only,
              'created_at': datetime.now(timezone.utc).isoformat(timespec='seconds'), 'labels': LABELS, 'disclosure': DISCLOSURE,
              'reference_group': reference_group(), 'counts': counts, 'rows': rows}
    write_report(report)
    return report


def write_report(report) -> Path:
    folder = OUT / 'batches' / report['batch_id']
    folder.mkdir(parents=True, exist_ok=False)
    with open(folder / 'report.json', 'x', encoding='utf-8') as handle:
        json.dump(report, handle, ensure_ascii=False, indent=1, default=str)
    lines = [f"# Reference batch {report['batch_id']}", '', f"{' / '.join(report['labels'])}. {report['disclosure']}", '',
             '| strategy | state | reason | base return | base Sharpe | base MDD | 2x return | 2x Sharpe | 2x MDD |', '|' + '---|' * 9]
    for row in report['rows']:
        m = row['metrics'] or {}
        cell = lambda track, name: '' if not m else (f"{m[track][name]['value']:.4f}" if m[track][name]['value'] is not None
                                                     else f"None ({m[track][name]['reason']})")
        lines.append(f"| {row['strategy_id']} | {row['state']} | {row['reason']} | " + ' | '.join(
            cell(t, n) for t in ('base', 'x2') for n in ('total_return', 'sharpe', 'max_drawdown')) + ' |')
    (folder / 'report.md').write_text('\n'.join(lines) + '\n', encoding='utf-8')
    latest = OUT / 'LATEST.json'
    tmp = latest.with_suffix('.tmp')
    tmp.write_text(json.dumps({'batch_id': report['batch_id'], 'path': str(folder / 'report.json')}), encoding='utf-8')
    os.replace(tmp, latest)
    return folder


def latest_report() -> dict | None:
    try:
        pointer = json.loads((OUT / 'LATEST.json').read_text(encoding='utf-8'))
        return json.loads(Path(pointer['path']).read_text(encoding='utf-8'))
    except (OSError, ValueError, KeyError):
        return None


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description='Reference batch over the 21 web strategies (REFERENCE_ONLY, NOT_CERTIFIED).')
    parser.add_argument('--plan-only', action='store_true')
    parser.add_argument('--workers', type=int, default=6)
    args = parser.parse_args(argv)
    report = run_batch(workers=args.workers, plan_only=args.plan_only)
    print(json.dumps({'batch_id': report['batch_id'], 'counts': report['counts']}, ensure_ascii=True))
    return 0


if __name__ == '__main__':
    sys.exit(main())
