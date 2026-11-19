"""V6 research program (change 2026-11-18-return-target-research-program-v6): fixed targets and budget, an append-only hash-chained
queue, failure classes, a single-use holdout, and rules that cannot see data past their window."""
import json

import numpy as np
import pandas as pd
import pytest

from scripts import strategy_v6_program as v6
from scripts import strategy_v6_rules as rules


@pytest.fixture
def prog(tmp_path, monkeypatch):
    monkeypatch.setattr(v6, 'OUT', tmp_path)
    v6.write_charter(snapshot_id='HS1-x')
    return tmp_path


def _admit(hid, variants=('a',), **extra):
    return v6.admit({'id': hid, 'family': 'test', 'source': 'internal', 'mechanism': 'm', 'addresses': 'RETURN', 'kind': 'index_switch',
                     'data': ['benchmark'], 'variants': {vid: {'ma': 200} for vid in variants}} | extra)


def test_the_charter_is_written_once_and_a_changed_charter_stops_everything(prog):
    with pytest.raises(RuntimeError, match='CHARTER_EXISTS'):
        v6.write_charter(snapshot_id='HS1-y')
    path = prog / 'charter.json'
    path.write_text(path.read_text(encoding='utf-8').replace('0.15', '0.12'), encoding='utf-8')
    with pytest.raises(ValueError, match='CHARTER_CHANGED'):
        v6.charter()


def test_the_queue_is_a_hash_chain_and_an_edited_row_is_detected(prog):
    _admit('H1')
    v6.append({'hypothesis': 'H1', 'event': 'NOTE'})
    assert [r['event'] for r in v6.queue()] == ['REGISTERED', 'NOTE']
    path = prog / 'queue.jsonl'
    path.write_text(path.read_text(encoding='utf-8').replace('"NOTE"', '"NOTED"'), encoding='utf-8')
    with pytest.raises(ValueError, match='QUEUE_CHAIN_BROKEN'):
        v6.queue()


def test_admission_enforces_the_variant_limit_the_budget_and_blocks_missing_data(prog):
    with pytest.raises(ValueError, match='TOO_MANY_VARIANTS'):
        _admit('H1', variants=('a', 'b', 'c'))
    for i in range(v6.charter()['budget']['hypotheses']):
        _admit(f'H{i}')
    with pytest.raises(ValueError, match='BUDGET_SPENT'):
        _admit('H99')
    assert v6.state('H0') == 'REGISTERED'


def test_a_hypothesis_without_data_is_recorded_blocked_and_does_not_use_budget(prog):
    row = v6.admit({'id': 'H3', 'family': 'revenue', 'source': 'x', 'mechanism': 'm', 'addresses': 'RETURN', 'kind': 'engine',
                    'data': ['monthly_revenue'], 'variants': {'a': {}}, 'blocked': 'monthly revenue starts 2023-01'})
    assert row['event'] == 'DATA_BLOCKED' and v6.state('H3') == 'DATA_BLOCKED' and v6.registered_count() == 0
    with pytest.raises(ValueError, match='NOT_REGISTERED'):
        v6.require_runnable('H3', 'a', 'development')


def _engine(hid, variants=('a',)):
    return v6.admit({'id': hid, 'family': 'test', 'source': 'internal', 'mechanism': 'm', 'addresses': 'COST', 'kind': 'engine',
                     'data': ['bars'], 'variants': {vid: {'host': 'defensive_low_volatility', 'entry': 'monthly'} for vid in variants}})


def test_only_registered_variants_run_and_the_holdout_is_for_finalists_once(prog):
    _engine('H1', variants=('a', 'b'))
    v6.require_runnable('H1', 'a', 'development')
    with pytest.raises(ValueError, match='UNREGISTERED_VARIANT'):
        v6.require_runnable('H1', 'c', 'development')
    with pytest.raises(ValueError, match='NOT_A_FINALIST'):
        v6.require_runnable('H1', 'a', 'holdout')
    path = prog / 'results' / 'H1__a__development.json'
    v6.write(path, {'measurement_code': v6.measurement_code()})
    v6.append({'hypothesis': 'H1', 'event': 'RESULT', 'variant': 'a', 'window': 'development', 'verdict': 'PROMISING', 'failure': None,
               'result_sha256': v6._sha(path.read_bytes())})
    v6.append({'hypothesis': 'H1', 'event': 'FINALIST', 'variant': 'a'})
    v6.require_runnable('H1', 'a', 'holdout')
    v6.append({'hypothesis': 'H1', 'event': 'HOLDOUT_STARTED', 'variant': 'a'})
    with pytest.raises(ValueError, match='HOLDOUT_ALREADY_USED'):
        v6.require_runnable('H1', 'a', 'holdout')


