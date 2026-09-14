"""Quota-controlled writers and remote fetches through the actual ASGI routes."""

import io
import pathlib
import types
import uuid

import blosc2
import fsspec
import h5py
import httpx
import numpy as np
import pytest
import pytest_asyncio

from caterva2.services import remote_proxy


@pytest_asyncio.fixture
async def quota_api(tmp_path, monkeypatch):
    monkeypatch.setenv("CATERVA2_SECRET", "quota-test-secret")
    from caterva2.services import server

    user = types.SimpleNamespace(id=uuid.uuid4(), is_superuser=True, is_active=True)
    monkeypatch.setattr(server.settings, "statedir", tmp_path)
    monkeypatch.setattr(server.settings, "quota", 100_000)
    monkeypatch.setattr(server.settings, "publish_root", None)
    monkeypatch.setattr(server, "_quota_instances", {})
    monkeypatch.setattr(remote_proxy, "policy", remote_proxy.policy)
    for name in ("public", "shared", "personal"):
        path = tmp_path / name
        path.mkdir()
        monkeypatch.setattr(server.settings, name, path)
    overrides = dict(server.app.dependency_overrides)
    server.app.dependency_overrides[server.current_active_user] = lambda: user
    server.app.dependency_overrides[server.optional_user] = lambda: user
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=server.app), base_url="http://test"
        ) as client:
            yield server, client, user
    finally:
        server.app.dependency_overrides.clear()
        server.app.dependency_overrides.update(overrides)


def assert_usage(server):
    quota = server.quota_coordinator()
    measured = sum(row[1] for row in quota.inventory()) + quota.usage()["remote_cache_used"]
    assert quota.usage()["used"] == measured <= quota.usage()["quota"]
    assert quota.usage()["reserved"] == quota.usage()["working"] == 0


@pytest.mark.asyncio
async def test_upload_copy_append_remove_and_quota_denial(quota_api):
    server, client, _ = quota_api
    array = blosc2.arange(20, chunks=(10,), blocks=(5,))
    response = await client.post("/api/upload/@public/a.b2nd", files={"file": ("a.b2nd", array.to_cframe())})
    assert response.status_code == 200, response.text
    response = await client.post("/api/copy/", json={"src": "@public/a.b2nd", "dst": "@shared/b.b2nd"})
    assert response.status_code == 200, response.text
    response = await client.post("/api/append/@public/a.b2nd", files={"file": ("a.b2nd", array.to_cframe())})
    assert response.status_code == 200, response.text
    np.testing.assert_array_equal(
        blosc2.open(server.settings.public / "a.b2nd")[:], np.tile(np.arange(20), 2)
    )
    assert_usage(server)
    response = await client.post(
        "/api/upload/@public/too-big.b2nd", files={"file": ("large", b"x" * 100_001)}
    )
    assert response.status_code == 400
    assert not (server.settings.public / "too-big.b2nd").exists()
    response = await client.post("/api/remove/@shared/b.b2nd")
    assert response.status_code == 200, response.text
    assert_usage(server)


@pytest.mark.asyncio
async def test_chunk_writes_charge_metadata_and_keep_generation(quota_api):
    server, client, _ = quota_api
    empty = blosc2.uninit(20, dtype="i4", chunks=(10,), blocks=(5,))
    source = blosc2.arange(20, dtype="i4", chunks=(10,), blocks=(5,))
    response = await client.post(
        "/api/upload/@public/fill.b2nd", files={"file": ("fill.b2nd", empty.to_cframe())}
    )
    assert response.status_code == 200
    for i in range(2):
        response = await client.post(
            "/api/chunk/@public/fill.b2nd", params={"nchunk": i}, content=source.schunk.get_chunk(i)
        )
        assert response.status_code == 200, response.text
    np.testing.assert_array_equal(blosc2.open(server.settings.public / "fill.b2nd")[:], np.arange(20))
    assert_usage(server)
    response = await client.post(
        "/api/chunk/@public/fill.b2nd", params={"nchunk": 0}, content=source.schunk.get_chunk(0)
    )
    assert response.status_code == 409


@pytest.mark.asyncio
async def test_expression_and_notebook_use_admission(quota_api):
    server, client, user = quota_api
    array = blosc2.arange(20, chunks=(10,), blocks=(5,))
    await client.post("/api/upload/@public/a.b2nd", files={"file": ("a.b2nd", array.to_cframe())})
    expr = types.SimpleNamespace(
        name="expr", expression="a + 1", operands={"a": "@public/a.b2nd"}, func=None, compute=False
    )
    server.make_expr(expr, user)
    result = blosc2.open(server.settings.personal / str(user.id) / "expr.b2nd")
    np.testing.assert_array_equal(result[:], np.arange(20) + 1)
    expr.compute = True
    server.make_expr(expr, user)
    response = await client.post("/api/addnotebook/@public/new.ipynb")
    assert response.status_code == 200, response.text
    assert_usage(server)


