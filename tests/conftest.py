import os
import shutil
import signal
import subprocess
import time

import pytest

from pathlib import Path


def supervisor_send(*args, conf=None):
    return subprocess.check_output([
        'supervisorctl',
        '-c', conf,
        *args])


def wait_for_programs(start_timeout_secs, get_status):
    start_sleep_secs = 1
    for _ in range(start_timeout_secs // start_sleep_secs):
        time.sleep(start_sleep_secs)
        status = get_status()
        if all(l.split()[1] == b'RUNNING' for l in status.splitlines()):
            break
    else:
        progs = b" ".join(p for (p, s) in (l.split()[:2] for l in status.splitlines())
                          if s != b'RUNNING').decode()
        raise RuntimeError(f"test programs failed to start on time: {progs}")


@pytest.fixture(scope='session')
def services():
    tests_dir = Path('tests')
    purge_var = False  # toggle to start with an empty state directory
    start_timeout_secs = 10

    data_dir = tests_dir / 'data'
    if not data_dir.is_dir() and not data_dir.is_symlink():
        data_dir.symlink_to('../root-example', target_is_directory=True)

    var_dir = tests_dir / 'var'
    if purge_var and var_dir.is_dir():
        shutil.rmtree(var_dir)
    var_dir.mkdir(exist_ok=not purge_var)

    conf_file = tests_dir / 'supervisor.conf'

    logs_dir = var_dir / 'supervisord-logs'
    logs_dir.mkdir(exist_ok=not purge_var)

    pid_file = var_dir / 'supervisord.pid'

    subprocess.check_call([
        'supervisord',
        '-c', conf_file,
        '-l', var_dir / 'supervisord.log',
        '-q', logs_dir,
        '-j', pid_file])

    try:
        wait_for_programs(start_timeout_secs,
                          lambda: supervisor_send('status', conf=conf_file))
        yield
    finally:
        try:
            pid = int(pid_file.read_text())
        except:
            pass
        else:
            os.kill(pid, signal.SIGTERM)
