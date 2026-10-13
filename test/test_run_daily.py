import pandas as pd

from jobs import run_daily


class _RecordingConnection:
    def __init__(self):
        self.executed = []
        self.commit_count = 0

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def execute(self, sql, params=None):
        self.executed.append((str(sql), params))

    def commit(self):
        self.commit_count += 1


class _RecordingEngine:
    def __init__(self):
        self.connection = _RecordingConnection()

    def connect(self):
        return self.connection


def test_run_strategy_writes_heartbeat_when_candidates_empty():
    class FakeStrategy:
        name = 'v38_value_dividend'
        display_name = 'V38'
        target_return = 10
        look_ahead_days = 20
        stop_loss = 0.08
        features = []

        def filter_candidates(self, df):
            return pd.DataFrame(columns=df.columns)

    engine = _RecordingEngine()
    market_df = pd.DataFrame([
        {'stock_id': '2330', 'close_price': 952.0, 'rsi': 59.0, 'volume': 200000}
    ])

    result = run_daily.run_strategy(FakeStrategy(), market_df, '2026-04-15', engine)

    assert result.empty
    insert_calls = [
        params for sql, params in engine.connection.executed
        if 'INSERT INTO daily_recommendations' in sql
    ]
    assert len(insert_calls) == 1
    assert insert_calls[0]['stock_id'] == 'NONE'
    assert insert_calls[0]['strategy'] == 'v38_value_dividend'
    assert insert_calls[0]['date'] == '2026-04-15'


def test_compute_indicators_from_history_supports_current_pandas_groupby_apply(monkeypatch):
    rows = []
    for stock_id, base in [('2330', 100.0), ('2317', 80.0)]:
        for day in range(1, 16):
            rows.append(
                {
                    'stock_id': stock_id,
                    'trade_date': f'2026-05-{day:02d}',
                    'open_price': base + day,
                    'high_price': base + day + 1,
                    'low_price': base + day - 1,
                    'close_price': base + day,
                    'volume': 1000 + day,
                    'foreign_buy': 1,
                    'trust_buy': 0,
                    'dealer_buy': 0,
                }
            )

    monkeypatch.setattr(run_daily.pd, 'read_sql', lambda sql, engine, params=None: pd.DataFrame(rows))
    monkeypatch.setattr(run_daily, '_write_indicators_to_db', lambda df, engine: None)

    result = run_daily.compute_indicators_from_history('2026-05-15', object())

    assert not result.empty
    assert {'kd_k', 'atr', 'natr'}.issubset(result.columns)


def test_daily_recovery_ignores_anomalies_without_changing_clean_indicators(monkeypatch):
    dates = pd.bdate_range(end='2026-10-13', periods=61).difference(pd.DatetimeIndex(['2026-10-09']))
    rows = [{'stock_id': sid, 'trade_date': day, 'open_price': 100 + i,
             'high_price': 102 + i, 'low_price': 98 + i, 'close_price': 101 + i,
             'volume': 1000 + i, 'foreign_buy': 1, 'trust_buy': 0, 'dealer_buy': 0}
            for sid in ('2330', '2317') for i, day in enumerate(dates)]
    clean = pd.DataFrame(rows)
    monkeypatch.setattr(run_daily.pd, 'read_sql', lambda *a, **kw: clean.copy())
    expected = run_daily.compute_indicators_from_history('2026-10-13', object(), dry_run=True)
    extra = [dict(rows[0], trade_date=pd.Timestamp('2026-10-11'), close_price=99999),
             dict(rows[-1], trade_date=pd.Timestamp('2026-10-09'), close_price=99999),
             dict(rows[-1], stock_id='123456'), rows[0]]
    dirty = pd.concat([clean, pd.DataFrame(extra)], ignore_index=True)
    monkeypatch.setattr(run_daily.pd, 'read_sql', lambda *a, **kw: dirty.copy())
    actual = run_daily.compute_indicators_from_history('2026-10-13', object(), dry_run=True)
    pd.testing.assert_frame_equal(actual.reset_index(drop=True), expected.reset_index(drop=True),
                                  check_exact=False, rtol=1e-9, atol=1e-12)
