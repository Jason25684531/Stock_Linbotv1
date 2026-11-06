import pandas as pd

from scripts.research_fundamental_g2g3 import admission, asof


def _records(prior_available='2024-05-16'):
    rows = []
    for year, available, pub in ((2024, prior_available, '2024-05-15'), (2025, '2025-05-16', '2025-05-15')):
        for metric, value in (('operating_profit', 100.0 if year == 2024 else 150.0), ('eps', 1.0 if year == 2024 else 1.2)):
            rows.append({'ticker': '2330', 'metric': metric, 'value': value, 'fiscal_year': year, 'quarter': 1, 'period_end': f'{year}-03-31',
                         'publication_date': pub, 'publication_timestamp': f'{pub}T10:00:00+08:00', 'available_date': available,
                         'revision_id': 'r', 'retrieved_at': '2026-09-15T00:00:00+00:00', 'REPORTING_BASIS': 'STANDALONE'})
    for metric, value in (('operating_income_yoy', 0.5), ('eps_yoy', 0.2)):
        rows.append({**rows[-1], 'metric': metric, 'value': value})
    return pd.DataFrame(rows)


def test_asof_uses_only_available_records():
    panel = asof(_records(), 'operating_income_yoy', [pd.Timestamp('2025-05-15'), pd.Timestamp('2025-05-16')], {'2330'})
    assert panel['value'].isna().tolist() == [True, False]


def test_admission_flags_prior_year_value_available_later_and_values_never_certified():
    members = pd.DataFrame({'asof_date': pd.to_datetime(['2025-05-16']), 'ticker': ['2330']})
    for prior, pairing in (('2024-05-16', True), ('2025-06-01', False)):
        records = _records(prior)
        matrix = asof(records, 'operating_income_yoy', members['asof_date'], {'2330'}).rename(columns={'value': 'operating_income_yoy'})
        matrix['eps_yoy'] = asof(records, 'eps_yoy', members['asof_date'], {'2330'})['value']
        result, _ = admission(members, records, matrix.drop(columns='available_date'))
        assert result['checks']['A3_asof_equals_matrix'] and result['checks']['A2_available_after_publication']
        assert result['checks']['A4_prior_year_pairing'] is pairing
        assert result['checks']['A6_first_published_values'] is False and result['status'] == 'BLOCKED'
