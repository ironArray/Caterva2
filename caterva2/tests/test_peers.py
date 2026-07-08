"""Caterva3 remote peer mounts: two real servers, one mounting the other's
@public root. See plans/caterva3-remote-peer-mounts-impl.md Phase 7.

Uses its own subprocess pair (ports 8031/8032) instead of the port-8000
pytest harness in services.py, since peer mounts need two servers talking
to each other.
"""

import asyncio
import os
import shutil
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


def test_cache_sidecar_appears_and_is_removed_with_cache(two_servers):
    """Every peer-cache handle opens with locking=True (plans/peercache-locking.md
    §1): the cache's `.b2lock` sidecar must appear, and since the cache is
    directory-backed (sframe), it lives inside that directory, so it
    disappears together with the cache on removal -- no separate cleanup
    needed."""
    urlbase, _data, adir = two_servers
    r = httpx.get(f"{urlbase}/api/fetch/@labb/mc.b2nd", params={"slice_": "0:1"}, timeout=5)
    r.raise_for_status()

    caches = list((adir / "peercache" / "labb").glob("*.b2nd"))
    assert len(caches) == 1, caches
    cache_dir = caches[0]
    assert (cache_dir / ".b2lock").exists()

    shutil.rmtree(cache_dir)
    assert not cache_dir.exists()


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


@pytest.fixture(scope="module")
def tiny_quota_peers(tmp_path_factory):
    # A tiny peer_cache_quota forces peercache to evict under load, which is
    # exactly the concurrency scenario that corrupted sparse-frame handles
    # before per-cache locking + blosc2's frame-level locking closed the gap
    # (see plans/peercache-locking.md).
    bdir = tmp_path_factory.mktemp("peerB3")
    adir = tmp_path_factory.mktemp("peerA3")
    pub = bdir / "public"
    pub.mkdir()
    data = np.random.default_rng(2).random((8, 100_000))
    blosc2.asarray(data, chunks=(1, 100_000), urlpath=str(pub / "mc.b2nd"))
    b_port, a_port = 8035, 8036
    b = _start(bdir, b_port)
    peer_toml = (
        'peer_cache_quota = "100K"\n'
        f'[[server.peer]]\nname = "labb3"\nurlbase = "http://localhost:{b_port}"\n'
    )
    a = _start(adir, a_port, peer_toml)
    yield f"http://localhost:{a_port}", data
    for p in (a, b):
        p.send_signal(signal.SIGTERM)
        p.wait(timeout=10)


async def _fetch(client, urlbase, i):
    return await client.get(
        f"{urlbase}/api/fetch/@labb3/mc.b2nd", params={"slice_": f"{i % 8}:{i % 8 + 1}"}, timeout=10
    )


async def _path_view(client, urlbase, i):
    return await client.post(
        f"{urlbase}/htmx/path-view/@labb3/mc.b2nd",
        data={"index": [i % 8, 0], "sizes": [1, 10]},
        timeout=10,
    )


async def _run_concurrent_requests(urlbase):
    async with httpx.AsyncClient() as client:
        calls = [
            _fetch(client, urlbase, i) if i % 2 == 0 else _path_view(client, urlbase, i) for i in range(12)
        ]
        return await asyncio.gather(*calls, return_exceptions=True)


def test_concurrent_requests_under_tiny_quota_dont_crash(tiny_quota_peers):
    urlbase, data = tiny_quota_peers
    results = asyncio.run(_run_concurrent_requests(urlbase))
    for i, r in enumerate(results):
        assert not isinstance(r, Exception), r
        # 200 (served), 503 (evicted before use, offline), 400 (bad request
        # edge case) are all fine; a 500 means a crash/corruption regression.
        assert r.status_code != 500, r.text
        # A 200 from a fetch (the even i's) must never be silent UNINIT
        # zeros: fetch->read->touch under this cache's lock must be atomic
        # against a concurrent eviction of the same cache.
        if i % 2 == 0 and r.status_code == 200:
            arr = blosc2.ndarray_from_cframe(r.content)
            np.testing.assert_array_equal(arr[:], data[i % 8 : i % 8 + 1])

    # the peer must still be responsive after the storm, not knocked over
    roots = httpx.get(f"{urlbase}/api/roots", timeout=5).json()
    assert "@labb3" in roots


