"""Remote-store policy, sparse retention, and existing container API integration."""

import dataclasses
import io

import blosc2
import fsspec
import h5py
import numpy as np
import pytest

from caterva2.services import remote_proxy, remote_store, sparse_cache, srv_utils, storage_quota


@pytest.fixture
def table_runtime(tmp_path, monkeypatch):
    monkeypatch.setenv("CATERVA2_SECRET", "test-secret")
    from caterva2.services import server

    @dataclasses.dataclass
    class Row:
        x: int = blosc2.field(blosc2.int64())
        text: str = blosc2.field(blosc2.vlstring(batch_rows=2))

    source = tmp_path / "table.b2z"
    blosc2.CTable(Row, [(1, "one"), (2, "two")], create_summary_index=False).to_b2z(source)
    fs = fsspec.filesystem("memory")
    url = "https://data.example/table.b2z"
    fs.pipe_file(url, source.read_bytes())
    root = tmp_path / "state"
    (root / "public").mkdir(parents=True)
    path = root / "public/table-reference.b2z"
    with blosc2.RemoteCTable(url, cache_policy=blosc2.CachePolicy.NONE, _filesystem=fs) as table:
        table.save(path, include_cache=False)
    q = storage_quota.StorageQuota(root, 0, cache_backend="sparse")
    monkeypatch.setattr(server, "quota_coordinator", lambda: q)
    monkeypatch.setattr(
        remote_proxy, "policy", remote_proxy.Policy(enabled=True, allowed_hosts=("data.example",))
    )
    monkeypatch.setattr(remote_proxy, "_public_addresses", lambda *args: ("93.184.216.34",))
    monkeypatch.setattr(remote_proxy, "_https_filesystem", lambda *args: fs)
    return path


def test_standalone_remote_ctable_dispatch(table_runtime):
    from caterva2.services import server

    path = table_runtime
    assert remote_store.root_kind(remote_store.inspect(path)) == "ctable"
    assert not srv_utils.is_container_file(path)
    assert srv_utils.read_metadata(path).kind == "ctable"
    table = server.open_b2(path, "@public/table-reference.b2z")
    result = blosc2.ctable_from_cframe(table.fetch())
    assert list(result.x[:]) == [1, 2]
    assert list(result.text[:]) == ["one", "two"]


def test_standalone_remote_ctable_view_methods(table_runtime):
    from caterva2.services import server

    table = server.open_b2(table_runtime, "@public/table-reference.b2z")
    narrowed = table.where("x > 0").where("x < 2")
    assert list(blosc2.ctable_from_cframe(narrowed.fetch()).x[:]) == [1]
    with pytest.raises(NotImplementedError, match="view=True"):
        table.sort_by("x")


def test_standalone_remote_ctable_unavailable_source(table_runtime):
    import fastapi

    from caterva2.services import server

    table = server.open_b2(table_runtime, "@public/table-reference.b2z")
    source = remote_store.inspect(table_runtime)["source"]["urlpath"]
    fsspec.filesystem("memory").rm(source)
    with pytest.raises(fastapi.HTTPException) as exc:
        table.fetch()
    assert exc.value.status_code == 502


def test_refresh_no_quota_replacement_is_atomic(tmp_path, monkeypatch):
    from caterva2.services import server

    path = tmp_path / "reference.b2z"
    path.write_bytes(b"old")
    expected = storage_quota.signature(path)
    monkeypatch.setattr(server, "quota_coordinator", lambda: None)

    def fail_replace(*args):
        raise OSError("interrupted replacement")

    with monkeypatch.context() as patch:
        patch.setattr(server.os, "replace", fail_replace)
        with pytest.raises(OSError, match="interrupted"):
            server.write_dataset(path, b"new", expected=expected, compare=True)
    assert path.read_bytes() == b"old"
    assert sorted(tmp_path.iterdir()) == [path]

    path.write_bytes(b"winner")
    with pytest.raises(storage_quota.StorageBusy):
        server.write_dataset(path, b"new", expected=expected, compare=True)
    assert path.read_bytes() == b"winner"


