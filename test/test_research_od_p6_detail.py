import pytest

from scripts import od_p6_fetch

PROBE = {'job': 'od_p6', 'reserved_at': '2026-10-30T07:00:00+00:00', 'outcome': 'VERIFIED', 'request_key': 'k', 'sha256': None}


def test_targets_stay_in_the_development_window():
    found = od_p6_fetch.targets()
    assert found and all(day <= '20251231' for _, day in found)


def test_round_cap_stops_before_any_request(monkeypatch):
    from core.runtime import research_budget
    monkeypatch.setattr(research_budget, 'audit_rows', lambda: [PROBE])  # past the probe gate, so the cap is what stops it
    monkeypatch.setattr(od_p6_fetch, 'round_used', lambda: od_p6_fetch.ROUND_CAP)
    monkeypatch.setattr(od_p6_fetch, 'fetch', lambda *a, **k: pytest.fail('request sent past the round cap'))
    od_p6_fetch.run(od_p6_fetch.ENDPOINTS[0])
    assert od_p6_fetch.probe() == 'STOPPED'


def test_probe_sends_one_attempt_and_never_falls_back_to_the_alias(monkeypatch):
    sent = []
    monkeypatch.setattr(od_p6_fetch, 'round_used', lambda: 0)
    monkeypatch.setattr(od_p6_fetch, 'fetch', lambda gate, url, stock, day: sent.append(url) or 'FAILED')
    assert od_p6_fetch.probe() == 'FAILED' and sent == [od_p6_fetch.ENDPOINTS[0]]


def test_probe_rejects_targets_outside_the_window():
    with pytest.raises(SystemExit, match='outside'):
        od_p6_fetch.probe(('2330', '20260708'))


def test_run_needs_a_verified_probe_and_stops_after_three_failures(monkeypatch):
    from core.runtime import research_budget
    sent = []
    monkeypatch.setattr(od_p6_fetch, 'round_used', lambda: 0)
    monkeypatch.setattr(od_p6_fetch, 'fetch', lambda gate, url, stock, day: sent.append(stock) or 'FAILED')
    monkeypatch.setattr(research_budget, 'audit_rows', lambda: [])
    assert od_p6_fetch.run(od_p6_fetch.ENDPOINTS[0]) == 'REFUSED' and sent == []
    monkeypatch.setattr(research_budget, 'audit_rows', lambda: [PROBE])
    assert od_p6_fetch.run(od_p6_fetch.ENDPOINTS[0]) == {'FAILED': 3} and len(sent) == 3


def test_round_rows_and_the_not_requested_list(tmp_path, monkeypatch):
    from core.runtime import research_budget
    first = od_p6_fetch.targets()[0]
    rows = [dict(PROBE, outcome='FAILED', request_key='first'), dict(PROBE, reserved_at='2026-10-29T07:00:00+00:00', request_key='old'),
            dict(PROBE, job='t1b', request_key='t1b')]
    monkeypatch.setattr(research_budget, 'audit_rows', lambda: rows)
    monkeypatch.setattr(research_budget, 'status', lambda: {'jobs': {'od_p6': {'remaining': 379}}})
    monkeypatch.setattr(od_p6_fetch, 'key', lambda stock, day: 'first' if (stock, day) == first else 'other')
    assert od_p6_fetch.round_used() == 1  # other jobs and earlier rounds are not counted
    summary = od_p6_fetch.report(tmp_path / 'report')
    assert summary['status'] == 'BLOCKED' and first not in summary['not_requested_targets']
    assert len(summary['not_requested_targets']) == len(od_p6_fetch.targets()) - 1
