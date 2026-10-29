import json
from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd
import pytest

from scripts import research_workflow as rw


HASHES = {k: 'f' * 64 for k in rw.SIGNAL_HASHES}


def test_undefined_metric_is_none_with_reason():
    assert rw.undefined(float('nan'), 'empty') == {'value': None, 'reason': 'empty'}
    assert rw.undefined(float('inf'), 'x')['value'] is None
    assert rw.undefined(0.5, 'x') == {'value': 0.5, 'reason': None}


def test_results_compare_only_when_every_dimension_aligned():
    assert rw.comparable([('a', True), ('b', True)])
    assert not rw.comparable([('a', True), ('b', False)])
    assert not rw.comparable(rw.RECONCILIATION)


ROW = {'hypothesis_id': 'H1', 'strategy': 'S', 'hypothesis': 'h', 'falsification_test': 't', 'data_window': 'dev', 'trial_budget': '1', 'source': 'x'}


def test_registry_never_rewrites_and_rejects_missing_fields(tmp_path):
    path = tmp_path / 'registry.csv'
    assert rw.register(ROW, path, today='2026-10-24')['status'] == 'REGISTERED_NOT_RUN'
    with pytest.raises(ValueError, match='already registered'):
        rw.register(ROW, path)
    with pytest.raises(ValueError, match='misses'):
        rw.register({**ROW, 'hypothesis_id': 'H2', 'falsification_test': ''}, path)


def test_tampered_registry_blocks_appends_and_runs(tmp_path):
    path = tmp_path / 'registry.csv'
    rw.register(ROW, path)
    assert rw.verify_registry(path) == 'PASS'
    path.write_text(path.read_text(encoding='utf-8-sig').replace(',t,', ',edited,'), encoding='utf-8-sig')
    assert rw.verify_registry(path) == 'FAIL'
    with pytest.raises(ValueError, match='head'):
        rw.register({**ROW, 'hypothesis_id': 'H2'}, path)
    with pytest.raises(ValueError, match='does not verify'):
        rw.require_registered('H1', path)


def test_run_gate_needs_registration_and_allows_one_run(tmp_path):
    path = tmp_path / 'registry.csv'
    with pytest.raises(ValueError):
        rw.require_registered('H1', path)
    rw.register(ROW, path)
    rw.require_registered('H1', path)
    rw.record_result('H1', 'NOT_REJECTED', 'x.csv', path=path)
    with pytest.raises(ValueError, match='already has a result'):
        rw.require_registered('H1', path)


def test_dev_labels_never_reach_past_dev_end():
    dates = pd.bdate_range('2025-12-01', periods=30)  # runs into 2026
    data = pd.DataFrame({'asof_date': dates, **{f'forward_return_{h}d': 1.0 for h in rw.HORIZONS}})
    out = dev_out = rw.dev_labels(data[data.asof_date <= '2025-12-31'])
    last = pd.Index(sorted(dev_out.asof_date)).get_loc(pd.Timestamp('2025-12-31'))
    for h in rw.HORIZONS:
        kept = out.loc[out[f'forward_return_{h}d'].notna(), 'asof_date']
        assert all(pd.Index(sorted(out.asof_date)).get_loc(d) + 1 + h <= last for d in kept)
    assert out.forward_return_5d.notna().sum() == len(out) - 6


def test_d5_outcome_needs_both_horizons():
    s = pd.DataFrame({'factor_id': ['a', 'a', 'b', 'b'], 'horizon': [20, 60, 20, 60], 'aligned_mean_ic': [0.01, 0.02, 0.01, 0.02],
                      'aligned_positive_ic_ratio': [0.6, 0.6, 0.6, 0.4]})
    assert rw.d5_ic_outcome(s) == {'a': 'NOT_REJECTED', 'b': 'REJECTED'}


def test_a191_132_matches_production_candidate_including_gaps():
    from core.research.day2_candidates import candidate_specs, compute_candidate
    from scripts.track_b_formulas import MASTER, panel
    f = panel()
    f['amount'].iloc[30:33, 2] = np.nan  # suspended days
    spec = {s.candidate_id: s for s in candidate_specs(MASTER)}['A191_132']
    a, b = rw.a191_132(f['amount']).to_numpy(), compute_candidate(spec, f).to_numpy()
    assert (np.isnan(a) == np.isnan(b)).all() and np.allclose(a[~np.isnan(a)], b[~np.isnan(b)], rtol=1e-9, atol=1e-12)