def test_standalone_remote_ctable_disk_cache(table_runtime, monkeypatch):
    from caterva2.services import server

    path = table_runtime.with_name("cached-table.b2z")
    manifest = remote_store.inspect(table_runtime)
    manifest = dict(manifest, cache_policy="disk", max_cache_bytes=1 << 20)
    remote_store.cold_export(manifest, path)
    table = server.open_b2(path, "@public/cached-table.b2z")
    assert list(blosc2.ctable_from_cframe(table.fetch()).text[:]) == ["one", "two"]
    from blosc2.b2z_source import B2ZBatchSource

    def no_fetch(*args, **kwargs):
        raise AssertionError("warm batch fetched upstream payload")

    monkeypatch.setattr(B2ZBatchSource, "get_chunk", no_fetch)
    assert list(blosc2.ctable_from_cframe(table.fetch()).text[:]) == ["one", "two"]


def test_standalone_remote_ctable_warm_batch_seed(table_runtime, tmp_path, monkeypatch):
    from blosc2.b2z_source import B2ZBatchSource

    path = table_runtime
    manifest = remote_store.inspect(path)
    warm = tmp_path / "warm-table.b2z"
    with blosc2.RemoteCTable(
        manifest["source"]["urlpath"],
        cache_dir=tmp_path / "creator",
        _filesystem=fsspec.filesystem("memory"),
    ) as table:
        assert list(table.text[:]) == ["one", "two"]
        table.save(warm)
    assert remote_store.inspect(warm)["batch_caches"]
    from caterva2.services import server

    q = server.quota_coordinator()
    q.publish(path, warm.read_bytes(), expected=storage_quota.signature(path))
    table = server.open_b2(path, "@public/table-reference.b2z")
    assert remote_store.inspect(path)["batch_caches"] == []

    def no_fetch(*args, **kwargs):
        raise AssertionError("warm batch seed fetched upstream payload")

    monkeypatch.setattr(B2ZBatchSource, "get_chunk", no_fetch)
    assert list(blosc2.ctable_from_cframe(table.fetch()).text[:]) == ["one", "two"]


def test_standalone_remote_ctable_warm_hit_under_quota(table_runtime, monkeypatch):
    from blosc2.b2z_source import B2ZBatchSource

    from caterva2.services import server

    path = table_runtime.with_name("cached-table.b2z")
    manifest = dict(remote_store.inspect(table_runtime), cache_policy="disk", max_cache_bytes=1 << 20)
    remote_store.cold_export(manifest, path)
    table = server.open_b2(path, "@public/cached-table.b2z")
    assert list(blosc2.ctable_from_cframe(table.fetch()).text[:]) == ["one", "two"]
    q = server.quota_coordinator()
    used = q.usage()["used"]
    with q.transaction() as db:
        db.execute("UPDATE account SET cache_fill_suspended=1,quota=?", (used,))
    monkeypatch.setattr(
        B2ZBatchSource,
        "get_chunk",
        lambda *args: pytest.fail("warm hit fetched upstream payload"),
    )
    assert list(blosc2.ctable_from_cframe(table.fetch()).text[:]) == ["one", "two"]


