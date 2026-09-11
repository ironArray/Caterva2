"""Private cache lifecycle, recovery, admission and offline reclamation."""

import blosc2
import fsspec
import numpy as np
import pytest
from blosc2.b2objects import make_b2object_carrier, write_b2object_payload

from caterva2.services import remote_proxy
from caterva2.services.sparse_cache import measure
from caterva2.services.storage_quota import StorageQuota, signature


@pytest.fixture
def runtime(tmp_path):
    for root in ("public", "shared", "personal"):
        (tmp_path / root).mkdir()
    data = np.random.default_rng(33).integers(0, 256, 60000, dtype="u1")
    array = blosc2.asarray(data, chunks=(20000,), blocks=(5000,))
    url = "https://data.example/private-test.b2nd"
    fs = fsspec.filesystem("memory")
    fs.pipe_file(url, array.to_cframe())
    carrier = make_b2object_carrier(
        "remote_array",
        array.shape,
        array.dtype,
        chunks=array.chunks,
        blocks=array.blocks,
        meta={"user-fixed": {"test": True}},
    )
    carrier.schunk.vlmeta["user-variable"] = {"sample": 42}
    payload = {
        "kind": "remote_array",
        "version": 1,
        "source": {"kind": "fsspec", "version": 1, "urlpath": url, "assume_immutable": True},
        "cache_policy": "disk",
        "max_cache_bytes": None,
    }
    write_b2object_payload(carrier, payload)
    q = StorageQuota(tmp_path, 1 << 20, cache_backend="sparse")
    path = tmp_path / "public/proxy.b2nd"
    q.publish(path, carrier.to_cframe(), expected=None)

    def resolve():
        source = blosc2.FsspecNDSource(url, _filesystem=fs)
        c = remote_proxy.raw_carrier(path)
        return remote_proxy.ServerRemoteArray(
            source, (array.shape, array.dtype, array.chunks, array.blocks), c, payload
        )

    return q, resolve, data, path


def test_offline_pruning_and_hysteresis(runtime, monkeypatch):
    q, resolve, data, _ = runtime
    np.testing.assert_array_equal(q.remote.read(resolve()), data)
    used = q.usage()["used"]
    with q.transaction() as db:
        db.execute("UPDATE account SET quota=?", (used - 10000,))

    def forbidden(*args, **kwargs):
        raise AssertionError("offline pruning contacted source")

    monkeypatch.setattr(blosc2, "FsspecNDSource", forbidden)
    q.remote.prune()
    assert q.usage()["used"] < used
    assert q.usage()["used"] <= int((used - 10000) * 0.9)
    assert not q.usage()["cache_fill_suspended"]


def test_failed_mutation_is_charged_and_recovered_without_network(runtime, monkeypatch):
    q, resolve, data, _ = runtime
    read = blosc2.RemoteArray.__getitem__

    def fail_after_write(self, item):
        read(self, item)
        raise OSError("injected interrupted cache publication")

    with monkeypatch.context() as patch:
        patch.setattr(blosc2.RemoteArray, "__getitem__", fail_after_write)
        np.testing.assert_array_equal(q.remote.read(resolve()), data)
    with q.connect() as db:
        assert db.execute("SELECT count(*) FROM remote_operations").fetchone()[0] == 1

    def forbidden(*args, **kwargs):
        raise AssertionError("offline recovery contacted source")

    monkeypatch.setattr(blosc2, "FsspecNDSource", forbidden)
    q.remote.maintain()
    assert q.usage()["remote_cache_used"] == 0
    assert q.usage()["reserved"] == 0


def test_replace_reuses_path_while_trash_is_charged(runtime):
    q, resolve, data, path = runtime
    q.remote.read(resolve(), slice(0, 5000))
    before = q.usage()["remote_cache_used"]
    cold = path.read_bytes()
    q.publish(path, cold, expected=signature(path))
    assert q.usage()["remote_cache_used"] == before
    np.testing.assert_array_equal(q.remote.read(resolve(), slice(5000, 10000)), data[5000:10000])
    with q.connect() as db:
        assert db.execute("SELECT count(*) FROM remote_objects WHERE path IS NOT NULL").fetchone()[0] == 1
    q.remote.maintain()
    assert q.usage()["remote_cache_used"] > 0


