import pandas as pd
import pytest
import json
from pathlib import Path

from core.research.repair_runner import _hook
from scripts.research_v7_hypcost import overlap, paired_bootstrap


@pytest.mark.parametrize('held,claims,expected', [({'B': 1}, [], 'B'), ({'C': 1}, [], 'A'),
                                                  ({'C': 1}, [{'stock_id': 'B', 'shares': 1}], 'B')])
def test_buffer_uses_actual_positions_and_uncredited_shares(held, claims, expected):
    timestamp = '2025-01-02T06:00:00+00:00'
    rows = [{'asof_date': pd.Timestamp('2025-01-02'), 'execution_date': pd.Timestamp('2025-01-03'),
             'asset_id': asset, 'top_n': 1, 'stock_weighting': 'score_weighted', 'composite_score': score,
             'target_weight': 0.5, 'signal_at': timestamp, 'score_available_at': timestamp,
             'membership_available_at': timestamp} for asset, score in [('A', 3), ('B', 2)]]
    bars = {'2025-01-02': {asset: {'close': 10, 'close_available_at': timestamp} for asset in 'ABC'},
            '2025-01-03': {}}
    decisions = []
    hook = _hook(pd.DataFrame(rows), bars, execution_calendar_mode='NEXT_SESSION_EVENT',
                 rank_buffer_multiple=2, decisions=decisions)
    hook({'date': '2025-01-02', 'positions': held, 'stock_receivable': claims,
          'stale_marks': {}, 'nav': 100}, [])
    assert [(row['asset_id'], row['target_weight']) for row in decisions] == [(expected, 1.0)]


def test_missing_score_drops_existing_holding():
    timestamp = '2025-01-02T06:00:00+00:00'
    candidate = {'asof_date': pd.Timestamp('2025-01-02'), 'execution_date': pd.Timestamp('2025-01-03'),
                 'asset_id': 'A', 'top_n': 1, 'stock_weighting': 'score_weighted', 'composite_score': 3.,
                 'target_weight': 1., 'signal_at': timestamp, 'score_available_at': timestamp,
                 'membership_available_at': timestamp}
    bars = {'2025-01-02': {asset: {'close': 10, 'close_available_at': timestamp} for asset in 'AB'}, '2025-01-03': {}}
    decisions = []
    hook = _hook(pd.DataFrame([candidate]), bars, execution_calendar_mode='NEXT_SESSION_EVENT',
                 rank_buffer_multiple=2, decisions=decisions)
    result = hook({'date': '2025-01-02', 'positions': {'B': 1}, 'stock_receivable': [],
                   'stale_marks': {}, 'nav': 100}, [])
    assert [row['asset_id'] for row in decisions] == ['A']
    assert {(order['stock_id'], order['side']) for order in result['orders']} == {('A', 'buy'), ('B', 'sell')}


def test_nonfinite_score_is_excluded_before_pit_candidate_selection(monkeypatch):
    from core.research import repair_runner
    from scripts.research_mode_run import FAMILIES, configs
    spec = FAMILIES['day3']
    config = list(configs('day3', spec['prepared'], spec['signals'], spec['output'] / 'runner_evidence.json'))[289]
    scores = json.loads(Path(config['inputs']['scores']).read_text(encoding='utf-8'))
    universe = json.loads(Path(config['inputs']['universe']).read_text(encoding='utf-8'))
    day = scores[0]['asof_date']
    scores = [row for row in scores if row['asof_date'] == day]
    universe = [row for row in universe if row['asof_date'] == day]
    target = scores[0]['asset_id']
    scores[0]['composite_score'] = float('nan')
    original = repair_runner.build_targets
    class Checked(Exception):
        pass
    def check(*args, **kwargs):
        selected = original(*args, **kwargs)
        assert target not in set(selected.asset_id)
        raise Checked
    monkeypatch.setattr(repair_runner, 'build_targets', check)
    with pytest.raises(Checked):
        repair_runner._research_tracks(bars={}, scores=scores, universe=universe, actions=[], coverage=[],
                                       unresolved_events=[], parameters={**config, 'rank_buffer_multiple': 2})
    scores[0]['score_available_at'] = '2099-01-01T00:00:00+00:00'
    with pytest.raises(repair_runner.DataBlocked, match='PIT_LOOKAHEAD'):
        repair_runner._research_tracks(bars={}, scores=scores, universe=universe, actions=[], coverage=[],
                                       unresolved_events=[], parameters={**config, 'rank_buffer_multiple': 2})


