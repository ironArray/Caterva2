"""Remote-store policy, sparse retention, and existing container API integration."""

import io

import blosc2
import fsspec
import h5py
import numpy as np
import pytest

from caterva2.services import remote_proxy, remote_store, sparse_cache, srv_utils, storage_quota


@pytest.fixture
def store_runtime(tmp_path, monkeypatch):
    monkeypatch.setenv("CATERVA2_SECRET", "test-secret")
    from caterva2.services import server

    source = tmp_path / "source.b2z"
    data = np.random.default_rng(42).integers(0, 10000, 20000, dtype="i4")
    with blosc2.TreeStore(source, mode="w", threshold=0) as tree:
        tree["/g/a"] = blosc2.asarray(data, chunks=(5000,), blocks=(1000,))
        tree["/g/b"] = blosc2.asarray(data + 1, chunks=(5000,), blocks=(1000,))
    fs = fsspec.filesystem("memory")
    url = "https://data.example/source.b2z"
    fs.pipe_file(url, source.read_bytes())
    root = tmp_path / "state"
    (root / "public").mkdir(parents=True)
    path = root / "public/store.b2z"
    with blosc2.RemoteStore(
        url, cache_policy=blosc2.CachePolicy.DISK, cache_dir=tmp_path / "creator", _filesystem=fs
    ) as store:
        store.save(path, include_cache=False)
    q = storage_quota.StorageQuota(root, 0, cache_backend="sparse")
    monkeypatch.setattr(server, "quota_coordinator", lambda: q)
    monkeypatch.setattr(
        remote_proxy, "policy", remote_proxy.Policy(enabled=True, allowed_hosts=("data.example",))
    )
    monkeypatch.setattr(remote_proxy, "_public_addresses", lambda *args: ("93.184.216.34",))
    monkeypatch.setattr(remote_proxy, "_https_filesystem", lambda *args: fs)
    return q, path, data


@pytest.fixture
def hdf5_table_runtime(tmp_path, monkeypatch):
    monkeypatch.setenv("CATERVA2_SECRET", "test-secret")
    from caterva2.services import server

    data = np.array(
        [(value, f"v{value}".encode()) for value in np.random.default_rng(4).permutation(21)],
        dtype=[("id", "<i4"), ("label", "S8")],
    )
    stream = io.BytesIO()
    with h5py.File(stream, "w") as h5file:
        table = h5file.create_dataset("table", data=data, chunks=(8,))
        table.attrs["CLASS"] = np.bytes_(b"TABLE")
        group = h5file.create_group("_i_table/id")
        group.attrs["DIRTY"] = np.int32(0)
        group.attrs["slicesize"] = np.uint32(16)
        group.attrs["optlevel"] = np.int32(6)
        group.attrs["is_csi"] = np.uint8(0)
        order = np.argsort(data["id"][:16], kind="stable")
        tail_order = np.argsort(data["id"][16:], kind="stable")
        group.create_dataset("sorted", data=data["id"][:16][order].reshape(1, 16))
        group.create_dataset("indices", data=order.astype("u8").reshape(1, 16))
        sorted_lr = group.create_dataset("sortedLR", data=data["id"][16:][tail_order])
        indices_lr = group.create_dataset("indicesLR", data=(tail_order + 16).astype("u8"))
        sorted_lr.attrs["nelements"] = indices_lr.attrs["nelements"] = np.int32(5)

    fs = fsspec.filesystem("memory")
    url = "https://data.example/table.h5"
    fs.pipe_file(url, stream.getvalue())
    root = tmp_path / "state"
    (root / "public").mkdir(parents=True)
    path = root / "public/table-store.b2z"
    with blosc2.RemoteStore(
        url, cache_policy=blosc2.CachePolicy.DISK, cache_dir=tmp_path / "creator", _filesystem=fs
    ) as store:
        store.save(path, include_cache=False)
    q = storage_quota.StorageQuota(root, 0, cache_backend="sparse")
    monkeypatch.setattr(server, "quota_coordinator", lambda: q)
    monkeypatch.setattr(
        remote_proxy, "policy", remote_proxy.Policy(enabled=True, allowed_hosts=("data.example",))
    )
    monkeypatch.setattr(remote_proxy, "_public_addresses", lambda *args: ("93.184.216.34",))
    monkeypatch.setattr(remote_proxy, "_https_filesystem", lambda *args: fs)
    return q, path, data