@pytest.mark.asyncio
async def test_legacy_python_udf_uses_in_memory_serialization(quota_api):
    server, client, user = quota_api
    array = blosc2.arange(20, dtype="f8", chunks=(10,), blocks=(5,))
    server.write_dataset(server.settings.public / "a.b2nd", array.to_cframe())
    expr = types.SimpleNamespace(
        name="legacy",
        expression=None,
        operands={"o0": "@public/a.b2nd"},
        compute=False,
        dtype=np.dtype("f8"),
        shape=(20,),
        func="def legacy(inputs, output, offset):\n    output[:] = np.logaddexp(inputs[0], 1)\n",
    )
    server.make_expr(expr, user)
    response = await client.get("/api/fetch/@personal/legacy.b2nd")
    assert response.status_code == 200, response.text
    np.testing.assert_allclose(
        blosc2.ndarray_from_cframe(response.content)[:], np.logaddexp(np.arange(20), 1)
    )
    assert_usage(server)


@pytest.mark.asyncio
async def test_hdf5_unfold_admits_each_proxy(quota_api):
    server, client, _ = quota_api
    buffer = io.BytesIO()
    with h5py.File(buffer, "w") as file:
        file.create_dataset("group/data", data=np.arange(20))
    response = await client.post("/api/upload/@public/a.h5", files={"file": ("a.h5", buffer.getvalue())})
    assert response.status_code == 200
    response = await client.post("/api/unfold/@public/a.h5")
    assert response.status_code == 200, response.text
    assert (server.settings.public / "a/group/data.b2nd").exists()
    assert_usage(server)


@pytest.mark.asyncio
async def test_local_publish_cannot_bypass_managed_storage(quota_api, monkeypatch):
    server, _, _ = quota_api
    path = server.settings.public / "source.b2nd"
    server.write_dataset(path, blosc2.arange(20).to_cframe())
    monkeypatch.setattr(server.settings, "publish_root", server.settings.public.as_uri())
    with pytest.raises(server.fastapi.HTTPException) as error:
        server.publish_dataset(path, pathlib.Path("copy.b2nd"))
    assert error.value.status_code == 400
    assert not (server.settings.public / "copy.b2nd").exists()
    assert_usage(server)


@pytest.mark.asyncio
async def test_overwrite_and_remove_do_not_read_old_payload(quota_api, monkeypatch):
    server, _, _ = quota_api
    path = server.settings.public / "data.b2nd"
    server.write_dataset(path, b"original")
    monkeypatch.setattr(pathlib.Path, "read_bytes", lambda self: pytest.fail("read old dataset payload"))
    server.write_dataset(path, b"replacement")
    server.remove_dataset(path)
    assert not path.exists()
    assert_usage(server)


@pytest.mark.asyncio
async def test_move_does_not_delete_a_concurrent_source_replacement(quota_api, monkeypatch):
    server, _, _ = quota_api
    source, destination = server.settings.public / "source", server.settings.shared / "dest"
    server.write_dataset(source, b"original")
    writer = server.write_dataset

    def racing_writer(path, data):
        writer(path, data)
        writer(source, b"newer data")

    monkeypatch.setattr(server, "write_dataset", racing_writer)
    with pytest.raises(server.storage_quota.StorageBusy):
        server.move_dataset(source, destination)
    assert destination.read_bytes() == b"original"
    assert source.read_bytes() == b"newer data"
    assert_usage(server)


