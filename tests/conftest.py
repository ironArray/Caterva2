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

    logs_dir = var_dir / 'supervisord-logs'
    logs_dir.mkdir(exist_ok=not purge_var)

    pid_file = var_dir / 'supervisord.pid'

    popen = subprocess.Popen([
        'supervisord',
        '-c', tests_dir / 'supervisor.conf',
        '-l', var_dir / 'supervisord.log',
        '-q', logs_dir,
        '-j', pid_file])
    assert popen.wait() == 0
    time.sleep(3.0)
    yield
    pid = int(pid_file.read_text())
    os.kill(pid, signal.SIGTERM)
