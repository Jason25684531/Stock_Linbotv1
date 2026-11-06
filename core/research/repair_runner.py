"""Composed, development-only PIT raw-price research runner."""
from contextlib import closing
from dataclasses import asdict
from datetime import date, datetime, time
from functools import lru_cache
import hashlib
import json
import math
from pathlib import Path
import sys
import uuid

import numpy as np
import pandas as pd
import requests

from core.backtest.costs import CostModel
from core.backtest.metrics import calculate_metrics
from core.research.evidence_ledger import read as read_evidence
from core.research.repair_accounting import DataBlocked, TAIPEI, _timestamp, reconcile, simulate
from core.research.repair_targets import build_targets
from core.runtime import operation_policy
from scripts import research_workflow as workflow

OUTPUT_ROOT = workflow.ROOT / 'outputs/research_repair'
RESEARCH_OUTPUT_ROOT = workflow.ROOT / 'outputs/research_mode'
OFFICIAL = 'OFFICIAL_VERIFIED_FIELD'
# publication_time: the event must be public strictly before its ex-date (Taipei). NOT_APPLICABLE counts only with official tier.
REQUIRED_TERMS = ('ex_date', 'cash_dividend', 'stock_dividend', 'cash_pay_date', 'stock_credit_date', 'rights_terms', 'publication_time')
# Which verified request may certify a field, and whether that request was for this stock and ex-date.
MATCHERS = {'TWT49U_DETAIL': lambda params, row: params.get('STK_NO') == row['stock_id']
            and params.get('T') == row['event_date'].replace('-', '')}
# The detail endpoint carries amounts and rights terms only; payment, credit and publication times and complete
# event lists have no registered certifying dataset until an approved MOPS source exists (OA-1).
CERTIFYING = {field: () for field in (*REQUIRED_TERMS, 'event_list_complete')}
CERTIFYING.update({field: ('TWT49U_DETAIL',) for field in ('ex_date', 'cash_dividend', 'stock_dividend', 'rights_terms')})


def verified_sources():
    """sha256 -> request contexts the public-data boundary recorded as VERIFIED, read through the budget module's
    anchored, integrity-checked connection (SELECT only). None when the audit cannot be read."""
    import sqlite3
    from core.runtime import research_budget
    try:
        with research_budget.connection() as conn:
            found = conn.execute("SELECT sha256, context FROM attempts WHERE outcome='VERIFIED' AND sha256 IS NOT NULL").fetchall()
        sources = {}
        for digest, context in found:
            context = json.loads(context)
            sources.setdefault(digest, []).append({'dataset': context['dataset'], 'parameters': dict(context['parameters'])})
        return sources
    except (OSError, ValueError, KeyError, TypeError, sqlite3.Error, research_budget.BudgetDenied):
        return None  # an unreadable audit verifies nothing: every official row stays unbound, and reports say why


def _bound(row, verified):
    return any(source['dataset'] in CERTIFYING.get(row['field'], ())
               and MATCHERS[source['dataset']](source['parameters'], row) for source in verified.get(row['sha256'], ()))


def held_event_terms(rows, stocks, events, verified=None):
    """Data admission shared by the dry run and the formal run; never reads prices, targets or OOS.

    Three separate questions: is every stock's event list certified complete, is every event's
    required term official and known, and was the event public before its ex-date. An official row
    counts only when its sha256 is a verified response to a certifying request for that stock and ex-date.
    """
    sources = 'AUDIT_UNREADABLE' if verified is None else len(verified)
    verified = verified or {}
    latest = {(r['stock_id'], r['event_date'], r['field']): r for r in rows}  # append-only: a later row supersedes
    def problem(row):
        if row is None:
            return 'MISSING'
        if row['tier'] != OFFICIAL:
            return f"TIER_{row['tier']}"
        if not _bound(row, verified):
            return 'UNBOUND_OFFICIAL_ROW'
        return 'UNKNOWN' if row['value_or_UNKNOWN'] in (None, '', 'UNKNOWN') else None
    unlisted = sorted(s for s in set(stocks) if problem(latest.get((s, '', 'event_list_complete'))) is not None
                      or latest[(s, '', 'event_list_complete')]['value_or_UNKNOWN'] is not True)
    checked = []
    for stock, day in sorted(set(events)):
        bad = {field: reason for field in REQUIRED_TERMS if (reason := problem(latest.get((stock, day, field))))}
        if 'ex_date' not in bad and str(latest[(stock, day, 'ex_date')]['value_or_UNKNOWN']) != day:
            bad['ex_date'] = 'EX_DATE_VALUE_MISMATCH'
        if 'publication_time' not in bad:
            try:  # an offset-aware ISO timestamp whose Taipei date is strictly before the ex-date
                published = datetime.fromisoformat(str(latest[(stock, day, 'publication_time')]['value_or_UNKNOWN']))
                if published.tzinfo is None:
                    raise ValueError('naive publication time')
                if published.astimezone(TAIPEI).date() >= date.fromisoformat(day):
                    bad['publication_time'] = 'NOT_PUBLISHED_BEFORE_EX_DATE'
            except ValueError:
                bad['publication_time'] = 'INVALID_PUBLICATION_TIME'
        checked.append({'stock_id': stock, 'ex_date': day, 'status': 'BLOCKED' if bad else 'VERIFIED', 'blocked_fields': bad})
    blocked = sum(e['status'] == 'BLOCKED' for e in checked)
    return {'status': 'BLOCKED' if blocked or unlisted else 'VERIFIED', 'total': len(checked), 'verified': len(checked) - blocked,
            'blocked': blocked, 'stocks_event_list_unverified': unlisted, 'events': checked,
            'verified_sources': sources}


