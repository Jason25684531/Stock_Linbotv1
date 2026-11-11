"""Strategy data requirements and the per-strategy, per-year data trust (design D4, D5). Pure: rule inputs are passed in, because building a
strategy instance needs core.strategies, which core.research may not import (scripts/historical_foundation.py does it).
"""
from __future__ import annotations

import pandas as pd

from core.research.history import categories as cats
from core.research.native_frame import LOOKBACK_DAYS

REQUIRED = 'REQUIRED'
WARMUP_DAYS = {'tquant': 550, 'native': LOOKBACK_DAYS + 10}  # _tquant.load_history window; native7 lookback 160
BACKTESTABLE = ('VERIFIED', 'PARTIAL')
MIN_VERIFIED_YEARS = 3
# A PARTIAL year is backtestable only if every required category that carries input values has at least this share of its rows; otherwise
# the engine would read absent data as "no signal". Dating categories (publication times) are not value gaps: undated rows fall back to the
# statutory deadline, which is later, never earlier.
VALUE_CATEGORIES = ('calendar', 'prices', 't86_flows', 'margin', 'financial_statements', 'monthly_revenue')
MIN_VALUE_COVERAGE = 0.95
GOVERNED_NEEDS = {'fundamental': ('financial_statements', 'financial_publication_time'), 'research': ('financial_statements', 'financial_publication_time')}


def requirement(cap, rule_inputs: frozenset) -> dict:
    """One strategy_data_requirements row: REQUIRED / NOT_REQUIRED / COMPARISON_ONLY per category, with the reason it is required."""
    extra = set(GOVERNED_NEEDS.get(cap.family, ()))
    needs, why = {}, {}
    for c in cats.CATEGORIES:
        used = sorted(c.inputs & set(rule_inputs))
        if c.role == 'COMPARISON':
            needs[c.id] = cats.COMPARISON_ONLY
        elif c.priced or used or c.id in extra:
            needs[c.id] = REQUIRED
            why[c.id] = 'priced' if c.priced else f"rule_inputs:{','.join(used)}" if used else f'catalog:{cap.family}'
        else:
            needs[c.id] = cats.NOT_REQUIRED
    return {'strategy_id': cap.strategy_id, 'family': cap.family, 'engine': cap.engine, 'engine_supported': cap.engine is not None,
            'governance': cap.governance, 'static_block': cap.static_block, 'rule_inputs': sorted(rule_inputs),
            'warmup_calendar_days': WARMUP_DAYS.get(cap.family, 0), 'needs': needs, 'why': why}


def cell(req: dict, year: int, coverage: dict) -> dict:
    """Trust of one strategy in one year: the worst required SIGNAL status over the year and the years its warm-up touches."""
    first = (pd.Timestamp(year, 1, 1) - pd.Timedelta(days=req['warmup_calendar_days'])).year
    touched = list(range(max(first, cats.YEARS[0]), year + 1))
    by_category, contribution, reasons, thin = {}, {}, [], []
    for cid, need in req['needs'].items():
        if need != REQUIRED:
            by_category[cid] = need
            continue
        entry = coverage[(year, cid)]
        if cid == 'calendar' and entry.get('expected') and entry.get('present') is not None and entry['present'] < entry['expected']:
            # the engine trades only on sessions it has bars for: a missing session in the traded year is a day without fills or marks
            thin.append(f"MISSING_SESSIONS_IN_YEAR:calendar:{entry['present']}/{entry['expected']}")
        if cid in VALUE_CATEGORIES:
            for y in touched:
                entry = coverage[(y, cid)]
                if entry.get('expected') and entry.get('present') is not None and entry['present'] / entry['expected'] < MIN_VALUE_COVERAGE:
                    thin.append(f"VALUE_COVERAGE_BELOW_{MIN_VALUE_COVERAGE}:{cid}{'' if y == year else f'@warmup{y}'}:{entry['present']}/{entry['expected']}")
        worst_year = max(touched, key=lambda y: cats.STATUSES.index(coverage[(y, cid)]['status']))
        status = coverage[(worst_year, cid)]['status']
        by_category[cid] = status
        soft = cats.BY_ID[cid].soft and status not in ('VERIFIED', 'FAILED_VALIDATION')
        contribution[cid] = 'PARTIAL' if soft else status
        if status != 'VERIFIED':
            where = '' if worst_year == year else f'@warmup{worst_year}'
            reasons.append(f'{cid}{where}:{status}:' + ';'.join(coverage[(worst_year, cid)]['reasons'][:2]))
    if first < cats.YEARS[0]:
        contribution['warmup'] = 'PARTIAL'
        reasons.append('WARMUP_BEFORE_SNAPSHOT')
    status = cats.worst(contribution.values())
    binding = sorted(k for k, v in contribution.items() if v == status and status != 'VERIFIED')
    # values from a later snapshot are dated by the first publication: without verified amendment history they may be seen too early
    if req['needs'].get('financial_publication_time') == REQUIRED and by_category.get('financial_publication_time') != 'VERIFIED':
        thin.append('PIT_VERSION_UNVERIFIED:financial_publication_time')
    reasons += thin
    if req['governance']:
        status, reasons = 'GOVERNED_ONLY', [req['governance']] + reasons
    backtestable = bool(req['engine_supported'] and not req['static_block'] and not req['governance'] and status in BACKTESTABLE and not thin)
    return {'strategy_id': req['strategy_id'], 'year': year, 'status': status, 'binding': binding, 'reasons': reasons,
            'by_category': by_category, 'warmup_years': touched[:-1], 'backtestable': backtestable}


def verified_span(cells) -> tuple[int, int] | None:
    """Longest run of consecutive VERIFIED years, if at least MIN_VERIFIED_YEARS long (earliest run on a tie)."""
    best, run = None, []
    for c in sorted(cells, key=lambda c: c['year']):
        run = run + [c['year']] if c['status'] == 'VERIFIED' else []
        if len(run) >= MIN_VERIFIED_YEARS and (best is None or len(run) > best[1] - best[0] + 1):
            best = (run[0], run[-1])
    return best


def first_backtestable_year(cells) -> int | None:
    """Earliest year from which every later year is backtestable (one continuous reference window)."""
    years = sorted(cells, key=lambda c: c['year'])
    for i, c in enumerate(years):
        if all(x['backtestable'] for x in years[i:]):
            return c['year']
    return None


def matrix(requirements: list[dict], coverage: dict) -> dict[str, list[dict]]:
    return {req['strategy_id']: [cell(req, y, coverage) for y in cats.YEARS] for req in requirements}