def test_standalone_remote_ctable_nested_columns(table_runtime, tmp_path):
    from caterva2.services import server

    @dataclasses.dataclass
    class RichRow:
        text: str = blosc2.field(blosc2.vlstring(nullable=True, batch_rows=2))
        tags: list[int] = blosc2.field(  # noqa: RUF009
            blosc2.list(blosc2.int64(), nullable=True, batch_rows=2)
        )
        category: str = blosc2.field(blosc2.dictionary(nullable=True))

    source = tmp_path / "rich.b2z"
    blosc2.CTable(
        RichRow,
        [("one", [1, 2], "a"), (None, None, None), ("three", [3], "b")],
        create_summary_index=False,
    ).to_b2z(source)
    url = "https://data.example/rich.b2z"
    fs = fsspec.filesystem("memory")
    fs.pipe_file(url, source.read_bytes())
    path = table_runtime.with_name("rich.b2z")
    with blosc2.RemoteCTable(url, cache_policy=blosc2.CachePolicy.NONE, _filesystem=fs) as remote:
        remote.save(path, include_cache=False)
    table = server.open_b2(path, "@public/rich.b2z")
    result = blosc2.ctable_from_cframe(table.fetch())
    assert list(result.text[:]) == ["one", None, "three"]
    assert list(result.tags[:]) == [[1, 2], None, [3]]
    assert list(result.category[:]) == ["a", None, "b"]
    projected = table.fetch(field="text")
    assert list(blosc2.ctable_from_cframe(projected).text[:]) == ["one", None, "three"]


@pytest.fixture(params=["taxi.parquet", "taxi-data"])
def parquet_runtime(table_runtime, tmp_path, request):
    import pyarrow as pa
    import pyarrow.parquet as pq

    fs = fsspec.filesystem("memory")
    url = f"https://data.example/{request.param}"
    source = tmp_path / "taxi.parquet"
    pq.write_table(pa.table({"fare": [10, 20, 30], "cab": ["a", "b", "c"]}), source, row_group_size=2)
    fs.pipe_file(url, source.read_bytes())
    path = table_runtime.with_name("parquet-reference.b2z")
    with blosc2.RemoteCTable(
        url,
        source_format="parquet",
        _filesystem=fs,
        cache_dir=tmp_path / "parquet-cache",
        columns=["fare", "cab"],
        blosc2_batch_size=2,
    ) as remote:
        assert list(remote.slice(0, 2).fare[:]) == [10, 20]
        remote.save(path)
    return path


def test_remote_parquet_reference(parquet_runtime, tmp_path, monkeypatch):
    import blosc2.remote_parquet as parquet
    import pyarrow as pa
    import pyarrow.parquet as pq

    from caterva2.services import server

    path = parquet_runtime
    manifest = remote_store.inspect(path)
    assert manifest["parquet_caches"]
    assert remote_store.root_kind(manifest) == "ctable"
    q = server.quota_coordinator()
    with monkeypatch.context() as patch:
        patch.setattr(
            parquet, "_open_source_handle", lambda *args, **kwargs: pytest.fail("warm Parquet row fetched")
        )
        assert srv_utils.read_metadata(path).kind == "ctable"
        table = server.open_b2(path, "@public/parquet-reference.b2z")
        assert table.nrows == 3
        assert list(blosc2.ctable_from_cframe(table.fetch(slice_=slice(0, 2))).fare[:]) == [10, 20]
        assert q.usage()["remote_cache_used"] > 0
        assert remote_store.inspect(path)["parquet_caches"] == []
        used = q.usage()["used"]
        with q.transaction() as db:
            db.execute("UPDATE account SET cache_fill_suspended=1,quota=?", (used,))
        assert list(blosc2.ctable_from_cframe(table.fetch(slice_=slice(0, 2))).fare[:]) == [10, 20]
        with q.transaction() as db:
            db.execute("UPDATE account SET cache_fill_suspended=0,quota=0")
    for include_cache in (True, False):
        export, _, cleanup = q.remote.export_store(table.store, include_cache=include_cache)
        try:
            exported = remote_store.inspect(export)
            assert exported["source"] == manifest["source"]
            assert bool(exported["parquet_caches"]) == include_cache
        finally:
            cleanup()

    source = tmp_path / "taxi.parquet"
    pq.write_table(pa.table({"fare": [40, 50, 60, 70], "cab": ["d", "e", "f", "g"]}), source)
    fsspec.filesystem("memory").pipe_file(manifest["source"]["urlpath"], source.read_bytes())
    assert list(blosc2.ctable_from_cframe(table.fetch(slice_=slice(0, 2))).fare[:]) == [10, 20]
    path.write_bytes(table.store.refreshed_bytes())
    updated = server.open_b2(path, "@public/parquet-reference.b2z")
    assert list(blosc2.ctable_from_cframe(updated.fetch(slice_=slice(0, 2))).fare[:]) == [40, 50]