def test_a_proxy_never_reaches_the_holdout(prog):
    _admit('P1')
    v6.append({'hypothesis': 'P1', 'event': 'FINALIST', 'variant': 'a'})
    with pytest.raises(ValueError, match='PROXY_HAS_NO_HOLDOUT'):
        v6.require_runnable('P1', 'a', 'holdout')


def test_a_rule_edited_after_registration_is_refused_and_rebinding_is_closed_once_a_result_exists(prog, monkeypatch):
    _engine('H2')
    real = v6.bound_code
    monkeypatch.setattr(v6, 'bound_code', lambda kind: real(kind) | {'rules.engine_rule': 'edited'})
    with pytest.raises(ValueError, match='CODE_CHANGED_SINCE_REGISTRATION'):
        v6.require_runnable('H2', 'a', 'development')
    v6.rebind('H2', 'test')
    v6.require_runnable('H2', 'a', 'development')
    v6.append({'hypothesis': 'H2', 'event': 'RESULT', 'variant': 'a', 'window': 'development', 'verdict': 'FAILED', 'failure': 'COST'})
    with pytest.raises(ValueError, match='REBIND_AFTER_RESULT'):
        v6.rebind('H2', 'again')
    assert 'H2 rebound' in (prog / 'deviations.jsonl').read_text(encoding='utf-8')


def test_once_a_result_exists_every_new_hypothesis_must_answer_a_failure(prog):
    _admit('H1')
    v6.append({'hypothesis': 'H1', 'event': 'RESULT', 'variant': 'a', 'window': 'development', 'verdict': 'NEAR', 'failure': 'RETURN'})
    with pytest.raises(ValueError, match='PARENT_NOT_FAILED'):
        _admit('H9')


def test_a_stored_result_must_match_the_hash_its_queue_row_recorded(prog):
    _admit('H1')
    path = prog / 'results' / 'H1__a__development.json'
    v6.write(path, {'cagr': {'value': 0.1}})
    v6.append({'hypothesis': 'H1', 'event': 'RESULT', 'variant': 'a', 'window': 'development', 'verdict': 'FAILED', 'failure': 'RETURN',
               'result_sha256': v6._sha(path.read_bytes())})
    assert v6.stored_result('H1', 'a', 'development')['cagr']['value'] == 0.1
    v6.write(path, {'cagr': {'value': 0.2}})
    with pytest.raises(ValueError, match='RESULT_CHANGED'):
        v6.stored_result('H1', 'a', 'development')


class _FakeData:
    """A cross-section source with a memo, like core.research.native_frame: the fresh call returns the memo first."""
    def __init__(self, fresh):
        self._cache, self._fresh = {}, fresh

    def feature_cache_original(self, day):
        return self._cache[day] if day in self._cache else self._fresh(day)


def test_cache_verification_compares_against_a_fresh_computation_not_the_memo(tmp_path):
    day = pd.Timestamp('2020-01-02')
    cached = pd.DataFrame({'x': [1.0]})
    cached.to_pickle(tmp_path / 'c.pkl')
    data = _FakeData(lambda d: pd.DataFrame({'x': [2.0]}))
    data._cache[day] = cached
    assert v6.verify_against_cache(data, [day], lambda d: tmp_path / 'c.pkl') != []
    assert data._cache[day] is cached
    same = _FakeData(lambda d: pd.DataFrame({'x': [1.0]}))
    assert v6.verify_against_cache(same, [day], lambda d: tmp_path / 'c.pkl') == []
    assert v6.verify_against_cache(same, [day], lambda d: tmp_path / 'missing.pkl') == ['NO_CACHED_DAY_COMPARED']


