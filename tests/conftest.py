import os
import shutil
import signal
import subprocess
import time

import pytest

from pathlib import Path



@pytest.fixture(scope='session')
def services():
    tests_dir = Path('tests')
    purge_var = False  # toggle to start with an empty state directory

    data_dir = tests_dir / 'data'
    if not data_dir.is_dir() and not data_dir.is_symlink():
        data_dir.symlink_to('../root-example', target_is_directory=True)

    var_dir = tests_dir / 'var'
    if purge_var and var_dir.is_dir():
        shutil.rmtree(var_dir)
    var_dir.mkdir(exist_ok=not purge_var)
    (var_dir / 'supervisord-logs').mkdir(exist_ok=not purge_var)

    popen = subprocess.Popen(['supervisord', '-c', tests_dir / 'supervisor.conf'])
    assert popen.wait() == 0
    time.sleep(3.0)
    yield
    pid = int((var_dir / 'supervisord.pid').read_text())
    os.kill(pid, signal.SIGTERM)
