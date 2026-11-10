import json

import pandas as pd
import pytest

from core.research import benchmark_tri
from core.research.strategy_signal import ResearchOnlyViolation

CALENDAR = ['2024-01-02', '2024-01-03', '2024-01-04', '2025-12-31']


def roc(iso):
    year, month, day = iso.split('-')
    return f'{int(year) - 1911}/{month}/{day}'


def write_json(tmp_path, rows, name='MFI94U_2024.json'):
    payload = {'stat': 'OK', 'fields': ['date', 'index'], 'data': [[roc(d), f'{v:,.2f}'] for d, v in rows]}
    (tmp_path / name).write_text(json.dumps(payload, ensure_ascii=False), encoding='utf-8')


def full_rows():
    return [(day, 30000.0 + i) for i, day in enumerate(CALENDAR)]


def test_complete_file_is_ready_and_aligned(tmp_path):
    write_json(tmp_path, full_rows())
    report = benchmark_tri.quality(CALENDAR, root=tmp_path)
    assert report['status'] == 'READY'
    assert report['covered_days'] == report['calendar_days'] == 4
    series = benchmark_tri.research_series(CALENDAR, root=tmp_path)
    assert list(series.index.strftime('%Y-%m-%d')) == CALENDAR
    assert series.iloc[0] == 30000.0


def test_csv_with_iso_dates_and_commas(tmp_path):
    lines = ['date,value'] + [f'{d},"{v:,.2f}"' for d, v in full_rows()]
    (tmp_path / 'MFI94U.csv').write_text('\n'.join(lines), encoding='utf-8')
    assert benchmark_tri.quality(CALENDAR, root=tmp_path)['status'] == 'READY'
    assert benchmark_tri.load(tmp_path).iloc[-1] == 30003.0


def test_missing_day_is_data_blocked_with_that_date(tmp_path):
    write_json(tmp_path, [r for r in full_rows() if r[0] != '2024-01-03'])
    report = benchmark_tri.quality(CALENDAR, root=tmp_path)
    assert report['status'] == 'DATA_BLOCKED'
    assert report['missing_dates'] == ['2024-01-03']
    with pytest.raises(benchmark_tri.BenchmarkBlocked):
        benchmark_tri.research_series(CALENDAR, root=tmp_path)


def test_duplicate_and_non_positive_are_data_blocked(tmp_path):
    write_json(tmp_path, full_rows() + [('2024-01-04', 30002.0)])
    assert benchmark_tri.quality(CALENDAR, root=tmp_path)['duplicate_dates'] == ['2024-01-04']
    write_json(tmp_path, [(d, 0.0 if d == '2024-01-02' else v) for d, v in full_rows()])
    report = benchmark_tri.quality(CALENDAR, root=tmp_path)
    assert report['status'] == 'DATA_BLOCKED' and report['non_positive_dates'] == ['2024-01-02']


def test_2026_rows_are_dropped(tmp_path):
    write_json(tmp_path, full_rows() + [('2026-01-02', 40000.0), ('2026-03-02', 41000.0)])
    report = benchmark_tri.quality(CALENDAR, root=tmp_path)
    assert report['status'] == 'READY' and report['rows_after_2025_dropped'] == 2
    series = benchmark_tri.research_series(CALENDAR, root=tmp_path)
    assert series.index.max() <= pd.Timestamp('2025-12-31')


def test_no_file_is_auth_blocked(tmp_path):
    report = benchmark_tri.quality(CALENDAR, root=tmp_path / 'absent')
    assert report['status'] == 'IMPLEMENTED_BUT_AUTH_BLOCKED'
    assert 'Owner authorization' in report['reason']
    with pytest.raises(benchmark_tri.BenchmarkBlocked):
        benchmark_tri.research_series(CALENDAR, root=tmp_path / 'absent')


