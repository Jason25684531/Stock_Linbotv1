"""Factor Combination Lab (design D11): round queue, 23-trial round cap, registered composite runs on the research engine.

Runs reuse core.research.repair_runner._research_tracks (corporate actions, suspensions, base + 2x cost tracks, reconciliation)
on prepared_v6 bars and the H4 REB20 universe; repair_runner.py itself is not modified. Every run is admitted first.
"""
import hashlib
import json
from pathlib import Path

import pandas as pd

from core.research import condition_pool as cp
from core.research import strategy_signal as ss
from core.strategy import composite
from core.strategy.contracts import StrategyContext
from scripts import research_workflow as workflow

ROOT = Path(__file__).resolve().parents[2]
OUTPUT = ROOT / 'outputs/research_mode/combination_lab_v1'
ROUND = '2026-11-06-msv'
ROUND_CAP = 23
START, END = '2023-01-03', '2025-12-31'
TOP_N, MIN_NAMES, REBALANCE = 10, 5, 20
COSTS = {'initial_cash': 1_000_000, 'fee_rate': .001425, 'tax_rate': .003, 'minimum_fee': 20, 'slippage': .001}
WEIGHTED = {'c4': 0.3, 'c2': 0.4, 'c3': 0.3}  # registered substitute: c5 quality is BLOCKED_PIT; 0.5/0.5 growth+momentum is the archived H4 REB20
HYPOTHESES = {'single': 'LAB-SINGLE-V1', 'weighted': 'LAB-WEIGHTED-V1', 'intersection': 'LAB-INTERSECT-V1'}
MODES = {'single': composite.CONDITION_INTERSECTION, 'intersection': composite.CONDITION_INTERSECTION, 'weighted': composite.FACTOR_WEIGHTED}


class BudgetExhausted(RuntimeError):
    pass


def components(item):
    if item['kind'] == 'weighted':
        return [(cid, item['weights'][cid]) for cid in sorted(item['weights'])]
    return [(cid, 1.0 / len(item['conditions'])) for cid in item['conditions']]


def item_sha(item):
    return composite.config_sha256(MODES[item['kind']], components(item), TOP_N, MIN_NAMES)


def run_config(item):
    """Admission identity of a lab run; `round` ties it to the 23-trial cap."""
    return {'round': ROUND, 'item_id': item['item_id'], 'kind': item['kind'], 'strategy_type': MODES[item['kind']],
            'conditions': item.get('conditions') or sorted(item['weights']), 'weights': item.get('weights'),
            'composite_config_sha256': item_sha(item), 'top_n': TOP_N, 'min_names': MIN_NAMES, 'rebalance_days': REBALANCE,
            'stock_weighting': 'equal', **COSTS, 'start': START, 'end': END, 'universe': 'h4_signals_v1/universe_20.json'}


def code_paths():
    return [ROOT / p for p in ('core/research/combination_lab.py', 'core/research/condition_pool.py', 'core/strategy/composite.py',
                               'core/research/repair_runner.py', 'core/research/repair_accounting.py', 'core/research/repair_targets.py')]


def input_paths():
    return {'bars': cp.PREPARED / 'bars.json', 'actions': cp.PREPARED / 'actions.json', 'universe': cp.SIGNALS / 'universe_20.json',
            'fundamental': cp.FUNDAMENTAL / 'fundamental_matrix.parquet'}


# ---------------------------------------------------------------- round budget

def round_trials(ledger=workflow.TRIAL_LEDGER):
    """TRIAL rows of this round (all hypotheses together)."""
    return sum(1 for r in workflow._trial_rows(ledger) if r['kind'] == 'TRIAL' and (r.get('config') or {}).get('round') == ROUND)


def _experiment_id(hypothesis_id, config, code, inputs, data_end):
    """Same identity as research_workflow._admit_experiment_locked, to tell a free REPLAY from a new TRIAL before admitting."""
    identity = {'hypothesis_id': hypothesis_id, 'data_end': data_end,
                'config_sha256': hashlib.sha256(workflow._canonical(config).encode('utf-8')).hexdigest(),
                'code_sha256': {Path(p).as_posix(): workflow.sha256(p) for p in sorted(code, key=str)},
                'inputs_sha256': {name: workflow._tree_sha(p) for name, p in sorted(inputs.items())}}
    return hashlib.sha256(workflow._canonical(identity).encode('utf-8')).hexdigest()[:16]


