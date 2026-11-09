"""Fixed-configuration composite strategies for research: condition intersection or factor-weighted percentile sum.

Binding a configuration: the hypothesis registry row (hypothesis, falsification_test or source text) must contain
`config_sha256=<config_sha256(mode, components, top_n, min_names)>`. Weights are therefore fixed at registration and
cannot be searched at run time. Nothing here is registered in StrategyManager or the legacy registry.
"""
from __future__ import annotations

import csv
import hashlib
import json
import math
from pathlib import Path

import pandas as pd

from core.recommendation.contracts import Recommendation
from core.research import strategy_signal as ss
from core.strategy.contracts import SelectionResult, StrategyContext
from scripts import research_workflow as workflow

CONDITION_INTERSECTION = 'CONDITION_INTERSECTION'
FACTOR_WEIGHTED = 'FACTOR_WEIGHTED'


def composite_config(mode, components, top_n, min_names) -> dict:
    """components: (condition_id, weight) or (condition_id, scorer, weight); scorers are not part of the hash."""
    return {'mode': mode, 'components': sorted([str(c[0]), float(c[-1])] for c in components), 'top_n': int(top_n), 'min_names': int(min_names)}


def config_sha256(mode, components, top_n, min_names) -> str:
    return hashlib.sha256(json.dumps(composite_config(mode, components, top_n, min_names), sort_keys=True).encode()).hexdigest()


class CompositeExecutor:
    """scorer(context, signal_date) -> DataFrame indexed by ticker with 'score' (float, higher better) and 'passes' (bool)."""

    def __init__(self, mode, components, hypothesis_id, top_n=10, min_names=5, registry=workflow.REGISTRY):
        components = tuple(components)
        if mode not in (CONDITION_INTERSECTION, FACTOR_WEIGHTED):
            raise ValueError(f'MODE_INVALID:{mode}')
        ids = [c[0] for c in components]
        if not components or len(set(ids)) != len(ids):
            raise ValueError('WEIGHTS_INVALID:EMPTY_OR_DUPLICATE_CONDITION')
        weights = [c[2] for c in components]
        if not all(isinstance(w, (int, float)) and math.isfinite(w) for w in weights):
            raise ValueError('WEIGHTS_INVALID:MISSING_OR_NON_FINITE')
        if mode == FACTOR_WEIGHTED and abs(sum(weights) - 1) > 1e-9:
            raise ValueError(f'WEIGHTS_INVALID:SUM={sum(weights)}')
        status = workflow.verify_registry(registry)
        if status != 'PASS':
            raise ValueError(f'REGISTRY_UNVERIFIED:{status}')
        rows = {r['hypothesis_id']: r for r in csv.DictReader(Path(registry).open(encoding='utf-8-sig'))}
        if hypothesis_id not in rows:
            raise ValueError(f'NOT_REGISTERED:{hypothesis_id}')
        self.config_sha256 = config_sha256(mode, components, top_n, min_names)
        row = rows[hypothesis_id]
        if f'config_sha256={self.config_sha256}' not in ' '.join(row[k] for k in ('hypothesis', 'falsification_test', 'source')):
            raise ValueError(f'CONFIG_NOT_REGISTERED:{self.config_sha256}')
        self.mode, self.components, self.hypothesis_id, self.top_n, self.min_names = mode, components, hypothesis_id, top_n, min_names

    def execute(self, context: StrategyContext, spec=None) -> SelectionResult:
        signal_date = context.asof_date
        if signal_date is None:
            raise ValueError('SIGNAL_DATE_REQUIRED')
        ss.check_window(signal_date)
        pct, passes = {}, {}
        for cid, scorer, _ in self.components:
            frame = scorer(context, signal_date)
            pct[cid] = frame['score'].astype(float).rank(pct=True, method='average')
            passes[cid] = frame['passes'].astype(bool)
        table = pd.DataFrame(pct)
        table.index.name = 'ticker'
        if self.mode == CONDITION_INTERSECTION:
            ok = pd.DataFrame({cid: p.reindex(table.index, fill_value=False) for cid, p in passes.items()}).all(axis=1)
            table = table[ok]
            final = table.mean(axis=1)
        else:  # ponytail: only tickers scored by every factor enter; standardize has no status for a partial score
            table = table.dropna()
            final = sum(w * table[cid] for cid, _, w in self.components)
        frame = table.assign(final_score=final, eligibility_status='ELIGIBLE').reset_index()
        if frame.empty:
            frame = pd.DataFrame(columns=['ticker', 'final_score', 'eligibility_status', *pct])
        data = context.data if isinstance(context.data, dict) else {}
        version = f'{self.hypothesis_id}:{self.mode}'
        signals = ss.standardize(frame, signal_date=signal_date, top_n=self.top_n, strategy_version=version,
                                 data_version=data.get('data_version', 'UNSPECIFIED'),
                                 digest=ss.source_hash({**data.get('digests', {}), 'config_sha256': self.config_sha256}), components=tuple(pct))
        picks = signals.loc[signals['target_weight'] > 0]
        status = 'NO_ELIGIBLE_STOCKS' if picks.empty else 'INSUFFICIENT_DIVERSIFICATION' if len(picks) < self.min_names else 'OK'
        return SelectionResult(rows=[Recommendation(stock_id=r.ticker, strategy_id=self.hypothesis_id, asof_date=r.signal_date, score=float(r.final_score),
                                                    rank=int(r.rank), selected=True, target_weight=float(r.target_weight))
                                     for r in picks.itertuples(index=False)],
                               metadata={'signals': signals, 'status': status, 'strategy_type': self.mode, 'strategy_version': version,
                                         'config_sha256': self.config_sha256, 'input_dates': data.get('input_dates', {})})