def test_2026_calendar_is_refused(tmp_path):
    write_json(tmp_path, full_rows())
    with pytest.raises(ResearchOnlyViolation):
        benchmark_tri.research_series(CALENDAR + ['2026-01-02'], root=tmp_path)
    with pytest.raises(ResearchOnlyViolation):
        benchmark_tri.quality(['2026-01-02'], root=tmp_path)


def test_date_formats():
    assert str(benchmark_tri.parse_date('112/01/03')) == '2023-01-03'
    assert str(benchmark_tri.parse_date('1120103')) == '2023-01-03'
    assert str(benchmark_tri.parse_date('20230103')) == '2023-01-03'
    assert str(benchmark_tri.parse_date('2023-01-03')) == '2023-01-03'


def test_quality_report_markdown(tmp_path):
    path = tmp_path / 'report.md'
    report = benchmark_tri.write_quality_report(CALENDAR, path, root=tmp_path / 'absent')
    text = path.read_text(encoding='utf-8')
    assert report['status'] == 'IMPLEMENTED_BUT_AUTH_BLOCKED'
    assert 'not data certification' in text and 'data.gov.tw/dataset/11871' in text and 'H4 archive is untouched' in text


def test_extract_from_mi_index_reads_no_2026_file(tmp_path):
    import json
    from core.research import benchmark_tri as b
    raw = tmp_path / 'raw'
    raw.mkdir()
    row = b.TRI_ROW
    for day, value in (('20251230', '50,000.10'), ('20251231', '50,100.20'), ('20260102', '1')):
        (raw / f'MI_INDEX_{day}.json').write_text(json.dumps({'tables': [{'data': [[row, value]]}]}), encoding='utf-8')
    (raw / 'MI_INDEX_20260105.json').write_text('not json: must never be opened', encoding='utf-8')
    out = tmp_path / 'tri'
    assert b.extract_from_mi_index(raw=raw, root=out) == {'files_read': 2, 'rows': 2}
    series = b.load(out)
    assert list(series.index.strftime('%Y-%m-%d')) == ['2025-12-30', '2025-12-31'] and series.iloc[-1] == 50100.2


def test_provenance_is_deterministic_and_anomalies_are_diagnostic_only(tmp_path):
    rows = full_rows()
    rows[2] = (rows[2][0], rows[1][1] * 1.15)  # a 15% one-day move
    write_json(tmp_path, rows)
    before = {p.name: p.read_bytes() for p in tmp_path.iterdir()}
    price = pd.Series([v for _, v in full_rows()], index=pd.to_datetime(CALENDAR))
    first, second = (benchmark_tri.provenance(CALENDAR, root=tmp_path, price=price) for _ in range(2))
    assert first == second and first['status'] == 'BENCHMARK_SOURCE_NOT_CROSS_VERIFIED'
    assert first['DIAGNOSTIC_ANOMALY']['jumps_over_10pct'] == ['2024-01-04', '2025-12-31']  # up 15%, then back down
    assert '2024-01-04' in first['DIAGNOSTIC_ANOMALY']['return_gap_over_3pct']
    assert {p.name: p.read_bytes() for p in tmp_path.iterdir()} == before  # nothing corrected


def test_crosscheck_lists_differences_and_never_downloads(tmp_path):
    local, external = tmp_path / 'local', tmp_path / 'ext'
    local.mkdir(), external.mkdir()
    write_json(local, full_rows())
    changed = full_rows()
    changed[1] = (changed[1][0], changed[1][1] + 1.0)
    write_json(external, changed, name='MFI94U.json')
    assert benchmark_tri.crosscheck(None, root=local)['status'] == 'INPUT_MISSING'
    result = benchmark_tri.crosscheck(external / 'MFI94U.json', root=local)
    assert result['status'] == 'DIFFERENCES' and [r['date'] for r in result['differences']] == ['2024-01-03']
    assert benchmark_tri.crosscheck(local / 'MFI94U_2024.json', root=local)['status'] == 'MATCH'