def test_run_calls_the_guard_records_failures_and_counts_a_holdout_before_computing(prog, monkeypatch):
    _engine('E1')
    calls = []
    monkeypatch.setattr(v6, 'measurement_code', lambda: 'M')
    monkeypatch.setattr(v6, '_guard', lambda: calls.append('guard'))
    monkeypatch.setattr(v6, '_context', lambda: {})
    monkeypatch.setattr(v6, 'numbers_for', lambda *a: (_ for _ in ()).throw(RuntimeError('boom')))
    with pytest.raises(RuntimeError, match='boom'):
        v6.run('E1', 'a')
    assert calls == ['guard'] and v6.state('E1') == 'RUN_FAILED' and (prog / 'failures' / 'E1__a__development.json').exists()
    good = _n(0.18, 0.12, -0.15) | {'run_identity': 'R', 'span': [2017, 2023], 'measurement_code': v6.measurement_code(), 'proxy': False,
                                    'max_drawdown_stress': {'value': -0.16}}
    monkeypatch.setattr(v6, 'numbers_for', lambda *a: good)
    monkeypatch.setattr(v6, 'bench_numbers', lambda *a: BENCH)
    assert v6.run('E1', 'a')['verdict'] == 'PROMISING'
    v6.finalize()
    events = []
    monkeypatch.setattr(v6, 'numbers_for', lambda *a: events.append(v6.state('E1')) or good)
    assert v6.run('E1', 'a', 'holdout')['verdict'] == 'HOLDOUT_PASS'
    assert events == ['HOLDOUT_STARTED']


def test_a_holdout_needs_the_measurement_code_of_its_development_result(prog, monkeypatch):
    _engine('E1')
    path = prog / 'results' / 'E1__a__development.json'
    v6.write(path, {'measurement_code': 'old'})
    v6.append({'hypothesis': 'E1', 'event': 'RESULT', 'variant': 'a', 'window': 'development', 'verdict': 'PROMISING', 'failure': None,
               'result_sha256': v6._sha(path.read_bytes())})
    v6.append({'hypothesis': 'E1', 'event': 'FINALIST', 'variant': 'a'})
    with pytest.raises(ValueError, match='MEASUREMENT_CHANGED_SINCE_DEVELOPMENT'):
        v6.require_runnable('E1', 'a', 'holdout')


def test_finalize_takes_promising_non_proxy_results_only(prog):
    _admit('P1')
    _engine('E1')
    for hid, proxy in (('P1', True), ('E1', False)):
        path = prog / 'results' / f'{hid}__a__development.json'
        v6.write(path, {'proxy': proxy, 'cagr_stress': {'value': 0.12}, 'max_drawdown_stress': {'value': -0.15}})
        v6.append({'hypothesis': hid, 'event': 'RESULT', 'variant': 'a', 'window': 'development', 'verdict': 'PROMISING', 'failure': None,
                   'result_sha256': v6._sha(path.read_bytes())})
    assert [r['hypothesis'] for r in v6.finalize()] == ['E1']


def test_a_sourced_hypothesis_needs_a_failed_parent_a_source_card_and_one_per_failure(prog):
    _admit('H1')
    card = prog / 'sources' / 'card.md'
    with pytest.raises(ValueError, match='PARENT_NOT_FAILED'):
        _admit('S1', parent='H1', source_card='sources/card.md')
    v6.append({'hypothesis': 'H1', 'event': 'RESULT', 'variant': 'a', 'window': 'development', 'verdict': 'FAILED', 'failure': 'RETURN'})
    with pytest.raises(ValueError, match='SOURCE_CARD_MISSING'):
        _admit('S1', parent='H1', source_card='sources/card.md')
    card.parent.mkdir()
    card.write_text('claim only', encoding='utf-8')
    with pytest.raises(ValueError, match='SOURCE_CARD_MISSING'):
        _admit('S1', parent='H1', source_card='sources/card.md')
    card.write_text('URL: https://example.org\nRetrieved: 2026-11-18\nclaim', encoding='utf-8')
    _admit('S1', parent='H1', source_card='sources/card.md')
    with pytest.raises(ValueError, match='ONE_PER_FAILURE'):
        _admit('S2', parent='H1', source_card='sources/card.md')


