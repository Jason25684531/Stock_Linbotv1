import pytest

from core.runtime import public_data as public
from core.runtime import research_budget as rb

TWSE = 'https://www.twse.com.tw/rwd/zh'


def quote(day, kind='ALL'):
    return public.context(f'{TWSE}/afterTrading/MI_INDEX', params={'date': day, 'type': kind, 'response': 'json'})


def test_only_the_exact_historical_queries_use_the_new_job():
    assert quote('20150105').job == 'h4_hist' and quote('20211229').job == 'h4_hist'
    assert quote('20150103').job is None  # Saturday outside the approved make-up list
    assert quote('20211230').job is None and quote('20261027').job is None and quote('20141231').job is None
    assert all(quote(d).job == 'hist_foundation_quotes' for d in public.HIST_SATURDAYS)  # 2026-11-10 Owner plan: exact Saturdays only
    assert quote('20160129').job == 'h4_hist' and quote('20160131').job is None
    actions = lambda s, e: public.context(f'{TWSE}/exRight/TWT49U', params={'startDate': s, 'endDate': e, 'response': 'json'})
    assert actions('20150101', '20221231').job == 'h4_hist' and actions('20180101', '20181231').job == 'h4_hist'
    assert actions('20150101', '20231231').job is None
    window = {'startDate': '20150101', 'endDate': '20211231', 'response': 'json'}
    assert public.context(f'{TWSE}/reducation/TWTAUU', params=window).job == 'h4_hist'
    with pytest.raises(public.AuthorizationDenied):
        public.context(f'{TWSE}/change/TWTB8U', params={**window, 'endDate': '20220131'})


def test_new_job_has_its_own_ceiling_outside_the_396(public_budget, monkeypatch):
    before = rb.status()
    assert before['jobs']['h4_hist']['limit'] == 1870 and before['total_limit'] == 396
    monkeypatch.setitem(rb.CAPS, 'h4_hist', 2)
    for day in ('20150105', '20150106'):
        rb.finish(rb.reserve(quote(day)), outcome='FAILED')
    with pytest.raises(rb.BudgetDenied, match='BUDGET_EXHAUSTED: h4_hist'):
        rb.reserve(quote('20150107'))
    assert rb.status()['used'] == before['used']  # the 396 round count is untouched
    with pytest.raises(rb.BudgetDenied, match='BUDGET_UNASSIGNED'):
        rb.check(quote('20220103').job)


def test_empty_placeholder_tables_pass_but_partial_tables_still_fail():
    import json
    import requests
    from core.research.sources.twse import REQUIRED_CLOSING_FIELDS
    fields = sorted(REQUIRED_CLOSING_FIELDS)
    closing = {'title': 'x', 'fields': fields, 'data': [['1'] * len(fields)]}

    def check(tables):
        response = requests.Response()
        response.status_code = 200
        response._content = json.dumps({'stat': 'OK', 'date': '20160104', 'tables': tables}).encode()
        public.verify_response(quote('20160104'), response)
    check([closing, {}, {}])
    with pytest.raises(ValueError):
        check([closing, {'title': 'x', 'data': [['1']]}])


def test_fund_recovery_scope_is_the_fixed_fifteen_companies():
    form = {'id': '', 'key': '', 'step': '1', 'co_id': '2448', 'year': '', 'seamon': '', 'mtype': 'A', 'dtype': 'AI1'}
    assert public.context('https://doc.twse.com.tw/server-java/t57sb01', method='POST', data=form).job == 'h4_fund_recovery'
    assert public.context('https://doc.twse.com.tw/server-java/t57sb01', method='POST', data={**form, 'co_id': '2330'}).job is None
    t164 = {**public.FUND_T164, 'co_id': '2448', 'year': '108', 'season': '01'}
    url = 'https://mopsov.twse.com.tw/mops/web/ajax_t164sb04'
    assert public.context(url, method='POST', data=t164).job == 'h4_fund_recovery'
    for bad in ({**t164, 'co_id': '2330'}, {**t164, 'year': '115'}, {**t164, 'TYPEK': 'sii'}):
        with pytest.raises(public.AuthorizationDenied):
            public.context(url, method='POST', data=bad)


def test_xbrl_viewer_scope_is_the_fixed_fifteen_companies():
    url = 'https://mopsov.twse.com.tw/server-java/t164sb01'
    query = {'step': '1', 'CO_ID': '2448', 'SYEAR': '2019', 'SSEASON': '1', 'REPORT_ID': 'C'}
    assert public.context(url, params=query).job == 'h4_fund_recovery'
    for bad in ({**query, 'CO_ID': '2330'}, {**query, 'SYEAR': '2023'}, {**query, 'SSEASON': '5'}, {**query, 'REPORT_ID': 'B'}, {**query, 'extra': '1'}):
        with pytest.raises(public.AuthorizationDenied):
            public.context(url, params=bad)


def test_twse_security_page_starts_a_cooldown():
    import requests
    response = requests.Response()
    response.status_code = 307
    response._content = '因為安全性考量，您所執行的頁面無法呈現'.encode('utf-8')
    assert rb.response_cooldown(response) is not None
