"""Engine adapter for platform V2 runs (change 2026-11-12, design D3). Part of every RID2 identity: keep report and CLI code out of it.

Runs the unchanged scripts/legacy_baseline_run._run_track on snapshot inputs (scripts/history_engine._prepared), exactly as
scripts/reference_engine.execute_one does for V1, with five names replaced at call time: runner.BacktestEngine (the V2 class for the
rule), legacy.INITIAL_CAPITAL, legacy._costs, reference_engine._bars and, for TQuant ports, _tquant.load_history. patch.object raises if
any of them disappears.
"""
from __future__ import annotations

import contextlib
from unittest.mock import patch


def cache_parts(prep, snapshot_id) -> dict:
    from scripts import native7_rules_only as n7
    from scripts.research_workflow import sha256
    return {'snapshot_id': snapshot_id, 'financials': prep.digests.get('financials'), 'revenue': prep.digests.get('revenue'),
            'publications': prep.digests.get('publications'), 'chip_manifest': sha256(n7.odr.PREV_CHIPS),
            'daily_equivalent_fill': prep.data.daily_equivalent_fill}


def execute(strategy_id, snapshot_id, start, end, capital, costs, rule, use_cache=True) -> dict:
    """Base and 2x cost tracks with fills, positions and execution events; the selection is computed once and shared by both tracks."""
    import core.backtest.runner as engine_runner
    from core.research.platform import feature_cache
    from core.research.platform.engine_v2 import rule_engine
    from core.research.reference.catalog import capabilities
    from core.research.reference.runner import check_reference_window
    from scripts import history_engine, reference_engine
    from scripts import legacy_baseline_run as legacy
    check_reference_window(start, end)
    cap = {c.strategy_id: c for c in capabilities()}[strategy_id]
    engine_class = rule_engine(rule)
    prep = history_engine._prepared(snapshot_id, start, end)
    key = feature_cache.cache_key(cache_parts(prep, snapshot_id)) if use_cache else None
    cached = bool(key) and (getattr(prep.data, 'feature_cache_original', None) is not None or feature_cache.attach(prep.data, key))
    bars = history_engine._tquant_bars(snapshot_id)
    select, memo = engine_runner.select_backtest_candidates, {}

    def shared_select(strategy, platform, name, frame, asof_date, runtime_overrides=None):
        memo_key = (name, str(asof_date))
        if memo_key not in memo:
            memo[memo_key] = select(strategy, platform, name, frame, asof_date, runtime_overrides)
        return memo[memo_key].copy()

    tracks = {}
    reference_engine.snapshot_history.cache_clear()
    try:
        with contextlib.ExitStack() as stack:
            stack.enter_context(patch.object(engine_runner, 'BacktestEngine', engine_class))
            stack.enter_context(patch.object(engine_runner, 'select_backtest_candidates', shared_select))
            stack.enter_context(patch.object(legacy, 'INITIAL_CAPITAL', capital))
            stack.enter_context(patch.object(legacy, '_costs', lambda multiple=1: {k: v * multiple for k, v in costs.items()}))
            stack.enter_context(patch.object(reference_engine, '_bars', lambda: bars))
            if cap.engine == 'TQUANT_SNAPSHOT':
                from core.strategies import _tquant
                stack.enter_context(patch.object(_tquant, 'load_history', reference_engine.snapshot_history))
            for name, multiple in (('base', 1), ('stress', legacy.STRESS_MULTIPLE)):
                track, engine, _ = legacy._run_track(cap.strategy_id, {}, prep, multiple)
                tracks[name] = track | {'fills': engine.fill_rows(), 'execution_events': list(engine.execution_events)}
    finally:
        reference_engine.snapshot_history.cache_clear()
    sample = prep.sessions[:: max(1, len(prep.sessions) // 3)][:3]
    import pandas as pd
    mismatches = feature_cache.verify(prep.data, key, [pd.Timestamp(d) for d in sample]) if cached else []
    return {'tracks': tracks, 'ledger_version': prep.digests.get('action_ledger'), 'family': cap.family,
            'feature_cache': {'key': key, 'attached': cached, 'verified_dates': sample if cached else [], 'mismatches': mismatches}}