def admit(hypothesis_id, config, code, inputs, *, data_end=END, registry=workflow.REGISTRY, ledger=workflow.TRIAL_LEDGER):
    """admit_experiment under the shared ledger lock, refusing a new TRIAL once the round holds ROUND_CAP of them."""
    if config.get('round') != ROUND:
        raise ValueError('ROUND_TAG_REQUIRED')
    ss.check_window(data_end)
    with workflow._ledger_lock(ledger):
        rows = workflow._trial_rows(ledger)
        replay = any(r['experiment_id'] == _experiment_id(hypothesis_id, config, code, inputs, data_end) for r in rows)
        used = sum(1 for r in rows if r['kind'] == 'TRIAL' and (r.get('config') or {}).get('round') == ROUND)
        if not replay and used >= ROUND_CAP:
            raise BudgetExhausted(f'BUDGET_EXHAUSTED:{used}/{ROUND_CAP}')
        return workflow._admit_experiment_locked(hypothesis_id, config, code, inputs, data_end, registry, ledger)


# ---------------------------------------------------------------- queue (frozen before any performance exists)

def build_queue(qualified, combos, legacy_runnable, legacy_sensitivity, tq_candidates):
    """The seven-position round queue (design D11). Only qualification and combination statistics are inputs: no returns."""
    queue = [{'position': 1, 'item_id': f'LEGACY:{sid}', 'kind': 'legacy_baseline', 'strategy_id': sid} for sid in legacy_runnable]
    queue += [{'position': 2, 'item_id': f'TQ:{key}', 'kind': 'tq_baseline', 'strategy_id': key} for key in tq_candidates]
    queue += [{'position': 3, 'item_id': f'LAB:{cid}', 'kind': 'single', 'conditions': [cid]} for cid in sorted(qualified)[:8]]
    weights = WEIGHTED if all(c in qualified for c in WEIGHTED) else None
    queue.append({'position': 4, 'item_id': 'LAB:W:' + '+'.join(f'{c}x{w}' for c, w in sorted((weights or {}).items())), 'kind': 'weighted',
                  'weights': weights, 'skip': None if weights else 'WEIGHTED_COMPONENT_NOT_QUALIFIED'})
    eligible = sorted((c for c in combos if c['size'] >= 2 and c['coverage'] >= 0.8),
                      key=lambda c: (c['size'], c['mean_pairwise_jaccard'], c['combination_id']))
    queue += [{'position': 5 if i < 3 else 7, 'item_id': 'LAB:' + c['combination_id'], 'kind': 'intersection', 'conditions': c['conditions']}
              for i, c in enumerate(eligible)]
    sensitivity = [{'position': 6, 'item_id': f'LEGACY-SENS:{sid}', 'kind': 'legacy_sensitivity', 'strategy_id': sid, 'overrides': ov}
                   for sid, ov in legacy_sensitivity]
    queue = sorted(queue + sensitivity, key=lambda q: q['position'])  # stable: original order inside a position
    return queue


def freeze_queue(queue, extra, path=None):
    path = Path(path or OUTPUT / 'queue.json')
    path.parent.mkdir(parents=True, exist_ok=True)
    body = {'round': ROUND, 'cap': ROUND_CAP, 'queue': queue, **extra}
    if path.exists():
        saved = json.loads(path.read_text(encoding='utf-8'))
        if saved['queue'] != json.loads(json.dumps(queue)):
            raise ValueError('QUEUE_CHANGED_AFTER_FREEZE')
        return saved, workflow.sha256(path)
    with path.open('x', encoding='utf-8') as stream:
        json.dump(body, stream, ensure_ascii=False, indent=1)
    return body, workflow.sha256(path)


