from types import SimpleNamespace as NS

import pytest

from core.research.history import categories as cats
from core.research.history import trust


def cap(sid, family='tquant', engine='TQUANT_SNAPSHOT', governance=None, block=None):
    return NS(strategy_id=sid, family=family, engine=engine, governance=governance, static_block=block, data_needs=())


def coverage(overrides=None, default='VERIFIED'):
    cells = {(y, c.id): default for y in cats.YEARS for c in cats.CATEGORIES}
    cells.update(overrides or {})
    return {k: {'status': v, 'reasons': [f'{k[1]}:{v}']} for k, v in cells.items()}


def test_price_strategy_needs_priced_categories_only():
    req = trust.requirement(cap('bollinger_reversion'), frozenset())
    assert req['needs']['prices'] == 'REQUIRED' and req['needs']['dividends'] == 'REQUIRED'
    assert req['needs']['financial_statements'] == cats.NOT_REQUIRED and req['needs']['t86_flows'] == cats.NOT_REQUIRED
    assert req['needs']['benchmark_price'] == cats.COMPARISON_ONLY


def test_flow_inputs_require_t86():
    req = trust.requirement(cap('hybrid_trend_rank', 'native', 'LEGACY_V2'), frozenset({'foreign_ratio', 'ma20'}))
    assert req['needs']['t86_flows'] == 'REQUIRED' and req['needs']['margin'] == cats.NOT_REQUIRED


def test_missing_t86_blocks_flow_strategy_but_not_price_strategy():
    cov = coverage({(2018, 't86_flows'): 'ACCESS_BLOCKED'})
    flow = trust.requirement(cap('hybrid_trend_rank', 'native', 'LEGACY_V2'), frozenset({'foreign_ratio'}))
    price = trust.requirement(cap('bollinger_reversion'), frozenset())
    assert trust.cell(flow, 2018, cov)['status'] == 'ACCESS_BLOCKED'
    assert 't86_flows' in trust.cell(flow, 2018, cov)['binding']
    assert trust.cell(price, 2018, cov)['status'] == 'VERIFIED'


def test_soft_category_caps_at_partial_but_failure_propagates():
    req = trust.requirement(cap('bollinger_reversion'), frozenset())
    assert trust.cell(req, 2024, coverage({(2024, 'capital_changes'): 'ACCESS_BLOCKED'}))['status'] == 'PARTIAL'
    assert trust.cell(req, 2024, coverage({(2024, 'capital_changes'): 'FAILED_VALIDATION'}))['status'] == 'FAILED_VALIDATION'


def test_comparison_category_does_not_change_trust():
    req = trust.requirement(cap('bollinger_reversion'), frozenset())
    assert trust.cell(req, 2020, coverage({(2020, 'benchmark_price'): 'PARTIAL'}))['status'] == 'VERIFIED'


def test_warmup_years_are_included():
    req = trust.requirement(cap('bollinger_reversion'), frozenset())  # 550 calendar days
    cov = coverage({(2018, 'calendar'): 'PARTIAL'})
    assert trust.cell(req, 2019, cov)['status'] == 'PARTIAL' and trust.cell(req, 2021, cov)['status'] == 'VERIFIED'
    assert 'WARMUP_BEFORE_SNAPSHOT' in trust.cell(req, 2015, coverage())['reasons']


def test_partial_with_large_value_gap_is_not_backtestable():
    req = trust.requirement(cap('quality_value_low_volatility', 'native', 'LEGACY_V2'), frozenset({'eps'}))
    cov = coverage({(2024, 'financial_statements'): 'PARTIAL'})
    cov[(2024, 'financial_statements')] |= {'present': 2971, 'expected': 4152}
    cell = trust.cell(req, 2024, cov)
    assert cell['status'] == 'PARTIAL' and not cell['backtestable']
    assert any(r.startswith('VALUE_COVERAGE_BELOW_0.95:financial_statements') for r in cell['reasons'])


def test_financial_strategy_needs_verified_publication_versions():
    req = trust.requirement(cap('quality_value_low_volatility', 'native', 'LEGACY_V2'), frozenset({'eps'}))
    cell = trust.cell(req, 2024, coverage({(2024, 'financial_publication_time'): 'PARTIAL'}))
    assert not cell['backtestable'] and 'PIT_VERSION_UNVERIFIED:financial_publication_time' in cell['reasons']


def test_year_with_a_missing_session_is_not_backtestable_but_warmup_may_miss_a_few():
    req = trust.requirement(cap('bollinger_reversion'), frozenset())
    cov = coverage({(2017, 'calendar'): 'PARTIAL'})
    cov[(2017, 'calendar')] |= {'present': 260, 'expected': 263}
    assert not trust.cell(req, 2017, cov)['backtestable']
    assert 'MISSING_SESSIONS_IN_YEAR:calendar:260/263' in trust.cell(req, 2017, cov)['reasons']
    assert trust.cell(req, 2018, cov)['backtestable']  # 2017 is only warm-up for 2018


def test_partial_without_value_gap_stays_backtestable():
    req = trust.requirement(cap('hybrid_trend_rank', 'native', 'LEGACY_V2'), frozenset({'foreign_ratio'}))
    cov = coverage({(2024, 't86_flows'): 'PARTIAL'})
    cov[(2024, 't86_flows')] |= {'present': 9800, 'expected': 10000}
    assert trust.cell(req, 2024, cov)['backtestable']


def test_governed_strategy_is_governed_only():
    req = trust.requirement(cap('H4:GROWTH-MOMENTUM-V2', 'research', None, 'H4_SEALED_RESEARCH'), frozenset())
    assert trust.cell(req, 2020, coverage())['status'] == 'GOVERNED_ONLY'


def test_engine_unsupported_is_not_backtestable():
    req = trust.requirement(cap('x', 'native', None), frozenset())
    assert not trust.cell(req, 2020, coverage())['backtestable']


@pytest.mark.parametrize('statuses,expected', [
    (['PARTIAL', 'VERIFIED', 'VERIFIED', 'VERIFIED', 'PARTIAL'], ('2016', '2018')),
    (['VERIFIED', 'VERIFIED', 'PARTIAL', 'VERIFIED'], None),
])
def test_longest_verified_span_needs_three_years(statuses, expected):
    cells = [{'year': 2015 + i, 'status': s} for i, s in enumerate(statuses)]
    span = trust.verified_span(cells)
    assert (span and (str(span[0]), str(span[1]))) == expected if expected else span is None


def test_earliest_backtestable_year_requires_every_later_year():
    cells = [{'year': y, 'backtestable': y >= 2017 and y != 2019} for y in cats.YEARS]
    assert trust.first_backtestable_year(cells) == 2020