@pytest.mark.parametrize("policy", ["none", "memory"])
def test_remote_parquet_without_retention(parquet_runtime, policy):
    from caterva2.services import server

    manifest = dict(
        remote_store.inspect(parquet_runtime),
        cache_policy=policy,
        max_cache_bytes=None if policy == "none" else 1 << 20,
    )
    path = parquet_runtime.with_name("uncached.b2z")
    remote_store.cold_export(manifest, path)
    table = server.open_b2(path, "@public/uncached.b2z")
    result = blosc2.ctable_from_cframe(table.fetch(slice_=slice(1, 3), field="cab"))
    assert list(result.cab[:]) == ["b", "c"]
    assert server.quota_coordinator().usage()["remote_cache_used"] == 0
    path.write_bytes(table.store.refreshed_bytes())
    assert remote_store.inspect(path)["cache_policy"] == policy


@pytest.mark.asyncio
async def test_remote_parquet_api(parquet_runtime, monkeypatch):
    import httpx

    from caterva2.services import server

    path = parquet_runtime
    monkeypatch.setattr(server.settings, "statedir", path.parents[1])
    monkeypatch.setattr(server.settings, "public", path.parent)
    monkeypatch.setitem(server.app.dependency_overrides, server.optional_user, lambda: None)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=server.app), base_url="http://test"
    ) as client:
        response = await client.get("/api/info/@public/parquet-reference.b2z")
        assert response.status_code == 200, response.text
        assert response.json()["kind"] == "ctable"
        response = await client.get(
            "/api/fetch/@public/parquet-reference.b2z",
            params={"filter": "fare >= 20", "field": "cab", "slice_": "0:1"},
        )
        assert response.status_code == 200, response.text
        assert list(blosc2.ctable_from_cframe(response.content).cab[:]) == ["b"]
        response = await client.post(
            "/htmx/path-view/@public/parquet-reference.b2z", data={"sortby": "fare"}
        )
        assert response.status_code == 200, response.text
        response = await client.get(
            "/api/download/@public/parquet-reference.b2z", params={"include_cache": "false"}
        )
        assert response.status_code == 200, response.text
        exported = path.with_name("downloaded.b2z")
        exported.write_bytes(response.content)
        assert remote_store.inspect(exported)["parquet_caches"] == []


