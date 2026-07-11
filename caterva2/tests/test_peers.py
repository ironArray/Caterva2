"""Caterva3 remote peer mounts: two real servers, one mounting the other's
@public root. See plans/caterva3-remote-peer-mounts-impl.md Phase 7.

Uses its own subprocess pair (ports 8031/8032) instead of the port-8000
pytest harness in services.py, since peer mounts need two servers talking
to each other.
"""
# ruff: noqa: RUF009  # blosc2.field() is the standard CTable dataclass default API

import asyncio
import json
import os
import shutil
import signal
import subprocess
import sys
import time
from dataclasses import dataclass

import blosc2
import httpx
import numpy as np
import pytest

B_PORT, A_PORT = 8031, 8032


def _fixed_row(chunks=(16,), blocks=(8,)):
    """Row type for a fixed-width, multi-chunk test CTable."""

    @dataclass
    class Row:
        x: int = blosc2.field(blosc2.int32(), chunks=chunks, blocks=blocks)
        y: str = blosc2.field(blosc2.string(max_length=8), chunks=chunks, blocks=blocks)
        f: float = blosc2.field(blosc2.float64(), chunks=chunks, blocks=blocks)

    return Row


def _make_fixed_table(urlpath, n=70):
    # expected_size matters: default capacity with 16-row chunks means tens
    # of thousands of chunks per column (minutes to create/compact).
    t = blosc2.CTable(_fixed_row(), urlpath=str(urlpath), mode="w", compact=True, expected_size=n)
    for i in range(n):
        t.append((i, f"s{i}", i / 2))
    t.close()


def _seed_ctables(pub):
    """Peer-B seeds for the CTable tests: a fixed-width table (5 chunks of
    16 rows), a varlen-column table (non-cacheable -> pass-through), and a
    TreeStore holding an NDArray leaf plus a nested CTable."""
    _make_fixed_table(pub / "tbl.b2z")

    @dataclass
    class VRow:
        a: int = blosc2.field(blosc2.int64())
        s: str = blosc2.field(blosc2.vlstring())

    tv = blosc2.CTable(VRow, urlpath=str(pub / "vtbl.b2z"), mode="w", compact=True, expected_size=10)
    for i in range(10):
        tv.append((i, "x" * (i + 1)))
    tv.close()

    tree = blosc2.TreeStore(str(pub / "tree.b2z"), mode="w")
    tree["/dir/arr"] = blosc2.asarray(np.arange(20))
    nested = blosc2.CTable(_fixed_row(), expected_size=40)
    for i in range(40):
        nested.append((i, f"n{i}", i * 1.5))
    tree["/dir/tbl"] = nested
    tree.close()


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
    _seed_ctables(pub)
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


# --- peer CTables: fetch, cache layout, offline, navigation, web UI --------
# (plans/caterva3-remote-peer-simplified.md)


def _fetch_table(urlbase, path, slice_=None, timeout=15):
    params = {"slice_": slice_} if slice_ else None
    r = httpx.get(f"{urlbase}/api/fetch/{path}", params=params, timeout=timeout)
    r.raise_for_status()
    return blosc2.ctable_from_cframe(r.content)


def _ctable_caches(adir, peer="labb"):
    """(path, open handle, _peer_src meta) of every structured CTable cache
    in the peer's pool dir."""
    out = []
    for c in (adir / "peercache" / peer).glob("*.b2nd"):
        try:
            arr = blosc2.open(str(c), mode="a", locking=True)
            meta = json.loads(arr.schunk.vlmeta.get("_peer_src", "{}"))
        except Exception:
            continue
        if meta.get("kind") == "ctable":
            out.append((c, arr, meta))
    return out


def test_ctable_slice_fetch(two_servers):
    urlbase, _data, _adir = two_servers
    t = _fetch_table(urlbase, "@labb/tbl.b2z", "5:20")
    assert t.nrows == 15
    assert t["x"][:].tolist() == list(range(5, 20))
    assert t["y"][:].tolist() == [f"s{i}" for i in range(5, 20)]
    np.testing.assert_allclose(t["f"][:], [i / 2 for i in range(5, 20)])
    # whole table (no slice_)
    t = _fetch_table(urlbase, "@labb/tbl.b2z")
    assert t.nrows == 70
    assert t["x"][:].tolist() == list(range(70))
    # single row
    t = _fetch_table(urlbase, "@labb/tbl.b2z", "33")
    assert t.nrows == 1
    assert t["x"][0] == 33
    assert t["y"][0] == "s33"


