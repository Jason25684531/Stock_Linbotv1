"""Strategy Validation Gap Report and Data Gap Recovery Agent (design D8). Read-only probes injected; no network."""
from core.research.reference import catalog, gaps


def _caps():
    return {c.strategy_id: c for c in catalog.capabilities()}


def test_every_strategy_gets_a_gap_record_with_the_required_fields():
    records = gaps.scan(catalog.capabilities())
    assert len(records) == 21
    for record in records.values():
        assert {'gaps', 'engine_support', 'requires_formal_trial', 'immediate_actions'} <= set(record)
        for gap in record['gaps']:
            assert {'field', 'period', 'issue', 'legal_public_source'} <= set(gap)
    revenue = [g for g in records['quality_growth']['gaps'] if g['field'] == 'monthly_revenue.announcement_time']
    assert revenue and revenue[0]['issue'] == 'PIT' and revenue[0]['period'] == ['2023-01-03', '2025-12-31']


def test_recovery_tries_sources_in_order_with_at_most_three_rounds():
    attempts = []
    probes = {'local': lambda gap: attempts.append('local') or None,
              'official': lambda gap: attempts.append('official') or {'denied': 'BUDGET_UNASSIGNED'},
              'alternative': lambda gap: attempts.append('alternative') or None}
    result = gaps.recover({'field': 'monthly_revenue.announcement_time', 'issue': 'PIT'}, probes=probes)
    assert attempts[:3] == ['local', 'official', 'alternative']
    assert result['status'] == 'PIT_UNVERIFIABLE' and 1 <= result['rounds'] <= 3 and len(attempts) == 3 * result['rounds']
    assert 'BUDGET_UNASSIGNED' in str(result['attempts']) and result['next_step']


def test_budget_blocked_official_route_is_source_unavailable_and_others_continue():
    probes = {'local': lambda g: None, 'official': lambda g: {'denied': 'BUDGET_UNASSIGNED'}, 'alternative': lambda g: None}
    result = gaps.recover({'field': 'benchmark.total_return_index', 'issue': 'BENCHMARK'}, probes=probes)
    assert result['status'] == 'SOURCE_UNAVAILABLE' and result['reason'] == 'BUDGET_UNASSIGNED'


def test_partial_local_evidence_is_never_promoted_to_trusted():
    probes = {'local': lambda g: {'found': 'outputs/research_mode/prepared_v6/actions.json', 'verified': False},
              'official': lambda g: None, 'alternative': lambda g: None}
    result = gaps.recover({'field': 'corporate_actions.splits_capital_reductions', 'issue': 'CORPORATE_ACTIONS'}, probes=probes)
    assert result['status'] == 'ACTIONS_UNKNOWN' and result['recovered'] is False


def test_verified_recovery_marks_the_gap_recovered():
    probes = {'local': lambda g: {'found': 'x.parquet', 'verified': True}, 'official': lambda g: None, 'alternative': lambda g: None}
    result = gaps.recover({'field': 'official_bars', 'issue': 'DATA'}, probes=probes)
    assert result['recovered'] is True and result['rounds'] == 1
