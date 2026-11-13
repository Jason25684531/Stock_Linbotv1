"""5/20-session up probability and return interval (change 2026-11-12, design D6).

Price/volume features known at the close of t; label = close[t+h] / close[t] - 1 > 0 over h official sessions, dropped when a corporate
event or an unexplained jump falls in (t, t+h]. B0 = the training up rate; M1 = logistic regression. Expanding walk-forward by calendar
year with a 20-session purge: every training label ends before the test year starts. There is no random split anywhere.
The spec is frozen (append-only) before any evaluation; evaluate() refuses a spec whose hash differs from the latest freeze.
Passing every frozen gate gives DEV_WINDOW_SKILL (a walk-forward inside the development window, not independent OOS); else NOT_VALIDATED.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

FEATURES = ['ret_5', 'ret_20', 'ret_60', 'vol_20', 'volume_ratio_20', 'dist_high_60']
SPEC = {
    'version': 'PRED-SPEC-1',
    'universe': 'four-digit stock ids 1xxx-9xxx, TRADED rows with close > 0, snapshot bars 2015-01-05..2025-12-31',
    'features': FEATURES, 'feature_note': 'past-only rolling windows in sessions; cross-sectional z-score per date; no financials, revenue or chips',
    'labels': {'horizons': [5, 20], 'definition': 'close[t+h]/close[t]-1 > 0, h market sessions (a stock without a close at t+h is excluded)',
               'exclude': 'corporate event or unexplained jump in (t, t+h]; missing close at t+h'},
    'models': {'B0': 'training-window up rate', 'M1': 'sklearn LogisticRegression(C=1.0, solver=lbfgs, max_iter=500)'},
    'split': {'scheme': 'expanding walk-forward by calendar year', 'test_years': list(range(2019, 2026)), 'purge_sessions': 20},
    'interval': {'buckets': 10, 'quantiles': [0.1, 0.5, 0.9], 'source': 'training-window forward returns per M1 probability decile'},
    'evaluation': {'bootstrap': {'unit': 'blocks of consecutive test dates', 'block_sessions': 20, 'reps': 1000, 'seed': 20261112},
                   'calibration_bins': 10, 'top_fraction': 0.1, 'round_trip_cost': 0.001425 * 2 + 0.003},
    'pass': {'brier_skill_ci95_low_gt': 0.0, 'positive_skill_year_share_gt': 0.5, 'coverage_80_within': [0.75, 0.85], 'net_top_decile_mean_gt': 0.0},
    'statuses': ['DEV_WINDOW_SKILL', 'NOT_VALIDATED'],
}


def spec_hash(spec=SPEC) -> str:
    return hashlib.sha256(json.dumps(spec, sort_keys=True).encode('utf-8')).hexdigest()


def freeze(path, spec=SPEC) -> dict:
    """Append the spec unless the latest entry already has its hash; earlier entries are never changed."""
    path = Path(path)
    entries = json.loads(path.read_text(encoding='utf-8')) if path.is_file() else []
    if entries and entries[-1]['spec_hash'] == spec_hash(spec):
        return entries[-1]
    entry = {'spec_hash': spec_hash(spec), 'spec': spec, 'frozen_at': datetime.now(timezone.utc).isoformat(timespec='seconds')}
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(entries + [entry], ensure_ascii=False, indent=1), encoding='utf-8')
    return entry


def check_frozen(path, spec=SPEC) -> str:
    path = Path(path)
    entries = json.loads(path.read_text(encoding='utf-8')) if path.is_file() else []
    if not entries or entries[-1]['spec_hash'] != spec_hash(spec):
        raise ValueError('MODEL_SPEC_NOT_FROZEN: freeze the spec before any evaluation')
    return entries[-1]['spec_hash']


def wide(bars: pd.DataFrame, official_sessions):
    """Reindex on official sessions so a missing whole market day cannot shift labels."""
    sessions = pd.DatetimeIndex(pd.to_datetime(official_sessions))
    if sessions.empty or sessions.has_duplicates or not sessions.is_monotonic_increasing:
        raise ValueError('INVALID_OFFICIAL_SESSIONS')
    if sessions.max() > pd.Timestamp('2025-12-31'):
        raise ValueError('OD-2: official sessions after 2025-12-31')
    bars = bars[bars.stock_id.astype(str).str.fullmatch(r'[1-9]\d{3}') & (bars.status == 'TRADED') & (bars.close > 0)]
    bars = bars.assign(stock_id=bars.stock_id.astype(str))
    bars = bars.assign(trade_date=pd.to_datetime(bars.trade_date))
    if not bars.trade_date.isin(sessions).all():
        raise ValueError('BAR_DATE_NOT_OFFICIAL_SESSION')
    close = bars.pivot(index='trade_date', columns='stock_id', values='close').reindex(sessions)
    volume = bars.pivot(index='trade_date', columns='stock_id', values='volume').reindex_like(close)
    return close, volume


def features(close: pd.DataFrame, volume: pd.DataFrame) -> pd.DataFrame:
    """Long frame (date, stock_id, features) from rows <= t only, z-scored per date."""
    ret = close.pct_change(fill_method=None)
    raw = {'ret_5': close / close.shift(5) - 1, 'ret_20': close / close.shift(20) - 1, 'ret_60': close / close.shift(60) - 1,
           'vol_20': ret.rolling(20).std(), 'volume_ratio_20': volume / volume.rolling(20).mean(),
           'dist_high_60': close / close.rolling(60).max() - 1}
    long = pd.concat({k: v.stack(future_stack=True) for k, v in raw.items()}, axis=1)
    long.index.names = ['date', 'stock_id']
    long = long.replace([np.inf, -np.inf], np.nan).dropna()
    grouped = long.groupby(level='date')
    return ((long - grouped.transform('mean')) / grouped.transform('std')).replace([np.inf, -np.inf], np.nan).dropna()


def labels(close: pd.DataFrame, horizon: int, events: pd.DataFrame | None = None) -> pd.DataFrame:
    """Long frame (date, stock_id) -> forward return and up label; excluded rows are dropped and counted."""
    forward = close.shift(-horizon) / close - 1
    excluded = pd.DataFrame(False, index=close.index, columns=close.columns)
    if events is not None:
        hits = events.reindex_like(close).fillna(False).astype(float)
        ahead = hits[::-1].rolling(horizon, min_periods=1).sum()[::-1].shift(-1).fillna(0) > 0  # any event in t+1..t+h
        excluded = ahead & forward.notna()
    kept = forward.where(~excluded)
    long = kept.stack(future_stack=True).dropna().rename('forward').to_frame()
    long.index.names = ['date', 'stock_id']
    long['label'] = (long.forward > 0).astype(int)
    long.attrs['excluded_events'] = int(excluded.to_numpy().sum())
    return long


def _buckets(train_p, train_forward, quantiles, n):
    edges = np.unique(np.quantile(train_p, np.linspace(0, 1, n + 1)[1:-1]))
    which = np.searchsorted(edges, train_p, side='right')
    table = {b: np.quantile(train_forward[which == b], quantiles) for b in np.unique(which)}
    return edges, table


def walk_forward(frame: pd.DataFrame, horizon: int, test_years, purge: int, spec=SPEC, *, sessions) -> tuple[pd.DataFrame, list[dict]]:
    """frame: features + forward + label indexed by (date, stock_id). Returns test predictions and one row per fold."""
    from sklearn.linear_model import LogisticRegression
    if purge < horizon:
        raise ValueError('PURGE_SHORTER_THAN_HORIZON')
    if horizon not in spec['labels']['horizons'] or list(test_years) != spec['split']['test_years'] or purge != spec['split']['purge_sessions']:
        raise ValueError('SPLIT_NOT_FROZEN: horizon, test years and purge must match the specification')
    sessions = pd.DatetimeIndex(pd.to_datetime(sessions))
    if sessions.empty or sessions.has_duplicates or not sessions.is_monotonic_increasing:
        raise ValueError('INVALID_OFFICIAL_SESSIONS')
    if sessions.max() > pd.Timestamp('2025-12-31'):
        raise ValueError('OD-2: official sessions after 2025-12-31')
    position = pd.Series(np.arange(len(sessions)), index=sessions)
    dates = frame.index.get_level_values('date')
    if not dates.isin(sessions).all():
        raise ValueError('SAMPLE_DATE_NOT_OFFICIAL_SESSION')
    index = position.reindex(dates).to_numpy()
    quantiles = spec['interval']['quantiles']
    out, folds = [], []
    for year in test_years:
        in_year = sessions[sessions.year == year]
        if not len(in_year):
            continue
        first = position[in_year[0]]
        train = index <= first - purge - 1  # every training label ends before the test year (purge >= horizon)
        test = dates.year == year
        if train.sum() < 1000 or test.sum() == 0:
            folds.append({'year': year, 'status': 'INSUFFICIENT_TRAINING_DATA', 'train_rows': int(train.sum())})
            continue
        x_train, y_train = frame.loc[train, FEATURES].to_numpy(), frame.loc[train, 'label'].to_numpy()
        model = LogisticRegression(C=1.0, solver='lbfgs', max_iter=500).fit(x_train, y_train)
        base = float(y_train.mean())
        p_train = model.predict_proba(x_train)[:, 1]
        edges, table = _buckets(p_train, frame.loc[train, 'forward'].to_numpy(), quantiles, spec['interval']['buckets'])
        part = frame.loc[test, ['forward', 'label']].copy()
        part['p_m1'] = model.predict_proba(frame.loc[test, FEATURES].to_numpy())[:, 1]
        part['p_b0'] = base
        which = np.searchsorted(edges, part.p_m1.to_numpy(), side='right')
        bounds = np.array([table.get(b, [np.nan] * 3) for b in which])
        part['q10'], part['q50'], part['q90'] = bounds[:, 0], bounds[:, 1], bounds[:, 2]
        part['year'] = year
        out.append(part)
        last_train = sessions[first - purge - 1]
        folds.append({'year': year, 'status': 'FITTED', 'train_rows': int(train.sum()), 'test_rows': int(test.sum()), 'train_end': str(last_train.date()),
                      'test_start': str(in_year[0].date()), 'purged_sessions': purge, 'base_rate': base,
                      'coefficients': dict(zip(FEATURES, model.coef_[0].round(6).tolist())), 'intercept': float(model.intercept_[0])})
    predictions = pd.concat(out) if out else pd.DataFrame()
    predictions.attrs['fold_provenance'] = {'spec_hash': spec_hash(spec), 'horizon': horizon, 'folds': folds,
                                           'prediction_hash': _prediction_hash(predictions)}
    return predictions, folds


def _prediction_hash(pred):
    """Bind fold evidence to the exact predictions evaluated in this process."""
    digest = hashlib.sha256()
    digest.update(pd.util.hash_pandas_object(pred, index=True).values.tobytes())
    digest.update(json.dumps(list(pred.columns)).encode('utf-8'))
    return digest.hexdigest()


def _folds_match(pred, horizon, spec):
    provenance = pred.attrs.get('fold_provenance')
    if not isinstance(provenance, dict) or provenance.get('spec_hash') != spec_hash(spec) or provenance.get('horizon') != horizon:
        return False
    if provenance.get('prediction_hash') != _prediction_hash(pred):
        return False
    folds = provenance.get('folds')
    years = spec['split']['test_years']
    if not isinstance(folds, list) or [f.get('year') for f in folds] != years:
        return False
    dates = pred.index.get_level_values('date')
    if not pd.DatetimeIndex(dates).year.equals(pd.Index(pred['year'].to_numpy())):
        return False
    for fold in folds:
        year = fold['year']
        count = int((pred['year'] == year).sum())
        if fold.get('status') != 'FITTED' or fold.get('purged_sessions') != spec['split']['purge_sessions'] or count == 0 or fold.get('test_rows') != count:
            return False
        if str(dates[pred['year'].to_numpy() == year].min().date()) < fold['test_start'] or fold['train_end'] >= fold['test_start']:
            return False
    return True


def brier(p, y) -> float:
    return float(np.mean((np.asarray(p, float) - np.asarray(y, float)) ** 2))


def skill(p, base, y) -> float | None:
    reference = brier(base, y)
    model = brier(p, y)
    return 1 - model / reference if np.isfinite(reference) and np.isfinite(model) and reference > 0 else None


def _block_bootstrap(pred, spec):
    """Brier skill CI from resampled blocks of consecutive test dates (keeps same-day dependence between stocks)."""
    boot = spec['evaluation']['bootstrap']
    per_day = pred.assign(e1=(pred.p_m1 - pred.label) ** 2, e0=(pred.p_b0 - pred.label) ** 2).groupby(level='date')[['e1', 'e0']].sum()
    blocks = [per_day.iloc[i:i + boot['block_sessions']].sum() for i in range(0, len(per_day), boot['block_sessions'])]
    blocks = np.array([[b.e1, b.e0] for b in blocks])
    rng = np.random.default_rng(boot['seed'])
    draws = rng.integers(0, len(blocks), size=(boot['reps'], len(blocks)))
    sums = blocks[draws].sum(axis=1)
    if not np.isfinite(sums).all():
        return [None, None], 'NONFINITE_BRIER'
    if (sums[:, 1] <= 0).any():
        return [None, None], 'ZERO_BASELINE_BRIER'
    values = 1 - sums[:, 0] / sums[:, 1]
    if not np.isfinite(values).all():
        return [None, None], 'NONFINITE_BRIER'
    return [float(np.quantile(values, 0.025)), float(np.quantile(values, 0.975))], None


def evaluate(pred: pd.DataFrame, horizon: int, spec=SPEC, *, freeze_path=None) -> dict:
    """Frozen metrics and status for one horizon's walk-forward predictions."""
    check_frozen(freeze_path or Path(__file__).resolve().parents[3] / 'outputs/platform_v2/model_profile_freeze.json', spec)
    ev, gates = spec['evaluation'], spec['pass']
    if pred.empty:
        return {'horizon': horizon, 'status': 'NOT_VALIDATED', 'reasons': ['NO_PREDICTIONS']}
    if not np.isfinite(pred[['p_m1', 'p_b0', 'label', 'forward', 'q10', 'q50', 'q90']].to_numpy(dtype=float)).all():
        raise ValueError('NONFINITE_PREDICTION_INPUT')
    if not pred[['p_m1', 'p_b0']].apply(lambda s: s.between(0, 1)).to_numpy().all() or not pred.label.isin([0, 1]).all():
        raise ValueError('INVALID_PREDICTION_PROBABILITY_OR_LABEL')
    yearly = {int(y): skill(g.p_m1, g.p_b0, g.label) for y, g in pred.groupby('year')}
    bins = pd.cut(pred.p_m1, np.linspace(0, 1, ev['calibration_bins'] + 1), include_lowest=True)
    calib = pred.groupby(bins, observed=True).agg(predicted=('p_m1', 'mean'), observed=('label', 'mean'), n=('label', 'size')).reset_index(drop=True)
    slope = float(np.polyfit(calib.predicted, calib.observed, 1, w=np.sqrt(calib.n))[0]) if len(calib) > 1 else None
    cover = float(((pred.forward >= pred.q10) & (pred.forward <= pred.q90)).mean())
    cut = pred.groupby(level='date').p_m1.transform(lambda s: s.quantile(1 - ev['top_fraction']))
    top = pred[pred.p_m1 >= cut].groupby(level='date').forward.mean() - ev['round_trip_cost']
    top_every_h = top.iloc[::horizon]  # non-overlapping holding periods
    ci, ci_reason = _block_bootstrap(pred, spec)
    total_skill = skill(pred.p_m1, pred.p_b0, pred.label)
    yearly_undefined = {year: 'ZERO_BASELINE_BRIER' for year, value in yearly.items() if value is None}
    result = {'horizon': horizon, 'rows': int(len(pred)), 'dates': int(pred.index.get_level_values('date').nunique()),
              'brier_m1': brier(pred.p_m1, pred.label), 'brier_b0': brier(pred.p_b0, pred.label), 'brier_skill': total_skill,
              'brier_skill_ci95': ci, 'yearly_skill': yearly, 'positive_skill_year_share': None if yearly_undefined else float(np.mean([v > 0 for v in yearly.values()])),
              'calibration': calib.round(6).to_dict('records'), 'calibration_slope': slope,
              'median_abs_error': float((pred.forward - pred.q50).abs().mean()), 'coverage_80': cover,
              'net_top_decile_mean': float(top_every_h.mean()), 'net_top_decile_periods': int(len(top_every_h)),
              'up_rate_test': float(pred.label.mean()),
              'undefined_reasons': {'brier_skill': 'ZERO_BASELINE_BRIER' if total_skill is None else None,
                                    'brier_skill_ci95': ci_reason, 'yearly_skill': yearly_undefined,
                                    'calibration_slope': 'INSUFFICIENT_CALIBRATION_BINS' if slope is None else None,
                                    'positive_skill_year_share': 'UNDEFINED_YEARLY_BRIER_SKILL' if yearly_undefined else None}}
    reasons = []
    if not _folds_match(pred, horizon, spec):
        reasons.append('FOLD_PROVENANCE_MISSING_OR_INVALID')
    if total_skill is None:
        reasons.append('BRIER_SKILL_UNDEFINED_ZERO_BASELINE')
    if ci_reason:
        reasons.append('BRIER_SKILL_CI_UNDEFINED_' + ('ZERO_BASELINE' if ci_reason == 'ZERO_BASELINE_BRIER' else ci_reason))
    elif not ci[0] > gates['brier_skill_ci95_low_gt']:
        reasons.append(f"BRIER_SKILL_CI_LOW {ci[0]:.4f} <= {gates['brier_skill_ci95_low_gt']}")
    if yearly_undefined:
        reasons.append('YEARLY_BRIER_SKILL_UNDEFINED_ZERO_BASELINE')
    elif not result['positive_skill_year_share'] > gates['positive_skill_year_share_gt']:
        reasons.append(f"POSITIVE_SKILL_YEARS {result['positive_skill_year_share']:.2f}")
    low, high = gates['coverage_80_within']
    if not low <= cover <= high:
        reasons.append(f'COVERAGE_80 {cover:.3f} outside {low}-{high}')
    if not result['net_top_decile_mean'] > gates['net_top_decile_mean_gt']:
        reasons.append(f"NET_TOP_DECILE {result['net_top_decile_mean']:.5f}")
    return result | {'status': 'NOT_VALIDATED' if reasons else 'DEV_WINDOW_SKILL', 'reasons': reasons}