def test_ctable_cache_hit_and_layout(two_servers):
    urlbase, _data, adir = two_servers
    vals = []
    for _ in range(2):
        t = _fetch_table(urlbase, "@labb/tbl.b2z", "0:32")
        vals.append((t["x"][:].tolist(), t["y"][:].tolist()))
    assert vals[0] == vals[1] == (list(range(32)), [f"s{i}" for i in range(32)])
    # exactly one structured cache artifact for this table, schema in vlmeta
    matching = [(c, a, m) for c, a, m in _ctable_caches(adir) if m.get("path") == "@public/tbl.b2z"]
    assert len(matching) == 1, matching
    cpath, arr, meta = matching[0]
    assert arr.dtype.names == ("x", "y", "f")
    assert [c["name"] for c in meta["schema"]["columns"]] == ["x", "y", "f"]
    assert (cpath.parent / (cpath.name + ".atime.npy")).exists()
    # the old per-column design's family manifest must not exist
    assert not list((adir / "peercache").rglob("*.ctbl.json"))


def test_ctable_nested_in_tree_fetch(two_servers):
    urlbase, _data, _adir = two_servers
    t = _fetch_table(urlbase, "@labb/tree.b2z/dir/tbl", "10:20")
    assert t["y"][:].tolist() == [f"n{i}" for i in range(10, 20)]
    np.testing.assert_allclose(t["f"][:], [i * 1.5 for i in range(10, 20)])


def test_ctable_varlen_table_passthrough(two_servers):
    urlbase, _data, adir = two_servers
    before = set((adir / "peercache" / "labb").glob("*.b2nd"))
    t = _fetch_table(urlbase, "@labb/vtbl.b2z", "2:6")
    assert t["a"][:].tolist() == [2, 3, 4, 5]
    assert [str(s) for s in t["s"][:]] == ["x" * (i + 1) for i in range(2, 6)]
    # pass-through: no cache artifact appears for the varlen table
    assert set((adir / "peercache" / "labb").glob("*.b2nd")) == before


def test_peer_container_deep_list(two_servers):
    urlbase, _data, _adir = two_servers
    # the flat catalog still lists the container as one opaque entry
    listing = httpx.get(f"{urlbase}/api/list/@labb", timeout=15).json()
    assert "tree.b2z" in listing
    assert not any(name.startswith("tree.b2z/") for name in listing)
    # ...but a container path deep-lists members (relative names, B's rule)
    listing = httpx.get(f"{urlbase}/api/list/@labb/tree.b2z", timeout=15).json()
    assert sorted(listing) == ["dir/arr", "dir/tbl"]
    listing = httpx.get(f"{urlbase}/api/list/@labb/tree.b2z/dir", timeout=15).json()
    assert sorted(listing) == ["arr", "tbl"]


def test_peer_container_mount_ui(two_servers):
    urlbase, _data, _adir = two_servers
    # plug icon on the TreeStore row only; the CTable .b2z row opens directly
    html = httpx.get(f"{urlbase}/htmx/path-list/", params=[("roots", "@labb")], timeout=30).text
    assert 'title="Mount as root" data-path="@labb/tree.b2z"' in html  # plug icon
    assert 'title="Mount as root" data-path="@labb/tbl.b2z"' not in html
    assert "@labb/tbl.b2z" in html  # still listed as a plain row
    # a mounted peer container expands into member rows
    html = httpx.get(f"{urlbase}/htmx/path-list/", params=[("roots", "@labb/tree.b2z")], timeout=30).text
    assert "@labb/tree.b2z/dir/arr" in html
    assert "@labb/tree.b2z/dir/tbl" in html
    # the root list keeps a mounted peer path (localStorage round-trip)
    html = httpx.get(f"{urlbase}/htmx/root-list/", params=[("mounted", "@labb/tree.b2z")], timeout=15).text
    assert "@labb/tree.b2z" in html