T = {'base_cagr': 0.15, 'stress_cagr': 0.10, 'max_drawdown': -0.20, 'min_closed_trades': 30, 'min_years_without_trades': 5}


def _n(base, stress, mdd, trades=40, years=7.0, kind='engine'):
    return {'cagr': {'value': base}, 'cagr_stress': {'value': stress}, 'max_drawdown': {'value': mdd}, 'closed_trades': trades,
            'years': years, 'kind': kind, 'publishable': True}


BENCH = {'cagr': {'value': 0.147}, 'max_drawdown': {'value': -0.286}}


@pytest.mark.parametrize('numbers,verdict,failure', [
    (_n(0.18, 0.12, -0.15), 'PROMISING', None),
    (_n(0.10, 0.09, -0.10), 'NEAR', 'RETURN'),
    (_n(0.10, 0.02, -0.30), 'FAILED', 'RETURN'),
    (_n(0.18, 0.04, -0.15), 'FAILED', 'COST'),
    (_n(0.18, 0.12, -0.35), 'FAILED', 'DRAWDOWN'),
    (_n(0.18, 0.12, -0.15, trades=12), 'FAILED', 'SAMPLE'),
    (_n(0.18, 0.12, -0.15, trades=0, years=7.0, kind='index_switch'), 'PROMISING', None),
    (_n(0.18, 0.12, -0.15, trades=0, years=3.0, kind='index_switch'), 'FAILED', 'SAMPLE'),
    (_n(None, None, None), 'FAILED', 'DATA'),
    (_n(0.18, 0.12, -0.15) | {'publishable': False}, 'FAILED', 'DATA'),
])
def test_verdict_and_failure_class(numbers, verdict, failure):
    result = v6.classify(numbers, BENCH, T)
    assert (result['verdict'], result['failure']) == (verdict, failure)


def test_the_end_outcome_follows_the_holdout_and_the_budget():
    budget = {'hypotheses': 2}
    reg = lambda h: {'hypothesis': h, 'event': 'REGISTERED'}
    assert v6.outcome([reg('H1')], budget, final=False) == 'IN_PROGRESS'
    assert v6.outcome([reg('H1')], budget) == 'STOPPED_BY_BLOCKER'
    assert v6.outcome([reg('H1'), reg('H2')], budget) == 'BUDGET_SPENT_TARGET_NOT_MET'
    assert v6.outcome([reg('H1'), {'hypothesis': 'H1', 'event': 'HOLDOUT', 'verdict': 'HOLDOUT_PASS'}], budget) == 'TARGET_MET_ON_HOLDOUT'
    assert v6._pct({'value': None, 'reason': 'X'}) == '—' and v6._pct({'value': 0.123}) == '+12.3%'


def _prices(values, start='2020-01-01'):
    return pd.DataFrame({'date': pd.bdate_range(start, periods=len(values)), 'taiex_total_return': values})


def test_the_index_switch_steps_out_one_session_after_the_close_falls_below_its_average_and_pays_each_switch():
    up, down = list(np.linspace(100, 200, 60)), list(np.linspace(200, 120, 40))
    nav = rules.index_switch(_prices(up + down), ma=20, costs={'buy': 0.002, 'sell': 0.003}, multiple=1)
    held = nav.position.tolist()
    first_below = next(i for i, (p, m) in enumerate(zip(up + down, pd.Series(up + down).rolling(20).mean())) if i > 60 and p < m)
    assert held[first_below] == 1 and held[first_below + 1] == 0
    no_cost = rules.index_switch(_prices(up + down), ma=20, costs={'buy': 0, 'sell': 0}, multiple=1)
    assert nav.nav.iloc[-1] < no_cost.nav.iloc[-1]
    assert rules.index_switch(_prices(up + down), ma=20, costs={'buy': 0.002, 'sell': 0.003}, multiple=2).nav.iloc[-1] < nav.nav.iloc[-1]


