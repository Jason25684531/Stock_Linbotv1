import pandas as pd

from jobs import run_daily


class _FakeStrategy:
    def __init__(self, name: str, display_name: str, rows: list[dict]):
        self.name = name
        self.display_name = display_name
        self.target_return = 10
        self.look_ahead_days = 5
        self.features = []
        self._rows = rows

    def filter_candidates(self, df):
        return pd.DataFrame(self._rows)


def test_run_daily_for_date_persists_every_persistence_strategy(monkeypatch):
    persisted_calls = []
    persistence_strategies = [
        _FakeStrategy(
            'v31_hybrid',
            'V31',
            [
                {
                    'stock_id': '2330',
                    'close_price': 950.0,
                    'ai_score': 0.81,
                    'rsi': 58.0,
                    'volume': 150000,
                    'op_profit_margin': 0.1,
                }
            ],
        ),
        _FakeStrategy('v33_low_vol', 'V33', []),
    ]

    class _FakeManager:
        def get_persistence_strategies(self):
            return persistence_strategies

        def get_persistence_strategy_names(self):
            return [strategy.name for strategy in persistence_strategies]

        def get_active_strategy_names(self):
            return ['v34_turbo']

    monkeypatch.setattr(run_daily, 'StrategyManager', _FakeManager)
    monkeypatch.setattr(run_daily, 'get_db_engine', lambda: object())
    monkeypatch.setattr(run_daily, 'get_latest_trade_date', lambda: '2026-04-28')
    monkeypatch.setattr(
        run_daily,
        'compute_indicators_from_history',
        lambda date_str, engine: pd.DataFrame([
            {'stock_id': '2330', 'trade_date': pd.Timestamp('2026-04-28'), 'close_price': 950.0}
        ]),
    )
    monkeypatch.setattr(run_daily, 'calculate_ratio_features', lambda df: df)
    monkeypatch.setattr(run_daily, 'merge_financial_data', lambda df, engine: df)
    monkeypatch.setattr(run_daily, 'merge_revenue_data', lambda df, engine: df)
    monkeypatch.setattr(run_daily.Config, 'NEWS_BOOST_ENABLED', False)
    monkeypatch.setattr(run_daily, 'load_strategy_model', lambda strategy_name: (None, None))

    def fake_persist(candidates, strategy_name, date_str, engine):
        persisted_calls.append((strategy_name, date_str, len(candidates)))
        wrote_heartbeat = candidates is None or candidates.empty
        return (1 if wrote_heartbeat else len(candidates.head(10))), wrote_heartbeat

    monkeypatch.setattr(run_daily, '_persist_strategy_recommendations', fake_persist)

    summary = run_daily.run_daily_for_date('2026-04-28')

    assert summary['strategy_names'] == ['v31_hybrid', 'v33_low_vol']
    assert summary['active_strategy_names'] == ['v34_turbo']
    assert persisted_calls == [
        ('v31_hybrid', '2026-04-28', 1),
        ('v33_low_vol', '2026-04-28', 0),
    ]


def test_persist_strategy_recommendations_writes_single_heartbeat_for_empty_candidates():
    executed = []
    committed = []

    class _FakeConnection:
        def execute(self, sql, params=None):
            executed.append((str(sql), params))
            return None

        def commit(self):
            committed.append(True)

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

    class _FakeEngine:
        def connect(self):
            return _FakeConnection()

    written_count, wrote_heartbeat = run_daily._persist_strategy_recommendations(
        pd.DataFrame(),
        'v38_value_dividend',
        '2026-04-28',
        _FakeEngine(),
    )

    insert_calls = [params for sql, params in executed if 'INSERT INTO daily_recommendations' in sql]
    delete_calls = [params for sql, params in executed if 'DELETE FROM daily_recommendations' in sql]

    assert written_count == 1
    assert wrote_heartbeat is True
    assert len(delete_calls) == 1
    assert len(insert_calls) == 1
    assert insert_calls[0]['stock_id'] == run_daily.RECOMMENDATION_HEARTBEAT_STOCK_ID
    assert insert_calls[0]['strategy'] == 'v38_value_dividend'
    assert insert_calls[0]['date'] == '2026-04-28'