def test_ctable_web_view(two_servers):
    urlbase, _data, _adir = two_servers
    r = httpx.post(f"{urlbase}/htmx/path-view/@labb/tbl.b2z", data={"index": 0, "sizes": 10}, timeout=30)
    assert r.status_code == 200, r.text
    assert "s0" in r.text
    assert "s9" in r.text
    # paging reflects the requested window
    r = httpx.post(f"{urlbase}/htmx/path-view/@labb/tbl.b2z", data={"index": 32, "sizes": 10}, timeout=30)
    assert r.status_code == 200, r.text
    assert "s32" in r.text
    assert "s41" in r.text
    assert "s31<" not in r.text
    # a nested CTable renders directly, no mount needed
    r = httpx.post(
        f"{urlbase}/htmx/path-view/@labb/tree.b2z/dir/tbl", data={"index": 0, "sizes": 5}, timeout=30
    )
    assert r.status_code == 200, r.text
    assert "n0" in r.text
    # filter/sort still refused on external roots
    r = httpx.post(f"{urlbase}/htmx/path-view/@labb/tbl.b2z", data={"sortby": "x"}, timeout=15)
    assert "not supported on external roots" in r.text


def test_ctable_offline_reads_cached_range(tmp_path_factory):
    # Independent pair: B gets SIGKILLed here.
    bdir = tmp_path_factory.mktemp("peerB5")
    adir = tmp_path_factory.mktemp("peerA5")
    pub = bdir / "public"
    pub.mkdir()
    _make_fixed_table(pub / "tbl.b2z")
    b_port, a_port = 8039, 8040
    b = _start(bdir, b_port)
    peer_toml = f'[[server.peer]]\nname = "labb5"\nurlbase = "http://localhost:{b_port}"\n'
    a = _start(adir, a_port, peer_toml)
    try:
        urlbase = f"http://localhost:{a_port}"
        t = _fetch_table(urlbase, "@labb5/tbl.b2z", "0:20")
        assert t["x"][:].tolist() == list(range(20))

        b.send_signal(signal.SIGKILL)
        b.wait(timeout=10)

        # the cached range is still served, values intact
        t = _fetch_table(urlbase, "@labb5/tbl.b2z", "0:20", timeout=20)
        assert t["x"][:].tolist() == list(range(20))
        assert t["y"][:].tolist() == [f"s{i}" for i in range(20)]

        # a disjoint, uncached range cannot be served while B is down
        r = httpx.get(f"{urlbase}/api/fetch/@labb5/tbl.b2z", params={"slice_": "40:60"}, timeout=20)
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
    _make_fixed_table(pub / "tbl.b2z")  # CTable fetches join the storm too
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


async def _fetch_ctable(client, urlbase, j):
    lo = (j * 8) % 64
    return await client.get(
        f"{urlbase}/api/fetch/@labb3/tbl.b2z", params={"slice_": f"{lo}:{lo + 8}"}, timeout=10
    )


async def _run_concurrent_requests(urlbase):
    async with httpx.AsyncClient() as client:
        calls = [
            _fetch(client, urlbase, i) if i % 2 == 0 else _path_view(client, urlbase, i) for i in range(12)
        ]
        calls += [_fetch_ctable(client, urlbase, j) for j in range(6)]
        return await asyncio.gather(*calls, return_exceptions=True)


def test_concurrent_requests_under_tiny_quota_dont_crash(tiny_quota_peers):
    urlbase, data = tiny_quota_peers
    results = asyncio.run(_run_concurrent_requests(urlbase))
    for i, r in enumerate(results[:12]):
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
    # concurrent CTable fetches under the same tiny quota: single lock,
    # single artifact — same no-corruption bar as the NDArray fetches.
    for j, r in enumerate(results[12:]):
        assert not isinstance(r, Exception), r
        assert r.status_code != 500, r.text
        if r.status_code == 200:
            lo = (j * 8) % 64
            t = blosc2.ctable_from_cframe(r.content)
            assert t["x"][:].tolist() == list(range(lo, lo + 8))

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


