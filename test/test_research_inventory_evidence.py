import json

import pytest

from scripts import research_inventory_evidence as inventory


def test_only_development_files_and_unknowns(tmp_path, monkeypatch):
    monkeypatch.setattr(inventory, 'ROOT', tmp_path)
    path = tmp_path / 'MI_INDEX_20250102.json'
    fields = ['證券代號', '證券名稱', '成交股數', '成交筆數', '成交金額', '開盤價', '最高價', '最低價', '收盤價']
    payload = {'stat': 'OK', 'date': '20250102', 'tables': [{'title': '每日收盤行情', 'fields': fields,
               'data': [['2330', 'fixture', '2,000', '1', '20000', '10', '11', '9', '10']]}]}
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding='utf-8')
    rows = inventory.market_records(path, ['2330', '9999'])
    assert [r['value_or_UNKNOWN'] for r in rows] == [10.0, 2000.0, 'UNKNOWN', 'UNKNOWN']
    assert all(r['tier'] == 'LOCAL_CACHE_UNCERTIFIED' and r['fetched_at'] is None for r in rows)
    payload['date'] = '20250103'
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding='utf-8')
    assert all(r['value_or_UNKNOWN'] == 'UNKNOWN' for r in inventory.market_records(path, ['2330']))
    with pytest.raises(ValueError, match='OUTSIDE_DEVELOPMENT_WINDOW'):
        inventory.market_records(tmp_path / 'MI_INDEX_20260102.json', ['2330'])
