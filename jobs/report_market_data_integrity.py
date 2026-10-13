"""Print a read-only integrity summary for configured market data."""
from __future__ import annotations

import json
from collections import Counter
from datetime import date

from sqlalchemy import text

from core.db_helper import get_db_engine, normalize_date_str


def _latest_snapshot(conn, table_name: str) -> dict:
    total_rows = conn.execute(text(f'SELECT COUNT(*) FROM {table_name}')).scalar() or 0
    latest = conn.execute(text(
        f'SELECT trade_date, COUNT(*) FROM {table_name} '
        'GROUP BY trade_date ORDER BY trade_date DESC LIMIT 1'
    )).first()
    return {
        'total_rows': int(total_rows),
        'latest_date': normalize_date_str(latest[0]) if latest else None,
        'latest_date_rows': int(latest[1]) if latest else 0,
    }


def run_integrity_audit(engine=None, sample_limit: int = 10) -> dict:
    """Query counts and example keys only; this function never writes data."""
    engine = engine or get_db_engine()
    sample_limit = max(1, int(sample_limit))
    with engine.connect() as conn:
        market = _latest_snapshot(conn, 'daily_market_data')
        recommendations = _latest_snapshot(conn, 'daily_recommendations')
        duplicate_group_count = conn.execute(text(
            'SELECT COUNT(*) FROM ('
            'SELECT stock_id, trade_date FROM daily_market_data '
            'GROUP BY stock_id, trade_date HAVING COUNT(*) > 1'
            ') duplicate_keys'
        )).scalar() or 0
        duplicate_samples = conn.execute(text(
            'SELECT stock_id, trade_date, COUNT(*) AS row_count '
            'FROM daily_market_data GROUP BY stock_id, trade_date '
            'HAVING COUNT(*) > 1 ORDER BY trade_date DESC, stock_id LIMIT :sample_limit'
        ), {'sample_limit': sample_limit}).mappings().all()

        weekend_rows = 0
        weekend_dates = []
        malformed_date_rows = 0
        for raw_date, row_count in conn.execute(text(
            'SELECT trade_date, COUNT(*) FROM daily_market_data '
            'GROUP BY trade_date ORDER BY trade_date DESC'
        )).all():
            normalized = normalize_date_str(raw_date)
            if normalized is None:
                malformed_date_rows += int(row_count)
            elif date.fromisoformat(normalized).weekday() >= 5:
                weekend_rows += int(row_count)
                weekend_dates.append({'trade_date': normalized, 'row_count': int(row_count)})

        invalid_lengths: Counter[str] = Counter()
        for stock_id, row_count in conn.execute(text(
            'SELECT stock_id, COUNT(*) FROM daily_market_data GROUP BY stock_id'
        )).all():
            length = len(str(stock_id or '').strip())
            if length not in {4, 5, 6, 7}:
                invalid_lengths[str(length)] += int(row_count)
        invalid_identifier_samples = conn.execute(text(
            'SELECT stock_id, trade_date FROM daily_market_data '
            'WHERE stock_id IS NULL OR LENGTH(TRIM(stock_id)) NOT IN (4, 5, 6, 7) '
            'ORDER BY trade_date DESC, stock_id LIMIT :sample_limit'
        ), {'sample_limit': sample_limit}).mappings().all()

    return {
        'read_only': True,
        'market_data': market,
        'recommendations': recommendations,
        'duplicate_key_group_count': int(duplicate_group_count),
        'duplicate_key_samples': [dict(row) for row in duplicate_samples],
        'weekend_row_count': weekend_rows,
        'weekend_date_samples': weekend_dates[:sample_limit],
        'malformed_trade_date_row_count': malformed_date_rows,
        'invalid_identifier_length_row_count': sum(invalid_lengths.values()),
        'invalid_identifier_length_rows_by_length': dict(sorted(invalid_lengths.items())),
        'invalid_identifier_length_key_samples': [dict(row) for row in invalid_identifier_samples],
    }


if __name__ == '__main__':
    print(json.dumps(run_integrity_audit(), ensure_ascii=False, indent=2, default=str))
