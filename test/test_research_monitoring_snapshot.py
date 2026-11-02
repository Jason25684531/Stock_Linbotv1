from datetime import date

import pandas as pd

from scripts import research_acceptance as acceptance
from scripts import research_monitoring_snapshot as snapshot


def _days(*items):
    return {pd.Timestamp(day): (trading, None, None) for day, trading in items}


def test_unimported_schedule_stops_the_calendar_and_keeps_staleness_unknown():
    days = _days(('2026-10-21', True), ('2026-10-22', True), ('2026-10-23', False))
    built = snapshot.build(days, {pd.Timestamp('2026-10-26'): {}}, frozenset(), date(2026, 10, 30), [])
    market = built['daily_market']
    assert market['latest_official_session'] == '2026-10-22'
    assert market['calendar_complete_through'] == '2026-10-26' and market['calendar_blocker'] == 'ANNUAL_SCHEDULE_NOT_IMPORTED'
    assert market['official_sessions'] == ['2026-10-21', '2026-10-22']
    result = acceptance.monitoring(built)
    assert any(row['reason'] == 'OFFICIAL_CALENDAR_OR_CACHE_DATE_UNAVAILABLE' for row in result['unknown'])
    assert built['degradation_status'] == 'NOT_EVALUABLE' and built['degradation_reason'] == 'NO_VERIFIED_STRATEGY'
    assert result['states'] == [{'scope': 'degradation', 'status': 'NOT_EVALUABLE', 'reason': 'NO_VERIFIED_STRATEGY'}]
    assert not any(row.get('scope') == 'rolling_metrics' for row in result['unknown'])


def test_imported_schedule_counts_scheduled_sessions_and_flags_stale():
    days = _days(('2026-10-21', True), ('2026-10-22', True))
    built = snapshot.build(days, {}, frozenset({2026}), date(2026, 10, 29))
    assert built['daily_market']['official_sessions'] == ['2026-10-21', '2026-10-22', '2026-10-23', '2026-10-26', '2026-10-27', '2026-10-28', '2026-10-29']
    flags = acceptance.monitoring(built, threshold=3)['flags']
    assert [row['flag'] for row in flags] == ['STALE'] and flags[0]['sessions'] == 5


def test_stale_threshold_is_three_trading_days_and_blocked_strategies_never_degrade():
    days = _days(('2026-10-21', True), ('2026-10-22', True))
    at_threshold = snapshot.build(days, {}, frozenset({2026}), date(2026, 10, 27), [{'strategy': 'S', 'status': 'DATA_BLOCKED'}])
    assert acceptance.monitoring(at_threshold, threshold=3)['flags'] == []  # 10-23, 10-26, 10-27
    one_more = snapshot.build(days, {}, frozenset({2026}), date(2026, 10, 28))
    assert [row['flag'] for row in acceptance.monitoring(one_more, threshold=3)['flags']] == ['STALE']
    assert at_threshold['degradation_status'] == 'NOT_EVALUABLE'
    verified = snapshot.degradation([{'strategy': 'S', 'status': 'OOS_PASSED'}])
    assert verified == {'degradation_status': 'NOT_EVALUABLE', 'degradation_reason': 'NO_REGISTERED_THRESHOLDS', 'verified_strategies': ['S']}
    assert acceptance.monitoring({**at_threshold, **verified})['unknown'] == [{'scope': 'degradation', 'reason': 'NO_REGISTERED_THRESHOLDS'}]
    missing = snapshot.degradation(None)
    assert missing['degradation_reason'] == 'STATUS_SNAPSHOT_MISSING'
    assert acceptance.monitoring({**at_threshold, **missing})['unknown'] == [{'scope': 'degradation', 'reason': 'STATUS_SNAPSHOT_MISSING'}]
