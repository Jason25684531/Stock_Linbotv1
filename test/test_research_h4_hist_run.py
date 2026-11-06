import scripts.research_h4_hist_run as hist
from core.research import repair_runner


def test_roc_dates_and_quote_files_never_pass_the_window(tmp_path, monkeypatch):
    assert hist.roc('104年01月05日') == '2015-01-05' and hist.roc('111/12/28') == '2022-12-28'
    raw_hist, raw_local = tmp_path / 'hist', tmp_path / 'local'
    raw_hist.mkdir()
    raw_local.mkdir()
    for folder, day in ((raw_hist, '20150105'), (raw_hist, '20211230'), (raw_local, '20211230'), (raw_local, '20221230'), (raw_local, '20230103')):
        (folder / f'MI_INDEX_{day}.json').write_text('{}', encoding='utf-8')
    monkeypatch.setattr(hist, 'RAW_HIST', raw_hist)
    monkeypatch.setattr(hist, 'RAW_LOCAL', raw_local)
    names = [(p.parent.name, p.stem[-8:]) for p in hist.quote_paths()]
    assert names == [('hist', '20150105'), ('local', '20211230'), ('local', '20221230')]


def test_historical_kind_is_bound_to_its_registration_and_window():
    assert repair_runner.EVIDENCE_GRIDS[hist.KIND] == ('H4_HIST_GRID', 'outputs/strategy_research/h4_growth_momentum_v1/strategy_grid.csv', hist.HID)
    assert repair_runner.BAR_START == {hist.KIND: hist.START}
