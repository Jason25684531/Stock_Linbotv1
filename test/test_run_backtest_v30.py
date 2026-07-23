from jobs.run_backtest import BacktestEngine


def test_v30_exit_logic_uses_engine_params(monkeypatch):
    engine = BacktestEngine.__new__(BacktestEngine)
    engine.mode = 'v30'
    engine.strategy_obj = object()
    engine.take_profit_pct = 0.05
    engine.max_hold_days = 10
    engine.positions = {
        '2330': {
            'days': 0,
            'cost': 100.0,
            'stop_loss': 90.0,
            'highest': 100.0,
        }
    }

    sold = {}

    monkeypatch.setattr(engine, 'get_data', lambda sid, date_str: {'close_price': 106.0})
    monkeypatch.setattr(
        engine,
        'sell',
        lambda sid, price, date_str, reason: sold.setdefault('reason', reason),
    )

    engine.check_and_execute_exit('2330', '2026-03-26', 'BULL')

    assert sold['reason'] == '停利'