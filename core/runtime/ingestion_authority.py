"""Routine market-data ingestion authority (change 2026-11-09-market-data-autonomy-and-reference-research-v1, design D1).

One trusted Owner grant (`operation: market_data_ingestion`, outside the repo, see task_controller.grant_trusted) authorizes reading
the six daily public sources and committing the three designated market tables, in RESEARCH or PRODUCTION alike. It never consults
the research budget, the official_fetch grant or the execution mode, and it is bound to the written Owner decision by SHA256: an edit
of decision.md withdraws the authority (DECISION_RECORD_CHANGED). Without a trusted grant the built-in routine policy (decision.md D7)
applies the same scope with a daily HTTP cap; the kill switch turns it off. Read-only: nothing here writes a file.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
from datetime import datetime
from pathlib import Path

from scripts import task_controller as tc

OPERATION = 'market_data_ingestion'
SOURCE_IDS = {'MI_INDEX': 'TWSE:MI_INDEX', 'T86': 'TWSE:T86', 'MI_MARGN': 'TWSE:MI_MARGN', 'TWT49U': 'TWSE:TWT49U',
              't163sb04': 'MOPS:t163sb04', 'monthly_revenue': 'MOPS:t21sc03', 'holidaySchedule': 'TWSE:holidaySchedule'}
CALENDAR_JOBS = ('calendar_2026', 'calendar_2027')  # the annual schedule is the one research-job context the built-in policy reads
TABLES = ('daily_market_data', 'monthly_revenue', 'financial_statements')
SCOPES = ('TWSE_ONLY', 'ALL')
MIN_INTERVAL = 3.2
DECISION_REF = 'openspec/changes/2026-11-09-market-data-autonomy-and-reference-research-v1/decision.md'  # the only binding record
DAILY_CAP = 400
BUILTIN_POLICY = {'builtin': True, 'sources': sorted(SOURCE_IDS.values()), 'tables': list(TABLES), 'market_scope': 'TWSE_ONLY',
                  'datasets': ['prices_volume', 'financials', 'monthly_revenue', 'corporate_actions', 'benchmark', 'calendar'],
                  'operations': ['read', 'commit'], 'rate_limit_seconds': MIN_INTERVAL, 'max_uses': DAILY_CAP}


def after_floor(context) -> bool:
    """The requested period lies after the commit floor (2025-12-31): routine reads never fetch the research window, whose replies
    would land in the shared cache outside the research budget (review 2026-11-09)."""
    params = dict(context.parameters)
    days = [str(params[k]) for k in ('date', 'startDate', 'endDate') if params.get(k)]
    if days:
        return all(day > '20251231' for day in days)
    if context.dataset == 'holidaySchedule':
        return str(params.get('year', '')) >= '2026'
    roc = params.get('year') if context.dataset == 't163sb04' else (re.search(r't21sc03_(\d+)_', context.url) or [None, None])[1]
    return str(roc or '').isdigit() and int(roc) >= 115


MACHINE_ROOT = tc.ROOT / 'outputs' / 'market_data_updates'  # the switch and the cap hold whatever STOCK_LINBOT_UPDATE_ROOT says


def _roots() -> list:
    from core.runtime import public_data
    return list(dict.fromkeys([Path(MACHINE_ROOT), public_data.ingestion_audit_path().parent]))


def disabled() -> bool:
    """Kill switch for the built-in policy: env STOCK_LINBOT_INGESTION_DISABLED=1, or an INGESTION_DISABLED file in the machine's
    update folder or in the current update root."""
    return os.environ.get('STOCK_LINBOT_INGESTION_DISABLED') == '1' or any((root / 'INGESTION_DISABLED').exists() for root in _roots())


def _builtin_used_today() -> int:
    """HTTP attempts reserved today (local date) in the machine audit, which every root also writes. A line that does not parse (a
    write cut short) counts as a use: the cap errs on the side of fewer requests, never raises."""
    today = datetime.now().astimezone().date().isoformat()
    used = 0
    for root in [Path(MACHINE_ROOT)]:
        try:
            lines = (root / 'ingestion_audit.jsonl').read_text(encoding='utf-8').splitlines()
        except FileNotFoundError:
            continue
        for line in filter(str.strip, lines):
            try:
                row = json.loads(line)
            except ValueError:
                used += 1
                continue
            used += row.get('result') == 'RESERVED' and str(row.get('at', '')).startswith(today)
    return used


def effective_scope(requested, grant) -> str:
    """The narrowest of the requested scope and the grant's scope; nothing widens a scope."""
    return 'TWSE_ONLY' if 'TWSE_ONLY' in (requested, (grant or {}).get('market_scope')) else 'ALL'


def _invalid(grant) -> str | None:
    def strings(key):
        value = grant.get(key)
        return isinstance(value, list) and value and all(isinstance(v, str) for v in value)
    for key in ('sources', 'datasets', 'tables', 'operations'):
        if not strings(key):
            return key
    if not set(grant['tables']) <= set(TABLES):
        return 'tables'
    if not set(grant['operations']) <= {'read', 'commit'}:
        return 'operations'
    if grant.get('market_scope') not in SCOPES:
        return 'market_scope'
    rate = grant.get('rate_limit_seconds')
    if isinstance(rate, bool) or not isinstance(rate, (int, float)) or rate < MIN_INTERVAL:
        return 'rate_limit_seconds'
    uses = grant.get('max_uses')
    if isinstance(uses, bool) or not isinstance(uses, int) or uses < 1:
        return 'max_uses'
    for key in ('decision_ref', 'decision_sha256', 'expires_at'):
        if not isinstance(grant.get(key), str) or not grant[key]:
            return key
    try:
        datetime.fromisoformat(grant['expires_at'])
    except ValueError:
        return 'expires_at'
    return None