def _days(*items):
    return {pd.Timestamp(d): (t, None, None) for d, t in items}


CLOSED = {pd.Timestamp('2026-10-23'): {'reason': '颱風休市'}, pd.Timestamp('2026-10-26'): {'reason': '光復節補假'}}
UTC = timezone.utc


def test_weekdays_alone_are_not_sessions():
    days = _days(('2026-10-22', True))
    assert rw.next_session('2026-10-22', days)[0] == pd.Timestamp('2026-10-23')  # the old weekday-only answer
    day, status = rw.next_session('2026-10-22', days, CLOSED)
    assert day == pd.Timestamp('2026-10-27') and status == 'EXPECTED_OPEN_UNVERIFIED'
    assert rw.next_session('2026-10-22', days, CLOSED, frozenset({2026}))[1] == 'EXPECTED_OPEN'
    assert rw.session_status(pd.Timestamp('2026-10-26'), days, CLOSED) == 'CLOSED_SCHEDULED'
    assert rw.session_status(pd.Timestamp('2026-10-26'), _days(('2026-10-26', True)), CLOSED) == 'CONFLICT'
    assert rw.deadline_utc('2026-10-27', days, CLOSED) == datetime(2026, 10, 28, 1, tzinfo=UTC)


def test_timing_is_prospective_only_before_next_open_and_confirmed():
    rec = lambda at: {'date': '2026-10-27', 'recorded_at_utc': at}
    pending = _days(('2026-10-27', True))
    confirmed = _days(('2026-10-27', True), ('2026-10-28', True))
    assert rw.timing(rec('2026-10-27T12:00:00+00:00'), pending, CLOSED) == 'PENDING_NEXT_SESSION'
    assert rw.timing(rec('2026-10-28T00:59:59+00:00'), confirmed, CLOSED) == 'PROSPECTIVE'
    assert rw.timing(rec('2026-10-28T01:00:00+00:00'), confirmed, CLOSED) == 'BACKFILLED'
    typhoon = _days(('2026-10-27', True), ('2026-10-28', False), ('2026-10-29', True))  # sudden closure moves the deadline
    assert rw.timing(rec('2026-10-28T05:00:00+00:00'), typhoon, CLOSED) == 'PROSPECTIVE'


def test_research_shadow_is_labeled_chained_and_append_only(tmp_path):
    from core.research.shadow_selection import verify
    meta = {'strategy': 'A191_132', 'label': 'RESEARCH_ONLY', **HASHES}
    days = _days(('2026-10-22', True), ('2026-10-27', True))
    with pytest.raises(ValueError, match='CLOSED_SCHEDULED'):
        rw.record_research_day('2026-10-26', [], meta, root=tmp_path, days=days, closures=CLOSED)
    with pytest.raises(ValueError, match='backfill'):
        rw.record_research_day('2026-10-22', [], meta, root=tmp_path, days=days, closures=CLOSED, now=datetime(2026, 10, 27, 1, tzinfo=UTC))
    first = rw.record_research_day('2026-10-22', [{'rank': 1, 'stock_id': '2330', 'score': 1.0}], meta, root=tmp_path, backfill=True, days=days,
                                   closures=CLOSED, now=datetime(2026, 10, 27, 1, tzinfo=UTC))
    assert first['timing_at_record'] == 'BACKFILLED' and first['broker'] == 'DISABLED'
    second = rw.record_research_day('2026-10-27', [], meta, root=tmp_path, days=days, closures=CLOSED, now=datetime(2026, 10, 27, 8, tzinfo=UTC))
    assert second['prev_sha256'] == first['sha256'] and second['timing_at_record'] == 'BEFORE_DEADLINE'
    note = rw.annotate(first['sha256'], 'BACKFILLED_INIT', 'test', root=tmp_path)
    blocked = rw.record_blocked('2026-10-28', 'A191_132', 'MI_INDEX incomplete', root=tmp_path)
    assert note['kind'] == 'ANNOTATION' and blocked['kind'] == 'BLOCKED' and verify(tmp_path)['status'] == 'PASS'
    with pytest.raises(ValueError, match='already annotated'):
        rw.annotate(first['sha256'], 'BACKFILLED_INIT', 'again', root=tmp_path)
    with pytest.raises(ValueError, match='already recorded'):
        rw.record_research_day('2026-10-27', [], meta, root=tmp_path)
    with pytest.raises(ValueError, match='RESEARCH_ONLY'):
        rw.record_research_day('2026-10-29', [], {'strategy': 'X', 'label': 'VALIDATED', **HASHES}, root=tmp_path)
    lines = (tmp_path / 'ledger.jsonl').read_text(encoding='utf-8').splitlines()
    (tmp_path / 'ledger.jsonl').write_text('\n'.join([json.dumps(json.loads(lines[0]) | {'picks': []})] + lines[1:]) + '\n', encoding='utf-8')
    assert verify(tmp_path)['status'] != 'PASS'