def test_store_policy_inspection_and_shared_retention(store_runtime, monkeypatch):
    q, path, data = store_runtime
    original = path.read_bytes()
    adapter = srv_utils.open_container(path)
    assert adapter.leaves() == ["/g/a", "/g/b"]
    first = adapter.get("/g/a")
    np.testing.assert_array_equal(first[:5000], data[:5000])
    second = srv_utils.open_container(path).get("/g/a")

    def no_fetch(*args, **kwargs):
        raise AssertionError("warm leaf fetched upstream payload")

    with monkeypatch.context() as patch:
        patch.setattr(blosc2.B2ZNDSource, "get_chunk", no_fetch)
        np.testing.assert_array_equal(second[:5000], data[:5000])
    assert path.read_bytes() == original
    with q.connect() as db:
        assert (
            db.execute(
                "SELECT count(*) FROM remote_generations WHERE kind='store' AND state='active'"
            ).fetchone()[0]
            == 1
        )
        rel = db.execute("SELECT relpath FROM remote_generations WHERE state='active'").fetchone()[0]
        assert db.execute("SELECT count(*) FROM remote_operations").fetchone()[0] == 0
    assert q.usage()["remote_cache_used"] >= sparse_cache.measure(q.root / rel)[0] > 0
    info = srv_utils.container_member_info(path, "/g/a")
    assert info.shape == data.shape
    assert info.accept_ranges == "none"
    info.model_dump_json()
    monkeypatch.setattr(remote_proxy, "policy", remote_proxy.Policy())
    assert remote_store.inspect(path) is not None
    assert srv_utils.open_container(path).leaves() == ["/g/a", "/g/b"]
    with pytest.raises(Exception, match=r"403|disabled"):
        first[:1]


def test_store_retirement_and_offline_pruning(store_runtime, monkeypatch):
    q, path, data = store_runtime
    leaf = srv_utils.open_container(path).get("g/a")
    np.testing.assert_array_equal(leaf[:], data)
    with q.connect() as db:
        private, source = db.execute("SELECT relpath,source_stamp FROM remote_generations").fetchone()
    import json

    removed, remaining = blosc2.RemoteStore.trim_sparse_cache(q.root / private, json.loads(source), 0)
    assert removed
    assert remaining == 0
    q.publish(path, None, expected=storage_quota.signature(path))
    q.remote.maintain()
    assert q.usage()["remote_cache_used"] == 0


def test_store_quota_denial_does_not_retain(store_runtime):
    q, path, data = store_runtime
    q.quota = path.stat().st_size
    with q.transaction() as db:
        db.execute("UPDATE account SET quota=?", (q.quota,))
    leaf = srv_utils.open_container(path).get("g/a")
    np.testing.assert_array_equal(leaf[:], data)
    assert q.usage()["remote_cache_used"] == 0


@pytest.mark.parametrize("key", ["missing", "g/missing", "g//a", "g/../a", "g/\0a"])
def test_store_missing_and_malformed_members_are_offline(store_runtime, monkeypatch, key):
    _, path, _ = store_runtime
    adapter = srv_utils.open_container(path)
    monkeypatch.setattr(remote_proxy, "policy", remote_proxy.Policy())
    assert adapter.get(key) is None
    assert not adapter.is_leaf(key)
    assert adapter.leaves(key) == []


def test_store_warm_seed_is_migrated_once(store_runtime, tmp_path, monkeypatch):
    q, path, data = store_runtime
    manifest = remote_store.inspect(path)
    warm = tmp_path / "uploaded.b2z"
    with blosc2.RemoteStore(
        manifest["source"]["urlpath"],
        cache_dir=tmp_path / "warm-creator",
        _filesystem=fsspec.filesystem("memory"),
    ) as store:
        with store["g/a"] as array:
            array[:5000]
        store.save(warm)
    q.publish(path, warm.read_bytes(), expected=storage_quota.signature(path))
    assert remote_store.inspect(path)["caches"]
    leaf = srv_utils.open_container(path).get("g/a")
    assert remote_store.inspect(path)["caches"] == []
    with monkeypatch.context() as patch:
        patch.setattr(blosc2.B2ZNDSource, "get_chunk", lambda *args: pytest.fail("seed was not retained"))
        np.testing.assert_array_equal(leaf[:5000], data[:5000])


def test_store_interrupted_generation_recovers_offline(store_runtime, monkeypatch):
    q, path, data = store_runtime
    leaf = srv_utils.open_container(path).get("g/a")
    np.testing.assert_array_equal(leaf[:5000], data[:5000])
    with q.connect() as db:
        gid = db.execute("SELECT generation_id FROM remote_generations WHERE state='active'").fetchone()[0]
    q.remote._intent(gid, "fill")
    monkeypatch.setattr(
        remote_proxy, "_https_filesystem", lambda *args: pytest.fail("recovery contacted source")
    )
    q.remote.maintain()
    assert q.usage()["remote_cache_used"] == 0
    assert q.usage()["reserved"] == 0


def test_store_warm_hit_survives_admission_denial(store_runtime, monkeypatch):
    q, path, data = store_runtime
    leaf = srv_utils.open_container(path).get("g/a")
    np.testing.assert_array_equal(leaf[:5000], data[:5000])
    used = q.usage()["used"]
    with q.transaction() as db:
        db.execute("UPDATE account SET cache_fill_suspended=1,quota=?", (used,))
    monkeypatch.setattr(
        blosc2.B2ZNDSource, "get_chunk", lambda *args: pytest.fail("warm hit fetched payload")
    )
    np.testing.assert_array_equal(leaf[:5000], data[:5000])


