"""Budget, resume and calendar rules of scripts/official_data_recovery.py (no network, no DB)."""
import json

import pytest

from scripts import official_data_recovery as odr


@pytest.fixture
def sandbox(tmp_path, monkeypatch):
    monkeypatch.setattr(odr, 'CHIP_CACHE', tmp_path / 'chips')
    (tmp_path / 'chips').mkdir()
    calls = []

    def fake_fetch(dataset, day, gate):
        calls.append((dataset, day))
        body = {'stat': 'OK', 'date': day.replace('-', ''), 'data': [['2330', 'TSMC', '1', '2', '3', '4', '5', '6', '7', '8', '9', '10', '11', '12', '13', '14', '15', '16', '17']]}
        (odr.CHIP_CACHE / f'{dataset}_{day.replace("-", "")}.json').write_text(json.dumps(body), encoding='utf-8')

    monkeypatch.setattr(odr, 'fetch_raw', fake_fetch)
    return tmp_path / 'ledger.jsonl', calls


def test_budget_blocks_the_request_after_the_limit(tmp_path):
    ledger = tmp_path / 'ledger.jsonl'
    for day in ('2026-07-29', '2026-07-30'):
        odr.reserve(ledger, 'T86', day, 'u', budget=2)
    with pytest.raises(odr.BudgetExceeded):
        odr.reserve(ledger, 'T86', '2026-07-31', 'u', budget=2)
    assert odr.sent_count(ledger) == 2  # the refused request was not counted or sent


def test_cached_file_is_not_requested_again_and_a_changed_hash_is_flagged(sandbox):
    ledger, calls = sandbox
    path = odr.CHIP_CACHE / 'T86_20260729.json'
    path.write_text(json.dumps({'stat': 'OK', 'date': '20260729', 'data': [['2330'] + ['1'] * 18]}), encoding='utf-8')
    state, frame = odr.acquire_chip('T86', '2026-07-29', gate=None, ledger=ledger, trusted={})
    assert state == 'OK' and not calls and odr.sent_count(ledger) == 0
    state, _ = odr.acquire_chip('T86', '2026-07-29', gate=None, ledger=ledger, trusted={path.name: {'sha256': '0' * 64}})
    assert state == 'HASH_MISMATCH' and not calls  # flagged, never silently re-downloaded


def test_new_session_costs_exactly_one_request(sandbox):
    ledger, calls = sandbox
    state, frame = odr.acquire_chip('T86', '2026-07-30', gate=None, ledger=ledger, trusted={})
    assert state == 'OK' and calls == [('T86', '2026-07-30')] and odr.sent_count(ledger) == 1
    assert list(frame.stock_id) == ['2330']


def test_response_for_another_date_is_rejected_and_quarantined(sandbox, monkeypatch):
    ledger, _ = sandbox

    def wrong_date(dataset, day, gate):
        (odr.CHIP_CACHE / f'{dataset}_{day.replace("-", "")}.json').write_text(json.dumps({'stat': 'OK', 'date': '20200101', 'data': []}), encoding='utf-8')

    monkeypatch.setattr(odr, 'fetch_raw', wrong_date)
    state, frame = odr.acquire_chip('T86', '2026-07-31', gate=None, ledger=ledger, trusted={})
    assert state.startswith('REJECTED') and frame is None
    assert not (odr.CHIP_CACHE / 'T86_20260731.json').exists() and (odr.CHIP_CACHE / '_rejected/T86_20260731.json').exists()


def test_failed_request_invents_nothing_and_unpublished_trading_day_is_not_a_holiday(sandbox, monkeypatch):
    ledger, _ = sandbox
    monkeypatch.setattr(odr, 'fetch_raw', lambda *a: (_ for _ in ()).throw(ConnectionError('down')))
    assert odr.acquire_chip('T86', '2026-08-03', None, ledger, {})[0] == 'FAILED:ConnectionError'
    assert odr.sent_count(ledger) == 1 and not list(odr.CHIP_CACHE.glob('*.json'))

    def no_data(dataset, day, gate):
        (odr.CHIP_CACHE / f'{dataset}_{day.replace("-", "")}.json').write_text(json.dumps({'stat': '很抱歉，沒有符合條件的資料!'}), encoding='utf-8')

    monkeypatch.setattr(odr, 'fetch_raw', no_data)
    assert odr.acquire_chip('T86', '2026-08-04', None, ledger, {}, trading=True)[0] == 'MISSING_ON_TRADING_DAY'
    assert not (odr.CHIP_CACHE / 'T86_20260804.json').exists()  # quarantined so a later run can ask again
    assert odr.acquire_chip('T86', '2026-08-05', None, ledger, {}, trading=False)[0] == 'NON_TRADING'


# ---- reconciliation rules ----
import pandas as pd  # noqa: E402


