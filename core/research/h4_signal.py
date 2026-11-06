"""H4:GROWTH-MOMENTUM-V2 as a research StrategyExecutor. Scores come from the archived producer (prepare_h4_signals.score_components).

Registered only in build_research_registry(), never in the legacy registry that StrategyManager, Web and LINE read.
"""
from __future__ import annotations

from functools import lru_cache
import json

import numpy as np
import pandas as pd

from core.recommendation.contracts import Recommendation
from core.research import strategy_signal as ss
from core.strategy.contracts import SelectionResult, StrategyContext
from core.strategy.lifecycle import StrategyLifecycle
from core.strategy.registry import StrategyRegistry
from core.strategy.spec import StrategySpec

STRATEGY_ID = 'H4:GROWTH-MOMENTUM-V2'
COMPONENTS = {'g2_score': 'operating_income_yoy', 'g3_score': 'eps_yoy', 'momentum_score': None}
SPEC = StrategySpec(
    strategy_id=STRATEGY_ID, display_name='H4 Growth Momentum V2 (research)', version='V2', lifecycle=StrategyLifecycle.RESEARCH,
    required_features=('operating_income_yoy', 'eps_yoy', 'near_high_252d'),
    parameters={'top_n': 10, 'rebalance_days': 60, 'weights': {'growth': .5, 'momentum': .5}},
    ranking_policy={'order': 'final_score desc, ticker asc'}, portfolio_policy={'weighting': 'equal', 'max_weight': .1},
    execution_policy={'publish': 'DISABLED', 'research_only': True},
    metadata={'registration': 'H4:GROWTH-MOMENTUM-V2', 'certification': 'UNCERTIFIED_RESEARCH'})


class H4Executor:
    """context.data: members, non_members, growth_day {metric: Series}, momentum_day Series or None, ready, data_version, digests."""

    def execute(self, context: StrategyContext, spec: StrategySpec) -> SelectionResult:
        from scripts.prepare_h4_signals import score_components
        data = context.data
        ranks, g, mom = score_components(pd.Index(data['members']), data['growth_day'], data['momentum_day'], data['ready'])
        both = g.index.intersection(mom.index)
        final = .5 * g[both] + .5 * mom[both]
        rows = []
        excluded = set(data.get('event_excluded', ()))  # scored like the archive, dropped before selection like the archived runner
        for ticker in data['members']:
            status = 'NO_GROWTH' if ticker not in g.index else 'NO_MOMENTUM' if ticker not in both else 'EVENT_EXCLUDED' if ticker in excluded else 'ELIGIBLE'
            rows.append({'ticker': ticker, 'final_score': final.get(ticker, np.nan), 'eligibility_status': status,
                         'g2_score': ranks.get('operating_income_yoy', pd.Series(dtype=float)).get(ticker, np.nan),
                         'g3_score': ranks.get('eps_yoy', pd.Series(dtype=float)).get(ticker, np.nan),
                         'momentum_score': mom.get(ticker, np.nan)})
        rows += [{'ticker': t, 'final_score': np.nan, 'eligibility_status': 'NOT_MEMBER'} for t in data.get('non_members', ())]
        signals = ss.standardize(pd.DataFrame(rows), signal_date=context.asof_date, top_n=spec.parameters['top_n'], strategy_version=spec.strategy_id,
                                 data_version=data['data_version'], digest=ss.source_hash(data['digests']), components=tuple(COMPONENTS))
        picks = signals.loc[signals['rank'].notna()]
        return SelectionResult(rows=[Recommendation(stock_id=r.ticker, strategy_id=spec.strategy_id, asof_date=r.signal_date, score=float(r.final_score),
                                                    rank=int(r.rank), selected=r.target_weight > 0, target_weight=float(r.target_weight))
                                     for r in picks.itertuples(index=False)],
                               metadata={'signals': signals, 'input_dates': data.get('input_dates', {}), 'digests': data['digests']})


def build_research_registry() -> StrategyRegistry:
    """Research strategies only; adding one is a new executor plus one registration here."""
    registry = StrategyRegistry()
    registry.register(SPEC, factory=H4Executor)
    return registry


@lru_cache(maxsize=1)
def _momentum():
    from core.research.d5_research_variant import raw_factor_panels
    from scripts.prepare_h1_signals import frames_of
    from scripts.prepare_h4_signals import ROOT
    market = json.loads((ROOT / 'outputs/research_mode/prepared_v6/bars.json').read_text(encoding='utf-8'))
    ss.check_window(max(market['bars']))
    return raw_factor_panels(frames_of(market))['near_high_252d']