def test_warm_artifact_metadata_and_cleanup(runtime):
    q, resolve, data, path = runtime
    q.remote.read(resolve(), slice(0, 10000))
    public = remote_proxy.raw_carrier(path)
    assert public.schunk.meta["user-fixed"] == {"test": True}
    assert public.schunk.vlmeta["user-variable"] == {"sample": 42}
    artifact, etag, cleanup = q.remote.export(resolve())
    try:
        assert len(etag) == 64
        exported = remote_proxy.raw_carrier(artifact)
        np.testing.assert_array_equal(exported[:10000], data[:10000])
        assert exported.schunk.vlmeta["user-variable"] == {"sample": 42}
        assert exported.schunk.meta["user-fixed"] == {"test": True}
        assert q.usage()["working"] > 0
        q.remote.recover_exports()  # Live owner must not be stolen.
        assert artifact.exists()
        del exported
    finally:
        cleanup()
    assert q.usage()["working"] == 0
    assert not artifact.exists()


def test_reconcile_accounts_allocated_files_and_orphans(runtime):
    q, resolve, _, _ = runtime
    q.remote.read(resolve(), slice(0, 5000))
    with q.connect() as db:
        rows = db.execute("SELECT relpath,charge_bytes FROM remote_generations").fetchall()
    for rel, charge in rows:
        assert measure(q.root / rel)[0] == charge
    orphan = q.remote.root / ("a" * 32) / ("b" * 32)
    orphan.mkdir(parents=True)
    (orphan / "payload").write_bytes(b"x" * 10000)
    q.remote.maintain()
    assert not orphan.exists()


def test_missing_active_directory_retires_binding(runtime):
    q, resolve, _, path = runtime
    q.remote.read(resolve(), slice(0, 5000))
    with q.connect() as db:
        _gid, private = db.execute("SELECT generation_id,relpath FROM remote_generations").fetchone()
    import shutil

    shutil.rmtree(q.root / private)
    q.remote.maintain()
    assert q.usage()["remote_cache_used"] == 0
    assert path.exists()


def _worker_read(statedir, ready, errors):
    """Spawn-safe deterministic source; no shared in-memory transport state."""
    try:
        q = StorageQuota(statedir, 1 << 20, cache_backend="sparse")
        data = np.random.default_rng(33).integers(0, 256, 60000, dtype="u1")
        array = blosc2.asarray(data, chunks=(20000,), blocks=(5000,))
        url = "https://data.example/private-test.b2nd"
        fs = fsspec.filesystem("memory")
        fs.pipe_file(url, array.to_cframe())
        ready.wait(timeout=20)
        for offset in [0, 10000, 30000, 50000, 0]:
            carrier = remote_proxy.raw_carrier(q.root / "public/proxy.b2nd")
            source = blosc2.FsspecNDSource(url, _filesystem=fs)
            source.stamp = "immutable-multiprocess-test-source"
            proxy = remote_proxy.ServerRemoteArray(
                source,
                (array.shape, array.dtype, array.chunks, array.blocks),
                carrier,
                carrier.schunk.vlmeta["b2o"],
            )
            actual = q.remote.read(proxy, slice(offset, offset + 5000))
            np.testing.assert_array_equal(actual, data[offset : offset + 5000])
        errors.put(None)
    except BaseException as exc:
        errors.put(repr(exc))


