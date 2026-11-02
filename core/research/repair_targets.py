"""Point-in-time Research targets without the legacy next-session tradability leak."""
from __future__ import annotations

import numpy as np
import pandas as pd

from core.research.repair_accounting import TAIPEI, _timestamp


def build_targets(scores: pd.DataFrame, universe: pd.DataFrame, *, config_id: str, top_n: int, stock_weighting: str,
                  execution_calendar_mode='PREKNOWN') -> pd.DataFrame:
    """Select from information available at signal time; actual fills remain a later event.

    Source hashes are caller-provided provenance, not independent certification. This pure helper
    does not register or run a hypothesis; its caller must use the existing experiment admission.
    """
    scores, universe = scores.copy(), universe.copy()
    if execution_calendar_mode not in ('PREKNOWN', 'NEXT_SESSION_EVENT'):
        raise ValueError('INVALID_EXECUTION_CALENDAR_MODE')
    score_columns = ['asof_date', 'asset_id', 'composite_score', 'signal_at', 'score_available_at', 'score_source_sha256']
    universe_columns = ['asof_date', 'asset_id', 'member', 'execution_date', 'membership_available_at', 'membership_source_sha256',
                        'calendar_available_at', 'calendar_source_sha256']
    if not set(score_columns).issubset(scores.columns) or not set(universe_columns).issubset(universe.columns):
        raise ValueError('PIT_EVIDENCE_MISSING')
    if (not isinstance(config_id, str) or not config_id.strip() or config_id != config_id.strip()
            or isinstance(top_n, bool) or not isinstance(top_n, int) or top_n <= 0 or stock_weighting not in ('equal', 'score_weighted')):
        raise ValueError('INVALID_TARGET_CONFIGURATION')
    scores, universe = scores[score_columns], universe[universe_columns]
    if any(not isinstance(asset, str) or not asset.strip() or asset != asset.strip()
           for series in (scores['asset_id'], universe['asset_id']) for asset in series):
        raise ValueError('PIT_ASSET_ID_INVALID')
    scores['asof_date'], universe['asof_date'] = pd.to_datetime(scores['asof_date']), pd.to_datetime(universe['asof_date'])
    for column in (scores['asof_date'], universe['asof_date']):
        if column.dt.tz is not None or column.isna().any() or (column != column.dt.normalize()).any():
            raise ValueError('PIT_ASOF_DATE_INVALID')
    if (scores.empty or universe.empty or scores.duplicated(['asof_date', 'asset_id']).any()
            or universe.duplicated(['asof_date', 'asset_id']).any() or not pd.api.types.is_bool_dtype(universe['member'].dtype)):
        raise ValueError('PIT_MEMBERSHIP_OR_KEY_INVALID')
    universe = universe[universe.asof_date.isin(scores.asof_date)]
    if universe['member'].isna().any():
        raise ValueError('PIT_MEMBERSHIP_UNKNOWN')
    if set(scores['asof_date']) - set(universe.loc[universe['member'], 'asof_date']):
        raise ValueError('PIT_NO_ELIGIBLE_UNIVERSE')
    signal = scores['signal_at'].map(_timestamp)
    score_available = scores['score_available_at'].map(_timestamp)
    local_signal_dates = signal.map(lambda stamp: stamp.astimezone(TAIPEI).date())
    numbers = pd.to_numeric(scores['composite_score'], errors='coerce')
    if (any(isinstance(value, (bool, np.bool_)) for value in scores['composite_score']) or not np.isfinite(numbers).all()
            or (score_available > signal).any() or (local_signal_dates != scores['asof_date'].dt.date).any()
            or not scores['score_source_sha256'].astype(str).str.fullmatch(r'[0-9a-fA-F]{64}').all()):
        raise ValueError('PIT_LOOKAHEAD_OR_EVIDENCE_INVALID')
    if signal.groupby(scores['asof_date']).nunique().gt(1).any():
        raise ValueError('PIT_SIGNAL_TIME_MISMATCH')
    signal_by_day = pd.Series(signal.to_numpy(), index=scores['asof_date']).groupby(level=0).first()
    universe_signal = universe['asof_date'].map(signal_by_day)
    membership_available = universe['membership_available_at'].map(_timestamp)
    calendar_available = universe['calendar_available_at'].map(_timestamp) if execution_calendar_mode == 'PREKNOWN' else None
    execution_dates = pd.to_datetime(universe['execution_date'], errors='raise')
    if (universe_signal.isna().any() or (membership_available > universe_signal).any()
            or (execution_calendar_mode == 'PREKNOWN' and (calendar_available > universe_signal).any())
            or execution_dates.dt.tz is not None or execution_dates.isna().any() or (execution_dates != execution_dates.dt.normalize()).any()
            or (execution_dates.dt.date <= universe['asof_date'].dt.date).any()
            or (execution_dates.dt.date > pd.Timestamp('2025-12-31').date()).any()
            or not universe['membership_source_sha256'].astype(str).str.fullmatch(r'[0-9a-fA-F]{64}').all()
            or (execution_calendar_mode == 'PREKNOWN' and not universe['calendar_source_sha256'].astype(str).str.fullmatch(r'[0-9a-fA-F]{64}').all())
            or (execution_calendar_mode == 'NEXT_SESSION_EVENT' and (universe['calendar_available_at'].notna().any() or universe['calendar_source_sha256'].notna().any()))):
        raise ValueError('PIT_LOOKAHEAD_OR_EVIDENCE_INVALID')
    if execution_dates.groupby(universe['asof_date']).nunique().gt(1).any():
        raise ValueError('PIT_EXECUTION_CALENDAR_MISMATCH')
    scores['composite_score'] = numbers
    keys = scores[['asof_date', 'asset_id']].merge(universe[['asof_date', 'asset_id']], on=['asof_date', 'asset_id'], how='left', indicator=True)
    if keys['_merge'].ne('both').any():
        raise ValueError('PIT_UNIVERSE_EVIDENCE_MISSING')
    frame = universe.merge(scores, on=['asof_date', 'asset_id'], how='left', validate='one_to_one')
    if frame.loc[frame.member.fillna(False), 'composite_score'].isna().any():
        raise ValueError('PIT_MEMBER_SCORE_MISSING')
    frame = frame.loc[frame.member.fillna(False)].copy()
    if frame.empty:
        raise ValueError('PIT_NO_ELIGIBLE_UNIVERSE')
    # ponytail: preserve the frozen legacy helper; consolidate this small weighting rule after a new experiment identity is versioned.
    selected = frame.sort_values(['asof_date', 'composite_score', 'asset_id'], ascending=[True, False, True], kind='stable').groupby('asof_date', group_keys=False).head(top_n).copy()
    if stock_weighting == 'equal':
        selected['target_weight'] = 1 / selected.groupby('asof_date')['asset_id'].transform('size')
    else:
        clipped = selected['composite_score'].clip(lower=0)
        maximum = clipped.groupby(selected['asof_date']).transform('max')
        scaled = clipped / maximum.where(maximum > 0, 1.0)
        total = scaled.groupby(selected['asof_date']).transform('sum')
        equal = 1 / selected.groupby('asof_date')['asset_id'].transform('size')
        selected['target_weight'] = (scaled / total.where(total > 0, 1.0)).where(total > 0, equal)
    sums = selected.groupby('asof_date')['target_weight'].sum()
    if (not np.isfinite(selected['target_weight']).all() or selected['target_weight'].lt(0).any()
            or not np.isclose(sums, 1.0, rtol=1e-9, atol=1e-12).all()):
        raise ValueError('INVALID_TARGET_WEIGHTS')
    selected['config_id'], selected['top_n'], selected['stock_weighting'] = config_id, top_n, stock_weighting
    selected['point_in_time_status'] = 'BLOCKED_UNVERIFIED_SOURCES'
    return selected[['asof_date', 'execution_date', 'asset_id', 'config_id', 'target_weight', 'top_n', 'stock_weighting',
                     'signal_at', 'score_available_at', 'membership_available_at', 'calendar_available_at',
                     'score_source_sha256', 'membership_source_sha256', 'calendar_source_sha256', 'point_in_time_status']]