def registration(kind, items, budget):
    """Registry row binding every queued configuration of one lab hypothesis by its composite config SHA256."""
    shas = ' '.join(f'config_sha256={item_sha(i)}' for i in items)
    return {'hypothesis_id': HYPOTHESES[kind], 'strategy': f'combination_lab:{kind}',
            'hypothesis': f'{kind} lab runs of round {ROUND} (fixed queue, TOP{TOP_N} REB{REBALANCE} equal) {shas}',
            'falsification_test': 'exploratory: net 2x-cost Sharpe <= 0 or no excess vs TAIEX price; never a certification; OOS not available',
            'data_window': f'{START}..{END}', 'trial_budget': str(budget), 'source': f'design.md D11 queue.json round {ROUND}'}


# ---------------------------------------------------------------- one lab run

def score_rows(item, *, registry=workflow.REGISTRY):
    """Per signal day: CompositeExecutor selection turned into research-engine score rows, plus the period statuses."""
    scorer = lambda cid: (lambda context, day: cp.day_scores(cid, str(day)[:10]))  # noqa: E731
    comps = tuple((cid, scorer(cid), w) for cid, w in components(item))
    executor = composite.CompositeExecutor(MODES[item['kind']], comps, HYPOTHESES[item['kind']], top_n=TOP_N, min_names=MIN_NAMES,
                                           registry=registry)
    data = cp.inputs()
    digest = workflow.sha256(cp.PREPARED / 'bars.json')
    rows, statuses = [], {}
    for day in data['days']:
        result = executor.execute(StrategyContext(asof_date=day, data={'data_version': digest}))
        statuses[day] = result.metadata['status']
        for _, row in result.metadata['signals'].iterrows():
            rows.append({'asof_date': day, 'asset_id': str(row['ticker']), 'composite_score': float(row['final_score']),
                         'signal_at': f'{day}T15:00:00+08:00', 'score_available_at': f'{day}T13:30:00+08:00', 'score_source_sha256': digest})
    return rows, statuses


def simulate(item, rows):
    """The research engine on prepared_v6 (both cost tracks); the same call path the archived H4 runs used."""
    from core.research.repair_runner import _research_tracks
    from core.research.research_mode import RESEARCH_ASSUMPTIONS_V6
    market = json.loads((cp.PREPARED / 'bars.json').read_text(encoding='utf-8'))
    bars = {day: daily for day, daily in market['bars'].items() if START <= day <= END}
    actions = json.loads((cp.PREPARED / 'actions.json').read_text(encoding='utf-8'))
    parameters = {'config_id': item['item_id'], 'top_n': TOP_N, 'stock_weighting': 'equal', **COSTS}
    return _research_tracks(bars=bars, scores=rows, universe=cp.inputs()['universe'],
                            actions=[e for e in actions['actions'] if e['ex_date'] in bars], coverage=actions['coverage'],
                            unresolved_events=[e for e in actions['unresolved_events'] if e['ex_date'] in bars],
                            parameters=parameters, assumptions=RESEARCH_ASSUMPTIONS_V6), bars


def trades_from_fills(ledger):
    """Position episodes (flat -> flat) per stock from FILL rows: one trade each, cost = fees + taxes."""
    open_, trades = {}, []
    for row in ledger:
        if row['kind'] != 'FILL':
            continue
        lot = open_.setdefault(row['stock_id'], {'stock_id': row['stock_id'], 'buy_date': row['date'], 'shares': 0, 'bought': 0.0,
                                                 'sold': 0.0, 'buy_shares': 0, 'sell_shares': 0, 'cost': 0.0})
        lot['cost'] += row['fee'] + row['tax']
        if row['side'] == 'buy':
            lot['shares'] += row['shares']; lot['bought'] += row['notional']; lot['buy_shares'] += row['shares']
        else:
            lot['shares'] -= row['shares']; lot['sold'] += row['notional']; lot['sell_shares'] += row['shares']
        if lot['shares'] <= 0:
            trades.append({'stock_id': lot['stock_id'], 'buy_date': lot['buy_date'], 'sell_date': row['date'],
                           'buy_price': lot['bought'] / lot['buy_shares'], 'sell_price': lot['sold'] / max(lot['sell_shares'], 1),
                           'shares': lot['buy_shares'], 'cost': lot['cost'], 'pnl': lot['sold'] - lot['bought'] - lot['cost']})
            del open_[row['stock_id']]
    return pd.DataFrame(trades, columns=['stock_id', 'buy_date', 'sell_date', 'buy_price', 'sell_price', 'shares', 'cost', 'pnl'])
