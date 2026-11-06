import pandas as pd

import scripts.research_h4_unblock as unblock


def test_recovery_only_clears_the_recovered_companies_and_all_four_limits_must_pass():
    m = pd.DataFrame({'asof_date': ['d1'] * 20 + ['d2'] * 20, 'asset_id': [f'{i:04d}' for i in range(20)] * 2})
    m['no_source'] = m['asset_id'].isin(['0000', '0001', '0002', '0003'])
    m['survivor_gap'] = m['asset_id'].isin(['0000', '0001'])
    m['financial'] = m['asset_id'].eq('0002')
    base = unblock.daily(m)
    assert base['survivor_gap_share'].tolist() == [0.1, 0.1] and base['no_source_share'].tolist() == [0.2, 0.2]
    assert unblock.passes(base) == {'survivor_gap_mean': False, 'survivor_gap_max': False, 'no_source_mean': False, 'no_source_max': False}
    after = unblock.daily(m, ['0000', '0001'])
    assert after['survivor_gap_share'].max() == 0 and after['no_source_share'].max() == 0.1
    chosen, final = unblock.minimal_recovery(m, financial=False)
    assert set(chosen) == {'0000', '0001'} and all(unblock.passes(final).values())
