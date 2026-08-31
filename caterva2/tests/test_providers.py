"""Decoupling regression tests for the providers seam. See
plans/c2cache-decoupling.md M4.
"""

import asyncio
import importlib.metadata
import os
import re
import signal
import socket
import subprocess
import sys
import time

import httpx
import pytest


def test_bundled_c2cache_is_inert_without_peer_configuration():
    """Importing Caterva2 bundles C2Cache but does not activate it by itself."""
    from caterva2.c2cache import provider_factory

    class _FakeSettings:
        conf = {}  # noqa: RUF012

    assert provider_factory(_FakeSettings()) is None


def test_discovery_registers_bundled_c2cache_without_distribution_metadata(monkeypatch):
    """A source checkout must not need a refreshed editable-install entry point."""
    from caterva2 import c2cache
    from caterva2.services import providers

    class _Provider:
        name = "c2cache"

        def roots(self):
            return ["@test"]

    bundled = _Provider()
    monkeypatch.setattr(c2cache, "provider_factory", lambda settings: bundled)
    monkeypatch.setattr(importlib.metadata, "entry_points", lambda **kwargs: ())

    assert providers.discover(object()) == [bundled]


def test_discovery_ignores_stale_c2cache_entrypoint(monkeypatch):
    """Old editable metadata must neither fail discovery nor activate twice."""
    from caterva2 import c2cache
    from caterva2.services import providers

    class _Provider:
        name = "c2cache"

        def roots(self):
            return []

    class _StaleEntryPoint:
        name = "c2cache"

        def load(self):  # pragma: no cover - the stale entry must be skipped
            raise AssertionError("stale C2Cache entry point was loaded")

    bundled = _Provider()
    monkeypatch.setattr(c2cache, "provider_factory", lambda settings: bundled)
    monkeypatch.setattr(importlib.metadata, "entry_points", lambda **kwargs: (_StaleEntryPoint(),))

    assert providers.discover(object()) == [bundled]


def _start(statedir, port, extra_toml=""):
    statedir = str(statedir)
    toml = f'[server]\nurlbase = "http://127.0.0.1:{port}"\nlogin = false\n{extra_toml}'
    conf = f"{statedir}/caterva2-server.toml"
    os.makedirs(statedir, exist_ok=True)
    with open(conf, "w") as f:
        f.write(toml)
    env = dict(os.environ, CATERVA2_SECRET="t", PYTHONPATH=os.getcwd())
    proc = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "caterva2.services.server",
            "--statedir",
            statedir,
            "--listen",
            f"127.0.0.1:{port}",
            "--conf",
            conf,
        ],
        env=env,
        cwd=statedir,
    )
    for _ in range(50):  # wait until it answers
        returncode = proc.poll()
        if returncode is not None:
            raise RuntimeError(f"server on port {port} exited during startup with status {returncode}")
        try:
            httpx.get(f"http://127.0.0.1:{port}/api/roots", timeout=1)
            returncode = proc.poll()
            if returncode is not None:
                raise RuntimeError(
                    f"another process answered port {port}; the test server exited with status {returncode}"
                )
            return proc
        except Exception:
            time.sleep(0.2)
    proc.kill()
    proc.wait(timeout=10)
    raise RuntimeError("server did not start")


@pytest.fixture(scope="module")
def no_peer_server(tmp_path_factory):
    statedir = tmp_path_factory.mktemp("noPeer")
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    proc = _start(statedir, port)
    yield f"http://127.0.0.1:{port}"
    proc.send_signal(signal.SIGTERM)
    proc.wait(timeout=10)


def test_no_peer_config_boots_with_only_public_root(no_peer_server):
    roots = httpx.get(f"{no_peer_server}/api/roots", timeout=5).json()
    assert set(roots) == {"@public"}


def test_no_peer_config_still_serves_peer_manifest(no_peer_server):
    manifest = httpx.get(f"{no_peer_server}/api/peer", timeout=5).json()
    assert manifest["peer_id"]
    assert manifest["api_version"] == 1


def test_server_source_has_no_c2cache_coupling():
    """Seam-cleanliness guard: server.py must not reach into c2cache
    internals directly — only through the providers.py seam."""
    src = (
        __import__("pathlib").Path(__file__).resolve().parent.parent / "services" / "server.py"
    ).read_text()
    leak = re.compile(r"\b(c2cache|peers_mod|peercache|remote\.)\b")
    matches = leak.findall(src)
    assert not matches, f"server.py references c2cache internals directly: {matches}"


def test_open_view_releases_lock_on_exception():
    """A ProviderError or unexpected exception raised while the open_view
    context is held must not leak that cache's peercache lock."""
    os.environ.setdefault("CATERVA2_SECRET", "t")  # server.py import-time check
    from caterva2.c2cache import peercache
    from caterva2.c2cache.provider import C2CacheProvider

    class _FakeSettings:
        peer_id = "self"
        statedir = None
        conf = {}  # noqa: RUF012

    provider = C2CacheProvider(_FakeSettings(), [])

    class _FakeRegistry:
        def get_known(self, root):
            return object()  # anything not None

    provider.registry = _FakeRegistry()
    peercache.pool_dir = "/tmp/nonexistent-peercache-pool"

    class _BoomAdapter:
        def cache_path(self, key):
            return "/tmp/nonexistent-peercache-pool/boom-cache-path"

        def _info(self, key):
            return {"shape": [1], "schunk": {}}  # a plain dataset: not the CTable branch

        def get(self, key, info=None):
            raise RuntimeError("boom")

    provider._adapter = lambda root: _BoomAdapter()

    async def run():
        with pytest.raises(RuntimeError):
            async with provider.open_view("@labb", "x"):
                pass  # pragma: no cover - open_view raises before yielding

    asyncio.run(run())
    assert not peercache.cache_lock("/tmp/nonexistent-peercache-pool/boom-cache-path").locked()
