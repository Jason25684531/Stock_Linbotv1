"""Characterize the unmodified V6 PIT selection against its sealed outputs."""
import json
from pathlib import Path

import pandas as pd
import pytest
from pandas.testing import assert_frame_equal

from core.research.repair_targets import build_targets
from core.research.research_mode import apply_event_exclusions
from scripts.research_mode_run import FAMILIES, configs
from scripts.research_workflow import sha256


@pytest.mark.parametrize('index', [289, 129])
def test_v6_pit_archive(index):
    spec = FAMILIES['day3']
    archive = spec['output'] / str(index)
    manifest = json.loads((archive / 'manifest.json').read_text(encoding='utf-8'))
    for name, expected in manifest['output_sha256'].items():
        assert sha256(archive / name) == expected
    config = list(configs('day3', spec['prepared'], spec['signals'], spec['output'] / 'runner_evidence.json'))[index]
    for name, path in config['inputs'].items():
        assert sha256(path) == manifest['inputs_sha256'][name]
    scores = json.loads(Path(config['inputs']['scores']).read_text(encoding='utf-8'))
    universe = json.loads(Path(config['inputs']['universe']).read_text(encoding='utf-8'))
    actions = json.loads(Path(config['inputs']['actions']).read_text(encoding='utf-8'))
    days = {row['asof_date']: row['signal_at'] for row in scores}
    usable = [row for row in universe if row['asof_date'] in days]
    signals = {(row['asof_date'], row['asset_id']): days[row['asof_date']] for row in usable}
    events = [row for row in actions['unresolved_events'] if '2023-01-03' <= row['ex_date'] <= '2025-12-31']
    filtered = apply_event_exclusions(pd.DataFrame(usable), signals, events)
    score_keys = {(row['asof_date'], row['asset_id']) for row in scores}
    filtered['member'] &= pd.Series([(r.asof_date, r.asset_id) in score_keys for r in filtered.itertuples()], index=filtered.index)
    valid_days = set(filtered.loc[filtered.member, 'asof_date'])
    universe_keys = set(zip(filtered.asof_date, filtered.asset_id))
    selected_scores = [r for r in scores if r['asof_date'] in valid_days and (r['asof_date'], r['asset_id']) in universe_keys]
    actual = build_targets(pd.DataFrame(selected_scores), filtered[filtered.asof_date.isin(valid_days)],
                           config_id=config['config_id'], top_n=config['top_n'], stock_weighting=config['stock_weighting'],
                           execution_calendar_mode='NEXT_SESSION_EVENT')
    expected = pd.read_csv(archive / 'targets.csv', dtype={'asset_id': str})
    actual['asof_date'] = actual['asof_date'].dt.strftime('%Y-%m-%d')
    actual['execution_date'] = pd.to_datetime(actual['execution_date']).dt.strftime('%Y-%m-%d')
    assert_frame_equal(actual.reset_index(drop=True), expected.reset_index(drop=True), check_dtype=False,
                       rtol=1e-9, atol=1e-12)
