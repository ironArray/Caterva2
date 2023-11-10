import json
import os
import signal
import subprocess
import time

import httpx
import pytest


@pytest.fixture(scope='session')
def services():
    popen = subprocess.Popen(['supervisord', '-c', 'tests/supervisor.conf'])
    assert popen.wait() == 0
    time.sleep(2.5)
    yield
    pid = int(open('tests/supervisord.pid').read())
    os.kill(pid, signal.SIGTERM)


def test_broker(services):
    response = httpx.get('http://localhost:8000/api/datasets')
    assert response.status_code == 200


def test_cli_list_all():
    ret = subprocess.run(['./venv/bin/python', './src/cli.py', 'list', '-a'], capture_output=True, text=True)
    assert ret.returncode == 0
    out = json.loads(ret.stdout)
    assert out == ['foo/precip.b2nd', 'foo/temp.b2nd', 'foo/wind.b2nd']
