"""Check real job imports in a fresh interpreter, independently of collection."""
import subprocess
import sys
from pathlib import Path


def test_research_jobs_import_and_resolve_annotations():
    result = subprocess.run(
        [sys.executable, '-c', '''
from typing import get_type_hints
from jobs import run_factor_validation as factors, run_fundamental_pit_v3 as pit
for function in (factors._top_n_turnover, factors._evaluate_factor, pit._request_status, pit._copy_dataset_files):
    get_type_hints(function)
'''],
        cwd=Path(__file__).resolve().parents[1],
        capture_output=True, text=True, timeout=20,
    )
    assert result.returncode == 0, result.stderr
