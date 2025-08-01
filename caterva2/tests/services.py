"""Caterva2 services for tests.

This ensures that Caterva2 subscriber service is running before proceeding to tests.
It has three modes of operation:

- Standalone script: when run as a script, it starts the services as children
  and makes sure that they are available to other local programs.  If given an
  argument, it uses it as the directory to store state in; otherwise it uses
  the value in `DEFAULT_STATE_DIR`.  If the directory does not exist, it is
  created and populated with example datasets.  If further arguments are
  given, each one is taken as a root description ``[ROOT_NAME=]ROOT_SOURCE``
  with an optional root name (`TEST_DEFAULT_ROOT` by default) and the source
  of example datasets to be copied.

  Terminating the program stops the services.

  Usage example::

      $ cd Caterva2
      $ python -m caterva2.tests.services &  # state in ``_caterva2``
      [3] 12345
      $ pytest
      $ kill %3

- pytest fixture with external services: when using `services()` as a fixture,
  it checks that the services are available to other local programs.  It does
  not tamper with the state directory nor stop the services when tests finish.

  Usage example: same as above (but on the pytest side).
"""

import collections
import functools
import itertools
import logging
import os
import pathlib
import re
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path

import httpx
import pytest

BASE_DIR = pathlib.Path(__file__).resolve().parent
DEFAULT_STATE_DIR = "_caterva2"
TEST_STATE_DIR = DEFAULT_STATE_DIR + "_tests"
TEST_DEFAULT_ROOT = "foo"
TEST_CATERVA2_ROOT = "@public"

local_port_iter = itertools.count(8100)
logger = logging.getLogger("tests")


def service_ep_getter(first):
    def get_service_ep():
        nonlocal first
        if first is not None:
            ep, first = first, None
            return ep
        return f"localhost:{next(local_port_iter)}"

    return get_service_ep


get_sub_ep = service_ep_getter("localhost:8000")


def make_get_http(host, path="/"):
    def check():
        url = f"http://{host}{path}"
        try:
            r = httpx.get(url, timeout=0.5)
            return 100 <= r.status_code < 500  # not internal errors
        except httpx.ConnectError:
            return False

    check.__name__ = f"get_http_{host}"  # more descriptive
    check.host = host  # to get final value
    return check


def http_service_check(conf, conf_sect, def_host, path):
    return make_get_http(conf.get(f"{conf_sect}.http", def_host), path)


def sub_check(conf):
    return http_service_check(conf, "subscriber", get_sub_ep(), "/api/roots")


TestRoot = collections.namedtuple("TestRoot", ["name", "source"])