def test_a_rule_never_sees_data_after_its_window_end():
    values = list(np.linspace(100, 150, 300))
    inside = _prices(values)
    tail = _prices(values + [10.0] * 50)
    end = inside.date.iloc[-1]
    a = rules.index_switch(rules.until(inside, end), ma=50, costs={'buy': 0, 'sell': 0}, multiple=1)
    b = rules.index_switch(rules.until(tail, end), ma=50, costs={'buy': 0, 'sell': 0}, multiple=1)
    assert a.nav.equals(b.nav)


def test_a_monthly_rebalanced_mix_returns_to_its_weights_each_month():
    dates = pd.bdate_range('2021-01-01', '2021-03-31')
    a = pd.DataFrame({'date': dates, 'nav': np.cumprod(np.full(len(dates), 1.01))})
    b = pd.DataFrame({'date': dates, 'nav': np.ones(len(dates))})
    mix = rules.mix({'a': a, 'b': b}, {'a': 0.5, 'b': 0.5})
    month_starts = mix.groupby(mix.date.dt.to_period('M')).head(1).index[1:]
    for i in month_starts:
        assert mix.weight_a.iloc[i - 1] != pytest.approx(0.5) and mix.weight_a.iloc[i] == pytest.approx(0.5)


def test_entry_only_on_lets_candidates_through_only_on_listed_dates():
    class Host:
        def filter_candidates(self, df):
            return df
    frame = lambda day: pd.DataFrame({'stock_id': ['2330'], 'trade_date': [pd.Timestamp(day)]})
    with rules.entry_only_on(Host, {pd.Timestamp('2023-02-01')}):
        assert len(Host().filter_candidates(frame('2023-02-01'))) == 1
        assert Host().filter_candidates(frame('2023-02-02')).empty
    assert len(Host().filter_candidates(frame('2023-02-02'))) == 1


def _history(paths, volume=200_000.0):
    dates = pd.bdate_range('2022-01-03', periods=len(next(iter(paths.values()))))
    return pd.concat([pd.DataFrame({'stock_id': sid, 'trade_date': dates, 'close_price': p, 'volume': volume}) for sid, p in paths.items()],
                     ignore_index=True)


def test_risk_adjusted_momentum_prefers_a_steady_climber_over_a_jumpy_one_with_the_same_gain():
    rng = np.random.default_rng(0)
    steady = 100 * np.exp(np.linspace(0, 0.2, 130) + rng.normal(0, 0.002, 130))
    jumpy = steady * np.exp(rng.normal(0, 0.03, 130))
    jumpy[-1], jumpy[-121] = steady[-1], steady[-121]
    falling = 100 * np.exp(np.linspace(0, -0.2, 130))
    picks = rules.risk_adjusted_momentum(_history({'1101': jumpy, '1102': steady, '1103': falling}), lookback=120, top=2, min_volume=100_000)
    assert picks == ['1102', '1101']
    assert rules.risk_adjusted_momentum(_history({'1102': steady}, volume=50_000.0), 120, 2, 100_000) == []
    assert rules.risk_adjusted_momentum(_history({'1102': steady[:100]}), 120, 2, 100_000) == []


def test_the_market_gate_uses_only_data_up_to_each_session():
    values = list(np.linspace(100, 150, 250)) + list(np.linspace(150, 90, 60))
    full = rules.market_above(_prices(values), 50)
    cut = rules.market_above(_prices(values[:260]), 50)
    assert cut == {d for d in full if d <= _prices(values).date.iloc[259]}


def test_first_sessions_picks_the_first_trading_day_of_each_month_or_quarter():
    sessions = pd.to_datetime(['2023-01-03', '2023-01-04', '2023-02-01', '2023-03-01', '2023-04-03', '2023-04-04'])
    assert rules.first_sessions(sessions, 'monthly') == set(pd.to_datetime(['2023-01-03', '2023-02-01', '2023-03-01', '2023-04-03']))
    assert rules.first_sessions(sessions, 'quarterly') == set(pd.to_datetime(['2023-01-03', '2023-04-03']))