# --- CTable units (no servers) ---------------------------------------------


def test_ctable_row_range_edges():
    from caterva2.services.srv_utils import ctable_row_range

    n = 70
    assert ctable_row_range(None, n) == (0, 70)
    assert ctable_row_range((), n) == (0, 70)
    assert ctable_row_range(slice(5, 20), n) == (5, 20)
    assert ctable_row_range((slice(5, 20),), n) == (5, 20)
    assert ctable_row_range(slice(-10, None), n) == (60, 70)
    assert ctable_row_range(slice(None, -60), n) == (0, 10)
    assert ctable_row_range(4, n) == (4, 5)
    assert ctable_row_range(-1, n) == (69, 70)
    assert ctable_row_range(slice(0, 0), n) == (0, 0)  # stop == 0 stays 0
    assert ctable_row_range(slice(5, 500), n) == (5, 70)  # clamp
    assert ctable_row_range(slice(50, 20), n) == (50, 50)  # inverted -> empty
    assert ctable_row_range(500, n) == (70, 70)  # out-of-range int clamps


def test_synth_ctable_cframe_roundtrip(tmp_path):
    from c2cache import remote

    _make_fixed_table(tmp_path / "t.b2z", n=20)
    t = blosc2.open(str(tmp_path / "t.b2z"))
    sd = t.schema_dict()
    cols = {name: t[name][5:15] for name in ("x", "y", "f")}
    cf = remote._synth_ctable_cframe(sd, cols, 10)
    t2 = blosc2.ctable_from_cframe(cf)
    assert t2.nrows == 10
    assert t2["x"][:].tolist() == list(range(5, 15))
    assert t2["x"][:].dtype == np.int32
    assert t2["y"][:].tolist() == [f"s{i}" for i in range(5, 15)]
    np.testing.assert_allclose(t2["f"][:], [i / 2 for i in range(5, 15)])


def test_synth_ctable_cframe_preserves_null_sentinels(tmp_path):
    """Null sentinels (iinfo.min / NaN / the null string marker) and the
    schema's null metadata must survive the full structured-cache loop:
    numpy columns -> packed structured frame -> field reads -> synthesized
    cframe -> reconstructed CTable."""
    from c2cache import remote

    int_null = np.iinfo(np.int32).min

    @dataclass
    class NRow:
        x: int = blosc2.field(blosc2.int32(nullable=True), chunks=(16,), blocks=(8,))
        y: str = blosc2.field(blosc2.string(max_length=16, nullable=True), chunks=(16,), blocks=(8,))
        f: float = blosc2.field(blosc2.float64(nullable=True), chunks=(16,), blocks=(8,))

    t = blosc2.CTable(NRow, urlpath=str(tmp_path / "n.b2z"), mode="w", compact=True, expected_size=40)
    str_null = t["y"].null_value
    for i in range(40):
        t.append(
            (
                int_null if i % 7 == 3 else i,
                str_null if i % 5 == 2 else f"s{i}",
                float("nan") if i % 3 == 1 else i / 2,
            )
        )
    sd = t.schema_dict()
    dtype = remote.ctable_cacheable({"nrows": 40, "chunks": (16,), "schema_dict": sd})
    assert dtype is not None  # nullable fixed-width columns are cacheable

    # pack -> sparse structured frame -> read back (the cache's data path)
    rows = np.zeros(40, dtype=dtype)
    for name in dtype.names:
        rows[name] = t[name][0:40]
    frame = blosc2.asarray(rows, chunks=(16,))
    back = frame[0:40]

    cols = {name: back[name] for name in dtype.names}
    t2 = blosc2.ctable_from_cframe(remote._synth_ctable_cframe(sd, cols, 40))

    for name in ("x", "y", "f"):
        np.testing.assert_array_equal(t2[name][:], t[name][:])  # NaN-safe
        assert t2[name].null_count() == t[name].null_count()
    assert t2["x"].null_count() == sum(1 for i in range(40) if i % 7 == 3)
    assert t2["x"].null_value == int_null
    assert t2["y"].null_value == str_null
    assert np.isnan(t2["f"].null_value)