def test_multiple_workers_share_generation(runtime):
    import multiprocessing

    q, _, _, _ = runtime
    context = multiprocessing.get_context("spawn")
    ready, errors = context.Barrier(4), context.Queue()
    workers = [context.Process(target=_worker_read, args=(str(q.root), ready, errors)) for _ in range(4)]
    for worker in workers:
        worker.start()
    try:
        for _ in workers:
            assert errors.get(timeout=30) is None
        for worker in workers:
            worker.join(timeout=10)
            assert worker.exitcode == 0
    finally:
        for worker in workers:
            if worker.is_alive():
                worker.terminate()
                worker.join()
        errors.close()
    with q.connect() as db:
        assert db.execute("SELECT count(*) FROM remote_generations WHERE state='active'").fetchone()[0] == 1
        assert db.execute("SELECT count(*) FROM remote_operations").fetchone()[0] == 0


def test_old_authorization_cannot_coldify_replacement(runtime):
    q, resolve, data, path = runtime
    old = resolve()
    replacement = remote_proxy.raw_carrier(path)
    payload = dict(replacement.schunk.vlmeta["b2o"], max_cache_bytes=10000)
    cold = remote_proxy.cold_cframe(replacement, payload)
    del replacement
    q.publish(path, cold, expected=signature(path))
    # A replacement racing source authorization can be observed by a late stat.
    old.carrier_generation = signature(path)
    np.testing.assert_array_equal(q.remote.read(old, slice(0, 5000)), data[:5000])
    assert path.read_bytes() == cold
    q.remote.maintain()


def _worker_die(statedir):
    import os

    q = StorageQuota(statedir, 1 << 20, cache_backend="sparse")
    data = np.random.default_rng(33).integers(0, 256, 60000, dtype="u1")
    array = blosc2.asarray(data, chunks=(20000,), blocks=(5000,))
    url = "https://data.example/private-test.b2nd"
    fs = fsspec.filesystem("memory")
    fs.pipe_file(url, array.to_cframe())
    carrier = remote_proxy.raw_carrier(q.root / "public/proxy.b2nd")
    source = blosc2.FsspecNDSource(url, _filesystem=fs)
    proxy = remote_proxy.ServerRemoteArray(
        source, (array.shape, array.dtype, array.chunks, array.blocks), carrier, carrier.schunk.vlmeta["b2o"]
    )
    original = blosc2.Proxy._store_chunk

    def interrupted(self, *args):
        original(self, *args)
        os._exit(17)

    blosc2.Proxy._store_chunk = interrupted
    q.remote.read(proxy, nchunk=0)
    os._exit(18)  # The fault injection must actually hit a mutation boundary.


def test_process_death_releases_ownership_and_discards_dirty_generation(runtime):
    import multiprocessing

    q, resolve, data, path = runtime
    worker = multiprocessing.get_context("spawn").Process(target=_worker_die, args=(str(q.root),))
    worker.start()
    worker.join(timeout=20)
    if worker.is_alive():
        worker.terminate()
        worker.join()
        pytest.fail("worker did not reach the mutation boundary")
    assert worker.exitcode == 17
    q.remote.maintain()
    assert q.usage()["remote_cache_used"] == 0
    assert q.usage()["reserved"] == 0
    assert path.exists()
    np.testing.assert_array_equal(q.remote.read(resolve()), data)


def test_failed_parent_cleanup_keeps_its_charge(runtime, monkeypatch):
    from pathlib import Path

    q, resolve, _, path = runtime
    q.remote.read(resolve(), slice(0, 5000))
    q.publish(path, None, expected=signature(path))
    original = Path.rmdir

    def denied(parent):
        if parent.parent == q.remote.root:
            raise PermissionError("injected directory cleanup failure")
        return original(parent)

    with monkeypatch.context() as patch:
        patch.setattr(Path, "rmdir", denied)
        q.remote.maintain()
        with q.connect() as db:
            # APFS can report zero allocated directory blocks; ownership still
            # must survive so cleanup retries even when its byte charge is zero.
            assert db.execute("SELECT count(*) FROM remote_objects WHERE path IS NULL").fetchone()[0] == 1
    q.remote.maintain()
    assert q.usage()["remote_cache_used"] == 0
    with q.connect() as db:
        assert db.execute("SELECT count(*) FROM remote_objects").fetchone()[0] == 0
