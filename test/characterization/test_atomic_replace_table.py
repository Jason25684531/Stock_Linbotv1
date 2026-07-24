"""驗證 _atomic_replace_table() 的原子替換語意，對象為專用的一次性測試表，
從不觸及真正的 backtest_trades/backtest_equity_curve。
"""

import pytest
from sqlalchemy import text

import core.db_helper as db_helper

_TEST_TABLE = 'test_atomic_replace_probe'


@pytest.fixture
def probe_table():
    engine = db_helper.get_db_engine()
    with engine.connect() as conn:
        conn.execute(text(f'DROP TABLE IF EXISTS `{_TEST_TABLE}`'))
        conn.execute(text(f'DROP TABLE IF EXISTS `{_TEST_TABLE}_staging`'))
        conn.execute(text(f'DROP TABLE IF EXISTS `{_TEST_TABLE}_old`'))
        conn.execute(text(f"""
            CREATE TABLE `{_TEST_TABLE}` (
                id BIGINT AUTO_INCREMENT PRIMARY KEY,
                label VARCHAR(64) NOT NULL,
                value INT NOT NULL
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """))
        conn.execute(
            text(f'INSERT INTO `{_TEST_TABLE}` (label, value) VALUES (:label, :value)'),
            [{'label': 'a', 'value': 1}, {'label': 'b', 'value': 2}],
        )
        conn.commit()

    yield _TEST_TABLE

    with engine.connect() as conn:
        conn.execute(text(f'DROP TABLE IF EXISTS `{_TEST_TABLE}`'))
        conn.execute(text(f'DROP TABLE IF EXISTS `{_TEST_TABLE}_staging`'))
        conn.execute(text(f'DROP TABLE IF EXISTS `{_TEST_TABLE}_old`'))
        conn.commit()


@pytest.mark.allow_real_backtest_persistence
def test_atomic_replace_swaps_full_content(probe_table):
    engine = db_helper.get_db_engine()
    with engine.connect() as conn:
        db_helper._atomic_replace_table(
            conn,
            probe_table,
            insert_columns=['label', 'value'],
            insert_records=[{'label': 'c', 'value': 99}],
        )
        conn.commit()

    with engine.connect() as conn:
        rows = conn.execute(text(f'SELECT label, value FROM `{probe_table}` ORDER BY label')).fetchall()
    assert [(r[0], r[1]) for r in rows] == [('c', 99)]

    with engine.connect() as conn:
        leftovers = conn.execute(
            text(
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_name IN (:staging, :old)"
            ),
            {'staging': f'{probe_table}_staging', 'old': f'{probe_table}_old'},
        ).fetchall()
    assert leftovers == []


@pytest.mark.allow_real_backtest_persistence
def test_atomic_replace_can_preserve_rows_matching_where(probe_table):
    engine = db_helper.get_db_engine()
    with engine.connect() as conn:
        db_helper._atomic_replace_table(
            conn,
            probe_table,
            insert_columns=['label', 'value'],
            insert_records=[{'label': 'c', 'value': 99}],
            preserve_where='label NOT IN (:excluded)',
            preserve_params={'excluded': 'a'},
        )
        conn.commit()

    with engine.connect() as conn:
        rows = conn.execute(text(f'SELECT label, value FROM `{probe_table}` ORDER BY label')).fetchall()
    assert [(r[0], r[1]) for r in rows] == [('b', 2), ('c', 99)]


@pytest.mark.allow_real_backtest_persistence
def test_atomic_replace_failure_leaves_original_table_intact(probe_table, monkeypatch):
    engine = db_helper.get_db_engine()

    class _BrokenConn:
        def __init__(self, real_conn):
            self._real = real_conn
            self._calls = 0

        def execute(self, statement, params=None):
            self._calls += 1
            text_str = str(statement)
            if 'INSERT INTO' in text_str and 'staging' in text_str:
                raise RuntimeError('simulated failure mid-replace')
            return self._real.execute(statement, params) if params is not None else self._real.execute(statement)

        def commit(self):
            self._real.commit()

        def rollback(self):
            self._real.rollback()

    with engine.connect() as real_conn:
        broken = _BrokenConn(real_conn)
        with pytest.raises(RuntimeError, match='simulated failure mid-replace'):
            db_helper._atomic_replace_table(
                broken,
                probe_table,
                insert_columns=['label', 'value'],
                insert_records=[{'label': 'c', 'value': 99}],
            )
        real_conn.rollback()

    with engine.connect() as conn:
        rows = conn.execute(text(f'SELECT label, value FROM `{probe_table}` ORDER BY label')).fetchall()
    assert [(r[0], r[1]) for r in rows] == [('a', 1), ('b', 2)]