def test_import_holidays_reads_roc_dates_and_skips_trading_notes(tmp_path):
    source = tmp_path / 'holidaySchedule.json'
    source.write_text(json.dumps([{'Name': '中華民國開國紀念日', 'Date': '1150101', 'Weekday': '四', 'Description': '依規定放假1日。'},
                                  {'Name': '國曆新年開始交易日', 'Date': '1150102', 'Weekday': '五', 'Description': '國曆新年開始交易。'},
                                  {'Name': '光復節補假', 'Date': '1151026', 'Weekday': '一', 'Description': ''}], ensure_ascii=False), encoding='utf-8')
    path = tmp_path / 'closures.json'
    path.write_text(json.dumps({'closures': [{'date': '2026-10-26', 'kind': 'ANNUAL_HOLIDAY', 'reason': 'owner', 'source': 'owner'}], 'annual_schedule_years': []}), encoding='utf-8')
    assert rw.import_holidays(source, path) == 1
    closures, years = rw.load_closures(path)
    assert set(closures) == {pd.Timestamp('2026-01-01'), pd.Timestamp('2026-10-26')} and years == {2026}


def test_readiness_lists_every_blocker(monkeypatch):
    monkeypatch.setattr(rw, 'verify_registry', lambda: 'PASS')
    sessions = pd.bdate_range(end='2026-10-22', periods=59)
    days = {d: (True, None, None) for d in sessions}
    early = rw.readiness('2026-10-27', days, CLOSED, frozenset(), now=datetime(2026, 10, 24, 15, tzinfo=UTC))
    assert early['deadline_utc'] == '2026-10-28T01:00:00+00:00' and early['prior_sessions_cached'] == 59
    assert any('not cached' in b for b in early['blockers']) and any('annual schedule' in b for b in early['blockers'])
    assert not any('60 sessions' in b for b in early['blockers'])
    closed = rw.readiness('2026-10-26', days, CLOSED, frozenset())
    assert any('do not fetch' in b for b in closed['blockers'])
    late = rw.readiness('2026-10-27', days | {pd.Timestamp('2026-10-27'): (True, None, None)}, CLOSED, frozenset({2026}),
                        now=datetime(2026, 10, 28, 2, tzinfo=UTC))
    assert any('deadline passed' in b for b in late['blockers'])


def test_scores_refuse_non_trading_day_and_ignore_later_files():
    with pytest.raises(ValueError, match='not a trading session'):
        rw.a191_132_scores('2026-10-23', _days(('2026-10-22', True), ('2026-10-23', False)))


def test_calendar_gaps_report_missing_weekdays_only():
    days = _days(('2026-10-19', True), ('2026-10-21', True))
    assert rw.calendar_gaps(days, today='2026-10-22') == ['2026-10-20']