@pytest.mark.parametrize(
    "changes",
    [
        {"cache_policy": "none", "max_cache_bytes": 5},
        {"cache_policy": "memory", "max_cache_bytes": None},
        {"cache_policy": "disk", "max_cache_bytes": True},
    ],
)
def test_store_rejects_invalid_limits(store_runtime, changes):
    _, path, _ = store_runtime
    manifest = dict(remote_store.inspect(path), **changes)
    with pytest.raises(remote_proxy.RemoteArrayDenied):
        remote_store.validate_manifest(manifest)


@pytest.mark.asyncio
async def test_store_http_routes_and_exports(store_runtime, monkeypatch, tmp_path):
    import httpx

    from caterva2.services import server

    q, path, data = store_runtime
    monkeypatch.setattr(server.settings, "statedir", q.root)
    monkeypatch.setattr(server.settings, "public", path.parent)
    monkeypatch.setattr(server.settings, "shared", q.root / "shared")
    monkeypatch.setattr(server.settings, "personal", q.root / "personal")
    overrides = dict(server.app.dependency_overrides)
    server.app.dependency_overrides[server.optional_user] = lambda: None
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=server.app), base_url="http://test"
        ) as client:
            response = await client.get("/api/list/@public/store.b2z")
            assert response.status_code == 200, response.text
            assert response.json() == ["g/a", "g/b"]
            response = await client.get("/api/info/@public/store.b2z/g/a")
            assert response.status_code == 200, response.text
            assert response.json()["accept_ranges"] == "none"
            response = await client.get("/api/fetch/@public/store.b2z/g/a", params={"slice_": "0:5000"})
            assert response.status_code == 200, response.text
            np.testing.assert_array_equal(blosc2.ndarray_from_cframe(response.content)[:], data[:5000])
            response = await client.get("/api/chunk/@public/store.b2z/g/a", params={"nchunk": 0})
            assert response.status_code == 200, response.text
            np.testing.assert_array_equal(
                np.frombuffer(blosc2.decompress(response.content), dtype="i4"), data[:5000]
            )
            for include in (True, False):
                response = await client.get(
                    "/api/download/@public/store.b2z", params={"include_cache": str(include).lower()}
                )
                assert response.status_code == 200, response.text
                out = tmp_path / f"export-{include}.b2z"
                out.write_bytes(response.content)
                manifest = remote_store.inspect(out)
                assert bool(manifest["caches"]) == include
                assert q.usage()["working"] == 0
            monkeypatch.setattr(remote_proxy, "policy", remote_proxy.Policy())
            response = await client.get("/api/fetch/@public/store.b2z/g/a")
            assert response.status_code == 403
            response = await client.get("/api/download/@public/store.b2z", params={"include_cache": "false"})
            assert response.status_code == 200
    finally:
        server.app.dependency_overrides.clear()
        server.app.dependency_overrides.update(overrides)


@pytest.mark.asyncio
async def test_hdf5_table_uses_remote_ctable_and_reuses_native_index(hdf5_table_runtime, monkeypatch):
    import httpx

    from caterva2.services import server

    q, path, data = hdf5_table_runtime
    monkeypatch.setattr(server.settings, "statedir", q.root)
    monkeypatch.setattr(server.settings, "public", path.parent)
    monkeypatch.setattr(server.settings, "shared", q.root / "shared")
    monkeypatch.setattr(server.settings, "personal", q.root / "personal")
    overrides = dict(server.app.dependency_overrides)
    server.app.dependency_overrides[server.optional_user] = lambda: None
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=server.app), base_url="http://test"
        ) as client:
            response = await client.get("/api/info/@public/table-store.b2z/table")
            assert response.status_code == 200, response.text
            assert response.json()["kind"] == "ctable"

            params = {"filter": "id < 3"}
            get_chunk = blosc2.HDF5NDSource.get_chunk
            requests = 0

            def count_chunk(*args, **kwargs):
                nonlocal requests
                requests += 1
                return get_chunk(*args, **kwargs)

            monkeypatch.setattr(blosc2.HDF5NDSource, "get_chunk", count_chunk)
            response = await client.get("/api/fetch/@public/table-store.b2z/table", params=params)
            assert response.status_code == 200, response.text
            result = blosc2.ctable_from_cframe(response.content)
            np.testing.assert_array_equal(result.id[:], data["id"][data["id"] < 3])
            assert list(q.root.rglob("complete.json"))
            cold_requests = requests
            assert cold_requests > 0

            response = await client.get("/api/fetch/@public/table-store.b2z/table", params=params)
            assert response.status_code == 200, response.text
            assert requests == cold_requests
    finally:
        server.app.dependency_overrides.clear()
        server.app.dependency_overrides.update(overrides)