def test_ctable_cacheable_detection(tmp_path):
    from c2cache import remote

    _make_fixed_table(tmp_path / "t.b2z", n=20)
    sd = blosc2.open(str(tmp_path / "t.b2z")).schema_dict()
    dtype = remote.ctable_cacheable({"nrows": 20, "chunks": (16,), "schema_dict": sd})
    assert dtype is not None
    assert dtype.names == ("x", "y", "f")

    @dataclass
    class VRow:
        a: int = blosc2.field(blosc2.int64())
        s: str = blosc2.field(blosc2.vlstring())

    tv = blosc2.CTable(VRow)
    tv.append((1, "abc"))
    vsd = tv.schema_dict()
    assert remote.ctable_cacheable({"nrows": 1, "chunks": (16,), "schema_dict": vsd}) is None
    # no shared chunk grid
    assert remote.ctable_cacheable({"nrows": 20, "chunks": None, "schema_dict": sd}) is None
    # oversized compound chunk (rows_per_chunk x itemsize > cap)
    assert remote.ctable_cacheable({"nrows": 20, "chunks": (2**24,), "schema_dict": sd}) is None
    # numpy-hostile schema (duplicate field names)
    dup = {"version": 1, "columns": [{"name": "a", "kind": "int32"}, {"name": "a", "kind": "int32"}]}
    assert remote.ctable_cacheable({"nrows": 5, "chunks": (16,), "schema_dict": dup}) is None


def test_ctable_source_aget_chunk(tmp_path):
    """One api/fetch call per chunk; the trailing partial chunk comes back
    zero-padded to the full chunkshape."""
    from c2cache import remote

    _make_fixed_table(tmp_path / "t.b2z", n=70)
    t = blosc2.open(str(tmp_path / "t.b2z"))
    info = {"nrows": 70, "chunks": (16,), "schema_dict": t.schema_dict(), "mtime": None}
    dtype = remote.ctable_cacheable(info)
    src = remote.CTableSource("@public/t.b2z", "http://unused", info, dtype)

    calls = []

    class Resp:
        def __init__(self, content):
            self.content = content

        def raise_for_status(self):
            pass

    class StubAClient:
        async def get(self, url, params=None):
            calls.append(params["slice_"])
            lo, hi = map(int, params["slice_"].split(":"))
            return Resp(t.slice(lo, hi).to_cframe())

        async def aclose(self):
            pass

    src._aclient = StubAClient()
    chunk = asyncio.run(src.aget_chunk(4))  # trailing chunk: rows 64:70 of 70
    assert calls == ["64:70"]

    arr = blosc2.empty((16,), dtype=dtype, chunks=(16,))
    arr.schunk.update_chunk(0, chunk)
    assert arr[:]["x"][:6].tolist() == list(range(64, 70))
    assert (arr[:]["x"][6:] == 0).all()  # zero-padded tail
    assert arr[:]["y"][:6].tolist() == [f"s{i}" for i in range(64, 70)]


def test_afetch_retry_once():
    """A single transport timeout inside afetch is retried once (sporadic
    503 flake on concurrent first-touch fetches); a second failure still
    propagates for the caller's mark_offline handling."""
    from c2cache import remote

    class FlakyProxy:
        def __init__(self, failures):
            self.failures = failures
            self.calls = []

        async def afetch(self, slice_, **kwargs):
            self.calls.append((slice_, kwargs))
            if len(self.calls) <= self.failures:
                raise httpx.ReadTimeout("slow chunk")

    proxy = FlakyProxy(failures=1)
    asyncio.run(remote.afetch_retry_once(proxy, slice(0, 8), max_concurrency=4))
    assert proxy.calls == [(slice(0, 8), {"max_concurrency": 4})] * 2

    proxy = FlakyProxy(failures=2)
    with pytest.raises(httpx.ReadTimeout):
        asyncio.run(remote.afetch_retry_once(proxy, slice(0, 8)))
    assert len(proxy.calls) == 2


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
