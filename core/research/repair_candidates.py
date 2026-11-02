"""Registered, development-only candidate research; source metadata is not source certification."""
from __future__ import annotations

import csv
from datetime import date, datetime, timedelta, timezone
import hashlib
import io
import json
import math
from pathlib import Path
import re

import pandas as pd

from core.runtime import operation_policy
from scripts import research_workflow as workflow

HYPOTHESIS = 'RESEARCH-CANDIDATES:QUALITY-GROWTH-TREND-LIQUIDITY-V1'
STRATEGY = 'RESEARCH_CANDIDATES_QUALITY_GROWTH_TREND_LIQUIDITY'
FACTORS = ('quality', 'revenue_growth', 'trend', 'liquidity')
TOP_N = 20
TAIPEI = timezone(timedelta(hours=8))


def registration() -> dict:
    """Exact pre-declared research design, suitable for the existing registry's register()."""
    return {'hypothesis_id': HYPOTHESIS, 'strategy': STRATEGY,
            'hypothesis': 'Equal percentile ranks of quality, revenue growth, trend and liquidity identify a development-only candidate universe.',
            'falsification_test': 'Reject if the 95% block-bootstrap lower bound of development 20-session net excess returns is not positive; incomplete official evidence is BLOCKED.',
            'data_window': 'development window ending 2025-12-31', 'trial_budget': '1',
            'source': 'research-candidates-v1:four-equal-ranks:top20:point-in-time'}


def _timestamp(value):
    result = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
    if result.tzinfo is None or result.utcoffset() is None:
        raise ValueError('PIT_TIMESTAMP_REQUIRES_TIMEZONE')
    return result.astimezone(timezone.utc)


def candidates(input_path: Path, as_of: str) -> dict:
    """Admit the fixed registered experiment before returning research-only ranked observations.

    Input JSON is a list of observations; caller-provided source hashes are traceability metadata,
    not proof that a source is official. Real-data certification remains blocked.
    """
    if operation_policy.current_mode() is not operation_policy.ExecutionMode.RESEARCH:
        raise PermissionError('RESEARCH_MODE_REQUIRED')
    cutoff = _timestamp(as_of)
    if cutoff.astimezone(TAIPEI).date() > date(2025, 12, 31):
        raise ValueError('OOS_BLOCKED: development window ends 2025-12-31')
    if workflow.verify_registry(workflow.REGISTRY) != 'PASS':
        raise ValueError('HYPOTHESIS_REGISTRY_UNVERIFIED')
    raw_registry = Path(workflow.REGISTRY).read_bytes()
    registry_hash = hashlib.sha256(raw_registry).hexdigest()
    head = json.loads(workflow._head(workflow.REGISTRY).read_text(encoding='utf-8'))
    if head.get('sha256') != registry_hash:
        raise ValueError('HYPOTHESIS_REGISTRY_UNVERIFIED')
    rows = [row for row in csv.DictReader(io.StringIO(raw_registry.decode('utf-8-sig'), newline='')) if row['hypothesis_id'] == HYPOTHESIS]
    expected = registration()
    if len(rows) != 1 or any(rows[0].get(k) != v for k, v in expected.items()):
        raise ValueError('HYPOTHESIS_NOT_REGISTERED_FOR_CANDIDATE_STRATEGY')
    if date.fromisoformat(rows[0]['registered_at']) > date.today():
        raise ValueError('HYPOTHESIS_REGISTRATION_IN_FUTURE')
    raw_input = Path(input_path).read_bytes()
    records = json.loads(raw_input.decode('utf-8'))
    if not isinstance(records, list):
        raise ValueError('INPUT_MUST_BE_OBSERVATION_LIST')
    eligible = []
    for record in records:
        ticker, factor = str(record['stock_id']).strip(), record['factor']
        publication, available = _timestamp(record['published_at']), _timestamp(record['available_at'])
        period = date.fromisoformat(record['period_end'])
        value, source, digest = record['value'], record['source'], record['source_sha256']
        if not ticker or factor not in FACTORS or not isinstance(source, str) or not source.strip():
            raise ValueError('INVALID_OBSERVATION_IDENTITY')
        if not isinstance(digest, str) or not re.fullmatch(r'[0-9a-fA-F]{64}', digest):
            raise ValueError('SOURCE_HASH_REQUIRED')
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
            raise ValueError('INVALID_OBSERVATION_VALUE')
        published_date = publication.astimezone(TAIPEI).date()
        if available < publication or period > published_date:
            raise ValueError('INVALID_PIT_CHRONOLOGY')
        if factor in ('quality', 'revenue_growth') and period >= published_date:
            raise ValueError('FINANCIAL_PERIOD_MUST_PRECEDE_PUBLICATION')
        if available <= cutoff and publication <= cutoff:
            eligible.append({'stock_id': ticker, 'factor': factor, 'period': period, 'published': publication,
                             'available': available, 'value': float(value), 'source': source, 'source_sha256': digest.lower()})
    latest = {}
    for row in eligible:
        key = row['stock_id'], row['factor']
        order = row['period'], row['available'], row['published']
        if key in latest and order == latest[key][0] and row != latest[key][1]:
            raise ValueError('AMBIGUOUS_PIT_OBSERVATION')
        if key not in latest or order > latest[key][0]:
            latest[key] = order, row
    coverage = {}
    for ticker, factor in latest:
        coverage.setdefault(ticker, set()).add(factor)
    if not any(set(FACTORS) <= factors for factors in coverage.values()):
        return {'label': 'RESEARCH_ONLY', 'data_status': 'BLOCKED_MISSING_PIT_FEATURES', 'formal_recommendation': False,
                'as_of': cutoff.isoformat(), 'hypothesis_id': HYPOTHESIS, 'candidates': []}
    # Admission precedes scoring; changed code/config/input is a distinct, budgeted trial.
    config = {'strategy': STRATEGY, 'factors': FACTORS, 'top_n': TOP_N, 'weighting': 'equal_percentile_rank', 'as_of': cutoff.isoformat()}
    admission = workflow.admit_experiment(HYPOTHESIS, config, [Path(__file__)], {'observations': Path(input_path)},
                                           cutoff.astimezone(TAIPEI).date().isoformat(), registry=workflow.REGISTRY, ledger=workflow.TRIAL_LEDGER)
    if admission['inputs_sha256']['observations'] != hashlib.sha256(raw_input).hexdigest():
        raise ValueError('INPUT_CHANGED_DURING_ADMISSION')
    if hashlib.sha256(Path(workflow.REGISTRY).read_bytes()).hexdigest() != registry_hash:
        raise ValueError('REGISTRY_CHANGED_DURING_ADMISSION')
    data = pd.DataFrame([item[1] for item in latest.values()])
    ranked = []
    if not data.empty:
        matrix = data.pivot(index='stock_id', columns='factor', values='value').reindex(columns=FACTORS).dropna()
        if not matrix.empty:
            scores = matrix.rank(pct=True, method='average').mean(axis=1).rename('score')
            ranked = [{'stock_id': str(ticker), 'score': float(score)} for ticker, score in
                      sorted(scores.items(), key=lambda pair: (-pair[1], pair[0]))[:TOP_N]]
    return {'label': 'RESEARCH_ONLY', 'data_status': 'BLOCKED_UNVERIFIED_SOURCES', 'formal_recommendation': False,
            'as_of': cutoff.isoformat(), 'hypothesis_id': HYPOTHESIS, 'experiment_id': admission['experiment_id'],
            'trial_kind': admission['kind'], 'eligible_observations': len(eligible), 'candidates': ranked}
