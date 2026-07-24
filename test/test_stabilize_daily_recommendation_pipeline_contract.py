from __future__ import annotations

from pathlib import Path
import subprocess


REPO_ROOT = Path(__file__).resolve().parents[1]
OPENSPEC_CHANGE_NAME = "stabilize-daily-recommendation-pipeline"


def _read_text(relative_path: str) -> str:
    return (REPO_ROOT / relative_path).read_text(encoding="utf-8")


def _check_ignore(relative_path: str) -> bool:
    return subprocess.run(
        ["git", "check-ignore", relative_path],
        cwd=REPO_ROOT,
        check=False,
    ).returncode == 0


def _resolve_openspec_change_root(change_name: str = OPENSPEC_CHANGE_NAME) -> Path:
    changes_root = REPO_ROOT / "openspec" / "changes"
    active_root = changes_root / change_name
    if active_root.exists():
        return active_root

    archive_root = changes_root / "archive"
    archived_matches = sorted(archive_root.glob(f"*-{change_name}")) if archive_root.exists() else []
    if archived_matches:
        return archived_matches[-1]

    raise AssertionError(f"OpenSpec change not found: {change_name}")


def test_config_defaults_non_root_db_url_and_model_path_contract():
    settings_text = _read_text("config/settings.py")

    assert 'os.getenv(\n        "DB_URL",' in settings_text
    assert "mysql+pymysql://trader:" in settings_text
    assert '"MODEL_PATH", "ML_Data/pkl/stock_ai_model.pkl"' in settings_text


def test_env_example_documents_non_root_db_url_and_model_path_contract():
    env_example = _read_text(".env.example")

    assert "DB_URL=mysql+pymysql://trader:" in env_example
    assert "MODEL_PATH=ML_Data/pkl/stock_ai_model.pkl" in env_example
    assert "APP_HEALTH_PATH=/health" in env_example
    assert "ENABLE_DAILY_BACKTEST_VALIDATION=false" in env_example
    assert "DAILY_BACKTEST_WINDOW_DAYS=60" in env_example
    assert "DAILY_BACKTEST_STRATEGIES=v34_turbo" in env_example
    assert "DAILY_BACKTEST_UNIVERSE=2330,2317,2454" in env_example
    assert "DAILY_BACKTEST_INITIAL_CAPITAL=1000000" in env_example


def test_docker_compose_uses_non_root_app_db_url_and_python_stdlib_healthchecks():
    compose_text = _read_text("docker-compose.yaml")

    assert "DB_URL: mysql+pymysql://trader:" in compose_text
    assert "urllib.request.urlopen('http://localhost:8080/health'" in compose_text
    assert "urllib.request.urlopen('http://localhost:1688/health'" in compose_text
    assert "curl " not in compose_text
    assert "wget " not in compose_text


def test_readme_documents_runtime_contract_and_healthcheck_boundary():
    readme = _read_text("README.md")

    assert "jobs/scheduler.py" in readme
    assert "jobs/update_database.py" in readme
    assert "jobs/run_daily.py" in readme
    assert "jobs/run_daily_backtest_validation.py" in readme
    assert "jobs/push_to_line.py" in readme
    assert "daily_recommendations" in readme
    assert "pipeline_runs" in readme
    assert "MODEL_PATH=ML_Data/pkl/stock_ai_model.pkl" in readme
    assert "DB_URL=mysql+pymysql://trader:" in readme
    assert "/health" in readme
    assert "/api/dashboard/health-check" in readme
    assert "not the container health endpoint" in readme
    assert "ENABLE_DAILY_BACKTEST_VALIDATION" in readme
    assert "DAILY_BACKTEST_WINDOW_DAYS" in readme
    assert "not full historical research backtesting" in readme
    assert "not parameter optimization" in readme
    assert "trader user" in readme


def test_openspec_change_artifacts_exist_with_fixed_delta_spec_paths():
    change_root = _resolve_openspec_change_root()
    expected_files = [
        "proposal.md",
        "design.md",
        "specs/scheduler-pipeline/spec.md",
        "specs/runtime-config/spec.md",
        "specs/database-config/spec.md",
        "specs/container-health/spec.md",
    ]

    missing = [relative_path for relative_path in expected_files if not (change_root / relative_path).exists()]
    assert not missing, f"Missing OpenSpec artifacts: {missing}"


def test_openspec_tasks_reference_targeted_verification_files():
    tasks_path = _resolve_openspec_change_root() / "tasks.md"
    if not tasks_path.exists():
        return

    tasks_text = tasks_path.read_text(encoding="utf-8")

    assert "test/test_run_daily_persistence.py" in tasks_text
    assert "test/test_recommendation_fallback.py" in tasks_text
    assert "test/test_recommendation_channel_sync.py" in tasks_text
    assert "test/test_dashboard_health_check_api.py" in tasks_text


def test_openspec_design_keeps_dashboard_health_check_boundary_explicit():
    design_text = (_resolve_openspec_change_root() / "design.md").read_text(encoding="utf-8")

    assert "/health" in design_text
    assert "/api/dashboard/health-check" in design_text
    assert "not the container health endpoint" in design_text

def test_openspec_change_artifacts_are_not_git_tracked():
    result = subprocess.run(
        [
            "git",
            "ls-files",
            "--",
            "openspec",
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=True,
    )

    tracked_files = [
        line.strip()
        for line in result.stdout.splitlines()
        if line.strip()
    ]

    assert not tracked_files, (
        "OpenSpec files should remain local and must not be tracked by Git: "
        f"{tracked_files}"
    )