def test_remote_parquet_resource_limit(parquet_runtime, monkeypatch):
    import fastapi

    from caterva2.services import server

    monkeypatch.setattr(remote_proxy, "policy", dataclasses.replace(remote_proxy.policy, max_nbytes=1))
    with pytest.raises(fastapi.HTTPException) as exc:
        server.open_b2(parquet_runtime, "@public/parquet-reference.b2z")
    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_standalone_remote_ctable_api(table_runtime, monkeypatch):
    import httpx

    from caterva2.services import server

    path = table_runtime
    monkeypatch.setattr(server.settings, "statedir", path.parents[1])
    monkeypatch.setattr(server.settings, "public", path.parent)
    monkeypatch.setattr(server.settings, "shared", path.parents[1] / "shared")
    monkeypatch.setattr(server.settings, "personal", path.parents[1] / "personal")
    overrides = dict(server.app.dependency_overrides)
    server.app.dependency_overrides[server.optional_user] = lambda: None
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=server.app), base_url="http://test"
        ) as client:
            endpoint = "/api/info/@public/table-reference.b2z"
            response = await client.get(endpoint)
            assert response.status_code == 200, response.text
            assert response.json()["kind"] == "ctable"
            response = await client.get("/api/fetch/@public/table-reference.b2z", params={"filter": "x > 1"})
            assert response.status_code == 200, response.text
            assert list(blosc2.ctable_from_cframe(response.content).x[:]) == [2]
            response = await client.get("/api/fetch/@public/table-reference.b2z", params={"field": "text"})
            assert response.status_code == 200, response.text
            assert list(blosc2.ctable_from_cframe(response.content).text[:]) == ["one", "two"]
            response = await client.get(
                "/api/fetch/@public/table-reference.b2z", params={"field": "text", "slice_": "1:2"}
            )
            assert response.status_code == 200, response.text
            assert list(blosc2.ctable_from_cframe(response.content).text[:]) == ["two"]
            response = await client.get(
                "/api/fetch/@public/table-reference.b2z", params={"field": "text", "filter": "x > 1"}
            )
            assert response.status_code == 200, response.text
            assert list(blosc2.ctable_from_cframe(response.content).text[:]) == ["two"]
            response = await client.post(
                "/api/fetch/@public/table-reference.b2z",
                json={"field": "text", "filter": "x > 1", "slice_": "0:1"},
            )
            assert response.status_code == 200, response.text
            assert list(blosc2.ctable_from_cframe(response.content).text[:]) == ["two"]
            for selection in ("0:2:2", "0:2:0", "0:1,0:1"):
                response = await client.get(
                    "/api/fetch/@public/table-reference.b2z", params={"slice_": selection}
                )
                assert response.status_code == 400, response.text
            local = path.with_name("local-table.b2z")
            local.write_bytes((path.parents[2] / "table.b2z").read_bytes())
            response = await client.get(
                "/api/fetch/@public/local-table.b2z",
                params={"filter": "x > 1", "field": "text", "slice_": "0:1"},
            )
            assert response.status_code == 200, response.text
            assert list(blosc2.ctable_from_cframe(response.content).text[:]) == ["two"]
            response = await client.get("/api/fetch/@public/local-table.b2z", params={"field": "missing"})
            assert response.status_code == 400, response.text
            response = await client.get(
                "/api/fetch/@public/table-reference.b2z", params={"field": "missing"}
            )
            assert response.status_code == 400, response.text
            response = await client.get(
                "/api/fetch/@public/table-reference.b2z", params={"filter": "missing > 1"}
            )
            assert response.status_code == 400, response.text
            response = await client.post("/htmx/path-view/@public/table-reference.b2z", data={"sortby": "x"})
            assert response.status_code == 200, response.text
            assert "one" in response.text
            assert "two" in response.text
            response = await client.get(
                "/api/download/@public/table-reference.b2z", params={"include_cache": "false"}
            )
            assert response.status_code == 200, response.text
            downloaded = path.with_name("downloaded-reference.b2z")
            downloaded.write_bytes(response.content)
            assert remote_store.root_kind(remote_store.inspect(downloaded)) == "ctable"
            response = await client.get("/api/chunk/@public/table-reference.b2z", params={"nchunk": 0})
            assert response.status_code == 400
    finally:
        server.app.dependency_overrides.clear()
        server.app.dependency_overrides.update(overrides)