def frames(n=120, mysql_flow=None):
    ids = [str(1000 + i) for i in range(n)]
    day = pd.Timestamp('2026-08-03')
    official = pd.DataFrame({'stock_id': ids, 'trade_date': day, 'foreign_buy': [float(i + 1) for i in range(n)]})
    mysql = pd.DataFrame({'stock_id': ids, 'trade_date': day, 'foreign_buy': official.foreign_buy if mysql_flow is None else mysql_flow})
    return official, mysql, pd.MultiIndex.from_frame(official[['stock_id', 'trade_date']])


def test_exact_match_has_no_mismatch_and_a_whole_day_of_zeros_is_flagged():
    official, mysql, listed = frames()
    row = odr.compare(official, mysql, ('foreign_buy',), listed, 'T')[0]
    assert (row['shared_keys'], row['mismatch'], row['whole_day_error']) == (120, 0, False)
    official, mysql, listed = frames(mysql_flow=0.0)
    row = odr.compare(official, mysql, ('foreign_buy',), listed, 'T')[0]
    assert row['mismatch'] == 120 and row['mysql_zero_official_nonzero'] == 120 and row['whole_day_error'] is True


def test_null_missing_and_non_listed_rows_are_counted_apart():
    official, mysql, listed = frames()
    mysql.loc[0, 'foreign_buy'] = float('nan')  # NULL where the official value exists
    mysql = mysql.drop(index=1)  # stock absent from MySQL
    mysql = pd.concat([mysql, pd.DataFrame({'stock_id': ['6999'], 'trade_date': pd.Timestamp('2026-08-03'), 'foreign_buy': [5.0]})])  # not listed
    row = odr.compare(official, mysql, ('foreign_buy',), listed, 'T')[0]
    assert (row['mysql_null_official_present'], row['official_only_stock_days'], row['mysql_only_not_listed']) == (1, 1, 1)
    assert row['shared_keys'] == 118 and row['mismatch'] == 0 and row['mysql_null_ratio'] == round(1 / 120, 4)


def test_ohlc_is_one_field_and_any_unequal_price_is_a_mismatch():
    day = pd.Timestamp('2026-08-03')
    cols = ['open_price', 'high_price', 'low_price', 'close_price']
    official = pd.DataFrame({'stock_id': ['1101', '1102'], 'trade_date': day, **{c: [10.0, 20.0] for c in cols}})
    mysql = official.copy()
    mysql.loc[1, 'high_price'] = 21.0
    row = odr.compare(official, mysql, ('ohlc',), pd.MultiIndex.from_frame(official[['stock_id', 'trade_date']]), 'T')[0]
    assert (row['shared_keys'], row['equal'], row['mismatch']) == (2, 1, 1)


def test_sample_never_becomes_a_pass():
    stats = {'shared_keys': 1000, 'mismatch': 0, 'mysql_null_official_present': 0}
    assert odr.grade(stats) == 'DATA_PASS' and odr.grade({**stats, 'mismatch': 1}) == 'DATA_FAIL'
    assert odr.grade({**stats, 'shared_keys': 0}) == 'DATA_BLOCKED' and odr.grade(stats, complete=False) == 'DATA_FAIL'


# ---- retraining specification ----
@pytest.mark.parametrize('horizon', [5, 7, 8, 10, 15, 20])
def test_no_label_horizon_crosses_a_split_boundary(horizon):
    sessions = list(range(1000))
    parts = odr.split_sessions(sessions, purge=horizon)
    assert parts['train'][-1] + horizon < parts['validation'][0]  # the last training label is resolved before validation begins
    assert parts['validation'][0] - parts['train'][-1] - 1 == horizon + odr.SPLIT['embargo_sessions']  # purge + embargo are really removed
    assert parts['validation'][-1] + horizon < parts['test'][0]
    assert not set(parts['train']) & set(parts['validation']) and not set(parts['validation']) & set(parts['test'])
    assert abs(len(parts['train']) / 1000 - 0.6) < 0.03


def test_changing_a_threshold_changes_the_specification_hash():
    spec = {'acceptance': {'metrics': {'top_decile_lift_mean': {'min': 1.2}}}, 'version': 'v1'}
    spec['sha256'] = odr.spec_hash(spec)
    assert odr.spec_hash(spec) == spec['sha256']  # the hash never covers itself
    edited = {**spec, 'acceptance': {'metrics': {'top_decile_lift_mean': {'min': 1.1}}}}
    assert odr.spec_hash(edited) != spec['sha256']


def test_features_without_a_certified_source_are_not_in_the_model():
    in_model = {raw for raw, v in odr.FEATURE_SPEC.items() if v[3]}
    assert len(odr.FEATURE_SPEC) == 24 and len(in_model) == 17
    assert {'large_holder_ratio', 'large_holder_ratio_change', 'news_sentiment_score', 'revenue_yoy', 'rd_ratio', 'op_profit_margin', 'eps'}.isdisjoint(in_model)
    assert all(odr.FEATURE_SPEC[f][4] for f in odr.FEATURE_SPEC if f not in in_model)  # every exclusion states its reason
