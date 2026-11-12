import json
from pathlib import Path

from core.research.intelligence import evaluate as ev

PROFILE = json.loads(Path('core/research/intelligence/data/evaluation_profile.json').read_text(encoding='utf-8'))
STATUSES = {'GOOD', 'CAUTION', 'RISK', 'INSUFFICIENT_DATA', 'NOT_APPLICABLE'}


def _summary(cagr, **extra):
    base = {'cagr': {'value': cagr, 'reason': None}, 'cagr_stress': {'value': cagr - 0.02, 'reason': None}, 'max_drawdown': {'value': -0.2, 'reason': None},
            'benchmark_max_drawdown_total_return': {'value': -0.3, 'reason': None}, 'excess_cagr': {'value': cagr - 0.08, 'reason': None},
            'downside_capture': {'value': 0.6, 'reason': None}, 'profit_factor': {'value': 1.3, 'reason': None}, 'closed_trades': 40}
    return base | {k: {'value': v, 'reason': None} for k, v in extra.items()}


def _ctx(verified, provisional, ptype='trend_momentum'):
    return {'verified': verified, 'provisional': provisional, 'primary_type': ptype, 'scenarios_sample': {'status': 'SUFFICIENT', 'span': [2017, 2023]}}


def test_no_composite_score_and_seven_dimensions():
    out = ev.dimensions(_ctx(_summary(0.12), _summary(0.12)), PROFILE)
    assert set(out) - {'activity'} == {'data_trust', 'return_target', 'cost_tolerance', 'risk_budget', 'type_edge', 'benchmark', 'sample'}  # activity is information, not an eighth verdict
    assert 'status' not in out['activity']
    assert 'score' not in json.dumps(out)
    assert out['return_target']['status'] == 'RETURN_TARGET_PRIORITY'
    assert out['risk_budget']['status'] == 'RESEARCH_THRESHOLD_UNSET'


def test_provisional_only_gets_no_target_verdict_even_above_ten_percent():
    out = ev.dimensions(_ctx(None, _summary(0.25)), PROFILE)
    assert out['return_target']['status'] == 'INSUFFICIENT_VERIFIED_HISTORY'
    assert out['return_target']['provisional_cagr'] == 0.25
    statuses = {i['status'] for section in ev.health(_ctx(None, _summary(0.25)), PROFILE).values() for i in section}
    assert 'GOOD' not in statuses


def test_defensive_value_is_separate_from_the_target():
    out = ev.dimensions(_ctx(_summary(0.03), _summary(0.03), 'defensive'), PROFILE)
    assert out['return_target']['status'] == 'RETURN_TARGET_NOT_MET'
    assert out['type_edge']['defensive_value']['status'] == 'DEFENSIVE_VALUE'


def test_every_health_item_has_text_status_threshold_and_explanation():
    sections = ev.health(_ctx(_summary(0.07), _summary(0.07)), PROFILE)
    assert list(sections) == ['earn', 'drawdown', 'efficiency', 'consistency', 'tradability']
    for items in sections.values():
        for item in items:
            assert item['status'] in STATUSES and item['explanation'] and item['threshold']
            if item['threshold'] == 'RESEARCH_THRESHOLD_UNSET':
                assert item['status'] != 'GOOD'
    assert sections['earn'][0]['status'] == 'CAUTION'
