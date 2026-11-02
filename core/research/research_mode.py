"""Registered assumptions for development-only, uncertified research."""
import hashlib
import json


RESEARCH_ASSUMPTIONS_V1 = {
    'settlement': 'Unknown cash payment and stock credit dates settle on the last development session; receivables remain in NAV at face value.',
    'fractional_stock': 'Drop fractional stock entitlements and record their estimated close-price value as unmodeled.',
    'exclusions': 'Exclude unsplittable and rights events, reference-price changes, delistings and suspensions over five sessions from ex-date minus maximum rebalance interval through development end.',
    'publication': 'When publication time is unknown, use ex-date as the research-only known date.',
    'price_limits': 'Compute limit prices from prior close using the 10 percent rule and label COMPUTED.',
}

RESEARCH_ASSUMPTIONS_V2 = {
    **RESEARCH_ASSUMPTIONS_V1,
    'exclusions': 'Events with unknown announcement time are available after the event-day close and excluded only at the next legal decision; never exclude beforehand.',
    'publication': 'Unknown publication time becomes available after the event-day close, for research only.',
    'unresolved_held': 'Stop the configuration if a held stock has an event with insufficient terms; record NOT_COMPUTABLE, event evidence, and selection bias without screening truncated performance.',
}

RESEARCH_ASSUMPTIONS_V3 = {
    **RESEARCH_ASSUMPTIONS_V2,
    'listing_age_proxy': 'Use first observed official trading session as a conservative listing-age proxy; do not infer historical membership from a future current-listed snapshot.',
    'empty_rebalance': 'When no stock has both eligible membership and a finite score at a scheduled rebalance, issue no orders and retain the existing portfolio.',
}

# Terms whose absence changes the share count or ends trading: a held stock cannot be carried through them.
STOP_TERMS = frozenset({'share_count_change', 'settlement_terms', 'delisting_settlement_terms', 'suspension_or_delisting_status'})

RESEARCH_ASSUMPTIONS_V4 = {
    **RESEARCH_ASSUMPTIONS_V3,
    'unresolved_held': 'A held stock forfeits dividend or rights entitlements whose split or terms are unknown: shares unchanged, no cash, the official pre-close minus reference value logged as forfeited. The configuration stops with NOT_COMPUTABLE only when a held stock has a share-count change, delisting or suspension beyond five sessions. Forfeiture biases returns downward; disclose affected events and value.',
}

RESEARCH_ASSUMPTIONS_V5 = {
    **RESEARCH_ASSUMPTIONS_V4,
    'reference_change_held': 'A held stock whose capital reduction or par-value change has unknown share and settlement terms is exited on the resumption session at the official pre-suspension close with full sell costs (FORCED_EXIT); staleness during the announced suspension is tolerated until that session. Delistings still stop the configuration. The exit forgoes post-resumption moves and is disclosed per event.',
}

RESEARCH_ASSUMPTIONS_V6 = {
    **RESEARCH_ASSUMPTIONS_V5,
    'overlapping_entitlement': 'When a held stock has a further corporate action while earlier stock entitlements are still receivable, the new entitlement basis is the credited holding only; uncredited shares receive nothing and a forced exit sells them at the same official price. The uncredited count is logged on the action. Returns are understated.',
}

D5_RESEARCH_STRATEGY = 'D5_RESEARCH_PIT_V1'
D5_RESEARCH_ASSUMPTIONS_V1 = {
    **RESEARCH_ASSUMPTIONS_V2,
    'd5_variant': 'Rebuild candidate selection, correlations and weights from information available at each decision; never use the 2026-derived D4 handoff.',
    'original_d5': 'BLOCKED_OOS_LEAKAGE; the research variant is a distinct strategy.',
}
D5_RESEARCH_ASSUMPTIONS_V2 = {**RESEARCH_ASSUMPTIONS_V3,
    'd5_variant': D5_RESEARCH_ASSUMPTIONS_V1['d5_variant'], 'original_d5': D5_RESEARCH_ASSUMPTIONS_V1['original_d5']}
D5_RESEARCH_ASSUMPTIONS_V3 = {**RESEARCH_ASSUMPTIONS_V4,
    'd5_variant': D5_RESEARCH_ASSUMPTIONS_V1['d5_variant'], 'original_d5': D5_RESEARCH_ASSUMPTIONS_V1['original_d5']}
D5_RESEARCH_ASSUMPTIONS_V4 = {**RESEARCH_ASSUMPTIONS_V5,
    'd5_variant': D5_RESEARCH_ASSUMPTIONS_V1['d5_variant'], 'original_d5': D5_RESEARCH_ASSUMPTIONS_V1['original_d5']}
D5_RESEARCH_ASSUMPTIONS_V5 = {**RESEARCH_ASSUMPTIONS_V6,
    'd5_variant': D5_RESEARCH_ASSUMPTIONS_V1['d5_variant'], 'original_d5': D5_RESEARCH_ASSUMPTIONS_V1['original_d5']}

REGISTERED_ASSUMPTIONS = {'V1': RESEARCH_ASSUMPTIONS_V1, 'V2': RESEARCH_ASSUMPTIONS_V2, 'V3': RESEARCH_ASSUMPTIONS_V3,
                          'V4': RESEARCH_ASSUMPTIONS_V4, 'V5': RESEARCH_ASSUMPTIONS_V5, 'V6': RESEARCH_ASSUMPTIONS_V6,
                          'D5-V1': D5_RESEARCH_ASSUMPTIONS_V1, 'D5-V2': D5_RESEARCH_ASSUMPTIONS_V2, 'D5-V3': D5_RESEARCH_ASSUMPTIONS_V3,
                          'D5-V4': D5_RESEARCH_ASSUMPTIONS_V4, 'D5-V5': D5_RESEARCH_ASSUMPTIONS_V5}
