"""Provider inventory: generated offline, TWSE_ONLY rows name no TPEx endpoint, terms never inferred."""
from scripts import market_data_inventory as inv


def test_inventory_rows_and_scope():
    rows = inv.rows()
    twse_only = [r for r in rows if r[0] == 'TWSE_ONLY']
    assert {r[1] for r in twse_only} == set(inv.DATASETS)
    assert not any('tpex' in r[2] or 'restricted' in r[3] or '/otc/' in r[2] for r in twse_only)
    assert any(r[3] == 'restricted:tpex' for r in rows if r[0] == 'ALL')
    assert any(r[3] == 'TWSE:MI_INDEX' and r[5] == 'daily_market_data' for r in twse_only)