class ManagedServices:
    def __init__(self, state_dir, reuse_state=True, roots=None, configuration=None):
        super().__init__()

        self.state_dir = Path(state_dir).resolve()
        self.reuse_state = reuse_state
        self.roots = list(roots)
        self.configuration = configuration

        self._procs = {}
        self._endpoints = {}
        self._setup_done = False

    def _start_proc(self, name, *args, check=None):
        if check is not None and check():
            raise RuntimeError(
                f'check for service "{name}" succeeded before start'
                f" (external service running?): {check.__name__}"
            )

        if os.environ.get("CATERVA2_SECRET"):
            conf_file = "caterva2-login.toml"
        else:
            conf_file = "caterva2-nologin.toml"

        popen_args = [
            sys.executable,
            f"-mcaterva2.services.{name[:3]}",
            f"--conf={BASE_DIR / conf_file}",
            f"--statedir={self.state_dir / name}",
            *([f"--http={check.host}"] if check else []),
            *args,
        ]
        popen = subprocess.Popen(popen_args, stdout=sys.stdout, stderr=sys.stderr)
        command_line = " ".join(str(x) for x in popen_args)
        self._procs[name] = popen

        if check is None:
            return

        self._endpoints[name] = check.host

        start_timeout_secs = 10
        start_sleep_secs = 1
        for _ in range(int(start_timeout_secs / start_sleep_secs)):
            returncode = popen.poll()
            if returncode is not None:
                raise RuntimeError(
                    f"service {name} failed with returncode={returncode}, " f"command = {command_line}"
                )
            time.sleep(start_sleep_secs)
            if check():
                break
        else:
            raise RuntimeError(
                f"service {name} timeout after {start_timeout_secs} seconds, " f"command = {command_line}"
            )

    def _get_data_path(self, root):
        return self.state_dir / f"data.{root.name}"

    def _setup(self):
        if self._setup_done:
            return

        if not self.reuse_state and self.state_dir.is_dir():
            shutil.rmtree(self.state_dir)
        self.state_dir.mkdir(exist_ok=True)

        for root in self.roots:
            data_path = self._get_data_path(root)
            if root.source.is_dir():
                if not data_path.exists():
                    shutil.copytree(root.source, data_path, symlinks=True)
                data_path.mkdir(exist_ok=True)
            elif not data_path.exists():
                shutil.copy(root.source, data_path)

        self._setup_done = True

    def start_all(self):
        self._setup()
        self._start_proc("subscriber", check=sub_check(self.configuration))

    def stop_all(self):
        for proc in self._procs.values():
            try:
                os.kill(proc.pid, signal.SIGTERM)
                time.sleep(1)
                os.kill(proc.pid, signal.SIGHUP)
            except ProcessLookupError:
                pass

    def wait_for_all(self):
        for proc in self._procs.values():
            proc.wait()

    def get_endpoint(self, service):
        return self._endpoints.get(service)

    def get_urlbase(self, service):
        return f"http://{self.get_endpoint(service)}"


@pytest.fixture(scope="session")
def services(configuration, examples_dir):
    # TODO: Consider using a temporary directory to avoid
    # polluting the current directory with test files
    # and tests being influenced by the presence of a configuration file.
    roots = [TestRoot(TEST_CATERVA2_ROOT, examples_dir)]

    srvs = ManagedServices(TEST_STATE_DIR, reuse_state=False, roots=roots, configuration=configuration)

    try:
        srvs.start_all()
        yield srvs
    finally:
        srvs.stop_all()
    srvs.wait_for_all()


# Inspired by <https://towerbabbel.com/go-defer-in-python/>.
def defers(func):
    @functools.wraps(func)
    def wrapper(*args, **kwds):
        deferred = []
        try:
            return func(*args, defer=deferred.append, **kwds)
        finally:
            for f in reversed(deferred):
                f()

    return wrapper


@defers
def main(defer):
    from . import conf, files, sub_auth

    roots = [TestRoot(TEST_DEFAULT_ROOT, files.get_examples_dir())]

    if "--help" in sys.argv:
        rspecs = " ".join(f'"{r.name}={r.source}"' for r in roots)
        print(f"Usage: {sys.argv[0]} " f'[STATE_DIRECTORY="{DEFAULT_STATE_DIR}" [ROOTS={rspecs}]]')
        return

    state_dir = sys.argv[1] if len(sys.argv) >= 2 else DEFAULT_STATE_DIR
    if len(sys.argv) >= 3:
        roots = []  # user-provided roots
    rnames = {r.name for r in roots}
    rarg_rx = re.compile(r"(?:(.+?)=)?(.+)")  # ``[name=]source``
    for rarg in sys.argv[2:]:
        rname, rsource = rarg_rx.match(rarg).groups()
        rname = rname if rname else TEST_DEFAULT_ROOT
        if rname in rnames:
            raise ValueError(
                f"root name {rname!r} already in use; " f"please set a different name for {rsource!r}"
            )
        root = TestRoot(rname, Path(rsource))
        roots.append(root)
        rnames.add(root.name)

    # TODO: Consider allowing path to configuration file, pass here.
    configuration = conf.get_configuration()
    srvs = ManagedServices(state_dir, reuse_state=True, roots=roots, configuration=configuration)
    try:
        srvs.start_all()
        sub_auth.make_sub_user(srvs)
        srvs.wait_for_all()
    finally:
        srvs.stop_all()


if __name__ == "__main__":
    main()
