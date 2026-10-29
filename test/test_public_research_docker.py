"""Real Docker integration, synthetic isolated budget only. No test sends official HTTP or touches real Research state."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
import uuid

import pytest

from core.runtime import public_data as public, research_budget as rb

IMAGE = 'stock-linbot-research:public-data'
pytestmark = pytest.mark.integration


@pytest.fixture
def docker_image():
    if not shutil.which('docker'):
        pytest.skip('Docker CLI unavailable')
    check = subprocess.run(['docker', 'image', 'inspect', IMAGE, '--format', '{{.Id}}'], capture_output=True, text=True, timeout=20)
    if check.returncode:
        pytest.skip('Research test image unavailable; build Dockerfile.research first')
    return IMAGE


def command(image, script):
    authorization = Path(rb.STATE) / 'authorization'
    authorization.mkdir(exist_ok=True)
    return ['docker', 'run', '--rm', '--read-only', '--cap-drop=ALL', '--security-opt=no-new-privileges',
            '--mount', f'type=bind,src={Path(rb.STATE).resolve()},dst=/app/outputs/research_budget',
            '--mount', f'type=bind,src={authorization.resolve()},dst=/app/outputs/authorization',
            '--entrypoint', 'python', image, '-c', script]


def run(args):
    return subprocess.run(args, capture_output=True, text=True, check=True, timeout=45).stdout


def context():
    return public.context('https://www.twse.com.tw/rwd/zh/reducation/TWTAUU', params={'startDate': '20220101', 'endDate': '20261023', 'response': 'json'})


RESERVE = "from core.runtime import research_budget as rb, public_data as p; c=p.context('https://www.twse.com.tw/rwd/zh/reducation/TWTAUU',params={'startDate':'20220101','endDate':'20261023','response':'json'})\n"


def test_container_is_non_root_and_restart_preserves_budget(docker_image, public_budget):
    script = RESERVE + '''
import os,json
assert os.getuid()==10001
assert not os.environ.get('STOCK_LINBOT_GRANTS')
assert not os.path.exists('/app/.env') and not os.path.exists('/app/.claude/owner_grants.json')
if rb.status()['used']==7:
    rb.finish(rb.reserve(c),outcome='FAILED')
assert rb.status()['used']==8
print(json.dumps({'uid':os.getuid(),'used':rb.status()['used']}))
'''
    args = command(docker_image, script)
    args[1:3] = ['create']  # persistent container: start the same container twice, then a replacement
    container = run(args).strip()
    try:
        for _ in range(2):
            result = json.loads(run(['docker', 'start', '-a', container]))
            assert result == {'uid': 10001, 'used': 8}
        assert json.loads(run(command(docker_image, script)))['used'] == 8
    finally:
        subprocess.run(['docker', 'rm', '-f', container], capture_output=True, timeout=20, check=True)
    assert rb.status()['used'] == 8


def test_parallel_containers_cannot_exceed_the_last_use(docker_image, public_budget):
    for _ in range(5):
        rb.finish(rb.reserve(context()), outcome='FAILED')
    script = RESERVE + '''
try:
    rb.reserve(c)
    print('RESERVED')
except rb.BudgetDenied:
    print('DENIED')
'''
    processes = [subprocess.Popen(command(docker_image, script), stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True) for _ in range(4)]
    outcomes = []
    for process in processes:
        stdout, stderr = process.communicate(timeout=60)
        assert process.returncode == 0, stderr
        outcomes.append(stdout.strip())
    assert outcomes.count('RESERVED') == 1 and outcomes.count('DENIED') == 3
    assert rb.status()['jobs']['t1b']['used'] == 10


def test_native_and_container_compete_without_double_spending(docker_image, public_budget):
    for _ in range(5):
        rb.finish(rb.reserve(context()), outcome='FAILED')
    barrier = '''
from pathlib import Path
import time
root=Path(rb.STATE)
(root/READY).write_text('ready')
deadline=time.monotonic()+30
while not (root/'start').exists():
    if time.monotonic()>deadline: raise TimeoutError('test barrier timed out')
    time.sleep(0.01)
try:
    rb.reserve(c)
    print('RESERVED')
except rb.BudgetDenied:
    print('DENIED')
'''
    container_script = RESERVE + "READY='container-ready'\n" + barrier
    native_script = ('import sys\nfrom pathlib import Path\n' + RESERVE +
                     "rb.STATE=Path(sys.argv[1])\nREADY='native-ready'\n" + barrier)
    container = subprocess.Popen(command(docker_image, container_script), stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    native = subprocess.Popen([sys.executable, '-c', native_script, str(rb.STATE)], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    deadline = time.monotonic() + 30
    while not all((Path(rb.STATE) / name).exists() for name in ('container-ready', 'native-ready')):
        assert time.monotonic() < deadline, 'native/container test barrier failed'
        time.sleep(0.02)
    (Path(rb.STATE) / 'start').write_text('start')
    outcomes = []
    for process in (container, native):
        stdout, stderr = process.communicate(timeout=45)
        assert process.returncode == 0, stderr
        outcomes.append(stdout.strip())
    assert outcomes.count('RESERVED') == 1 and outcomes.count('DENIED') == 1
    assert rb.status()['jobs']['t1b']['used'] == 10


def test_real_container_policy_allows_public_fake_http_without_grants(docker_image, public_budget):
    script = RESERVE + '''
import requests,json
from core.runtime import operation_policy as op
from scripts import task_controller as tc
tc.grant_trusted=lambda *a: (_ for _ in ()).throw(AssertionError('grant trust consulted'))
sent=[]
def offline(*a,**k):
    sent.append(a)
    r=requests.Response(); r.status_code=200
    r._content=json.dumps({'stat':'OK','fields':['恢復買賣日期','股票代號','停止買賣前收盤價格','恢復買賣參考價'],'data':[['111/01/06','2330','100','90']]}).encode()
    return r
p._send=offline
assert op.current_mode().value=='RESEARCH'
assert p.request('GET',c.url,params=dict(c.parameters)).research_cache_hit is False
assert p.request('GET',c.url,params=dict(c.parameters)).research_cache_hit is True
assert len(sent)==1 and rb.status()['used']==8
print('MOCK_HTTP_IN_REAL_NONROOT_CONTAINER_PASS')
'''
    assert run(command(docker_image, script)).strip() == 'MOCK_HTTP_IN_REAL_NONROOT_CONTAINER_PASS'


def test_native_container_share_actual_sender_spacing(docker_image, public_budget):
    shared = '''
import json,time,requests
from pathlib import Path
from core.runtime import public_data as p,research_budget as rb,operation_policy as op
ROOT_ASSIGNMENT
op.LOG=Path(rb.STATE)/'test-decisions.jsonl'
def offline(*a,**kw):
    with (Path(rb.STATE)/'sends.jsonl').open('a') as output: output.write(json.dumps({'at':time.time()})+'\\n')
    r=requests.Response(); r.status_code=200
    r._content=json.dumps({'stat':'OK','fields':['恢復買賣日期','股票代號','停止買賣前收盤價格','恢復買賣參考價'],'data':[['111/01/06','2330','100','90']]}).encode()
    return r
p._send=offline
(Path(rb.STATE)/READY).write_text('ready')
deadline=time.monotonic()+30
while not (Path(rb.STATE)/'start').exists():
    if time.monotonic()>deadline: raise TimeoutError('test barrier')
    time.sleep(0.01)
p.request('GET','https://www.twse.com.tw/rwd/zh/'+ROUTE,params={'startDate':'20220101','endDate':'20261023','response':'json'})
print('SENT')
'''
    docker_script = shared.replace('ROOT_ASSIGNMENT', "READY='container-ready'\nROUTE='change/TWTB8U'")
    native_script = 'import sys\n' + shared.replace('ROOT_ASSIGNMENT', "rb.STATE=Path(sys.argv[1]); READY='native-ready'; ROUTE='reducation/TWTAUU'")
    processes = [subprocess.Popen(command(docker_image, docker_script), stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True),
                 subprocess.Popen([sys.executable, '-c', native_script, str(rb.STATE)], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)]
    deadline = time.monotonic() + 30
    while not all((Path(rb.STATE) / name).exists() for name in ('container-ready', 'native-ready')):
        assert time.monotonic() < deadline, 'native/container send barrier failed'
        time.sleep(0.02)
    (Path(rb.STATE) / 'start').write_text('start')
    for process in processes:
        stdout, stderr = process.communicate(timeout=45)
        assert process.returncode == 0 and stdout.strip() == 'SENT', stderr
    sends = [json.loads(row) for row in (Path(rb.STATE) / 'sends.jsonl').read_text().splitlines()]
    assert len(sends) == 2 and sends[1]['at'] - sends[0]['at'] >= 3.18
    assert rb.status()['used'] == 9
