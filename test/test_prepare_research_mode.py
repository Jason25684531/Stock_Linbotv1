import json
import pytest
from scripts.prepare_research_mode import prepare


def test_checkpoint_resume_detects_changed_source(tmp_path):
    cache, output = tmp_path / 'raw', tmp_path / 'cache'
    cache.mkdir()
    fields = ['證券代號', '證券名稱', '成交股數', '成交筆數', '成交金額', '開盤價', '最高價', '最低價', '收盤價']
    for day in ('20211231', '20220103', '20230103', '20240103', '20250103'):
        payload = {'stat': 'OK', 'date': day, 'tables': [{'title': '每日收盤行情', 'fields': fields,
                    'data': [['2330', 'X', '1000', '10', '10000', '10', '10', '10', '10']]}]}
        (cache / f'MI_INDEX_{day}.json').write_text(json.dumps(payload), encoding='utf-8')
    prepare(cache, output)
    prepare(cache, output)
    path = cache / 'MI_INDEX_20230103.json'
    path.write_text(path.read_text(encoding='utf-8') + ' ', encoding='utf-8')
    with pytest.raises(ValueError, match='CHECKPOINT_CHANGED'):
        prepare(cache, output)