@pytest.mark.asyncio
async def test_standalone_remote_ctable_refresh(table_runtime, monkeypatch):
    import httpx

    from caterva2.services import server

    path = table_runtime.with_name("refreshable-table.b2z")
    manifest = dict(remote_store.inspect(table_runtime), cache_policy="disk", max_cache_bytes=1 << 20)
    remote_store.cold_export(manifest, path)
    root = path.parents[1]
    monkeypatch.setattr(server.settings, "statedir", root)
    monkeypatch.setattr(server.settings, "public", path.parent)
    monkeypatch.setattr(server.settings, "shared", root / "shared")
    monkeypatch.setattr(server.settings, "personal", root / "personal")
    overrides = dict(server.app.dependency_overrides)
    server.app.dependency_overrides[server.current_active_user] = lambda: object()
    server.app.dependency_overrides[server.optional_user] = lambda: None
    endpoint = "/api/fetch/@public/refreshable-table.b2z"
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=server.app), base_url="http://test"
        ) as client:
            response = await client.post("/api/refresh/@public/missing.b2z")
            assert response.status_code == 404, response.text
            response = await client.get(endpoint)
            assert response.status_code == 200, response.text
            assert list(blosc2.ctable_from_cframe(response.content).x[:]) == [1, 2]
            with server.quota_coordinator().connect() as db:
                previous = db.execute(
                    "SELECT generation_id FROM remote_generations WHERE state='active'"
                ).fetchone()[0]

            @dataclasses.dataclass
            class Row:
                x: int = blosc2.field(blosc2.int64())
                text: str = blosc2.field(blosc2.vlstring(batch_rows=2))

            source = table_runtime.parents[2] / "table.b2z"
            blosc2.CTable(Row, [(3, "three")], create_summary_index=False).to_b2z(source, overwrite=True)
            fsspec.filesystem("memory").pipe_file(manifest["source"]["urlpath"], source.read_bytes())
            response = await client.post("/api/refresh/@public/refreshable-table.b2z")
            assert response.status_code == 200, response.text
            response = await client.get(endpoint)
            assert response.status_code == 200, response.text
            assert list(blosc2.ctable_from_cframe(response.content).x[:]) == [3]
            with server.quota_coordinator().connect() as db:
                current = db.execute(
                    "SELECT generation_id FROM remote_generations WHERE state='active'"
                ).fetchone()[0]
                old_state = db.execute(
                    "SELECT state FROM remote_generations WHERE generation_id=?", (previous,)
                ).fetchone()
            assert current != previous
            assert old_state is None or old_state[0] == "retired"
            before = path.read_bytes()
            fsspec.filesystem("memory").pipe_file(manifest["source"]["urlpath"], b"invalid B2Z")
            response = await client.post("/api/refresh/@public/refreshable-table.b2z")
            assert response.status_code in {400, 502}, response.text
            assert path.read_bytes() == before
            server.app.dependency_overrides[server.current_active_user] = lambda: None
            response = await client.post("/api/refresh/@public/refreshable-table.b2z")
            assert response.status_code == 401, response.text
            assert path.read_bytes() == before
            with server.quota_coordinator().connect() as db:
                assert (
                    db.execute(
                        "SELECT generation_id FROM remote_generations WHERE state='active'"
                    ).fetchone()[0]
                    == current
                )
            server.app.dependency_overrides[server.current_active_user] = lambda: object()

            def racing_refresh(store):
                server.quota_coordinator().publish(
                    path, before + b"replacement", expected=storage_quota.signature(path)
                )
                return before

            with monkeypatch.context() as patch:
                patch.setattr(remote_store.ServerRemoteStore, "refreshed_bytes", racing_refresh)
                response = await client.post("/api/refresh/@public/refreshable-table.b2z")
            assert response.status_code == 409, response.text
            assert path.read_bytes() == before + b"replacement"
    finally:
        server.app.dependency_overrides.clear()
        server.app.dependency_overrides.update(overrides)


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


def test_linked_remote_store_tables_and_arrays(store_runtime, tmp_path):
    _, path, data = store_runtime
    fs = fsspec.filesystem("memory")
    source = remote_store.inspect(path)["source"]["urlpath"]
    host = tmp_path / "host.b2z"
    with (
        blosc2.RemoteStore(source, dataset="g", _filesystem=fs) as linked,
        blosc2.TreeStore(host, mode="w") as tree,
    ):
        tree["linked"] = linked
    host_url = "https://data.example/host.b2z"
    fs.pipe_file(host_url, host.read_bytes())
    artifact = path.with_name("linked-store.b2z")
    with blosc2.RemoteStore(host_url, _filesystem=fs) as store:
        store.save(artifact, include_cache=False)

    adapter = srv_utils.open_container(artifact)
    assert adapter.leaves() == ["/linked/a", "/linked/b"]
    np.testing.assert_array_equal(adapter.get("linked/a")[:5000], data[:5000])


