"""Execute the batch file's actual health command against controlled responses."""
from pathlib import Path
import shutil
import subprocess

import pytest


@pytest.mark.skipif(shutil.which('powershell') is None, reason='Windows restart command')
@pytest.mark.parametrize('failed_port,status', [(None, 200), (8080, 503), (1688, 503), (8080, None), (1688, None)])
def test_restart_requires_both_health_endpoints(failed_port, status):
    source = (Path(__file__).resolve().parents[1] / 'execution/restart_services.bat').read_text(encoding='utf-8')
    line = next(line.strip() for line in source.splitlines() if 'foreach ($port' in line)
    command = line.split('-Command "', 1)[1].removesuffix('"')
    failure = 'throw "unavailable"' if status is None else f'return [pscustomobject]@{{StatusCode={status}}}'
    stub = f'''function Invoke-WebRequest {{
        param([switch]$UseBasicParsing, $Uri, $TimeoutSec)
        if ($Uri -like '*:{failed_port}/*') {{ {failure} }}
        [pscustomobject]@{{StatusCode=200}}
    }}; '''
    result = subprocess.run(['powershell', '-NoProfile', '-Command', stub + command], capture_output=True, text=True, timeout=10)
    assert result.returncode == (0 if failed_port is None else 1), result.stderr
