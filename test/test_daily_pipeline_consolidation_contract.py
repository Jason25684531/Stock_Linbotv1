from pathlib import Path

from jobs import scheduler


REPO_ROOT = Path(__file__).resolve().parents[1]


def test_scheduler_daily_pipeline_preserves_official_step_order():
    assert [step.target for step in scheduler.PIPELINES['daily']] == [
        'update_database',
        'run_daily',
        'daily_backtest_validation',
        'push_to_line',
    ]
    assert scheduler.JOBS['update_database'].script_path == 'jobs/update_database.py'
    assert scheduler.JOBS['run_daily'].script_path == 'jobs/run_daily.py'
    assert scheduler.JOBS['daily_backtest_validation'].script_path == 'jobs/run_daily_backtest_validation.py'
    assert scheduler.JOBS['push_to_line'].script_path == 'jobs/push_to_line.py'


def test_cleanup_inventory_exists_with_classification_and_evidence_fields():
    inventory_path = REPO_ROOT / 'docs' / 'cleanup_inventory.md'
    inventory_text = inventory_path.read_text(encoding='utf-8')

    assert 'active path' in inventory_text.lower()
    assert 'legacy compatibility' in inventory_text.lower()
    assert 'removable candidate' in inventory_text.lower()
    assert 'imports' in inventory_text.lower()
    assert 'cli references' in inventory_text.lower()
    assert 'readme/docs references' in inventory_text.lower()
    assert 'docker-compose references' in inventory_text.lower()
    assert 'scheduler references' in inventory_text.lower()
    assert 'test dependencies' in inventory_text.lower()
    assert 'openspec references' in inventory_text.lower()
    assert 'user-facing workflow references' in inventory_text.lower()
    assert 'jobs/scheduler.py' in inventory_text
    assert 'execution/daily_run.bat' in inventory_text
    assert '1_update_database.py' in inventory_text
    assert 'jobs/run_daily_backtest_validation.py' in inventory_text
    assert 'unknown / needs verification' in inventory_text.lower()
    assert 'deprecation marker' in inventory_text.lower()


def test_readme_documents_official_daily_flow_and_price_provenance():
    readme_text = (REPO_ROOT / 'README.md').read_text(encoding='utf-8')

    assert 'python jobs/scheduler.py daily' in readme_text
    assert 'jobs/update_database.py` -> `jobs/run_daily.py` -> `jobs/run_daily_backtest_validation.py` -> `jobs/push_to_line.py' in readme_text
    assert 'price_trade_date' in readme_text
    assert 'price_source_date' in readme_text
    assert 'price_basis' in readme_text
    assert 'price_data_source' in readme_text
    assert 'price_is_stale' in readme_text
    assert 'recommendation_close_price' in readme_text
    assert 'recommendation_trade_date' in readme_text
    assert 'recommendation_price_basis' in readme_text
    assert 'recommendation_is_stale' in readme_text
    assert 'trade_date' in readme_text
    assert 'source_date' in readme_text
    assert 'data_source' in readme_text
    assert 'is_stale' in readme_text


def test_legacy_compatibility_launchers_point_to_official_scheduler():
    launcher_paths = [
        '1_update_database.py',
        '2_rundaily.py',
        '3_train_model.py',
        '4_run_backtest.py',
        '5_push_to_line.py',
        '6_optimize_params.py',
    ]
    batch_paths = [
        'execution/daily_run.bat',
        'execution/morning_run.bat',
        'execution/evening_run.bat',
        'execution/run_manual.bat',
    ]

    for relative_path in launcher_paths:
        text = (REPO_ROOT / relative_path).read_text(encoding='utf-8')
        assert 'compatibility-only' in text.lower()
        assert 'jobs/scheduler.py' in text
        assert 'Do not remove' in text

    for relative_path in batch_paths:
        text = (REPO_ROOT / relative_path).read_text(encoding='utf-8')
        assert 'Compatibility-only' in text
        assert 'jobs\\scheduler.py' in text
        assert 'Do not remove' in text
