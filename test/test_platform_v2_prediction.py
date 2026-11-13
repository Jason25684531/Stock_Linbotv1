"""Leakage, purge and known-answer checks for the prediction module on a synthetic panel."""
import inspect

import numpy as np
import pandas as pd
import pytest

from core.research.platform import prediction as pr


def panel(seed=0, stocks=40):
    rng = np.random.default_rng(seed)
    days = pd.bdate_range('2017-01-02', '2020-12-31')
    close = pd.DataFrame(100 * np.exp(np.cumsum(rng.normal(0, 0.02, (len(days), stocks)), axis=0)), index=days,
                         columns=[f'{1100 + i}' for i in range(stocks)])
    volume = pd.DataFrame(rng.integers(1_000, 100_000, (len(days), stocks)).astype(float), index=days, columns=close.columns)
    return close, volume


def frame(close, volume, horizon=5, events=None):
    return pr.features(close, volume).join(pr.labels(close, horizon, events), how='inner')


def spec_for(years):
    return {**pr.SPEC, 'split': {**pr.SPEC['split'], 'test_years': years}}


def test_walk_forward_rejects_unfrozen_split_parameters():
    close, volume = panel()
    data = frame(close, volume)
    with pytest.raises(ValueError, match='SPLIT_NOT_FROZEN'):
        pr.walk_forward(data, 5, [2019], purge=5, sessions=close.index)


def test_evaluation_refuses_unproven_or_incomplete_folds(tmp_path):
    dates = pd.date_range('2019-01-01', periods=50)
    index = pd.MultiIndex.from_product([dates, ['1100']], names=['date', 'stock_id'])
    y = np.arange(50) % 2
    pred = pd.DataFrame({'p_m1': y * .8 + .1, 'p_b0': .5, 'label': y,
                         'forward': np.where(y, .1, -.1), 'q10': -.2, 'q50': 0.,
                         'q90': .2, 'year': 2019}, index=index)
    path = tmp_path / 'freeze.json'
    pr.freeze(path)
    result = pr.evaluate(pred, 5, freeze_path=path)
    assert result['status'] == 'NOT_VALIDATED'
    assert 'FOLD_PROVENANCE_MISSING_OR_INVALID' in result['reasons']


def test_features_use_only_past_rows():
    close, volume = panel()
    cut = pd.Timestamp('2019-06-28')
    before = pr.features(close, volume)
    changed = close.copy()
    changed.loc[changed.index > cut] *= 3
    after = pr.features(changed, volume)
    pd.testing.assert_frame_equal(before.loc[:cut], after.loc[:cut])


def test_changing_prices_after_the_test_year_does_not_change_its_predictions():
    close, volume = panel()
    pred, _ = pr.walk_forward(frame(close, volume), 5, [2019], purge=20, spec=spec_for([2019]), sessions=close.index)
    changed = close.copy()
    changed.loc[changed.index > '2019-12-31'] *= 0.5
    again, _ = pr.walk_forward(frame(changed, volume), 5, [2019], purge=20, spec=spec_for([2019]), sessions=close.index)
    common = pred.index.intersection(again.index)
    assert len(common) > 1000
    np.testing.assert_allclose(pred.loc[common, 'p_m1'], again.loc[common, 'p_m1'], rtol=0, atol=0)


def test_purge_window_has_no_training_sample():
    close, volume = panel()
    data = frame(close, volume, horizon=20)
    _, folds = pr.walk_forward(data, 20, [2020], purge=20, spec=spec_for([2020]), sessions=close.index)
    fold = folds[0]
    sessions = close.index
    gap = sessions.get_loc(pd.Timestamp(fold['test_start'])) - sessions.get_loc(pd.Timestamp(fold['train_end'])) - 1
    assert gap == 20
    with pytest.raises(ValueError, match='PURGE'):
        pr.walk_forward(data, 20, [2020], purge=5, sessions=close.index)


def test_purge_counts_official_days_without_feature_rows():
    close, volume = panel()
    data = frame(close, volume, horizon=20)
    data = data[data.index.get_level_values('date') != pd.Timestamp('2019-12-20')]
    _, folds = pr.walk_forward(data, 20, [2020], purge=20, spec=spec_for([2020]), sessions=close.index)
    fold = folds[0]
    assert close.index.get_loc(pd.Timestamp(fold['test_start'])) - close.index.get_loc(pd.Timestamp(fold['train_end'])) - 1 == 20


def test_no_random_split_exists():
    source = inspect.getsource(pr)
    assert 'train_test_split' not in source and 'shuffle' not in source
    assert set(inspect.signature(pr.walk_forward).parameters) == {'frame', 'horizon', 'test_years', 'purge', 'spec', 'sessions'}


def test_events_exclude_label_windows():
    close, _ = panel()
    events = pd.DataFrame(False, index=close.index, columns=close.columns)
    events.iloc[100, 0] = True
    plain, cleaned = pr.labels(close, 5), pr.labels(close, 5, events)
    sid = close.columns[0]
    dropped = [close.index[i] for i in range(95, 100)]  # windows (t, t+5] containing the event day
    assert all((d, sid) in plain.index and (d, sid) not in cleaned.index for d in dropped)
    assert (close.index[100], sid) in cleaned.index  # the event day itself starts a clean window
    assert cleaned.attrs['excluded_events'] == 5


def test_known_answers():
    y = np.array([1, 0, 1, 1, 0])
    assert pr.brier(y, y) == 0 and pr.skill(y, np.full(5, y.mean()), y) == 1
    assert pr.skill(np.full(5, y.mean()), np.full(5, y.mean()), y) == 0