def _resolve(ref) -> Path:
    path = Path(ref)
    return (path if path.is_absolute() else tc.ROOT / path).resolve()


def _decision_matches(grant) -> bool:
    path = _resolve(grant['decision_ref'])
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest() == grant['decision_sha256']
    except OSError:
        return False


def evaluate(*, operation, context=None, dataset=None, table=None, market_scope='TWSE_ONLY', trusted=None, grants_path=None,
             uses_root=None) -> tuple[dict | None, str]:
    """(grant, '') when the trusted grant covers this read (context + dataset) or commit (table); else (None, fixed reason).
    `trusted`, `grants_path` and `uses_root` exist for tests only; operation_policy and public_data never pass them."""
    path = grants_path or tc.GRANTS
    grant = tc.grant_for(OPERATION, path) if (tc.grant_trusted(path) if trusted is None else trusted) else None
    if grant is None:  # decision.md D7: no trusted grant, the built-in routine policy with the same scope
        if disabled():
            return None, 'INGESTION_DISABLED'
        grant = BUILTIN_POLICY
    else:
        field = _invalid(grant)
        if field:
            return None, f'INGESTION_GRANT_INVALID:{field}'
        if _resolve(grant['decision_ref']) != _resolve(DECISION_REF):
            return None, 'INGESTION_GRANT_INVALID:decision_ref'  # a hash of any other file binds nothing
        if not _decision_matches(grant):
            return None, 'DECISION_RECORD_CHANGED'
    if operation not in grant['operations']:
        return None, f'INGESTION_OPERATION_NOT_GRANTED:{operation}'
    if operation == 'read':
        source = source_id(context)
        if source is None:  # research-plan windows (a job) and every unmapped endpoint stay with the research budget
            return None, f'INGESTION_SOURCE_NOT_GRANTED:{getattr(context, "dataset", None)}'
        if source not in grant['sources']:
            return None, f'INGESTION_SOURCE_NOT_GRANTED:{source}'
        if dataset not in grant['datasets']:
            return None, f'INGESTION_DATASET_NOT_GRANTED:{dataset}'
        if not after_floor(context):
            return None, f'INGESTION_PERIOD_BEFORE_FLOOR:{context.dataset}'
        if effective_scope(market_scope, grant) == 'TWSE_ONLY' and _tpex(context):
            return None, 'INGESTION_SCOPE_EXCEEDED:TWSE_ONLY'  # enforced on the request itself, not only by the caller's endpoint list
    elif operation == 'commit':
        if table not in TABLES or table not in grant['tables']:
            return None, f'INGESTION_TABLE_NOT_GRANTED:{table}'
    else:
        return None, f'INGESTION_OPERATION_NOT_GRANTED:{operation}'
    if market_scope not in SCOPES or effective_scope(market_scope, grant) != market_scope:
        return None, f'INGESTION_SCOPE_EXCEEDED:{market_scope}'
    if grant.get('builtin'):
        used = _builtin_used_today() if operation == 'read' else 0  # the cap counts HTTP attempts, not commits
        return (None, f'INGESTION_QUOTA_EXHAUSTED:{used}/{DAILY_CAP}') if used + 1 > DAILY_CAP else (grant, '')
    try:
        used = tc.grant_uses(grant, uses_root or tc.STATE)
    except ValueError:
        return None, 'INGESTION_GRANT_INVALID:usage_log'
    if used + 1 > grant['max_uses']:
        return None, f"INGESTION_QUOTA_EXHAUSTED:{used}/{grant['max_uses']}"
    return grant, ''


def _tpex(context) -> bool:
    """An over-the-counter (TPEx) form of a MOPS endpoint: /otc/ revenue pages or TYPEK=otc statements."""
    return '/otc/' in context.url or dict(context.parameters).get('TYPEK') == 'otc'


def source_id(context) -> str | None:
    if context is None or (context.job is not None and context.job not in CALENDAR_JOBS):
        return None
    return SOURCE_IDS.get(context.dataset)


def describe(grants_path=None) -> dict:
    """Read-only status for /admin/data and verify_production_grant.py."""
    path = grants_path or tc.GRANTS
    trusted = tc.grant_trusted(path)
    grant = tc.grant_for(OPERATION, path)
    info = {'grants_path': str(path), 'trusted': trusted, 'present': grant is not None}
    if grant is None or not trusted:
        return {**info, 'mode': 'BUILTIN', 'reason': 'INGESTION_DISABLED' if disabled() else '', 'uses': _builtin_used_today(),
                **{key: BUILTIN_POLICY[key] for key in ('sources', 'datasets', 'tables', 'market_scope', 'operations', 'max_uses',
                                                        'rate_limit_seconds')}}
    field = _invalid(grant)
    try:
        used = tc.grant_uses(grant)
    except ValueError:
        used = None
    reason = (f'INGESTION_GRANT_INVALID:{field}' if field else 'DECISION_RECORD_CHANGED' if not _decision_matches(grant) else '')
    return {**info, 'mode': 'TRUSTED_GRANT', 'reason': reason, 'grant_id': tc.grant_id(grant), 'uses': used,
            **{key: grant.get(key) for key in ('sources', 'datasets', 'tables', 'market_scope', 'operations', 'max_uses', 'expires_at',
                                               'rate_limit_seconds', 'decision_ref')}}
