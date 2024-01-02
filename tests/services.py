import os
import signal
import subprocess
import sys

import pytest

from pathlib import Path


DEFAULT_STATE_DIR = '_caterva2'
TEST_STATE_DIR = DEFAULT_STATE_DIR + '_tests'


class Services:
    def __init__(self, state_dir):
        self.state_dir = Path(state_dir).resolve()
        self.data_dir = self.state_dir / 'data'
        self.src_dir = Path(__file__).parent.parent / 'src'

    def start_all(self):
        self.state_dir.mkdir(exist_ok=True)
        self.data_dir.mkdir(exist_ok=True)
        os.environ['CATERVA2_SOURCE'] = str(self.src_dir.parent)

        self._bro = subprocess.Popen([sys.executable,
                                      self.src_dir / 'bro.py',
                                      '--statedir=%s' % (self.state_dir / 'bro')])
        self._pub = subprocess.Popen([sys.executable,
                                      self.src_dir / 'pub.py',
                                      '--statedir=%s' % (self.state_dir / 'pub'),
                                      'foo', self.data_dir])
        self._sub = subprocess.Popen([sys.executable,
                                      self.src_dir / 'sub.py',
                                      '--statedir=%s' % (self.state_dir / 'sub')])

    def stop_all(self):
        for proc in [self._sub, self._pub, self._bro]:
            os.kill(proc.pid, signal.SIGTERM)

    def wait_for_all(self):
        self._sub.wait()
        self._pub.wait()
        self._bro.wait()


@pytest.fixture(scope='session')
def services():
    srvs = Services(TEST_STATE_DIR)
    srvs.start_all()
    yield srvs
    srvs.stop_all()
    srvs.wait_for_all()


def main(argv, env):
    state_dir = argv[1] if len(argv) >= 2 else DEFAULT_STATE_DIR
    srvs = Services(state_dir)
    srvs.start_all()
    srvs.wait_for_all()


if __name__ == '__main__':
    main(sys.argv, os.environ)
