"""Caterva3 remote peer mounts: two real servers, one mounting the other's
@public root. See plans/caterva3-remote-peer-mounts-impl.md Phase 7.

Uses its own subprocess pair (ports 8031/8032) instead of the port-8000
pytest harness in services.py, since peer mounts need two servers talking
to each other.
"""

import os
import signal
import subprocess
import sys
import time

import blosc2
import httpx
import numpy as np
import pytest

B_PORT, A_PORT = 8031, 8032


def _start(statedir, port, extra_toml=""):
    statedir = str(statedir)
    toml = f'[server]\nurlbase = "http://localhost:{port}"\nlogin = false\n{extra_toml}'
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
            f"localhost:{port}",
            "--conf",
            conf,
        ],
        env=env,
        # settings.py reads caterva2-server.toml relative to the CWD (the
        # --conf option is not honored, a known FIXME) — run from statedir.
        cwd=statedir,
    )
    for _ in range(50):  # wait until it answers
        try:
            httpx.get(f"http://localhost:{port}/api/roots", timeout=1)
            return proc
        except Exception:
            time.sleep(0.2)
    proc.kill()
    raise RuntimeError("server did not start")


@pytest.fixture(scope="module")
def two_servers(tmp_path_factory):
    bdir = tmp_path_factory.mktemp("peerB")
    adir = tmp_path_factory.mktemp("peerA")
    # seed B's @public with a 4-chunk dataset
    pub = bdir / "public"
    pub.mkdir()
    data = np.random.default_rng(0).random((4, 100_000))
    blosc2.asarray(data, chunks=(1, 100_000), urlpath=str(pub / "mc.b2nd"))
    # a nested dataset, to exercise path-relative listing
    (pub / "dir1").mkdir()
    blosc2.asarray(np.arange(10), urlpath=str(pub / "dir1" / "small.b2nd"))
    b = _start(bdir, B_PORT)
    peer_toml = f'[[server.peer]]\nname = "labb"\nurlbase = "http://localhost:{B_PORT}"\n'
    a = _start(adir, A_PORT, peer_toml)
    yield f"http://localhost:{A_PORT}", data, adir
    for p in (a, b):
        p.send_signal(signal.SIGTERM)
        p.wait(timeout=10)


def test_roots_contains_peer(two_servers):
    urlbase, _data, _adir = two_servers
    roots = httpx.get(f"{urlbase}/api/roots", timeout=5).json()
    assert "@labb" in roots


def test_list_and_info(two_servers):
    urlbase, _data, _adir = two_servers
    listing = httpx.get(f"{urlbase}/api/list/@labb", timeout=5).json()
    assert "mc.b2nd" in listing
    info = httpx.get(f"{urlbase}/api/info/@labb/mc.b2nd", timeout=5).json()
    assert tuple(info["shape"]) == (4, 100_000)


def test_list_is_path_relative(two_servers):
    urlbase, _data, _adir = two_servers
    listing = httpx.get(f"{urlbase}/api/list/@labb/dir1", timeout=5).json()
    assert listing == ["small.b2nd"]  # not ["dir1/small.b2nd"]


def test_info_404_relayed_and_peer_stays_online(two_servers):
    urlbase, data, _adir = two_servers
    # A bad path must relay the peer's 404 (not 503 "offline")...
    r = httpx.get(f"{urlbase}/api/info/@labb/nope.b2nd", timeout=5)
    assert r.status_code == 404
    # ...and must NOT knock the whole peer offline: a fetch right after works.
    r = httpx.get(f"{urlbase}/api/fetch/@labb/mc.b2nd", params={"slice_": "1:2"}, timeout=5)
    r.raise_for_status()
    arr = blosc2.ndarray_from_cframe(r.content)
    np.testing.assert_array_equal(arr[:], data[1:2])


def test_fetch_rejects_filter_field_and_steps(two_servers):
    urlbase, _data, _adir = two_servers
    for params in ({"field": "x"}, {"filter": "a > 0"}, {"slice_": "0:4:2"}):
        r = httpx.get(f"{urlbase}/api/fetch/@labb/mc.b2nd", params=params, timeout=5)
        assert r.status_code == 400, params


def test_fetch_slice_correct(two_servers):
    urlbase, data, _adir = two_servers
    r = httpx.get(f"{urlbase}/api/fetch/@labb/mc.b2nd", params={"slice_": "2:4"}, timeout=5)
    r.raise_for_status()
    arr = blosc2.ndarray_from_cframe(r.content)
    np.testing.assert_array_equal(arr[:], data[2:4])


def test_cache_hit(two_servers):
    urlbase, data, _adir = two_servers
    for _ in range(2):
        r = httpx.get(f"{urlbase}/api/fetch/@labb/mc.b2nd", params={"slice_": "0:1"}, timeout=5)
        r.raise_for_status()
        arr = blosc2.ndarray_from_cframe(r.content)
        np.testing.assert_array_equal(arr[:], data[0:1])


def test_chunk_endpoint_is_404_on_peer_root(two_servers):
    urlbase, _data, _adir = two_servers
    r = httpx.get(f"{urlbase}/api/chunk/@labb/mc.b2nd", params={"nchunk": 0}, timeout=5)
    assert r.status_code == 404


def test_peer_offline_tolerated(tmp_path_factory):
    # Independent pair (own ports) so killing B here doesn't affect the
    # module-scoped `two_servers` fixture used by the other tests.
    bdir = tmp_path_factory.mktemp("peerB2")
    adir = tmp_path_factory.mktemp("peerA2")
    pub = bdir / "public"
    pub.mkdir()
    data = np.random.default_rng(1).random((4, 100_000))
    blosc2.asarray(data, chunks=(1, 100_000), urlpath=str(pub / "mc.b2nd"))
    b_port, a_port = 8033, 8034
    b = _start(bdir, b_port)
    peer_toml = f'[[server.peer]]\nname = "labb2"\nurlbase = "http://localhost:{b_port}"\n'
    a = _start(adir, a_port, peer_toml)
    try:
        urlbase = f"http://localhost:{a_port}"
        # warm the cache for one slice before killing B
        r = httpx.get(f"{urlbase}/api/fetch/@labb2/mc.b2nd", params={"slice_": "0:1"}, timeout=5)
        r.raise_for_status()

        b.send_signal(signal.SIGTERM)
        b.wait(timeout=10)

        # /api/roots still answers even though a mounted peer is down
        roots = httpx.get(f"{urlbase}/api/roots", timeout=5).json()
        assert "@labb2" in roots

        # cached slice still works
        r = httpx.get(f"{urlbase}/api/fetch/@labb2/mc.b2nd", params={"slice_": "0:1"}, timeout=5)
        r.raise_for_status()
        arr = blosc2.ndarray_from_cframe(r.content)
        np.testing.assert_array_equal(arr[:], data[0:1])

        # uncached slice cannot be served while the peer is down
        r = httpx.get(f"{urlbase}/api/fetch/@labb2/mc.b2nd", params={"slice_": "2:4"}, timeout=5)
        assert r.status_code == 503
    finally:
        a.send_signal(signal.SIGTERM)
        a.wait(timeout=10)
