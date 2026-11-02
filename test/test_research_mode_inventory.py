import csv
from scripts.research_mode_inventory import LINEAGE, build_inventory


def test_inventory_is_development_only_and_defers_track_a(tmp_path):
    original = build_inventory()
    lineage = tmp_path / 'lineage.csv'
    with LINEAGE.open(encoding='utf-8-sig', newline='') as source:
        reader = csv.DictReader(source)
        rows, fields = list(reader), reader.fieldnames
    future = dict(rows[0], ticker='9999', roc_year='115', available_date='2026-01-01', parse_status='PASS')
    with lineage.open('w', encoding='utf-8', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        writer.writerows([*rows, future])
    modified = build_inventory(lineage=lineage)
    assert modified['yoy_tickers_available_in_development'] == original['yoy_tickers_available_in_development']
    assert original['strategy_count'] == 22
    assert all(row['execution_disposition'] == 'DEFERRED_BY_SCOPE' for row in original['strategies'] if row['family'] == 'TRACK_A')
    assert all(row['status'] == 'RESEARCH_NOT_RUNNABLE' for row in original['strategies'] if row['family'] == 'G2G3')