def test_linked_remote_store_warm_seed(store_runtime, tmp_path, monkeypatch):
    q, path, data = store_runtime
    fs = fsspec.filesystem("memory")
    source = remote_store.inspect(path)["source"]["urlpath"]
    host = tmp_path / "warm-linked-host.b2z"
    with (
        blosc2.RemoteStore(source, dataset="g", _filesystem=fs) as linked,
        blosc2.TreeStore(host, mode="w") as tree,
    ):
        tree["linked"] = linked
    host_url = "https://data.example/warm-linked-host.b2z"
    fs.pipe_file(host_url, host.read_bytes())
    warm = tmp_path / "warm-linked.b2z"
    with blosc2.RemoteStore(
        host_url,
        cache_dir=tmp_path / "creator-linked",
        _filesystem=fs,
        _filesystem_resolver=lambda url: fs,
    ) as store:
        with store["linked/a"] as array:
            np.testing.assert_array_equal(array[:5000], data[:5000])
        store.save(warm)
    artifact = path.with_name("warm-linked-reference.b2z")
    q.publish(artifact, warm.read_bytes(), expected=None)
    leaf = srv_utils.open_container(artifact).get("linked/a")
    assert remote_store.inspect(artifact)["linked"] == {}
    monkeypatch.setattr(
        blosc2.B2ZNDSource,
        "get_chunk",
        lambda *args: pytest.fail("warm linked leaf fetched upstream payload"),
    )
    np.testing.assert_array_equal(leaf[:5000], data[:5000])
    assert q.usage()["remote_cache_used"] > 0
    with q.connect() as db:
        private, source_stamp = db.execute(
            "SELECT g.relpath,g.source_stamp FROM remote_generations g "
            "JOIN remote_objects o ON g.object_id=o.object_id WHERE o.path=?",
            ("public/warm-linked-reference.b2z",),
        ).fetchone()
    import json

    removed, remaining = blosc2.RemoteStore.trim_sparse_cache(q.root / private, json.loads(source_stamp), 0)
    assert removed
    assert remaining == 0


def test_linked_remote_store_ctable(table_runtime, tmp_path):
    fs = fsspec.filesystem("memory")
    table_url = remote_store.inspect(table_runtime)["source"]["urlpath"]
    host = tmp_path / "host-table.b2z"
    with (
        blosc2.RemoteStore(table_url, allow_table_root=True, _filesystem=fs) as linked,
        blosc2.TreeStore(host, mode="w") as tree,
    ):
        tree["linked"] = linked
    host_url = "https://data.example/host-table.b2z"
    fs.pipe_file(host_url, host.read_bytes())
    artifact = table_runtime.with_name("linked-table.b2z")
    with blosc2.RemoteStore(host_url, _filesystem=fs) as store:
        store.save(artifact, include_cache=False)

    adapter = srv_utils.open_container(artifact)
    assert adapter.leaves() == ["/linked"]
    table = adapter.get("linked")
    assert list(blosc2.ctable_from_cframe(table.fetch()).text[:]) == ["one", "two"]


def test_linked_remote_store_denied_destination(store_runtime, tmp_path):
    from fastapi import HTTPException

    _, path, _ = store_runtime
    fs = fsspec.filesystem("memory")
    original = remote_store.inspect(path)["source"]["urlpath"]
    blocked = "https://blocked.example/source.b2z"
    fs.pipe_file(blocked, fs.cat_file(original))
    host = tmp_path / "host.b2z"
    with (
        blosc2.RemoteStore(blocked, dataset="g", _filesystem=fs) as linked,
        blosc2.TreeStore(host, mode="w") as tree,
    ):
        tree["linked"] = linked
    host_url = "https://data.example/host-with-blocked-link.b2z"
    fs.pipe_file(host_url, host.read_bytes())
    artifact = path.with_name("blocked-link.b2z")
    with blosc2.RemoteStore(host_url, _filesystem=fs) as store:
        store.save(artifact, include_cache=False)

    adapter = srv_utils.open_container(artifact)
    with pytest.raises(HTTPException, match=r"blocked\.example"):
        adapter.leaves()


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
