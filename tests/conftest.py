import shutil
import subprocess
import time

import pytest

from pathlib import Path


def _supervisor_cmd(prog, *args, conf=None):
    return subprocess.check_output([prog, '-c', conf, *args])


def supervisor_start(*args, conf=None):
    return _supervisor_cmd('supervisord', *args, conf=conf)


def supervisor_send(*args, conf=None):
    return _supervisor_cmd('supervisorctl', *args, conf=conf)


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
    purge_var = True  # toggle to keep the state directory on start
    start_timeout_secs = 10

    tests_dir = Path(__file__).parent

    var_dir = tests_dir / 'caterva2'
    if purge_var and var_dir.is_dir():
        shutil.rmtree(var_dir)
    var_dir.mkdir(exist_ok=not purge_var)

    data_dir = var_dir / 'data'
    if not data_dir.is_dir() and not data_dir.is_symlink():
        examples_dir = tests_dir.parent / 'root-example'
        data_dir.symlink_to(examples_dir, target_is_directory=True)

    conf_file = tests_dir / 'supervisor.conf'

    logs_dir = var_dir / 'supervisord-logs'
    logs_dir.mkdir(exist_ok=not purge_var)

    supervisor_start('-l', var_dir / 'supervisord.log',
                     '-q', logs_dir,
                     '-j', var_dir / 'supervisord.pid',
                     conf=conf_file)

    try:
        wait_for_programs(start_timeout_secs,
                          lambda: supervisor_send('status', conf=conf_file))
        yield
    finally:
        try:
            supervisor_send('shutdown', conf=conf_file)
        except:
            pass
