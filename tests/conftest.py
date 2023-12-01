import os
import signal
import subprocess
import time

import pytest



@pytest.fixture(scope='session')
def services():
    popen = subprocess.Popen(['supervisord', '-c', 'tests/supervisor.conf'])
    assert popen.wait() == 0
    time.sleep(3.0)
    yield
    pid = int(open('tests/supervisord.pid').read())
    os.kill(pid, signal.SIGTERM)
