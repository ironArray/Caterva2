import os
import signal
import subprocess
import time

import pytest

from pathlib import Path



@pytest.fixture(scope='session')
def services():
    tests_dir = Path('tests')

    data_dir = tests_dir / 'data'
    if not data_dir.is_dir() and not data_dir.is_symlink():
        data_dir.symlink_to('../root-example', target_is_directory=True)

    popen = subprocess.Popen(['supervisord', '-c', 'tests/supervisor.conf'])
    assert popen.wait() == 0
    time.sleep(3.0)
    yield
    pid = int(open('tests/supervisord.pid').read())
    os.kill(pid, signal.SIGTERM)
