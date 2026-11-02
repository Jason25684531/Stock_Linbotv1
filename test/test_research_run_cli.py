import json
from pathlib import Path
import subprocess
import sys


def test_run_repair_without_development_config_is_blocked():
    root = Path(__file__).resolve().parents[1]
    process = subprocess.run([sys.executable, str(root / 'scripts/research_workflow.py'), 'run-repair',
                              '--hypothesis', 'A191_132:PIT-RAW-ACTION-12CFG-V1'],
                             cwd=root, capture_output=True, text=True, encoding='utf-8')
    assert process.returncode == 2
    assert json.loads(process.stdout)['status'] == 'BLOCKED_MISSING_RUN_CONFIG'