def test_empty_candidate_day_sells_actual_position(monkeypatch):
    from core.research import repair_runner
    from scripts.research_mode_run import FAMILIES, configs
    spec = FAMILIES['day3']
    config = list(configs('day3', spec['prepared'], spec['signals'], spec['output'] / 'runner_evidence.json'))[289]
    all_scores = json.loads(Path(config['inputs']['scores']).read_text(encoding='utf-8'))
    all_universe = json.loads(Path(config['inputs']['universe']).read_text(encoding='utf-8'))
    day = all_scores[0]['asof_date']
    scores = [{**row, 'composite_score': None} for row in all_scores if row['asof_date'] == day]
    universe = [row for row in all_universe if row['asof_date'] == day]
    asset = scores[0]['asset_id']
    next_day = universe[0]['execution_date'][:10]
    bars = {day: {asset: {'close': 10, 'close_available_at': scores[0]['signal_at']}}, next_day: {}}
    class Checked(Exception):
        pass
    captured = []
    original_hook = repair_runner._hook
    def capture_hook(*args, **kwargs):
        captured.append(kwargs['decisions'])
        return original_hook(*args, **kwargs)
    def fake_simulate(*, close_hook, **kwargs):
        result = close_hook({'date': day, 'positions': {asset: 4}, 'stock_receivable': [],
                             'stale_marks': {}, 'nav': 100}, [])
        assert [(order['stock_id'], order['side'], order['shares']) for order in result['orders']] == [(asset, 'sell', 4)]
        assert captured[0][0]['empty_target_day'] is True
        assert captured[0][0]['target_weight'] == 0
        raise Checked
    monkeypatch.setattr(repair_runner, '_hook', capture_hook)
    monkeypatch.setattr(repair_runner, 'simulate', fake_simulate)
    with pytest.raises(Checked):
        repair_runner._research_tracks(bars=bars, scores=scores, universe=universe, actions=[], coverage=[],
                                       unresolved_events=[], parameters={**config, 'rank_buffer_multiple': 2})


def test_empty_target_marker_counts_zero_overlap():
    old = pd.DataFrame({'asof_date': ['2025-01-02'], 'asset_id': ['A'], 'target_weight': [1.]})
    empty = pd.DataFrame({'asof_date': ['2025-01-02'], 'asset_id': [''], 'target_weight': [0.]})
    assert overlap(old, empty, 1) == 0


def test_overlap_and_paired_boundaries():
    old = pd.DataFrame({'asof_date': ['d'], 'asset_id': ['A'], 'target_weight': [1.]})
    new = pd.DataFrame({'asof_date': ['d'], 'asset_id': ['B'], 'target_weight': [1.]})
    assert overlap(old, new, 1) == 0
    assert overlap(old, old, 1) == 1
    with pytest.raises(ValueError, match='DUPLICATE'):
        overlap(old, pd.concat([new, new]), 1)
    assert paired_bootstrap(pd.Series([0.] * 40), pd.Series([0.] * 40))['status'] == 'NOT_COMPUTABLE'
    assert paired_bootstrap(pd.Series([0.] * 40), pd.Series([0.] * 40, index=range(1, 41)))['reason'] == 'DATE_MISMATCH'


def test_bootstrap_confidence_quantiles(monkeypatch):
    import numpy as np
    from scripts import track_b_stats
    observed = []
    original = np.nanquantile
    def capture(a, q):
        observed.append(q)
        return original(a, q)
    monkeypatch.setattr(np, 'nanquantile', capture)
    track_b_stats.block_bootstrap_sharpe(np.arange(80), draws=100, confidence=0.95)
    track_b_stats.block_bootstrap_sharpe(np.arange(80), draws=100, confidence=0.975)
    assert observed[0][0] == pytest.approx(0.025)
    assert observed[1][0] == pytest.approx(0.0125)