# Versions a runner may execute today: the latest Day3 and D5 identities plus the superseded ones kept replayable.
RUNNABLE_VERSIONS = {'DAY3': ('V3', 'V4', 'V5', 'V6'), 'D5': ('D5-V2', 'D5-V3', 'D5-V4', 'D5-V5')}
# The Day3 base identity each D5 identity shares its prepared inputs with.
INPUT_VERSION_OF = {'V3': 'V3', 'V4': 'V4', 'V5': 'V5', 'V6': 'V6', 'D5-V2': 'V3', 'D5-V3': 'V4', 'D5-V4': 'V5', 'D5-V5': 'V6'}
CURRENT_DAY3_ASSUMPTIONS, CURRENT_D5_ASSUMPTIONS = RESEARCH_ASSUMPTIONS_V6, D5_RESEARCH_ASSUMPTIONS_V5


def reference_change_policy(assumptions):
    """STOP for every held reference change (V1–V4), FORCED_EXIT from V5 on."""
    return 'FORCED_EXIT' if 'reference_change_held' in assumptions else 'STOP'


def overlap_policy(assumptions):
    """STOP when a new action meets uncredited entitlements (V1–V5), HOLDINGS_ONLY from V6 on."""
    return 'HOLDINGS_ONLY' if 'overlapping_entitlement' in assumptions else 'STOP'


def registered_version(sha):
    """Resolve a registered assumptions SHA to its version name, or None."""
    return next((name for name, body in REGISTERED_ASSUMPTIONS.items() if assumptions_sha(body) == sha), None)


def held_event_policy(assumptions):
    """STOP for every unknown held event (V1–V3), FORFEIT_UNKNOWN_ENTITLEMENTS from V4 on."""
    return 'FORFEIT_UNKNOWN_ENTITLEMENTS' if assumptions.get('unresolved_held', '').startswith('A held stock forfeits') else 'STOP'


def assumptions_sha(assumptions=RESEARCH_ASSUMPTIONS_V3):
    encoded = json.dumps(assumptions, sort_keys=True, ensure_ascii=False, separators=(',', ':')).encode('utf-8')
    return hashlib.sha256(encoded).hexdigest()


def screen_registration():
    return {
        'hypothesis_id': 'RESEARCH-SCREEN:V1',
        'strategy': 'ALL',
        'hypothesis': 'Diagnostic candidates meet all five preregistered OD-24 screening criteria under research assumptions.',
        'falsification_test': 'Fail if net or excess 2x Sharpe is nonpositive, bootstrap lower bound is nonpositive, DSR is at most 0.95 using max(research trials, 743), stock or day profit concentration is at least 50%, or both regimes are negative or either has fewer than 60 sessions.',
        'data_window': '2023-01-03 to 2025-12-31',
        'trial_budget': '12 (registry metadata only; research ledger counts every admitted configuration)',
        'source': 'dual-track-research-mode / RESEARCH-SCREEN:V1',
    }


def assumptions_registration(version='V1'):
    assumptions = REGISTERED_ASSUMPTIONS[version]
    return {
        'hypothesis_id': 'RESEARCH-ASSUMPTIONS:' + version, 'strategy': 'ALL',
        'hypothesis': json.dumps(assumptions, sort_keys=True, ensure_ascii=False, separators=(',', ':')),
        'falsification_test': 'Any changed assumption requires a new registered identity.',
        'data_window': '2023-01-03 to 2025-12-31', 'trial_budget': '1',
        'source': 'assumptions_sha256:' + assumptions_sha(assumptions),
    }


def event_excluded(signal_at, event_date):
    """An unknown-announcement event may affect the first decision strictly after close."""
    from datetime import date, datetime, time
    from zoneinfo import ZoneInfo
    signal = datetime.fromisoformat(signal_at)
    if signal.tzinfo is None:
        raise ValueError('NAIVE_SIGNAL_TIME')
    close = datetime.combine(date.fromisoformat(event_date), time(13, 30), ZoneInfo('Asia/Taipei'))
    return signal > close


def apply_event_exclusions(universe, signals, events):
    """Apply only event evidence available by each signal; leave earlier memberships unchanged."""
    from datetime import datetime, time
    from zoneinfo import ZoneInfo
    from core.research.repair_accounting import _timestamp
    result = universe.copy()
    by_stock = {}
    for event in events:
        by_stock.setdefault(str(event['stock_id']), []).append(event)
    for index, row in result.iterrows():
        signal = signals[(row['asof_date'], row['asset_id'])]
        known = [event for event in by_stock.get(str(row['asset_id']), ()) if event_excluded(signal, event.get('known_date', event['ex_date']))]
        if known:
            event = max(known, key=lambda e: e.get('known_date', e['ex_date']))
            close = datetime.combine(datetime.fromisoformat(event.get('known_date', event['ex_date'])).date(), time(13, 30), ZoneInfo('Asia/Taipei')).isoformat()
            if _timestamp(row['membership_available_at']) < _timestamp(close):
                result.at[index, 'membership_available_at'] = close
                result.at[index, 'membership_source_sha256'] = event['source_sha256']
            result.at[index, 'member'] = False
    return result
