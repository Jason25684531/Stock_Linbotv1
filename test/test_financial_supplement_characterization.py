import pandas as pd

from core import db_helper
from jobs import run_backtest, run_daily


class _NoopConnection:
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def execute(self, *args, **kwargs):
        return None

    def commit(self):
        return None


class _NoopEngine:
    def connect(self):
        return _NoopConnection()


def test_daily_financial_and_revenue_merges_fill_release_fields(monkeypatch):
    source = pd.DataFrame([
        {'stock_id': '2330', 'trade_date': '2026-05-27', 'close_price': 100.0},
        {'stock_id': '2317', 'trade_date': '2026-05-27', 'close_price': 80.0},
    ])

    def fake_read_sql(sql, conn, params=None):
        sql_text = str(sql)
        if 'FROM financial_statements' in sql_text:
            return pd.DataFrame([
                {
                    'stock_id': '2330',
                    'year': 2026,
                    'quarter': 1,
                    'revenue': 1000.0,
                    'rd_expense': 50.0,
                    'operating_expense': 800.0,
                    'operating_profit': 200.0,
                    'eps': 3.5,
                    'rd_ratio': 0.05,
                    'op_profit_margin': 0.2,
                }
            ])
        if 'FROM monthly_revenue' in sql_text:
            return pd.DataFrame([
                {'stock_id': '2330', 'revenue_yoy': 42.0, 'revenue': 1200.0, 'year': 2026, 'month': 4}
            ])
        raise AssertionError(sql_text)

    monkeypatch.setattr(run_daily.pd, 'read_sql', fake_read_sql)
    monkeypatch.setattr(run_daily, '_write_revenue_yoy_to_db', lambda df, engine: None)

    merged = run_daily.merge_financial_data(source.copy(), _NoopEngine())
    merged = run_daily.merge_revenue_data(merged, _NoopEngine())

    assert {'revenue_yoy', 'op_profit_margin', 'eps'}.issubset(merged.columns)
    assert merged.loc[merged['stock_id'] == '2330', 'revenue_yoy'].iloc[0] == 42.0
    assert merged.loc[merged['stock_id'] == '2330', 'op_profit_margin'].iloc[0] == 0.2
    assert merged.loc[merged['stock_id'] == '2330', 'eps'].iloc[0] == 3.5
    assert merged.loc[merged['stock_id'] == '2317', ['revenue_yoy', 'op_profit_margin', 'eps']].iloc[0].tolist() == [0.0, 0.0, 0.0]


def test_daily_financial_and_revenue_merges_fallback_when_tables_unavailable(monkeypatch):
    source = pd.DataFrame([
        {'stock_id': '2330', 'trade_date': '2026-05-27', 'close_price': 100.0},
    ])

    monkeypatch.setattr(run_daily.pd, 'read_sql', lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError('missing table')))
    monkeypatch.setattr(run_daily, '_write_revenue_yoy_to_db', lambda df, engine: None)

    merged = run_daily.merge_financial_data(source.copy(), _NoopEngine())
    merged = run_daily.merge_revenue_data(merged, _NoopEngine())

    assert merged[['revenue_yoy', 'op_profit_margin', 'eps']].iloc[0].tolist() == [0.0, 0.0, 0.0]


def test_realtime_supplement_adds_eps_when_op_margin_already_exists(monkeypatch):
    source = pd.DataFrame([
        {'stock_id': '2330', 'revenue_yoy': 10.0, 'op_profit_margin': 0.12},
    ])

    def fake_read_sql(sql, conn, params=None):
        sql_text = str(sql)
        if 'FROM financial_statements' in sql_text:
            return pd.DataFrame([
                {'stock_id': '2330', 'op_profit_margin': 0.25, 'eps': 8.5}
            ])
        raise AssertionError(f'unexpected query: {sql_text}')

    monkeypatch.setattr(db_helper, 'get_db_engine', lambda: _NoopEngine())
    monkeypatch.setattr(db_helper.pd, 'read_sql', fake_read_sql)

    supplemented = db_helper.supplement_financial_data(source.copy())

    assert {'revenue_yoy', 'op_profit_margin', 'eps'}.issubset(supplemented.columns)
    assert supplemented.loc[0, 'revenue_yoy'] == 10.0
    assert supplemented.loc[0, 'op_profit_margin'] == 0.12
    assert supplemented.loc[0, 'eps'] == 8.5


def test_realtime_supplement_fallback_adds_all_release_fields(monkeypatch):
    source = pd.DataFrame([{'stock_id': '2330'}])

    monkeypatch.setattr(db_helper, 'get_db_engine', lambda: _NoopEngine())
    monkeypatch.setattr(db_helper.pd, 'read_sql', lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError('missing table')))

    supplemented = db_helper.supplement_financial_data(source.copy())

    assert supplemented[['revenue_yoy', 'op_profit_margin', 'eps']].iloc[0].tolist() == [0, 0, 0]


def test_backtest_supplement_adds_eps_when_op_margin_already_exists(monkeypatch):
    captured = {}

    class CapturingStrategy:
        def filter_candidates(self, df):
            captured['df'] = df.copy()
            return df.head(1)

    engine = run_backtest.BacktestEngine.__new__(run_backtest.BacktestEngine)
    engine.engine = _NoopEngine()
    engine._revenue_cache = None
    engine._financial_cache = None
    engine.strategy_obj = CapturingStrategy()
    engine.model = None
    engine.features = []
    engine.mode = 'v35_innovation'

    market_rows = pd.DataFrame([
        {
            'stock_id': '2330',
            'trade_date': '2026-05-27',
            'close_price': 100.0,
            'volume': 100000,
            'revenue_yoy': 12.0,
            'op_profit_margin': 0.11,
        }
    ])

    def fake_read_sql(sql, conn, params=None):
        sql_text = str(sql)
        if 'FROM daily_market_data' in sql_text:
            return market_rows.copy()
        if 'FROM financial_statements' in sql_text:
            return pd.DataFrame([
                {'stock_id': '2330', 'op_profit_margin': 0.2, 'eps': 6.25}
            ])
        if 'FROM monthly_revenue' in sql_text:
            raise AssertionError('revenue_yoy was already present')
        raise AssertionError(sql_text)

    monkeypatch.setattr(run_backtest.pd, 'read_sql', fake_read_sql)

    candidates = engine.find_candidates('2026-05-27')

    assert candidates == ['2330'] or list(candidates['stock_id']) == ['2330']
    assert {'revenue_yoy', 'op_profit_margin', 'eps'}.issubset(captured['df'].columns)
    assert captured['df'].loc[0, 'op_profit_margin'] == 0.11
    assert captured['df'].loc[0, 'eps'] == 6.25
