"""Strategy-neutral research signals: one format, target portfolio, simulated rebalance, performance-record shell and data health.

Research only. This module has no path to the recommendation table, LINE or a broker: it imports none of them and
writes only below outputs/research_mode. Signals later than the development window are refused.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
RESEARCH_DIR = ROOT / 'outputs/research_mode'
DEV_START, DEV_END = '2023-01-03', '2025-12-31'  # 2026 is sealed; a forward observation needs its own Owner authorization
COMMON = ('signal_date', 'ticker', 'final_score', 'rank', 'target_weight', 'strategy_version', 'data_version',
          'eligibility_status', 'source_hash')
STATUSES = ('ELIGIBLE', 'NO_GROWTH', 'NO_MOMENTUM', 'EVENT_EXCLUDED', 'NOT_MEMBER')
FILES = ('signals.csv', 'target_portfolio.csv', 'rebalance.csv', 'performance_record.csv', 'data_health.json', 'run_manifest.json')


class ResearchOnlyViolation(ValueError):
    """A request that would leave the research sandbox: sealed dates or an output outside the research directory."""


def check_window(*dates):
    for value in dates:
        if value is not None and str(value)[:10] > DEV_END:
            raise ResearchOnlyViolation('OOS_OR_INPUT_WINDOW_BLOCKED')


def source_hash(digests):
    """One hash over named input digests, order independent."""
    return hashlib.sha256('\n'.join(f'{k}:{v}' for k, v in sorted(digests.items())).encode()).hexdigest()


def standardize(frame, *, signal_date, top_n, strategy_version, data_version, digest, components):
    """frame: ticker, final_score, eligibility_status and component columns. Rank and equal weights follow the archived
    research runner (repair_targets.build_targets): score descending, ticker ascending, Top-N at 1/N."""
    check_window(signal_date)
    out = frame.copy()
    if not out['eligibility_status'].isin(STATUSES).all():
        raise ValueError('UNKNOWN_ELIGIBILITY_STATUS')
    eligible = out['eligibility_status'].eq('ELIGIBLE') & out['final_score'].notna()
    ranked = out[eligible].sort_values(['final_score', 'ticker'], ascending=[False, True], kind='stable')
    out['rank'] = pd.Series(range(1, len(ranked) + 1), index=ranked.index, dtype='Int64')
    chosen = ranked.index[:top_n]
    out['target_weight'] = 0.0
    out.loc[chosen, 'target_weight'] = 1 / len(chosen) if len(chosen) else 0.0
    out['signal_date'], out['strategy_version'], out['data_version'], out['source_hash'] = str(signal_date)[:10], strategy_version, data_version, digest
    out = out[list(COMMON) + list(components)]
    return out.sort_values(['rank', 'ticker'], na_position='last', kind='stable').reset_index(drop=True)


def target_portfolio(signals, max_weight=None):
    """Selected rows only; weights must sum to 1 and respect an optional single-name cap."""
    target = signals.loc[signals['target_weight'] > 0, ['signal_date', 'ticker', 'rank', 'final_score', 'target_weight']].reset_index(drop=True)
    if not target.empty and abs(target['target_weight'].sum() - 1) > 1e-12:
        raise ValueError('TARGET_WEIGHTS_DO_NOT_SUM_TO_ONE')
    if max_weight is not None and (target['target_weight'] > max_weight + 1e-12).any():
        raise ValueError('SINGLE_NAME_WEIGHT_ABOVE_CAP')
    return target


def rebalance(target, previous=None):
    """Simulated weight changes against the previous target. No order is created anywhere."""
    old = previous.set_index('ticker')['target_weight'] if previous is not None and not previous.empty else pd.Series(dtype=float)
    new = target.set_index('ticker')['target_weight']
    tickers = sorted(set(old.index) | set(new.index))
    out = pd.DataFrame({'ticker': tickers, 'previous_weight': old.reindex(tickers).fillna(0.0).to_numpy(),
                        'target_weight': new.reindex(tickers).fillna(0.0).to_numpy()})
    out['weight_change'] = out['target_weight'] - out['previous_weight']
    out['action'] = ['BUY' if c > 0 and p == 0 else 'SELL' if t == 0 else 'INCREASE' if c > 1e-12 else 'DECREASE' if c < -1e-12 else 'HOLD'
                     for p, t, c in zip(out['previous_weight'], out['target_weight'], out['weight_change'])]
    out.insert(0, 'signal_date', target['signal_date'].iloc[0] if not target.empty else None)
    out['simulated_only'] = True
    return out


def performance_record(target, holding_days):
    """The format future observations fill in. Nothing is observable at signal time, so values are empty with a reason."""
    out = target[['signal_date', 'ticker', 'target_weight']].copy()
    for column in ('entry_date', 'exit_date', 'gross_return', 'net_return_2x', 'benchmark_v2_return', 'excess_return_2x'):
        out[column] = None
    out['holding_days'] = holding_days
    out['status'] = 'NOT_OBSERVABLE'
    out['reason'] = 'FORWARD_PERIOD_NOT_OBSERVED_AT_SIGNAL_TIME'
    return out


def data_health(signals, input_dates, signal_date, previous=None):
    """Input dates, latency in calendar days, coverage and anomalies against the previous run."""
    day = pd.Timestamp(str(signal_date)[:10])
    members = signals.loc[signals['eligibility_status'] != 'NOT_MEMBER']
    scored = int(signals['eligibility_status'].isin(['ELIGIBLE', 'EVENT_EXCLUDED']).sum())
    eligible = int(signals['eligibility_status'].eq('ELIGIBLE').sum())
    coverage = scored / len(members) if len(members) else 0.0
    latency = {k: (day - pd.Timestamp(v)).days if v else None for k, v in input_dates.items()}
    anomalies = [f'INPUT_AFTER_SIGNAL_DATE:{k}' for k, v in latency.items() if v is not None and v < 0]
    anomalies += [f'INPUT_MISSING:{k}' for k, v in input_dates.items() if not v]
    if scored == 0:
        anomalies.append('NO_SCORED_MEMBER')
    if previous is not None:
        if len(members) < 0.8 * previous['members']:
            anomalies.append('MEMBER_COUNT_DROP_OVER_20PCT')
        if coverage < previous['score_coverage'] - 0.10:
            anomalies.append('SCORE_COVERAGE_DROP_OVER_10PT')
    return {'signal_date': str(signal_date)[:10], 'input_dates': input_dates, 'latency_days': latency,
            'members': len(members), 'scored': scored, 'eligible': eligible, 'score_coverage': coverage, 'status_counts': signals['eligibility_status'].value_counts().to_dict(),
            'anomalies': anomalies, 'status': 'ANOMALY' if anomalies else 'OK'}


def write_run(output, outputs, manifest):
    """Write the six run files below outputs/research_mode only."""
    output = Path(output).resolve()
    if not output.is_relative_to(RESEARCH_DIR.resolve()) or output == RESEARCH_DIR.resolve():
        raise ResearchOnlyViolation('OUTPUT_OUTSIDE_RESEARCH_DIRECTORY')
    output.mkdir(parents=True, exist_ok=True)
    for name in FILES[:4]:
        outputs[name].to_csv(output / name, index=False, encoding='utf-8')
    (output / 'data_health.json').write_text(json.dumps(outputs['data_health.json'], ensure_ascii=False, indent=1, default=str) + '\n', encoding='utf-8')
    (output / 'run_manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=1, default=str) + '\n', encoding='utf-8')
    return output
