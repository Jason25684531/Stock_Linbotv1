import json

import pytest

import scripts.research_h4_historical as hist


def test_taiex_reads_total_return_index_inside_the_window(tmp_path, monkeypatch):
    def write(day, value):
        table = {'title': 'returns', 'fields': ['index', 'close'], 'data': [['發行量加權股價報酬指數', f'{value:,.2f}']]}
        (tmp_path / f'MI_INDEX_{day}.json').write_text(json.dumps({'tables': [table]}), encoding='utf-8')
    for day, value in (('20221229', 1.0), ('20221230', 30000.0), ('20230103', 30300.0), ('20260102', 99999.0)):
        write(day, value)
    monkeypatch.setattr(hist, 'RAW', tmp_path)
    frame = hist.taiex()
    assert [d.strftime('%Y%m%d') for d in frame.index] == ['20221230', '20230103']
    assert frame['taiex_total_return'].pct_change().iloc[-1] == pytest.approx(0.01)
