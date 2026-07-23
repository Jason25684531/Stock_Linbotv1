from sqlalchemy import create_engine

from core import db_helper


def test_pipeline_run_state_persists_step_success_and_answers_daily_update_ran():
    engine = create_engine('sqlite:///:memory:')

    db_helper.ensure_pipeline_run_state_schema(engine)
    db_helper.record_pipeline_step_start(
        pipeline_name='daily',
        step_name='update_database',
        run_date='2026-04-29',
        engine=engine,
    )
    db_helper.record_pipeline_step_finish(
        pipeline_name='daily',
        step_name='update_database',
        run_date='2026-04-29',
        status='success',
        trade_date='2026-04-28',
        rows_inserted=3200,
        rows_updated=25,
        engine=engine,
    )

    record = db_helper.get_pipeline_step_record(
        pipeline_name='daily',
        step_name='update_database',
        run_date='2026-04-29',
        engine=engine,
    )

    assert record is not None
    assert record['status'] == 'success'
    assert record['trade_date'] == '2026-04-28'
    assert record['rows_inserted'] == 3200
    assert record['rows_updated'] == 25
    assert record['started_at'] is not None
    assert record['finished_at'] is not None
    assert db_helper.did_pipeline_step_run_on_date(
        pipeline_name='daily',
        step_name='update_database',
        run_date='2026-04-29',
        engine=engine,
    ) is True


def test_pipeline_run_state_persists_failure_status_and_error_summary():
    engine = create_engine('sqlite:///:memory:')

    db_helper.ensure_pipeline_run_state_schema(engine)
    db_helper.record_pipeline_step_start(
        pipeline_name='daily',
        step_name='run_daily',
        run_date='2026-04-29',
        engine=engine,
    )
    db_helper.record_pipeline_step_finish(
        pipeline_name='daily',
        step_name='run_daily',
        run_date='2026-04-29',
        status='failed',
        trade_date='2026-04-28',
        error_summary='model output contained NaN scores',
        engine=engine,
    )

    record = db_helper.get_pipeline_step_record(
        pipeline_name='daily',
        step_name='run_daily',
        run_date='2026-04-29',
        engine=engine,
    )

    assert record is not None
    assert record['status'] == 'failed'
    assert record['trade_date'] == '2026-04-28'
    assert record['error_summary'] == 'model output contained NaN scores'
    assert record['finished_at'] is not None
