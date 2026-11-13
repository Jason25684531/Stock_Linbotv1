"""Build a conservative readiness view from existing V1 decisions."""


def build_readiness(catalog, plan, results, v2=None):
    """v2: {strategy_id: publishable} of the stored V2 baseline runs; None before the baseline exists (nothing is web-runnable)."""
    ids = [row['strategy_id'] for row in catalog]
    plans = {row['strategy_id']: row for row in plan}
    if len(ids) != 21 or len(set(ids)) != 21 or len(plans) != len(plan) or set(ids) != set(plans):
        raise ValueError('catalog and plan must contain the same 21 unique strategies')
    rows = []
    for item in catalog:
        sid = item['strategy_id']
        decision = plans[sid]
        result = results.get(sid)
        state = decision['state']
        reason = decision.get('reason')
        years = (result or {}).get('year_status', {})
        if not isinstance(years, dict):
            raise ValueError(f'invalid V1 year_status for {sid}')
        current = (result or {}).get('run') and result['run'].get('identity_status') == 'CURRENT'
        trusted_years = sorted(int(year) for year, status in years.items() if status == 'VERIFIED') if current else []
        runnable = state in ('RUN', 'REUSE')
        if runnable and not result:
            runnable, reason = False, 'V1_RESULT_MISSING'
        elif runnable and (result.get('run') or {}).get('identity_status') != 'CURRENT':
            runnable, reason = False, 'IDENTITY_STALE'
        elif runnable and not trusted_years:
            runnable, reason = False, 'NO_VERIFIED_TRACK'
        data_eligible, web = runnable, False
        if data_eligible:
            if v2 is None:
                reason = 'V2_WEB_NOT_READY'
            elif sid not in v2:
                reason = 'V2_BASELINE_MISSING'
            elif not v2[sid]:
                reason = 'V2_BASELINE_NOT_PUBLISHABLE'
            else:
                web, reason = True, None
        rows.append({'strategy_id': sid, 'family': item['family'], 'state': state,
                     'v1_status': result.get('status') if result else None,
                     'backtest_window': decision.get('window'), 'year_status': years,
                     'trusted_years': trusted_years, 'data_eligible': data_eligible,
                     'web_runnable': web, 'reason': reason})
    return rows
