"""Strategy Validation Gap Report and Data Gap Recovery Agent (design D8).

scan() states, per strategy, every field and period that keeps it from a trustworthy result, whether a legal public source exists, the
point-in-time / corporate-action / benchmark / calendar issue, engine support, the next repair action and whether a formal TRIAL would be
needed. recover() tries legal local verified data, official history through existing authorization paths only, then registered public
alternatives, at most three rounds, and never estimates, synthesizes or substitutes a different field. Nothing here writes outside
outputs/reference_mode, opens 2026, adds a TRIAL or touches the ledger or registry.

CLI: python -m core.research.reference.gaps   (writes gap_report.json / .md next to the latest batch report)
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
PERIOD = ['2023-01-03', '2025-12-31']
MAX_ROUNDS = 3
ALTERNATIVE_SOURCES: dict = {}  # registered public alternatives per field; none is registered today
UNRECOVERED = {'PIT': 'PIT_UNVERIFIABLE', 'CORPORATE_ACTIONS': 'ACTIONS_UNKNOWN', 'DATA': 'DATA_BLOCKED', 'BENCHMARK': 'SOURCE_UNAVAILABLE',
               'UNIVERSE': 'SOURCE_UNAVAILABLE', 'CALENDAR': 'SOURCE_UNAVAILABLE'}

# One table of known gaps: field -> (issue, applies to, legal public source, local evidence path, official route, next step)
GAPS = {
    'monthly_revenue.announcement_time': ('PIT', {'growth_momentum_breakout', 'quality_growth'}, 'MOPS t21sc03 pages carry no announcement time',
                                          None, ('research_budget', 'monthly_revenue'),
                                          'register a source with first-publication timestamps (e.g. MOPS daily announcements) and a budget plan'),
    'financial_statements.publication_date': ('PIT', 'native', 'MOPS t57sb01 publication pages (allow-listed)',
                                              'core/research/native_frame.py:PIT_RECORDS', ('research_budget', 't57sb01'),
                                              'Owner assigns a t57sb01 budget plan for the filings without a MOPS publication date'),
    'corporate_actions.splits_capital_reductions': ('CORPORATE_ACTIONS', 'all_priced', 'TWSE TWT49U covers ex-rights/dividends only',
                                                    'outputs/research_mode/prepared_v6/actions.json', ('research_budget', 'TWT49U'),
                                                    'register an official split / capital-reduction source; until then valuation stays partially verified'),
    'benchmark.total_return_index': ('BENCHMARK', 'all_priced', 'TWSE MFI94U total-return index (not allow-listed)', 'data/benchmark/MFI94U',
                                     ('allow_list', 'MFI94U'), 'add MFI94U to the public allow-list by an Owner decision and cross-verify'),
    'universe.tpex_and_delisted': ('UNIVERSE', 'all_priced', 'TPEx is a restricted source (403 observed, not bypassed)',
                                   'outputs/native7_recovery/2026-10-22', ('restricted', 'tpex'),
                                   'separate TPEx authorization and certification (decision.md D2); survivorship from delistings.csv only'),
    'prices.adjustment_for_tquant_indicators': ('CORPORATE_ACTIONS', 'tquant', 'official bars are unadjusted closes',
                                                'outputs/research_mode/prepared_v6/actions.json', ('research_budget', 'TWT49U'),
                                                'derive adjustment factors only from verified actions; indicators across ex-dates stay unadjusted'),
}


def _applies(scope, cap) -> bool:
    return (cap.strategy_id in scope if isinstance(scope, set) else scope == cap.family or
            (scope == 'all_priced' and cap.family in ('native', 'tquant')))


def scan(caps, batch=None) -> dict:
    """Gap record per strategy (21). batch: the reference report, for engine support and warnings."""
    rows = {row['strategy_id']: row for row in (batch or {}).get('rows', [])}
    out = {}
    for cap in caps:
        row = rows.get(cap.strategy_id, {})
        gaps = [{'field': field, 'period': PERIOD, 'issue': issue, 'legal_public_source': source, 'local_evidence': local,
                 'official_route': list(route)} for field, (issue, scope, source, local, route, _) in GAPS.items() if _applies(scope, cap)]
        if cap.governance:
            gaps.append({'field': 'governed_research', 'period': PERIOD, 'issue': 'GOVERNED', 'legal_public_source': None, 'local_evidence': None,
                         'official_route': ['governed', cap.governance]})
        warnings = list(row.get('warnings') or [])
        out[cap.strategy_id] = {
            'strategy_id': cap.strategy_id, 'state': row.get('state'), 'engine_support': cap.engine or 'NONE', 'gaps': gaps, 'warnings': warnings,
            'requires_formal_trial': bool(cap.governance) or bool(cap.static_block),
            'immediate_actions': [GAPS[g['field']][5] for g in gaps if g['field'] in GAPS] or
                                 [f'governed by {cap.governance}: use its own registered research process'],
        }
    return out


def default_probes():
    """Read-only probes: local evidence = an existing file with a recorded hash; official = an authorization pre-check (no HTTP)."""
    def local(gap):
        path = gap.get('local_evidence')
        if not path:
            return None
        target = ROOT / path.split(':')[0]
        return {'found': path, 'verified': False} if target.exists() else None  # partial local data is never promoted to verified

    def official(gap):
        kind, name = (gap.get('official_route') or [None, None])[:2]
        if kind == 'research_budget':
            from core.runtime import research_budget
            try:
                research_budget.check(None if name not in research_budget.CAPS else name)
                return {'allowed': name}
            except Exception as exc:
                return {'denied': type(exc).__name__ if 'BUDGET' not in str(exc) else str(exc).split(':')[0].split()[-1]}
        return {'denied': {'allow_list': 'SOURCE_NOT_ALLOWLISTED', 'restricted': 'RESTRICTED_SOURCE_REQUIRES_SEPARATE_AUTHORIZATION',
                           'governed': 'GOVERNED_RESEARCH_ONLY'}.get(kind, 'NO_ROUTE')}

    def alternative(gap):
        return ALTERNATIVE_SOURCES.get(gap.get('field'))
    return {'local': local, 'official': official, 'alternative': alternative}


def recover(gap, probes=None) -> dict:
    """At most three rounds over local, official and alternative sources; stops early when a round brings nothing new."""
    probes = probes or default_probes()
    attempts, previous = [], None
    for round_no in range(1, MAX_ROUNDS + 1):
        outcome = []
        for name in ('local', 'official', 'alternative'):
            found = probes[name](gap)
            outcome.append({'round': round_no, 'source': name, 'result': found})
            if isinstance(found, dict) and found.get('verified'):
                attempts += outcome
                return {'field': gap['field'], 'recovered': True, 'status': 'RECOVERED', 'rounds': round_no, 'attempts': attempts,
                        'source': name, 'next_step': 'validate source, date, field, value, announcement time and actions, then rerun affected rows'}
        attempts += outcome
        if outcome == previous:
            break  # no progress: a fourth identical attempt cannot change the answer
        previous = [{**o, 'round': round_no + 1} for o in outcome]
    denied = [a['result']['denied'] for a in attempts if isinstance(a['result'], dict) and a['result'].get('denied')]
    status = UNRECOVERED.get(gap.get('issue'), 'DATA_BLOCKED')
    if gap.get('issue') in ('BENCHMARK', 'UNIVERSE') or (status == 'DATA_BLOCKED' and denied):
        status = 'SOURCE_UNAVAILABLE'
    return {'field': gap['field'], 'recovered': False, 'status': status, 'reason': denied[0] if denied else 'NO_LEGAL_SOURCE_FOUND',
            'rounds': attempts[-1]['round'], 'attempts': attempts, 'next_step': GAPS.get(gap['field'], (None,) * 6)[5] or 'needs a registered source'}


def report(batch, caps=None, probes=None) -> dict:
    from core.research.reference import catalog
    records = scan(caps or catalog.capabilities(), batch)
    cache = {}
    for record in records.values():
        for gap in record['gaps']:
            if gap['issue'] == 'GOVERNED':
                gap['recovery'] = {'status': 'DATA_BLOCKED', 'reason': 'GOVERNED_RESEARCH_ONLY', 'recovered': False, 'rounds': 0}
                continue
            if gap['field'] not in cache:
                cache[gap['field']] = recover(gap, probes)
            gap['recovery'] = cache[gap['field']]
    recovered = sorted({g['field'] for r in records.values() for g in r['gaps'] if (g.get('recovery') or {}).get('recovered')})
    return {'batch_id': (batch or {}).get('batch_id'), 'strategies': records, 'recovered_fields': recovered,
            'rerun_needed': sorted(sid for sid, r in records.items() if any(g['field'] in recovered for g in r['gaps']))}


def write(batch, result) -> Path:
    from core.research.reference import runner
    folder = runner.OUT / 'batches' / batch['batch_id']
    (folder / 'gap_report.json').write_text(json.dumps(result, ensure_ascii=False, indent=1, default=str), encoding='utf-8')
    lines = ['# Strategy Validation Gap Report', '', f"batch {batch['batch_id']}; recovered: {result['recovered_fields'] or 'none'}", '',
             '| strategy | state | gap field | issue | recovery | reason | rounds | next step | formal TRIAL |', '|' + '---|' * 9]
    for sid, record in result['strategies'].items():
        for gap in record['gaps']:
            rec = gap.get('recovery') or {}
            lines.append(f"| {sid} | {record['state']} | {gap['field']} | {gap['issue']} | {rec.get('status')} | {rec.get('reason', '')} | "
                         f"{rec.get('rounds')} | {GAPS.get(gap['field'], (None,) * 6)[5] or record['immediate_actions'][0]} | "
                         f"{'yes' if record['requires_formal_trial'] else 'no'} |")
    (folder / 'gap_report.md').write_text('\n'.join(lines) + '\n', encoding='utf-8')
    return folder


def main(argv=None) -> int:
    from core.research.reference import runner
    batch = runner.latest_report()
    if batch is None:
        print('NO_BATCH_REPORT', file=sys.stderr)
        return 1
    result = report(batch)
    write(batch, result)
    print(json.dumps({'batch_id': batch['batch_id'], 'recovered': result['recovered_fields'], 'rerun_needed': result['rerun_needed']}))
    return 0


if __name__ == '__main__':
    sys.exit(main())
