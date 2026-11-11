import pytest

from core.research.history import categories as c
from core.research.native_frame import FRAME_INPUTS


def ev(**kw):
    base = dict(local=True, expected=10, present=10, checks=(c.Check('finlab', 100, 100, 100),))
    return c.Evidence(**(base | kw))


def test_complete_and_reconciled_is_verified():
    assert c.decide(ev()) == ('VERIFIED', ('COMPLETE_AND_RECONCILED:finlab',))


def test_integrity_error_beats_everything():
    status, reasons = c.decide(ev(integrity=('RAW_SHA_MISMATCH:3',), local=False))
    assert status == 'FAILED_VALIDATION' and reasons == ('RAW_SHA_MISMATCH:3',)


def test_no_local_data_with_route_is_access_blocked():
    assert c.decide(ev(local=False, present=0, route='BUDGET_UNASSIGNED:T86')) == ('ACCESS_BLOCKED', ('BUDGET_UNASSIGNED:T86',))


def test_no_local_data_without_route_is_unavailable():
    assert c.decide(ev(local=False, present=0, route=None))[0] == 'SOURCE_UNAVAILABLE'


def test_disagreement_fails():
    status, reasons = c.decide(ev(checks=(c.Check('finlab', 100, 100, 98),)))
    assert status == 'FAILED_VALIDATION' and reasons[0].startswith('RECONCILE_MISMATCH:finlab')


@pytest.mark.parametrize('kw,reason', [
    (dict(present=9), 'INCOMPLETE:9/10'),
    (dict(checks=()), 'NO_INDEPENDENT_SOURCE'),
    (dict(checks=(c.Check('mysql', 100, 40, 40),)), 'INDEPENDENT_COVERAGE:mysql:0.4000'),
    (dict(checks=(c.Check('finlab', 1000, 1000, 995),)), 'RECONCILE_RATE:finlab:0.995000'),
    (dict(caveats=('LISTING_DATE_LEFT_CENSORED',)), 'LISTING_DATE_LEFT_CENSORED'),
])
def test_partial_reasons(kw, reason):
    status, reasons = c.decide(ev(**kw))
    assert status == 'PARTIAL' and reason in reasons


def test_download_success_alone_is_not_verified():
    assert c.decide(ev(checks=()))[0] != 'VERIFIED'


def test_zero_events_are_never_verified_without_independent_evidence():
    assert c.decide(c.Evidence(local=True, expected=0, present=0))[0] == 'PARTIAL'
    assert c.decide(c.Evidence(local=True, expected=0, present=0, covered=True)) == ('PARTIAL', ('ZERO_EVENTS_NOT_INDEPENDENTLY_CONFIRMED',))


def test_worst_order():
    assert c.worst(['VERIFIED', 'PARTIAL']) == 'PARTIAL'
    assert c.worst(['ACCESS_BLOCKED', 'PARTIAL']) == 'ACCESS_BLOCKED'
    assert c.worst(['ACCESS_BLOCKED', 'FAILED_VALIDATION']) == 'FAILED_VALIDATION'
    assert c.worst([]) == 'VERIFIED'


def test_every_frame_input_maps_to_a_category():
    assert set().union(*(cat.inputs for cat in c.CATEGORIES)) == set(FRAME_INPUTS)


def test_category_ids_unique_and_roles_known():
    ids = [cat.id for cat in c.CATEGORIES]
    assert len(ids) == len(set(ids))
    assert {cat.role for cat in c.CATEGORIES} == {'SIGNAL', 'COMPARISON'}
    assert {cat.id for cat in c.CATEGORIES if cat.priced} == set(c.PRICED)
