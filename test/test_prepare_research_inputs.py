from scripts.prepare_research_inputs import align_events


def test_non_session_event_is_not_silently_dropped_or_given_fabricated_terms():
    known = {'ex_date': '2023-08-03', 'stock_id': '1216', 'source_sha256': 'a' * 64,
             'cash_per_share': 1, 'stock_ratio': 0}
    result = align_events({'actions': [known], 'unresolved_events': []}, ['2023-08-02', '2023-08-04'])
    assert result['actions'] == []
    assert result['unresolved_events'] == [{'ex_date': '2023-08-04', 'known_date': '2023-08-04',
            'source_event_date': '2023-08-03', 'stock_id': '1216', 'source_sha256': 'a' * 64,
            'missing_terms': ['actual_effective_event_date']}]