@pytest.mark.asyncio
async def test_disk_fetch_and_chunk_admit_growth_via_secure_source(quota_api, monkeypatch):
    server, client, _ = quota_api
    data = np.random.default_rng(1).integers(0, 256, 30000, dtype="u1")
    array = blosc2.asarray(data, chunks=(10000,), blocks=(10000,))
    fs = fsspec.filesystem("memory")
    fs.pipe_file("quota-api-source.b2nd", array.to_cframe())
    creator = blosc2.RemoteArray("memory://quota-api-source.b2nd", cache_policy=blosc2.CachePolicy.MEMORY)
    carrier = blosc2.ndarray_from_cframe(creator.to_cframe(cache_policy=blosc2.CachePolicy.DISK), copy=True)
    payload = dict(carrier.schunk.vlmeta["b2o"])
    url = "https://data.example/quota.b2nd"
    payload["source"] = {
        "kind": "fsspec",
        "version": 1,
        "urlpath": url,
        "assume_immutable": True,
    }
    carrier.schunk.vlmeta["b2o"] = payload
    fs.pipe_file(url, array.to_cframe())
    monkeypatch.setattr(
        remote_proxy, "policy", remote_proxy.Policy(enabled=True, allowed_hosts=("data.example",))
    )
    monkeypatch.setattr(remote_proxy, "_public_addresses", lambda *a: ("93.184.216.34",))
    monkeypatch.setattr(remote_proxy, "_https_filesystem", lambda *a: fs)
    response = await client.post(
        "/api/upload/@public/proxy.b2nd", files={"file": ("proxy.b2nd", carrier.to_cframe())}
    )
    assert response.status_code == 200
    path = server.settings.public / "proxy.b2nd"
    response = await client.get("/api/fetch/@public/proxy.b2nd", params={"slice_": "0:10000"})
    assert response.status_code == 200, response.text
    np.testing.assert_array_equal(blosc2.ndarray_from_cframe(response.content)[:], data[:10000])
    assert server.quota_coordinator().usage()["remote_cache_used"] > 0
    response = await client.get("/api/chunk/@public/proxy.b2nd", params={"nchunk": 1})
    assert response.status_code == 200, response.text
    np.testing.assert_array_equal(
        np.frombuffer(blosc2.decompress(response.content), dtype="u1"), data[10000:20000]
    )
    assert_usage(server)
    response = await client.get("/api/download/@public/proxy.b2nd", params={"include_cache": "false"})
    assert response.status_code == 200
    cold = blosc2.ndarray_from_cframe(response.content)
    assert cold.schunk.vlmeta["b2o"] == payload


@pytest.mark.asyncio
@pytest.mark.parametrize("quota_enabled", [True, False])
async def test_sparse_boundary_lifecycle(quota_api, monkeypatch, quota_enabled):
    server, client, _ = quota_api
    monkeypatch.setattr(server.settings, "quota", 100_000 if quota_enabled else 0)
    fs = fsspec.filesystem("memory")
    data = np.random.default_rng(71).integers(0, 256, 30000, dtype="u1")
    array = blosc2.asarray(data, chunks=(10000,), blocks=(10000,))
    url = "https://data.example/sparse-boundary.b2nd"
    fs.pipe_file(url, array.to_cframe())
    monkeypatch.setattr(
        remote_proxy,
        "policy",
        remote_proxy.Policy(enabled=True, allowed_hosts=("data.example",), cache_backend="sparse"),
    )
    monkeypatch.setattr(remote_proxy, "_public_addresses", lambda *args: ("93.184.216.34",))
    monkeypatch.setattr(remote_proxy, "_https_filesystem", lambda *args: fs)
    from blosc2.b2objects import make_b2object_carrier, write_b2object_payload

    carrier = make_b2object_carrier(
        "remote_array", array.shape, array.dtype, chunks=array.chunks, blocks=array.blocks
    )
    write_b2object_payload(
        carrier,
        {
            "kind": "remote_array",
            "version": 1,
            "source": {
                "kind": "fsspec",
                "version": 1,
                "urlpath": url,
                "assume_immutable": True,
            },
            "cache_policy": "disk",
            "max_cache_bytes": 15000,
        },
    )
    response = await client.post(
        "/api/upload/@public/sparse.b2nd", files={"file": ("sparse.b2nd", carrier.to_cframe())}
    )
    assert response.status_code == 200, response.text
    for i in [0, 1, 2, 0]:
        response = await client.get("/api/chunk/@public/sparse.b2nd", params={"nchunk": i})
        assert response.status_code == 200, response.text
        np.testing.assert_array_equal(
            np.frombuffer(blosc2.decompress(response.content), dtype="u1"), data[i * 10000 : (i + 1) * 10000]
        )
    quota = server.quota_coordinator()
    assert quota.usage()["remote_cache_used"] > 10000
    public = blosc2.blosc2_ext.open(str(server.settings.public / "sparse.b2nd"), "r", 0)
    assert public.schunk.vlmeta.get("proxy-fetched") is None
    with quota.connect() as db:
        assert db.execute("SELECT count(*) FROM remote_generations WHERE state='active'").fetchone()[0] == 1
    response = await client.get("/api/download/@public/sparse.b2nd")
    assert response.status_code == 200, response.text
    exported = blosc2.ndarray_from_cframe(response.content)
    np.testing.assert_array_equal(exported[:10000], data[:10000])
    assert quota.usage()["working"] == 0
    response = await client.get("/api/download/@public/sparse.b2nd", headers={"Range": "bytes=0-127"})
    assert response.status_code == 206, response.text
    assert len(response.content) == 128
    assert quota.usage()["working"] == 0
    response = await client.get(
        "/api/download/@public/sparse.b2nd", headers={"Range": "bytes=0-127", "If-Range": '"old"'}
    )
    assert response.status_code == 200
    assert quota.usage()["working"] == 0
    response = await client.post("/api/remove/@public/sparse.b2nd")
    assert response.status_code == 200, response.text
    quota.remote.cleanup()
    assert quota.usage()["remote_cache_used"] == 0