def test_uninformative_model_is_not_validated(tmp_path):
    close, volume = panel(seed=3)
    spec = spec_for([2019, 2020])
    pred, _ = pr.walk_forward(frame(close, volume), 5, [2019, 2020], purge=20, spec=spec, sessions=close.index)
    path = tmp_path / 'freeze.json'
    pr.freeze(path, spec)
    result = pr.evaluate(pred, 5, spec, freeze_path=path)
    assert result['status'] == 'NOT_VALIDATED' and result['reasons']  # random walk: no skill to find
    assert 'FOLD_PROVENANCE_MISSING_OR_INVALID' not in result['reasons']
    assert result['brier_skill_ci95'][0] <= 0
    changed = pred.copy()
    changed.iloc[0, changed.columns.get_loc('p_m1')] = 0.5
    assert 'FOLD_PROVENANCE_MISSING_OR_INVALID' in pr.evaluate(changed, 5, spec, freeze_path=path)['reasons']
    missing = pred[pred['year'] == 2019]
    assert 'FOLD_PROVENANCE_MISSING_OR_INVALID' in pr.evaluate(missing, 5, spec, freeze_path=path)['reasons']


def test_evaluation_requires_frozen_spec(tmp_path):
    path = tmp_path / 'freeze.json'
    with pytest.raises(ValueError, match='NOT_FROZEN'):
        pr.check_frozen(path)
    with pytest.raises(ValueError, match='NOT_FROZEN'):
        pr.evaluate(pd.DataFrame(), 5, freeze_path=path)
    pr.freeze(path)
    assert pr.check_frozen(path) == pr.spec_hash()
    changed = dict(pr.SPEC, pass_={'loosened': True})
    with pytest.raises(ValueError, match='NOT_FROZEN'):
        pr.check_frozen(path, changed)
    with pytest.raises(ValueError, match='NOT_FROZEN'):
        pr.evaluate(pd.DataFrame(), 5, changed, freeze_path=path)
    first = path.read_text(encoding='utf-8')
    pr.freeze(path, changed)
    assert path.read_text(encoding='utf-8').startswith(first[:-2])  # append only: the first entry is unchanged


def test_official_sessions_preserve_missing_market_day():
    dates = pd.to_datetime(['2020-01-02', '2020-01-03', '2020-01-06'])
    bars = pd.DataFrame({'stock_id': ['1100', '1100'], 'status': ['TRADED'] * 2,
                         'close': [10, 12], 'volume': [100, 100],
                         'trade_date': [dates[0], dates[2]]})
    close, volume = pr.wide(bars, dates)
    assert close.index.tolist() == dates.tolist()
    assert np.isnan(close.loc[dates[1], '1100'])
    assert (dates[0], '1100') not in pr.labels(close, 1).index


def test_zero_baseline_brier_fails_closed_with_reasons(tmp_path):
    import json

    index = pd.MultiIndex.from_product([pd.date_range('2020-01-01', periods=3), ['1100']], names=['date', 'stock_id'])
    pred = pd.DataFrame({'p_m1': [0.9] * 3, 'p_b0': [1.0] * 3, 'label': [1] * 3,
                         'forward': [0.1] * 3, 'q10': [0.0] * 3, 'q50': [0.1] * 3,
                         'q90': [0.2] * 3, 'year': [2020] * 3}, index=index)
    assert pr.skill(pred.p_m1, pred.p_b0, pred.label) is None
    path = tmp_path / 'freeze.json'
    pr.freeze(path)
    result = pr.evaluate(pred, 5, freeze_path=path)
    assert result['status'] == 'NOT_VALIDATED'
    assert result['brier_skill'] is None and result['yearly_skill'][2020] is None
    assert result['brier_skill_ci95'] == [None, None]
    assert 'BRIER_SKILL_UNDEFINED_ZERO_BASELINE' in result['reasons']
    assert 'BRIER_SKILL_CI_UNDEFINED_ZERO_BASELINE' in result['reasons']
    assert result['undefined_reasons']['yearly_skill'][2020] == 'ZERO_BASELINE_BRIER'
    json.dumps(result, allow_nan=False)


def test_bootstrap_zero_reference_draws_fail_closed(tmp_path):
    index = pd.MultiIndex.from_product([pd.date_range('2020-01-01', periods=2), ['1100']], names=['date', 'stock_id'])
    pred = pd.DataFrame({'p_m1': [0.9, 0.9], 'p_b0': [1.0, 0.5], 'label': [1, 1],
                         'forward': [0.1, 0.1], 'q10': [0.0, 0.0], 'q50': [0.1, 0.1],
                         'q90': [0.2, 0.2], 'year': [2020, 2020]}, index=index)
    spec = {**pr.SPEC, 'evaluation': {**pr.SPEC['evaluation'], 'bootstrap': {'unit': 'date', 'block_sessions': 1, 'reps': 100, 'seed': 1}}}
    path = tmp_path / 'freeze.json'
    pr.freeze(path, spec)
    result = pr.evaluate(pred, 5, spec, freeze_path=path)
    assert result['brier_skill'] is not None
    assert result['brier_skill_ci95'] == [None, None]
    assert 'BRIER_SKILL_CI_UNDEFINED_ZERO_BASELINE' in result['reasons']
    assert result['status'] == 'NOT_VALIDATED'
    pred.iloc[0, pred.columns.get_loc('p_m1')] = np.nan
    with pytest.raises(ValueError, match='NONFINITE_PREDICTION_INPUT'):
        pr.evaluate(pred, 5, spec, freeze_path=path)
    pred.iloc[0, pred.columns.get_loc('p_m1')] = 2
    with pytest.raises(ValueError, match='INVALID_PREDICTION_PROBABILITY_OR_LABEL'):
        pr.evaluate(pred, 5, spec, freeze_path=path)