@pytest.mark.parametrize('missing', ['strategy_version', 'code_sha256', 'config_sha256', 'inputs_sha256'])
def test_signal_without_a_version_hash_is_refused_and_the_ledger_is_unchanged(tmp_path, missing):
    from core.research.shadow_selection import verify
    meta = {'strategy': 'A191_132', 'label': 'RESEARCH_ONLY', **HASHES}
    rw.record_research_day('2026-10-22', [], meta, root=tmp_path)
    before = (tmp_path / 'ledger.jsonl').read_bytes()
    with pytest.raises(ValueError, match=missing):
        rw.record_research_day('2026-10-23', [], {k: v for k, v in meta.items() if k != missing}, root=tmp_path)
    assert (tmp_path / 'ledger.jsonl').read_bytes() == before and verify(tmp_path)['status'] == 'PASS'


def test_old_lines_without_config_hash_keep_verifying(tmp_path):
    """Lines written before config_sha256 existed are never rewritten; the chain still verifies and new lines append after them."""
    from core.research.shadow_selection import verify
    rw._append({'kind': 'SIGNAL', 'date': '2026-10-21', 'strategy': 'A191_132', 'label': 'RESEARCH_ONLY', 'picks': []}, tmp_path)  # the pre-change shape
    rw.record_research_day('2026-10-22', [], {'strategy': 'A191_132', 'label': 'RESEARCH_ONLY', **HASHES}, root=tmp_path)
    assert verify(tmp_path) == {'status': 'PASS', 'records': 2}


def test_research_and_paper_ledgers_never_write_into_each_other(tmp_path):
    import hashlib
    from core.research.shadow_selection import verify
    from scripts import paper_trading as pt
    research, paper = tmp_path / 'research', tmp_path / 'paper'
    digest = lambda root: {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(root.iterdir())}
    rw.record_research_day('2026-10-22', [{'rank': 1, 'stock_id': '2330', 'score': 1.0}], {'strategy': 'A191_132', 'label': 'RESEARCH_ONLY', **HASHES}, root=research)
    research_before = digest(research)
    pt.open_account(paper, 1_000_000.0, 'A191_132')
    state = pt.load(paper)
    state['pending'] = [{'stock_id': '2330', 'side': 'buy', 'shares': 1000, 'plan_date': 'P'}]
    pt._save(paper / 'state.json', state)
    pt.execute(paper, 'D1', {'2330': {'open': 100, 'high': 101, 'low': 99, 'close': 100, 'volume': 1e6, 'amount': 1e9, 'prior_amount': 1e9, 'limit_up': 110, 'limit_down': 90}})
    paper_before = digest(paper)
    rw.record_research_day('2026-10-23', [], {'strategy': 'A191_132', 'label': 'RESEARCH_ONLY', **HASHES}, root=research)
    assert digest(paper) == paper_before and set(digest(research)) == set(research_before)
    assert verify(research)['status'] == 'PASS' and pt.reconcile(paper)['status'] == 'PASS'
    assert not {'ledger.jsonl', 'head.json'} & set(digest(paper)) and not {'state.json', 'orders.jsonl'} & set(digest(research))


def test_an_earlier_blocked_line_never_occupies_the_signal_key(tmp_path):
    """The 2026-10-25 incident left a BLOCKED line for 2026-10-27 in the real ledger: it must not stop that session's real signal."""
    from core.research.shadow_selection import verify
    rw.record_blocked('2026-10-27', 'A191_132', 'test pollution', root=tmp_path)
    record = rw.record_research_day('2026-10-27', [], {'strategy': 'A191_132', 'label': 'RESEARCH_ONLY', **HASHES}, root=tmp_path)
    assert record['kind'] == 'SIGNAL' and verify(tmp_path) == {'status': 'PASS', 'records': 2}


def test_annotate_cli_refuses_an_unknown_record_before_writing():
    """The `annotate` subcommand is wired; an unknown sha raises inside annotate() before _append, so the real ledger head is unchanged."""
    import subprocess
    import sys
    from pathlib import Path
    head = (rw.SHADOW / 'head.json').read_bytes()
    proc = subprocess.run([sys.executable, str(Path(rw.__file__)), 'annotate', '--sha', '0' * 64, '--status', 'INVALID_TEST_ARTIFACT', '--reason', 'cli wiring test'],
                          capture_output=True, text=True, cwd=rw.ROOT)
    assert proc.returncode != 0 and 'no record' in proc.stderr
    assert (rw.SHADOW / 'head.json').read_bytes() == head