def test_cache_lock_is_per_path():
    """Two different cache paths get independent locks (the whole point of
    per-cache locking, plans/peercache-locking.md §2): locking one must not
    block acquiring the other, and the same path always maps to the same
    lock object."""
    from c2cache import peercache

    async def run():
        lock_a = peercache.cache_lock("/tmp/does-not-exist-a")
        lock_b = peercache.cache_lock("/tmp/does-not-exist-b")
        assert lock_a is not lock_b
        assert peercache.cache_lock("/tmp/does-not-exist-a") is lock_a

        async with lock_a, asyncio.timeout(1), lock_b:
            pass  # must not block/timeout: lock_b is independent of lock_a

    asyncio.run(run())


@pytest.fixture(scope="module")
def two_dataset_peers(tmp_path_factory):
    # Two distinct datasets sharing one tiny-quota pool: fetches/evictions of
    # one must not serialize behind the other now that locking is per-cache
    # rather than pool-wide (plans/peercache-locking.md §3/§4).
    bdir = tmp_path_factory.mktemp("peerB4")
    adir = tmp_path_factory.mktemp("peerA4")
    pub = bdir / "public"
    pub.mkdir()
    data1 = np.random.default_rng(3).random((8, 100_000))
    data2 = np.random.default_rng(4).random((8, 100_000))
    blosc2.asarray(data1, chunks=(1, 100_000), urlpath=str(pub / "mc1.b2nd"))
    blosc2.asarray(data2, chunks=(1, 100_000), urlpath=str(pub / "mc2.b2nd"))
    b_port, a_port = 8037, 8038
    b = _start(bdir, b_port)
    peer_toml = (
        'peer_cache_quota = "100K"\n'
        f'[[server.peer]]\nname = "labb4"\nurlbase = "http://localhost:{b_port}"\n'
    )
    a = _start(adir, a_port, peer_toml)
    yield f"http://localhost:{a_port}", data1, data2
    for p in (a, b):
        p.send_signal(signal.SIGTERM)
        p.wait(timeout=10)


async def _fetch_dataset(client, urlbase, name, i):
    return await client.get(
        f"{urlbase}/api/fetch/@labb4/{name}.b2nd", params={"slice_": f"{i % 8}:{i % 8 + 1}"}, timeout=10
    )


def test_concurrent_fetches_of_different_datasets_dont_serialize(two_dataset_peers):
    """Interleaved concurrent fetches of two different datasets under a tiny
    shared quota: correctness under load is the regression net (per
    plans/peercache-locking.md §Tests point 3 -- the locking mechanics/timing
    itself is blosc2's own suite's job)."""
    urlbase, data1, data2 = two_dataset_peers

    async def run():
        async with httpx.AsyncClient() as client:
            calls = []
            for i in range(12):
                name = "mc1" if i % 2 == 0 else "mc2"
                calls.append(_fetch_dataset(client, urlbase, name, i))
            return await asyncio.gather(*calls, return_exceptions=True)

    results = asyncio.run(run())
    for i, r in enumerate(results):
        assert not isinstance(r, Exception), r
        assert r.status_code != 500, r.text
        if r.status_code == 200:
            data = data1 if i % 2 == 0 else data2
            arr = blosc2.ndarray_from_cframe(r.content)
            np.testing.assert_array_equal(arr[:], data[i % 8 : i % 8 + 1])

    roots = httpx.get(f"{urlbase}/api/roots", timeout=5).json()
    assert "@labb4" in roots
