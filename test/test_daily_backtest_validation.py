from sqlalchemy import create_engine, text

from core import db_helper
from jobs import scheduler


def test_daily_backtest_validation_records_not_configured_when_disabled(monkeypatch):
    from jobs import run_daily_backtest_validation as validation

    engine = create_engine('sqlite:///:memory:')
    config = validation.DailyBacktestValidationConfig(
        enabled=False,
        window_days=60,
        strategies=('v34_turbo',),
        universe=('2330', '2317'),
    )

    monkeypatch.setattr(validation, 'get_daily_backtest_validation_config', lambda: config)

    result = validation.run_daily_backtest_validation(
        pipeline_name='daily',
        run_date='2026-05-19',
        engine=engine,
    )

    record = db_helper.get_pipeline_step_record(
        pipeline_name='daily',
        step_name='lightweight_backtest_validation',
        run_date='2026-05-19',
        engine=engine,
    )

    assert result['status'] == 'not_configured'
    assert result['status_code'] == 0
    assert record is not None
    assert record['status'] == 'not_configured'
    assert 'disabled' in (record['error_summary'] or '').lower()


def test_daily_backtest_validation_records_success_summary_when_enabled(monkeypatch):
    from jobs import run_daily_backtest_validation as validation

    engine = create_engine('sqlite:///:memory:')
    config = validation.DailyBacktestValidationConfig(
        enabled=True,
        window_days=60,
        strategies=('v34_turbo', 'v36_chip_momentum'),
        universe=('2330', '2317'),
    )

    monkeypatch.setattr(validation, 'get_daily_backtest_validation_config', lambda: config)
    monkeypatch.setattr(validation, '_resolve_validation_end_date', lambda engine=None: '2026-05-19')
    monkeypatch.setattr(validation, '_resolve_validation_start_date', lambda end_date, window_days, engine=None: '2026-03-20')
    monkeypatch.setattr(
        validation,
        '_validate_market_data_window',
        lambda start_date, end_date, universe, engine=None: {
            'universe_size': 2,
            'missing_price_count': 0,
            'stale_price_count': 0,
            'alignment_issue_count': 0,
            'anomaly_flags': [],
        },
    )
    monkeypatch.setattr(
        validation,
        '_run_strategy_backtest_validations',
        lambda start_date, end_date, strategies: {
            'strategy_count': 2,
            'trades_evaluated': 18,
            'nan_result_count': 0,
            'impossible_return_count': 0,
            'anomaly_flags': [],
        },
    )

    result = validation.run_daily_backtest_validation(
        pipeline_name='daily',
        run_date='2026-05-19',
        engine=engine,
    )

    record = db_helper.get_pipeline_step_record(
        pipeline_name='daily',
        step_name='lightweight_backtest_validation',
        run_date='2026-05-19',
        engine=engine,
    )

    assert result['status'] == 'success'
    assert result['status_code'] == 0
    assert result['summary']['strategy_count'] == 2
    assert result['summary']['window_days'] == 60
    assert result['summary']['validation_end_date'] == '2026-05-19'
    assert record is not None
    assert record['status'] == 'success'
    assert record['trade_date'] == '2026-05-19'
    assert 'strategy_count' in (record['error_summary'] or '')


def test_daily_backtest_validation_records_failure_without_touching_recommendations(monkeypatch):
    from jobs import run_daily_backtest_validation as validation

    engine = create_engine('sqlite:///:memory:')
    with engine.begin() as conn:
        conn.execute(text('CREATE TABLE daily_recommendations (id INTEGER PRIMARY KEY AUTOINCREMENT, stock_id TEXT)'))
        conn.execute(text("INSERT INTO daily_recommendations (stock_id) VALUES ('2330')"))

    config = validation.DailyBacktestValidationConfig(
        enabled=True,
        window_days=60,
        strategies=('v34_turbo',),
        universe=('2330',),
    )

    monkeypatch.setattr(validation, 'get_daily_backtest_validation_config', lambda: config)
    monkeypatch.setattr(validation, '_resolve_validation_end_date', lambda engine=None: '2026-05-19')
    monkeypatch.setattr(validation, '_resolve_validation_start_date', lambda end_date, window_days, engine=None: '2026-03-20')
    monkeypatch.setattr(
        validation,
        '_validate_market_data_window',
        lambda start_date, end_date, universe, engine=None: {
            'universe_size': 1,
            'missing_price_count': 0,
            'stale_price_count': 0,
            'alignment_issue_count': 0,
            'anomaly_flags': [],
        },
    )

    def _raise_validation_failure(start_date, end_date, strategies):
        raise RuntimeError('NaN result detected during validation')

    monkeypatch.setattr(validation, '_run_strategy_backtest_validations', _raise_validation_failure)

    result = validation.run_daily_backtest_validation(
        pipeline_name='daily',
        run_date='2026-05-19',
        engine=engine,
    )

    record = db_helper.get_pipeline_step_record(
        pipeline_name='daily',
        step_name='lightweight_backtest_validation',
        run_date='2026-05-19',
        engine=engine,
    )
    with engine.connect() as conn:
        recommendation_count = conn.execute(text('SELECT COUNT(*) FROM daily_recommendations')).scalar()

    assert result['status'] == 'failed'
    assert result['status_code'] == 1
    assert record is not None
    assert record['status'] == 'failed'
    assert 'NaN result detected' in (record['error_summary'] or '')
    assert recommendation_count == 1


def test_scheduler_daily_pipeline_runs_validation_before_push_and_returns_failure_code(monkeypatch):
    executed_targets = []

    def fake_run_job(target, extra_args=None, *, pipeline_name=None, pipeline_run_date=None):
        executed_targets.append(target)
        if target == 'daily_backtest_validation':
            return 1
        return 0

    monkeypatch.setattr(scheduler, 'run_job', fake_run_job)

    exit_code = scheduler.run_pipeline('daily', stop_on_error=False)

    assert executed_targets == [
        'update_database',
        'run_daily',
        'daily_backtest_validation',
        'push_to_line',
    ]
    assert exit_code == 1