def _read_inputs(paths, admission):
    def unique_pairs(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(f'DUPLICATE_JSON_KEY:{key}')
            result[key] = value
        return result
    data = {}
    for name, path in paths.items():
        raw = Path(path).read_bytes()
        if hashlib.sha256(raw).hexdigest() != admission['inputs_sha256'][name]:
            raise DataBlocked(f'INPUT_CHANGED_AFTER_ADMISSION:{name}')
        if name == 'evidence' and Path(path).suffix.lower() == '.jsonl':
            records = read_evidence(path)
            if workflow.sha256(path) != admission['inputs_sha256'][name] or not records:
                raise DataBlocked('EVIDENCE_CHANGED_OR_EMPTY')
            data[name] = {'records': len(records), 'chain': 'PASS'}
            continue
        data[name] = json.loads(raw.decode('utf-8'), object_pairs_hook=unique_pairs,
                                parse_constant=lambda value: (_ for _ in ()).throw(ValueError(f'INVALID_JSON_NUMBER:{value}')))
    return data


def _hook(targets, bars, *, execution_calendar_mode='PREKNOWN', rank_buffer_multiple=None, decisions=None, empty_days=None):
    groups = {str(day.date()): group.to_dict('records') for day, group in targets.groupby('asof_date')}
    empty_days = empty_days or {}
    calendar = sorted(bars)
    next_session = dict(zip(calendar, calendar[1:]))
    for day, row in [(day, rows[0]) for day, rows in groups.items()] + list(empty_days.items()):
        if str(row['execution_date'])[:10] != next_session.get(day):
            raise DataBlocked('TARGET_EXECUTION_NOT_NEXT_SESSION')

    def after_close(state, pending):
        day = state['date']
        if day not in groups and day not in empty_days:
            return None
        rows = groups.get(day, [])
        evidence = rows[0] if rows else empty_days[day]
        if not rows and decisions is not None:
            decisions.append({**evidence, 'asof_date': day, 'asset_id': '', 'target_weight': 0.0,
                              'empty_target_day': True})
        if rank_buffer_multiple is not None and rows:
            held = {asset for asset, shares in state['positions'].items() if shares > 0}
            held.update(claim['stock_id'] for claim in state['stock_receivable'] if claim['shares'] > 0)
            count = int(rows[0]['top_n'])
            retained = [row for row in rows if row['asset_id'] in held]
            if len(retained) > count:
                raise DataBlocked('BUFFER_HELD_COUNT_EXCEEDS_TOP_N')
            chosen = retained + [row for row in rows if row['asset_id'] not in held][:count - len(retained)]
            chosen_ids = {row['asset_id'] for row in chosen}
            rows = [dict(row) for row in rows if row['asset_id'] in chosen_ids]
            clipped = [max(0.0, float(row['composite_score'])) for row in rows]
            maximum = max(clipped)
            scaled = [score / maximum if maximum > 0 else 0 for score in clipped]
            total = sum(scaled)
            weights = ([score / total for score in scaled] if total > 0 and rows[0]['stock_weighting'] == 'score_weighted'
                       else [1 / len(rows)] * len(rows))
            for row, weight in zip(rows, weights):
                row['target_weight'] = weight
            if decisions is not None:
                decisions.extend(rows)
        signal = _timestamp(evidence['signal_at'])
        if any(_timestamp(row['signal_at']) != signal for row in rows):
            raise DataBlocked('SIGNAL_TIME_MISMATCH')
        weights = {row['asset_id']: float(row['target_weight']) for row in rows}
        shares_due = {}
        for claim in state['stock_receivable']:
            shares_due[claim['stock_id']] = shares_due.get(claim['stock_id'], 0) + claim['shares']
        assets = set(weights) | set(state['positions']) | set(shares_due)
        time_columns = ('score_available_at', 'membership_available_at', 'calendar_available_at') if execution_calendar_mode == 'PREKNOWN' else ('score_available_at', 'membership_available_at')
        times = [_timestamp(row[column]) for row in (rows or [evidence]) for column in time_columns]
        orders = []
        for asset in sorted(assets):
            price_day = state['stale_marks'].get(asset, day)
            try:
                bar = bars[price_day][asset]
                available = _timestamp(bar['close_available_at'])
                price = float(bar['close'])
            except (KeyError, TypeError, ValueError) as exc:
                raise DataBlocked(f'SIGNAL_CLOSE_UNAVAILABLE:{asset}:{day}') from exc
            local_available = available.astimezone(TAIPEI)
            if (available > signal or local_available.date().isoformat() != price_day
                    or local_available.time() < time(13, 30) or not math.isfinite(price) or price <= 0):
                raise DataBlocked(f'SIGNAL_CLOSE_UNAVAILABLE:{asset}:{day}')
            times.append(available)
            desired = math.floor(state['nav'] * weights.get(asset, 0.0) / price)
            current = state['positions'].get(asset, 0)
            delta = desired - current - shares_due.get(asset, 0)
            if delta < 0:
                delta = -min(-delta, current)  # uncredited shares cannot be sold
            if delta:
                orders.append({'id': f'{day}:{asset}', 'date': str(evidence['execution_date'])[:10],
                               'stock_id': asset, 'side': 'buy' if delta > 0 else 'sell', 'shares': abs(delta),
                               'signal_at': signal.isoformat(), 'latest_input_available_at': None})
        latest = max(times).isoformat()
        for order in orders:
            order['latest_input_available_at'] = latest
        return {'cancel': [item['id'] for item in pending], 'orders': orders}
    return after_close


def _research_tracks(*, bars, scores, universe, actions, coverage, unresolved_events, parameters, assumptions=None):
    """Run both diagnostic cost tracks or return only the blocking evidence, never partial metrics."""
    from core.research.repair_accounting import UnresolvedCorporateAction
    from core.research.research_mode import RESEARCH_ASSUMPTIONS_V3, apply_event_exclusions, assumptions_sha, held_event_policy, overlap_policy

    assumptions = RESEARCH_ASSUMPTIONS_V3 if assumptions is None else assumptions
    policy = held_event_policy(assumptions)
    overlap = overlap_policy(assumptions)

    original_scores = scores
    score_keys = {(row['asof_date'], row['asset_id']) for row in scores}
    if len(score_keys) != len(scores):
        raise DataBlocked('DUPLICATE_SCORE_IDENTITY')
    if parameters.get('rank_buffer_multiple') is not None:
        for row in scores:
            signal = _timestamp(row['signal_at'])
            digest = str(row.get('score_source_sha256', ''))
            if (signal.astimezone(TAIPEI).date().isoformat() != row['asof_date']
                    or _timestamp(row['score_available_at']) > signal or len(digest) != 64
                    or any(c not in '0123456789abcdefABCDEF' for c in digest)):
                raise DataBlocked('PIT_LOOKAHEAD_OR_EVIDENCE_INVALID')
        scores = [row for row in scores if not isinstance(row.get('composite_score'), bool)
                  and isinstance(row.get('composite_score'), (int, float))
                  and math.isfinite(row['composite_score'])]
        score_keys = {(row['asof_date'], row['asset_id']) for row in scores}
    day_signals = {}
    for row in original_scores:
        if row['asof_date'] in day_signals and day_signals[row['asof_date']] != row['signal_at']:
            raise DataBlocked('PIT_SIGNAL_TIME_MISMATCH')
        day_signals[row['asof_date']] = row['signal_at']
    missing_signal_days = set(row['asof_date'] for row in universe) - set(day_signals)
    if (parameters.get('rank_buffer_multiple') is not None and day_signals
            and any(day > min(day_signals) for day in missing_signal_days)):
        raise DataBlocked('BUFFER_EMPTY_DAY_EVIDENCE_MISSING')
    usable_universe = [row for row in universe if row['asof_date'] in day_signals]
    signals = {(row['asof_date'], row['asset_id']): day_signals[row['asof_date']] for row in usable_universe}
    labels = {'data_label': 'RESEARCH_UNCERTIFIED', 'label': 'DIAGNOSTIC_ONLY', 'research_only': True,
              'terms_status': 'RESEARCH_ASSUMPTIONS', 'assumptions_sha256': assumptions_sha(assumptions),
              'held_event_policy': policy, 'overlap_policy': overlap}
    if not usable_universe:
        return {**labels, 'status': 'NOT_COMPUTABLE', 'reason': 'NO_ELIGIBLE_REBALANCE',
                'skipped_rebalance_dates': sorted({row['asof_date'] for row in universe})}
    initial_universe = pd.DataFrame(usable_universe)
    filtered = apply_event_exclusions(initial_universe, signals, unresolved_events)
    excluded = initial_universe.loc[initial_universe['member'] & ~filtered['member'], 'asset_id'].astype(str).unique().tolist()
    labels['excluded_stock_reasons'] = {asset: sorted({term for event in unresolved_events if str(event['stock_id']) == asset
                                                      for term in event['missing_terms']}) for asset in sorted(excluded)}
    labels['excluded_membership_rows'] = int((initial_universe['member'] & ~filtered['member']).sum())
    filtered['member'] = filtered['member'] & pd.Series([(row.asof_date, row.asset_id) in score_keys
                                                          for row in filtered.itertuples()], index=filtered.index)
    valid_days = set(filtered.loc[filtered['member'], 'asof_date'])
    skipped = sorted({row['asof_date'] for row in universe} - valid_days)
    empty_days = {}
    if parameters.get('rank_buffer_multiple') is not None:
        for day in sorted(set(day_signals) - valid_days):
            day_scores = [row for row in original_scores if row['asof_date'] == day]
            day_universe = [row for row in usable_universe if row['asof_date'] == day]
            if not day_scores or not day_universe:
                raise DataBlocked('BUFFER_EMPTY_DAY_EVIDENCE_MISSING')
            signal = _timestamp(day_signals[day])
            if signal.astimezone(TAIPEI).date().isoformat() != day:
                raise DataBlocked('PIT_LOOKAHEAD_OR_EVIDENCE_INVALID')
            for row in day_scores:
                digest = str(row.get('score_source_sha256', ''))
                if _timestamp(row['score_available_at']) > signal or len(digest) != 64 or any(c not in '0123456789abcdefABCDEF' for c in digest):
                    raise DataBlocked('PIT_LOOKAHEAD_OR_EVIDENCE_INVALID')
            for row in day_universe:
                digest = str(row.get('membership_source_sha256', ''))
                if (_timestamp(row['membership_available_at']) > signal or len(digest) != 64
                        or any(c not in '0123456789abcdefABCDEF' for c in digest)
                        or row.get('calendar_available_at') is not None or row.get('calendar_source_sha256') is not None):
                    raise DataBlocked('PIT_LOOKAHEAD_OR_EVIDENCE_INVALID')
            execution_dates = {str(row['execution_date'])[:10] for row in day_universe}
            if len(execution_dates) != 1 or not day < next(iter(execution_dates)) <= '2025-12-31':
                raise DataBlocked('PIT_EXECUTION_CALENDAR_MISMATCH')
            empty_days[day] = {'signal_at': signal.isoformat(), 'execution_date': next(iter(execution_dates)),
                               'score_available_at': max(_timestamp(row['score_available_at']) for row in day_scores).isoformat(),
                               'membership_available_at': max(_timestamp(row['membership_available_at']) for row in day_universe).isoformat(),
                               'score_source_sha256': day_scores[0]['score_source_sha256'],
                               'membership_source_sha256': day_universe[0]['membership_source_sha256'],
                               'config_id': parameters['config_id'], 'top_n': parameters['top_n'],
                               'stock_weighting': parameters['stock_weighting'],
                               'point_in_time_status': 'BLOCKED_UNVERIFIED_SOURCES'}
        skipped = [day for day in skipped if day not in empty_days]
    if not valid_days and not empty_days:
        return {**labels, 'status': 'NOT_COMPUTABLE', 'reason': 'NO_ELIGIBLE_REBALANCE', 'skipped_rebalance_dates': skipped}
    universe_keys = set(zip(filtered['asof_date'], filtered['asset_id']))
    valid_scores = [row for row in scores if row['asof_date'] in valid_days and (row['asof_date'], row['asset_id']) in universe_keys]
    rank_buffer_multiple = parameters.get('rank_buffer_multiple')
    targets = (build_targets(pd.DataFrame(valid_scores), filtered[filtered['asof_date'].isin(valid_days)],
                             config_id=parameters['config_id'], top_n=parameters['top_n'],
                             stock_weighting=parameters['stock_weighting'], execution_calendar_mode='NEXT_SESSION_EVENT',
                             rank_buffer_multiple=rank_buffer_multiple) if valid_days
               else pd.DataFrame(columns=['asof_date']))
    tracks = {}
    track_targets = {}
    for multiple, name in ((1, 'base'), (2, 'stress')):
        cost = CostModel(parameters['fee_rate'] * multiple, parameters['tax_rate'] * multiple,
                         parameters['minimum_fee'] * multiple)
        args = {'bars': bars, 'orders': [], 'actions': actions, 'action_coverage': coverage,
                'initial_cash': parameters['initial_cash'], 'cost': cost, 'slippage': parameters['slippage'] * multiple,
                'fractional_policy': 'DROP'}
        try:
            decisions = []
            result = simulate(**args, close_hook=_hook(targets, bars, execution_calendar_mode='NEXT_SESSION_EVENT',
                                                       rank_buffer_multiple=rank_buffer_multiple, decisions=decisions, empty_days=empty_days),
                              unresolved_events=unresolved_events, max_stale_sessions=5, unresolved_policy=policy, overlap_policy=overlap)
        except UnresolvedCorporateAction as exc:
            return {**labels, **exc.details, 'cost_track': name, 'skipped_rebalance_dates': skipped}
        check = reconcile(result=result, **{**args, 'orders': result['generated_orders']}, cancellations=result['cancellations'],
                          unresolved_events=unresolved_events, unresolved_policy=policy, overlap_policy=overlap)
        if check['status'] != 'PASS':
            raise DataBlocked(f'RECONCILIATION_FAILED:{name}:{check}')
        metrics = {key: asdict(metric) for key, metric in calculate_metrics(
            [parameters['initial_cash']] + [row['nav'] for row in result['daily']]).values.items()}
        forfeits = [row for row in result['ledger'] if row['kind'] == 'ENTITLEMENT_FORFEITED']
        exits = [row for row in result['ledger'] if row['kind'] == 'FORCED_EXIT']
        known = [row['forfeited_value'] for row in forfeits if row.get('forfeited_value') is not None]
        final_nav = result['daily'][-1]['nav']
        tracks[name] = {'result': result, 'reconciliation': check, 'metrics': metrics,
                        'label': 'DIAGNOSTIC_ONLY', 'research_only': True,
                        'forfeits': {'events': len(forfeits), 'stocks': sorted({row['stock_id'] for row in forfeits}),
                                     'events_with_official_value': len(known), 'forfeited_value_total': float(sum(known)),
                                     'forfeited_share_of_final_nav': float(sum(known) / final_nav) if final_nav > 0 else None,
                                     'forced_exits': len(exits), 'forced_exit_stocks': sorted({row['stock_id'] for row in exits}),
                                     'forced_exit_notional_share_of_final_nav': float(sum(row['notional'] for row in exits) / final_nav) if final_nav > 0 else None,
                                     'bias_direction': 'returns understated by forfeits; forced exits forgo post-resumption moves'}}
        if rank_buffer_multiple is not None:
            track_targets[name] = pd.DataFrame(decisions)
    return {**labels, 'status': 'COMPUTABLE', 'skipped_rebalance_dates': skipped,
            'targets': track_targets['stress'] if rank_buffer_multiple is not None else targets, 'tracks': tracks,
            **({'track_targets': track_targets} if rank_buffer_multiple is not None else {}),
            'forfeits': {name: track['forfeits'] for name, track in tracks.items()}}


@lru_cache(maxsize=2)
def _reproduce_day3_checkpoints(prepared_dir, signals_dir, input_manifest_sha, signals_manifest_sha):
    """Rebuild the offline conversion once per process and compare every sealed checkpoint."""
    from scripts.prepare_day3_signals import prepare as prepare_signals
    from scripts.prepare_d5_signals import prepare as prepare_d5
    from scripts.prepare_research_inputs import prepare as prepare_inputs
    from scripts.prepare_research_mode import prepare as prepare_bars
    from core.research.research_mode import REGISTERED_ASSUMPTIONS, registered_version
    prepared, signals = Path(prepared_dir), Path(signals_dir)
    if (workflow.sha256(prepared / 'input_manifest.json') != input_manifest_sha
            or workflow.sha256(signals / 'signals_manifest.json') != signals_manifest_sha):
        raise DataBlocked('SCORES_NOT_FROM_RAW_CONVERSION')
    inputs = json.loads((prepared / 'input_manifest.json').read_text(encoding='utf-8'))
    manifest = json.loads((signals / 'signals_manifest.json').read_text(encoding='utf-8'))
    input_version = registered_version(inputs.get('assumptions_sha256'))
    signal_version = registered_version(manifest.get('assumptions_sha256'))
    if input_version is None or signal_version is None:
        raise DataBlocked('UNREGISTERED_RESEARCH_ASSUMPTIONS')
    if manifest.get('kind') == 'H4_HIST_GRID':  # historical inputs are rebuilt from their own raw files, never from prepared_v6
        from scripts.research_h4_hist_run import prepare_inputs as hist_inputs, prepare_signals as hist_signals
        hist_inputs(prepared)
        hist_signals(prepared, signals)
        if (workflow.sha256(prepared / 'input_manifest.json') != input_manifest_sha
                or workflow.sha256(signals / 'signals_manifest.json') != signals_manifest_sha):
            raise DataBlocked('CHECKPOINT_CHANGED_DURING_VALIDATION')
        return True
    t1b = workflow.ROOT / 'outputs/finlab_recovery/2026-10-24/t1b_official'
    references = {}
    for pattern, kind in (('TWTAUU_*+0000.bin', 'CAPITAL_REDUCTION'), ('TWTB8U_*+0000.bin', 'PAR_VALUE_CHANGE')):
        files = list(t1b.glob(pattern))
        if len(files) != 1:
            raise DataBlocked('REFERENCE_CACHE_UNVERIFIED')
        references[kind] = files[0]
    cache = workflow.ROOT / 'outputs/research_mode/input_cache_v4'
    raw = workflow.ROOT / 'artifacts/factors/d3_full_20230103_20260728/_raw/twse_rwd'
    prepare_bars(raw, cache)
    prepare_inputs(cache, prepared, raw / 'TWT49U_20230103_20260728.json', references,
                   workflow.ROOT / 'artifacts/official_dual_track_cache/suspendListing.json',
                   assumptions=REGISTERED_ASSUMPTIONS[input_version])
    if manifest.get('kind') == 'D5_RESEARCH_PIT_V1':
        prepare_d5(prepared, signals, assumptions=REGISTERED_ASSUMPTIONS[signal_version])
    elif manifest.get('kind') == 'H1_SINGLE_FACTOR_GRID':
        from scripts.prepare_h1_signals import prepare as prepare_h1
        prepare_h1(prepared, signals, assumptions=REGISTERED_ASSUMPTIONS[signal_version])
    elif manifest.get('kind') == 'H4_GROWTH_MOMENTUM_GRID':
        from scripts.prepare_h4_signals import prepare as prepare_h4
        prepare_h4(prepared, signals, assumptions=REGISTERED_ASSUMPTIONS[signal_version])
    else:
        prepare_signals(prepared, signals, assumptions=REGISTERED_ASSUMPTIONS[signal_version])
    if (workflow.sha256(prepared / 'input_manifest.json') != input_manifest_sha
            or workflow.sha256(signals / 'signals_manifest.json') != signals_manifest_sha):
        raise DataBlocked('CHECKPOINT_CHANGED_DURING_VALIDATION')
    return True


def _verify_d5_provenance(evidence, paths, parameters):
    """Verify the distinct D5 research variant's inputs and reproduce its per-decision signals."""
    from core.research.d5_research_variant import policy_sha
    from core.research.research_mode import D5_RESEARCH_STRATEGY, INPUT_VERSION_OF, REGISTERED_ASSUMPTIONS, assumptions_sha, registered_version
    if evidence.get('kind') != 'D5_RESEARCH_PIT_REPRODUCIBLE_V1' or evidence.get('policy_sha256') != policy_sha():
        raise DataBlocked('SCORES_NOT_FROM_RAW_CONVERSION')
    version = registered_version(parameters.get('assumptions_sha256'))
    if version not in INPUT_VERSION_OF:
        raise DataBlocked('UNREGISTERED_RESEARCH_ASSUMPTIONS')
    input_sha = assumptions_sha(REGISTERED_ASSUMPTIONS[INPUT_VERSION_OF[version]])
    prepared = Path(paths['bars']).resolve().parent
    signals = Path(paths['scores']).resolve().parent
    if (not prepared.is_relative_to(Path(RESEARCH_OUTPUT_ROOT).resolve())
            or not signals.is_relative_to(Path(RESEARCH_OUTPUT_ROOT).resolve())
            or Path(paths['bars']).resolve() != prepared / 'bars.json'
            or Path(paths['actions']).resolve() != prepared / 'actions.json'):
        raise DataBlocked('SCORES_NOT_FROM_RAW_CONVERSION')
    try:
        inputs = json.loads((prepared / 'input_manifest.json').read_text(encoding='utf-8'))
        manifest = json.loads((signals / 'signals_manifest.json').read_text(encoding='utf-8'))
        cadence = int(parameters['rebalance_days'])
        index = manifest['composites'].index(parameters['combination_method'])
        score_name, universe_name = f'scores_{index:02d}_{cadence}.json', f'universe_{cadence}.json'
        if (parameters.get('strategy') != D5_RESEARCH_STRATEGY or manifest.get('kind') != 'D5_RESEARCH_PIT_V1'
                or manifest.get('policy_sha256') != policy_sha()
                or cadence not in (20, 60) or Path(paths['scores']).resolve() != signals / score_name
                or Path(paths['universe']).resolve() != signals / universe_name
                or inputs['assumptions_sha256'] != input_sha
                or manifest['assumptions_sha256'] != parameters['assumptions_sha256']
                or manifest['input_manifest_sha256'] != workflow.sha256(prepared / 'input_manifest.json')
                or evidence['signals_manifest_sha256'] != workflow.sha256(signals / 'signals_manifest.json')
                or any(workflow.sha256(prepared / name) != value for name, value in inputs['files'].items())
                or any(workflow.sha256(signals / name) != manifest['files'][name] for name in (score_name, universe_name))):
            raise DataBlocked('SCORES_NOT_FROM_RAW_CONVERSION')
        grid = pd.read_csv(workflow.ROOT / 'outputs/portfolio_research/d5_portfolio_research_20260814_v1/parameter_grid.csv')
        row = grid.loc[grid['config_id'] == parameters['config_id']]
        if (len(row) != 1 or row.iloc[0]['combination_method'] != parameters['combination_method']
                or int(row.iloc[0]['top_n']) != parameters['top_n'] or int(row.iloc[0]['rebalance_days']) != cadence
                or row.iloc[0]['stock_weighting'] != parameters['stock_weighting']):
            raise DataBlocked('ARCHIVED_CONFIGURATION_MISMATCH')
        _reproduce_day3_checkpoints(str(prepared), str(signals), manifest['input_manifest_sha256'], evidence['signals_manifest_sha256'])
    except (OSError, KeyError, ValueError, TypeError, IndexError) as exc:
        if isinstance(exc, DataBlocked):
            raise
        raise DataBlocked('SCORES_NOT_FROM_RAW_CONVERSION') from exc


EVIDENCE_GRIDS = {  # runner evidence kind -> (signals manifest kind, fixed grid file, hypothesis that must be registered without a result)
    'DAY3_RAW_REPRODUCIBLE_V1': ('DAY3_ARCHIVED_GRID', 'outputs/strategy_research/strategy_search_20260914_v2/strategy_search_manifest.csv', None),
    'H1_RAW_REPRODUCIBLE_V1': ('H1_SINGLE_FACTOR_GRID', 'outputs/strategy_research/h1_near_high_252d_v1/strategy_grid.csv', 'near_high_252d:H1'),
    'H4_RAW_REPRODUCIBLE_V1': ('H4_GROWTH_MOMENTUM_GRID', 'outputs/strategy_research/h4_growth_momentum_v1/strategy_grid.csv', 'H4:GROWTH-MOMENTUM-V2'),
    'H4_HIST_RAW_REPRODUCIBLE_V1': ('H4_HIST_GRID', 'outputs/strategy_research/h4_growth_momentum_v1/strategy_grid.csv', 'H4:HIST-2016-2022-V1'),
}
BAR_START = {'H4_HIST_RAW_REPRODUCIBLE_V1': '2016-01-04'}  # registered historical window; every other kind keeps 2023-01-03


def _verify_research_provenance(evidence, paths, parameters):
    """Verify registered inputs and reproduce the full Day3 (or registered H1) conversion."""
    if evidence.get('kind') not in EVIDENCE_GRIDS:
        raise DataBlocked('SCORES_NOT_FROM_RAW_CONVERSION')
    signal_kind, grid_file, hypothesis = EVIDENCE_GRIDS[evidence['kind']]
    if hypothesis:
        try:
            workflow.require_registered(hypothesis)
        except ValueError as exc:
            raise DataBlocked('NOT_REGISTERED_OR_ALREADY_JUDGED') from exc
    prepared = Path(paths['bars']).resolve().parent
    signals = Path(paths['scores']).resolve().parent
    if (not prepared.is_relative_to(Path(RESEARCH_OUTPUT_ROOT).resolve())
            or not signals.is_relative_to(Path(RESEARCH_OUTPUT_ROOT).resolve())
            or Path(paths['bars']).resolve() != prepared / 'bars.json'
            or Path(paths['actions']).resolve() != prepared / 'actions.json'):
        raise DataBlocked('SCORES_NOT_FROM_RAW_CONVERSION')
    try:
        inputs = json.loads((prepared / 'input_manifest.json').read_text(encoding='utf-8'))
        manifest = json.loads((signals / 'signals_manifest.json').read_text(encoding='utf-8'))
        cadence = int(parameters['rebalance_days'])
        index = manifest['composites'].index(parameters['composite_id'])
        score_name, universe_name = f'scores_{index:02d}_{cadence}.json', f'universe_{cadence}.json'
        grid_path = workflow.ROOT / grid_file
        if (cadence not in (20, 60) or Path(paths['scores']).resolve() != signals / score_name
                or manifest.get('kind') != signal_kind
                or (hypothesis and manifest.get('grid_sha256') != workflow.sha256(grid_path))
                or Path(paths['universe']).resolve() != signals / universe_name
                or inputs['assumptions_sha256'] != parameters['assumptions_sha256']
                or manifest['assumptions_sha256'] != parameters['assumptions_sha256']
                or manifest['input_manifest_sha256'] != workflow.sha256(prepared / 'input_manifest.json')
                or evidence['signals_manifest_sha256'] != workflow.sha256(signals / 'signals_manifest.json')
                or any(workflow.sha256(prepared / name) != value for name, value in inputs['files'].items())
                or any(workflow.sha256(signals / name) != manifest['files'][name] for name in (score_name, universe_name))):
            raise DataBlocked('SCORES_NOT_FROM_RAW_CONVERSION')
        grid = pd.read_csv(grid_path)
        row = grid.loc[grid['strategy_id'] == parameters.get('source_config_id', parameters['config_id'])]
        if (len(row) != 1 or row.iloc[0]['factor_set'] != parameters['strategy']
                or row.iloc[0]['composite_id'] != parameters['composite_id']
                or int(row.iloc[0]['top_n']) != parameters['top_n']
                or int(row.iloc[0]['rebalance_days']) != cadence
                or row.iloc[0]['portfolio_weighting'] != parameters['stock_weighting']):
            raise DataBlocked('ARCHIVED_CONFIGURATION_MISMATCH')
        _reproduce_day3_checkpoints(str(prepared), str(signals), manifest['input_manifest_sha256'], evidence['signals_manifest_sha256'])
    except (OSError, KeyError, ValueError, TypeError, IndexError) as exc:
        if isinstance(exc, DataBlocked):
            raise
        raise DataBlocked('SCORES_NOT_FROM_RAW_CONVERSION') from exc


def _run_research(config, *, registry, research_ledger):
    from core.research.research_mode import (D5_RESEARCH_STRATEGY, REGISTERED_ASSUMPTIONS, RUNNABLE_VERSIONS,
                                             assumptions_sha, registered_version)

    paths = {key: Path(value) for key, value in config['inputs'].items()}
    if not workflow.PIT_RAW_INPUTS <= paths.keys() or any(not path.is_file() for path in paths.values()):
        raise ValueError('PIT_RAW_INPUTS_INCOMPLETE')
    output = Path(config['output']).resolve()
    root = Path(RESEARCH_OUTPUT_ROOT).resolve()
    if not output.is_relative_to(root) or output == root:
        raise ValueError('OUTPUT_OUTSIDE_RESEARCH_DIRECTORY')
    if date.fromisoformat(config['data_end']) > date(2025, 12, 31):
        raise DataBlocked('OOS_OR_INPUT_WINDOW_BLOCKED')
    evidence = json.loads(paths['evidence'].read_text(encoding='utf-8'))
    source = workflow.ROOT / 'scripts/research_mode_inputs.py'
    parameters = {key: value for key, value in config.items() if key not in ('inputs', 'output', 'data_end')}
    if parameters.get('rank_buffer_multiple') is not None:
        allowed = {
            'RS_004_NEAR_HIGH_120D': ('RS_004_NEAR_HIGH_120D__EQUAL__TOP10__REB20__SCORE_WEIGHTED',
                                      'RS_004_NEAR_HIGH_120D__TOP10__REB20__SCORE_WEIGHTED__BUF2'),
            'A191_088': ('A191_088__EQUAL__TOP30__REB20__SCORE_WEIGHTED',
                         'A191_088__TOP30__REB20__SCORE_WEIGHTED__BUF2'),
        }
        identity = allowed.get(parameters.get('strategy'))
        if (parameters['rank_buffer_multiple'] != 2 or identity is None
                or (parameters.get('source_config_id'), parameters.get('config_id')) != identity):
            raise DataBlocked('UNREGISTERED_BUFFER_CONFIGURATION')
        for name in ('RS_004_NEAR_HIGH_120D', 'A191_088'):
            workflow.require_registered(name + ':HYP-COST', registry)
    elif 'source_config_id' in parameters:
        raise DataBlocked('UNREGISTERED_BUFFER_CONFIGURATION')
    family = 'D5' if parameters.get('strategy') == D5_RESEARCH_STRATEGY else 'DAY3'
    version = registered_version(parameters.get('assumptions_sha256'))
    if version not in RUNNABLE_VERSIONS[family]:
        raise DataBlocked('UNREGISTERED_RESEARCH_ASSUMPTIONS')
    assumptions = REGISTERED_ASSUMPTIONS[version]
    if family == 'D5':
        _verify_d5_provenance(evidence, paths, parameters)
    else:
        _verify_research_provenance(evidence, paths, parameters)
    code_paths = sorted({p.resolve() for p in workflow.PIT_RAW_CODE} | workflow._loaded_local_code()
                        | {source.resolve(), (workflow.ROOT / 'core/research/research_mode.py').resolve(),
                           (workflow.ROOT / 'scripts/research_mode_run.py').resolve()}
                        | ({(workflow.ROOT / 'core/research/d5_research_variant.py').resolve(),
                            (workflow.ROOT / 'scripts/prepare_d5_signals.py').resolve()} if family == 'D5' else set()), key=str)
    candidates = ([output] + sorted(output.glob('attempt_*'))) if output.exists() else []
    for candidate in candidates:
        manifest_path = candidate / 'manifest.json'
        if not manifest_path.is_file():
            continue
        saved = json.loads(manifest_path.read_text(encoding='utf-8'))
        if (saved.get('config') != parameters or saved.get('data_end') != config['data_end']
                or saved.get('inputs_sha256') != {name: workflow.sha256(path) for name, path in paths.items()}
                or saved.get('code_sha256') != {path.as_posix(): workflow.sha256(path) for path in code_paths}
                or saved.get('label') != 'DIAGNOSTIC_ONLY' or saved.get('research_only') is not True
                or workflow.verify_trials(research_ledger) != 'PASS'
                or not any(row['experiment_id'] == saved.get('experiment_id') for row in workflow._trial_rows(research_ledger))
                or any(workflow.sha256(candidate / name) != digest for name, digest in saved.get('output_sha256', {}).items())
                or any(workflow.sha256(candidate / f'targets_{name}.csv') != digest
                       for name, digest in saved.get('track_targets_sha256', {}).items())):
            raise DataBlocked('RESEARCH_CHECKPOINT_UNVERIFIED')
        if saved.get('status') == 'COMPUTABLE':
            if set(saved.get('output_sha256', {})) != {'targets.csv', 'base.json', 'stress.json'}:
                raise DataBlocked('RESEARCH_CHECKPOINT_UNVERIFIED')
            if parameters.get('rank_buffer_multiple') is not None and set(saved.get('track_targets_sha256', {})) != {'base', 'stress'}:
                raise DataBlocked('RESEARCH_CHECKPOINT_UNVERIFIED')
            tracks = {name: json.loads((candidate / f'{name}.json').read_text(encoding='utf-8')) for name in ('base', 'stress')}
            if any(track.get('reconciliation', {}).get('status') != 'PASS' for track in tracks.values()):
                raise DataBlocked('RESEARCH_CHECKPOINT_UNVERIFIED')
            return {'manifest': saved, 'targets': pd.read_csv(candidate / 'targets.csv'), 'tracks': tracks, 'reused': True}
        if saved.get('status') != 'NOT_COMPUTABLE' or saved.get('output_sha256') != {}:
            raise DataBlocked('RESEARCH_CHECKPOINT_UNVERIFIED')
        return {'manifest': saved, 'reused': True}
    admitted = workflow.admit_research_run(parameters['strategy'], parameters, assumptions,
                                           code_paths, paths, config['data_end'], registry=registry, ledger=research_ledger)
    if output.exists():
        output = output / ('attempt_' + uuid.uuid4().hex)
    output.mkdir(parents=True, exist_ok=False)
    inputs = _read_inputs(paths, admitted)
    if inputs['bars'].get('price_basis') != 'RAW' or inputs['bars'].get('source') != 'twse_rwd':
        raise DataBlocked('ADJUSTED_OR_UNVERIFIED_PRICE_BASIS')
    all_bars = inputs['bars']['bars']
    if not all_bars or max(all_bars) > config['data_end']:
        raise DataBlocked('OOS_OR_INPUT_WINDOW_BLOCKED')
    start = BAR_START.get(evidence.get('kind'), '2023-01-03')
    bars = {day: rows for day, rows in all_bars.items() if day >= start}
    if not bars:
        raise DataBlocked('NO_DEVELOPMENT_BARS')
    for row in inputs['scores'] + inputs['universe']:
        if str(row['asof_date'])[:10] > config['data_end'] or str(row.get('execution_date', ''))[:10] > config['data_end']:
            raise DataBlocked('OOS_OR_INPUT_WINDOW_BLOCKED')
    action_data = inputs['actions']
    if 'unresolved_events' not in action_data:
        raise DataBlocked('UNRESOLVED_EVENT_COVERAGE_UNKNOWN')
    try:
        result = _research_tracks(bars=bars, scores=inputs['scores'], universe=inputs['universe'],
                                  actions=[event for event in action_data['actions'] if event['ex_date'] in bars],
                                  coverage=action_data['coverage'],
                                  unresolved_events=[event for event in action_data['unresolved_events'] if event['ex_date'] in bars],
                                  parameters=parameters, assumptions=assumptions)
    except DataBlocked as exc:
        if not str(exc).startswith(('ACTION_COVERAGE_UNKNOWN', 'OFFICIAL_MARK_MISSING', 'OVERLAPPING_STOCK_ENTITLEMENT',
                                    'FRACTIONAL_CLOSE_UNAVAILABLE', 'UNSUPPORTED_OR_INCOMPLETE_ACTION', 'BUFFER_EMPTY_DAY_EVIDENCE_MISSING')):
            raise
        result = {'status': 'NOT_COMPUTABLE', 'reason': str(exc), 'data_label': 'RESEARCH_UNCERTIFIED',
                  'label': 'DIAGNOSTIC_ONLY', 'research_only': True, 'terms_status': 'RESEARCH_ASSUMPTIONS',
                  'assumptions_sha256': assumptions_sha(assumptions)}
    if {path: workflow.sha256(path) for path in admitted['code_sha256']} != admitted['code_sha256']:
        raise DataBlocked('CODE_CHANGED_DURING_RUN')
    if {name: workflow.sha256(path) for name, path in paths.items()} != admitted['inputs_sha256']:
        raise DataBlocked('INPUT_CHANGED_DURING_RUN')
    manifest = {key: value for key, value in result.items() if key not in ('targets', 'tracks', 'track_targets')}
    manifest.update(experiment_id=admitted['experiment_id'], config=parameters, data_end=config['data_end'],
                    inputs_sha256=admitted['inputs_sha256'], code_sha256=admitted['code_sha256'],
                    research_trials=workflow.research_trials(research_ledger), assumptions_version=version,
                    # Archived Day3 directions/weights were fitted on the whole development window: in-sample, disclosed.
                    parameter_fit_scope='PER_DECISION_POINT_IN_TIME' if family == 'D5' else 'IN_SAMPLE_DEVELOPMENT_WINDOW')
    if result['status'] == 'COMPUTABLE':
        result['targets'].to_csv(output / 'targets.csv', index=False, encoding='utf-8-sig', mode='x')
        if 'track_targets' in result:
            for name, frame in result['track_targets'].items():
                frame.to_csv(output / f'targets_{name}.csv', index=False, encoding='utf-8-sig', mode='x')
        for name, track in result['tracks'].items():
            with (output / f'{name}.json').open('x', encoding='utf-8') as stream:
                json.dump(track, stream, ensure_ascii=False, indent=2, allow_nan=False)
        manifest['output_sha256'] = {name: workflow.sha256(output / name) for name in ('targets.csv', 'base.json', 'stress.json')}
        if 'track_targets' in result:
            manifest['track_targets_sha256'] = {name: workflow.sha256(output / f'targets_{name}.csv') for name in ('base', 'stress')}
    else:
        manifest['output_sha256'] = {}
    with (output / 'manifest.json').open('x', encoding='utf-8') as stream:
        json.dump(manifest, stream, ensure_ascii=False, indent=2, allow_nan=False)
    return {'manifest': manifest, **({'targets': result['targets'], 'tracks': result['tracks']} if result['status'] == 'COMPUTABLE' else {})}


def run(config, *, registry=workflow.REGISTRY, ledger=workflow.TRIAL_LEDGER, research_ledger=workflow.RESEARCH_MODE_LEDGER):
    """Admit before selection; write only a new diagnostic snapshot beneath OUTPUT_ROOT."""
    if operation_policy.current_mode() is not operation_policy.ExecutionMode.RESEARCH:
        raise PermissionError('RESEARCH_MODE_REQUIRED')
    label = config['data_label']
    if label == 'RESEARCH_UNCERTIFIED':
        return _run_research(config, registry=registry, research_ledger=research_ledger)
    if label not in ('SYNTHETIC', 'REAL_DATA'):
        raise ValueError('INVALID_DATA_LABEL')
    paths = {key: Path(value) for key, value in config['inputs'].items()}
    if not workflow.PIT_RAW_INPUTS <= paths.keys() or any(not path.is_file() for path in paths.values()):
        raise ValueError('PIT_RAW_INPUTS_INCOMPLETE')
    if label == 'REAL_DATA' and paths['evidence'].suffix.lower() != '.jsonl':
        raise ValueError('REAL_DATA_REQUIRES_EVIDENCE_LEDGER')
    output = Path(config['output']).resolve()
    if not output.is_relative_to(Path(OUTPUT_ROOT).resolve()) or output == Path(OUTPUT_ROOT).resolve():
        raise ValueError('OUTPUT_OUTSIDE_RESEARCH_DIRECTORY')
    if not isinstance(config['data_end'], str) or config['data_end'] > '2025-12-31':
        raise DataBlocked('OOS_OR_INPUT_WINDOW_BLOCKED')  # before the dry run, so no 2026 event metadata is even listed
    output.mkdir(parents=True, exist_ok=False)
    def dry_hashes():
        return {name: workflow.sha256(paths[name]) for name in ('evidence', 'universe', 'actions')}
    if label == 'REAL_DATA':
        # Dry run before admission: universe members bound every possible holding, so a blocked member blocks the run
        # without building targets, simulating or adding a trial to the deflated-Sharpe denominator.
        dry_inputs = dry_hashes()
        rows = read_evidence(paths['evidence'])
        members = {str(r['asset_id']) for r in json.loads(paths['universe'].read_text(encoding='utf-8')) if r.get('member')}
        actions = json.loads(paths['actions'].read_text(encoding='utf-8'))['actions']
        events = {(r['stock_id'], r['event_date']) for r in rows if r['field'] == 'ex_date' and r['stock_id'] in members
                  and r['event_date'] and r['event_date'] <= config['data_end']}
        events |= {(str(a['stock_id']), a['ex_date']) for a in actions if str(a['stock_id']) in members}
        verified = verified_sources()
        dry = held_event_terms(rows, members, events, verified)
        if dry_hashes() != dry_inputs:
            raise DataBlocked('INPUT_CHANGED_DURING_DRY_RUN')
        if dry['status'] != 'VERIFIED':
            blocked = {'status': 'BLOCKED', 'label': 'DIAGNOSTIC_ONLY', 'admission': 'NOT_ADMITTED', 'hypothesis_id': workflow.PIT_RAW_HYPOTHESIS,
                       'effective_trials': workflow.effective_trials(ledger), 'held_events': dry,
                       'reason': 'BLOCKED_UNVERIFIED_OFFICIAL_TERMS: dry run found unverified terms or event lists; no trial admitted'}
            with (output / 'blockers.json').open('x', encoding='utf-8') as stream:
                json.dump(blocked, stream, ensure_ascii=False, indent=2, allow_nan=False)
            return blocked
    parameters = {key: value for key, value in config.items() if key not in ('inputs', 'output', 'data_end')}
    if '_runtime' in parameters:
        raise ValueError('RUNTIME_IDENTITY_IS_AUTOMATIC')
    parameters['_runtime'] = {'python': sys.version, 'numpy': np.__version__, 'pandas': pd.__version__,
                              'requests': requests.__version__}
    # The registered identity includes every input file, including the action-coverage evidence.
    code_paths = sorted({p.resolve() for p in workflow.PIT_RAW_CODE} | workflow._loaded_local_code(), key=str)
    if label == 'REAL_DATA' and dry_hashes() != dry_inputs:
        raise DataBlocked('INPUT_CHANGED_BETWEEN_DRY_RUN_AND_ADMISSION')  # refused before any trial row is written
    admitted = workflow.admit_experiment(workflow.PIT_RAW_HYPOTHESIS, parameters, code_paths, paths,
                                         config['data_end'], registry=registry, ledger=ledger)
    if {path: workflow.sha256(path) for path in admitted['code_sha256']} != admitted['code_sha256']:
        raise DataBlocked('CODE_CHANGED_AFTER_ADMISSION')
    if label == 'REAL_DATA' and any(admitted['inputs_sha256'][name] != digest for name, digest in dry_inputs.items()):
        raise DataBlocked('INPUT_CHANGED_DURING_ADMISSION')  # the trial stays counted; the run stops
    inputs = _read_inputs(paths, admitted)
    market = inputs['bars']
    if (market.get('price_basis') != 'RAW' or 'd3' in str(market.get('source', '')).lower()
            or any(bar.get('adjusted') is not None and bar.get('adjusted') is not False
                   for day in market['bars'].values() for bar in day.values())):
        raise DataBlocked('ADJUSTED_OR_UNVERIFIED_PRICE_BASIS')
    bars = market['bars']
    if not bars or max(bars) > config['data_end'] or config['data_end'] > '2025-12-31':
        raise DataBlocked('OOS_OR_INPUT_WINDOW_BLOCKED')
    actions, coverage = inputs['actions']['actions'], inputs['actions']['coverage']
    targets = build_targets(pd.DataFrame(inputs['scores']), pd.DataFrame(inputs['universe']),
                            config_id=parameters['config_id'], top_n=parameters['top_n'], stock_weighting=parameters['stock_weighting'])
    tracks = {}
    for multiple, name in ((1, 'base'), (2, 'stress')):
        cost = CostModel(parameters['fee_rate'] * multiple, parameters['tax_rate'] * multiple,
                         parameters['minimum_fee'] * multiple)
        args = {'bars': bars, 'orders': [], 'actions': actions, 'action_coverage': coverage,
                'initial_cash': parameters['initial_cash'], 'cost': cost, 'slippage': parameters['slippage'] * multiple}
        result = simulate(**args, close_hook=_hook(targets, bars))
        check = reconcile(result=result, **{**args, 'orders': result['generated_orders']}, cancellations=result['cancellations'])
        if check['status'] != 'PASS':
            raise DataBlocked(f'RECONCILIATION_FAILED:{name}:{check}')
        tracks[name] = {'result': result, 'reconciliation': check}
    held_events = None
    if label == 'REAL_DATA':  # the same admission function as the dry run, now on events actually held at ex-date
        held = {(e['stock_id'], e['date']) for track in tracks.values() for e in track['result']['ledger']
                if e['kind'] == 'ACTION' and e['basis_shares'] > 0}
        held_events = held_event_terms(read_evidence(paths['evidence']), {s for s, _ in held}, held, verified)
    terms_status = ('NOT_APPLICABLE_SYNTHETIC' if label == 'SYNTHETIC'  # synthetic fixtures verify nothing
                    else 'VERIFIED' if held_events['status'] == 'VERIFIED' else 'BLOCKED_UNVERIFIED_OFFICIAL_TERMS')
    for track in tracks.values():
        if terms_status == 'BLOCKED_UNVERIFIED_OFFICIAL_TERMS':
            track['metrics'] = {'total_return': {'value': None, 'reason': 'BLOCKED_UNVERIFIED_OFFICIAL_TERMS'}}
            continue
        metrics = {key: asdict(metric) for key, metric in calculate_metrics([parameters['initial_cash']] + [d['nav'] for d in track['result']['daily']]).values.items()}
        for metric in ('win_rate', 'profit_factor', 'payoff_ratio', 'max_consecutive_losses', 'exposure',
                       'turnover', 'trade_count', 'avg_hold_days'):
            metrics[metric] = {'value': None, 'reason': 'closed trades and exposure not independently reconstructed'}
        track['metrics'] = metrics
    if {path: workflow.sha256(path) for path in admitted['code_sha256']} != admitted['code_sha256']:
        raise DataBlocked('CODE_CHANGED_DURING_RUN')
    if not workflow._loaded_local_code() <= {Path(path).resolve() for path in admitted['code_sha256']}:
        raise DataBlocked('LOCAL_MODULE_LOADED_AFTER_ADMISSION')
    if {name: workflow.sha256(path) for name, path in paths.items()} != admitted['inputs_sha256']:
        raise DataBlocked('INPUT_CHANGED_DURING_RUN')
    manifest = {'hypothesis_id': workflow.PIT_RAW_HYPOTHESIS, 'experiment_id': admitted['experiment_id'],
                'code_sha256': admitted['code_sha256'], 'config_sha256': admitted['config_sha256'],
                'config': parameters, 'data_end': config['data_end'], 'inputs_sha256': admitted['inputs_sha256'],
                'input_paths': {name: str(path.resolve()) for name, path in paths.items()}, 'data_label': label,
                'terms_status': terms_status, 'effective_trials': workflow.effective_trials(ledger),
                'held_events': None if held_events is None else {k: v for k, v in held_events.items() if k != 'events'}
                | {'blocked_events': [e for e in held_events['events'] if e['status'] == 'BLOCKED']}}
    with (output / 'manifest.json').open('x', encoding='utf-8') as stream:
        json.dump(manifest, stream, ensure_ascii=False, indent=2, allow_nan=False)
    targets.to_csv(output / 'targets.csv', index=False, encoding='utf-8-sig', mode='x')
    for name, track in tracks.items():
        with (output / f'{name}.json').open('x', encoding='utf-8') as stream:
            json.dump(track, stream, ensure_ascii=False, indent=2, allow_nan=False)
    return {'manifest': manifest, 'targets': targets, 'tracks': tracks}