def dev_context(signal_date) -> StrategyContext:
    """Approved development inputs (prepared_v6, fundamental v3.2, G2/G3 factor result) for one signal day of 2023-01-03..2025-12-31."""
    from scripts.prepare_h4_signals import FACTOR_RESULT, FUNDAMENTAL, GROWTH, ROOT
    from scripts.prepare_research_mode import digest
    day = str(signal_date)[:10]
    ss.check_window(day)
    if day < ss.DEV_START:
        raise ss.ResearchOnlyViolation('BEFORE_DEVELOPMENT_WINDOW')
    prepared = ROOT / 'outputs/research_mode/prepared_v6'
    manifest = json.loads((prepared / 'input_manifest.json').read_text(encoding='utf-8'))
    for name, expected in manifest['files'].items():
        if digest(prepared / name) != expected:
            raise ValueError(f'INPUT_CHECKPOINT_UNVERIFIED:{name}')
    result = json.loads(FACTOR_RESULT.read_text(encoding='utf-8'))
    ready = [GROWTH[f] for f, d in result['decision'].items() if d == 'READY_FOR_STRATEGY_TEST']
    if result['admission'] != 'PASS' or not ready:
        raise ValueError('H4_RUN_CONDITION_NOT_MET')
    universe = pd.DataFrame(json.loads((prepared / 'universe.json').read_text(encoding='utf-8')))
    universe = universe.loc[universe['asof_date'] == day]
    if universe.empty:
        raise ValueError('NOT_A_DEVELOPMENT_SESSION')
    matrix = pd.read_parquet(FUNDAMENTAL / 'fundamental_matrix.parquet')
    ss.check_window(pd.to_datetime(matrix['asof_date']).max())
    rows = matrix.loc[pd.to_datetime(matrix['asof_date']) == pd.Timestamp(day)].assign(ticker=lambda f: f['ticker'].astype(str)).set_index('ticker')
    records = pd.read_parquet(FUNDAMENTAL / 'fundamental_records.parquet', columns=['available_date'])
    momentum = _momentum()
    stamp = pd.Timestamp(day)
    members = sorted(universe.loc[universe['member'], 'asset_id'].astype(str))
    # repair_runner applies unresolved-event exclusions known by the signal time after scoring; same call here
    from core.research.research_mode import apply_event_exclusions
    events = json.loads((prepared / 'actions.json').read_text(encoding='utf-8'))['unresolved_events']
    signal_at = day + 'T15:00:00+08:00'
    filtered = apply_event_exclusions(universe.reset_index(drop=True), {(day, a): signal_at for a in universe['asset_id']}, events)
    excluded = sorted(set(members) - set(filtered.loc[filtered['member'], 'asset_id'].astype(str)))
    files = {'bars.json': prepared / 'bars.json', 'universe.json': prepared / 'universe.json', 'factor_result.json': FACTOR_RESULT,
             'fundamental_matrix.parquet': FUNDAMENTAL / 'fundamental_matrix.parquet', 'fundamental_records.parquet': FUNDAMENTAL / 'fundamental_records.parquet'}
    available = pd.to_datetime(records['available_date'])
    return StrategyContext(asof_date=day, universe=tuple(members), data={
        'members': members, 'non_members': sorted(universe.loc[~universe['member'], 'asset_id'].astype(str)), 'ready': ready, 'event_excluded': excluded,
        'growth_day': {m: rows[m] for m in ready}, 'momentum_day': momentum.loc[stamp] if stamp in momentum.index else None,
        'data_version': 'prepared_v6+fundamental-pit-v3.2-verified', 'digests': {k: digest(v) for k, v in files.items()},
        'input_dates': {'bars': str(momentum.index[momentum.index <= stamp].max().date()),
                        'fundamentals_latest_available': str(available[available <= stamp].max().date()),
                        'universe': day}})


def synthetic_context(signal_date='2025-06-30', n=40, seed=None) -> StrategyContext:
    """Generated members, growth and momentum; no repository data is read."""
    seed = int(str(signal_date).replace('-', '')) if seed is None else seed
    rng = np.random.default_rng(seed)
    tickers = [f'9{i:03d}' for i in range(n)]
    growth = {m: pd.Series(rng.normal(size=n), index=tickers).mask(rng.random(n) < .1) for m in ('operating_income_yoy', 'eps_yoy')}
    momentum = pd.Series(rng.random(n), index=tickers).mask(rng.random(n) < .05)
    return StrategyContext(asof_date=signal_date, universe=tuple(tickers[:-3]), data={
        'members': tickers[:-3], 'non_members': tickers[-3:], 'ready': ['operating_income_yoy', 'eps_yoy'], 'growth_day': growth,
        'momentum_day': momentum, 'data_version': f'synthetic-seed-{seed}', 'digests': {'synthetic': f'seed={seed},n={n}'},
        'input_dates': {'bars': signal_date, 'fundamentals_latest_available': signal_date, 'universe': signal_date}})
