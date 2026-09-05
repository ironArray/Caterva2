"""Physical admission, independent processes, and filesystem/SQLite recovery."""

import concurrent.futures
import contextlib
import multiprocessing
import os
import pathlib
import sqlite3

import blosc2
import fsspec
import numpy as np
import pytest

from caterva2.services import remote_proxy, storage_quota


def writer_process(root, name, size, ready, release, result):
    quota = storage_quota.StorageQuota(root, 100, work_bytes=1000)
    original = os.replace

    def paused_replace(src, dst):
        ready.set()
        assert release.wait(10)
        return original(src, dst)

    os.replace = paused_replace
    try:
        quota.publish(pathlib.Path(root) / "public" / name, b"x" * size, expected=None, prune=False)
        result.put("ok")
    except storage_quota.QuotaExceeded:
        result.put("denied")


def crash_process(root, after_replace):
    quota = storage_quota.StorageQuota(root, 1000, work_bytes=1000)
    original = os.replace

    def crash(src, dst):
        if after_replace:
            original(src, dst)
        os._exit(23)

    os.replace = crash
    path = pathlib.Path(root) / "public" / "data"
    _, generation = quota.snapshot(path)
    quota.publish(path, b"new" * 20, expected=generation)


def test_exact_size_admission_and_replacement(tmp_path):
    quota = storage_quota.StorageQuota(tmp_path, 100, work_bytes=1000)
    path = tmp_path / "public" / "a"
    generation = quota.publish(path, b"a" * 70, expected=None)
    assert quota.usage()["used"] == 70
    with pytest.raises(storage_quota.QuotaExceeded):
        quota.publish(tmp_path / "shared" / "b", b"b" * 50, expected=None)
    quota.publish(path, b"a" * 90, expected=generation)
    assert quota.usage()["used"] == 90
    quota.publish(path, None, expected=storage_quota.signature(path))
    assert quota.usage()["used"] == 0
    assert quota.usage()["reserved"] == 0


def test_independent_processes_cannot_spend_same_capacity(tmp_path):
    quota = storage_quota.StorageQuota(tmp_path, 100, work_bytes=1000)
    ctx = multiprocessing.get_context("spawn")
    ready, release, result = ctx.Event(), ctx.Event(), ctx.Queue()
    first = ctx.Process(target=writer_process, args=(str(tmp_path), "a", 70, ready, release, result))
    first.start()
    try:
        assert ready.wait(10)
        assert quota.usage()["reserved"] == 70
        # Recovery cannot steal a live owner's reservation, regardless of elapsed time.
        quota.recover()
        assert quota.usage()["reserved"] == 70
        other = storage_quota.StorageQuota(tmp_path, 100, work_bytes=1000)
        with pytest.raises(storage_quota.QuotaExceeded):
            other.publish(tmp_path / "public" / "b", b"b" * 50, expected=None, prune=False)
    finally:
        release.set()
        first.join(10)
        if first.is_alive():
            first.terminate()
            first.join()
    assert first.exitcode == 0
    assert result.get(timeout=2) == "ok"
    assert quota.usage()["used"] == 70
    result.close()
    result.join_thread()


@pytest.mark.parametrize("after_replace", [False, True])
def test_recovery_after_worker_death(tmp_path, after_replace):
    quota = storage_quota.StorageQuota(tmp_path, 1000, work_bytes=1000)
    path = tmp_path / "public" / "data"
    quota.publish(path, b"old", expected=None)
    child = multiprocessing.get_context("spawn").Process(
        target=crash_process, args=(str(tmp_path), after_replace)
    )
    child.start()
    child.join(10)
    assert child.exitcode == 23
    assert quota.usage()["reserved"] > 0
    recovered = storage_quota.StorageQuota(tmp_path, 1000, work_bytes=1000)
    assert path.read_bytes() == (b"new" * 20 if after_replace else b"old")
    assert recovered.usage()["used"] == path.stat().st_size
    assert recovered.usage()["reserved"] == recovered.usage()["working"] == 0
    assert not list(recovered.control.glob("*.candidate"))
    recovered.recover()
    assert recovered.usage()["used"] == path.stat().st_size


def test_failed_publication_reconciles_before_retry(tmp_path, monkeypatch):
    quota = storage_quota.StorageQuota(tmp_path, 1000)
    path = tmp_path / "public" / "data"
    original = os.replace

    def fail(*args):
        raise OSError("simulated disk error")

    monkeypatch.setattr(os, "replace", fail)
    with pytest.raises(OSError):
        quota.publish(path, b"hello", expected=None)
    monkeypatch.setattr(os, "replace", original)
    quota.publish(path, b"recovered", expected=None)
    assert quota.usage()["used"] == len(b"recovered")
    assert quota.usage()["reserved"] == 0


def test_generation_conflict_preserves_newer_data(tmp_path):
    quota = storage_quota.StorageQuota(tmp_path, 1000)
    path = tmp_path / "public" / "data"
    generation = quota.publish(path, b"first", expected=None)
    quota.publish(path, b"second", expected=generation)
    with pytest.raises(storage_quota.StorageBusy):
        quota.publish(path, b"stale", expected=generation)
    assert path.read_bytes() == b"second"


def test_staging_budget_is_independent_of_data_quota(tmp_path):
    quota = storage_quota.StorageQuota(tmp_path, 1000, work_bytes=50)
    with pytest.raises(storage_quota.QuotaExceeded):
        quota.publish(tmp_path / "public" / "large", b"x" * 51, expected=None)
    assert quota.usage()["reserved"] == 0


def test_open_reader_keeps_old_snapshot_after_replacement(tmp_path):
    quota = storage_quota.StorageQuota(tmp_path, 1000)
    path = tmp_path / "public" / "data"
    generation = quota.publish(path, b"old snapshot", expected=None)
    with path.open("rb") as reader:
        quota.publish(path, b"new snapshot", expected=generation)
        assert reader.read() == b"old snapshot"
        assert path.read_bytes() == b"new snapshot"


def test_restart_reconciles_offline_edits_and_reduced_quota(tmp_path):
    quota = storage_quota.StorageQuota(tmp_path, 1000)
    path = tmp_path / "public" / "data"
    quota.publish(path, b"before", expected=None)
    path.write_bytes(b"offline replacement")
    restarted = storage_quota.StorageQuota(tmp_path, 10)
    assert restarted.usage()["used"] == len(b"offline replacement")
    with pytest.raises(storage_quota.QuotaExceeded):
        restarted.publish(tmp_path / "public" / "new", b"x", expected=None)
    restarted.publish(path, b"small", expected=storage_quota.signature(path))
    assert restarted.usage()["used"] == 5


def test_inventory_excludes_operational_and_peer_storage(tmp_path):
    for root in ("public", "personal", "shared", "peercache", "media"):
        (tmp_path / root).mkdir()
        (tmp_path / root / "data").write_bytes(b"123")
    (tmp_path / "public" / "data.b2lock").write_bytes(b"lock")
    quota = storage_quota.StorageQuota(tmp_path, 5)
    assert quota.usage()["used"] == 9
    with pytest.raises(storage_quota.QuotaExceeded):
        quota.publish(tmp_path / "public" / "new", b"x", expected=None)
    quota.publish(
        tmp_path / "public" / "data", None, expected=storage_quota.signature(tmp_path / "public" / "data")
    )
    assert quota.usage()["used"] == 6


def test_symlinks_and_path_escape_rejected(tmp_path):
    quota = storage_quota.StorageQuota(tmp_path, 1000)
    with pytest.raises(ValueError):
        quota.publish(tmp_path / "public" / ".." / "outside", b"x", expected=None)
    (tmp_path / "public").mkdir()
    (tmp_path / "public" / "link").symlink_to(tmp_path)
    with pytest.raises(ValueError):
        quota.publish(tmp_path / "public" / "link" / "outside", b"x", expected=None)


def test_configured_state_directory_alias_is_supported(tmp_path):
    root = tmp_path / "real"
    root.mkdir()
    alias = tmp_path / "alias"
    alias.symlink_to(root, target_is_directory=True)
    quota = storage_quota.StorageQuota(alias, 1000)
    path = alias / "public" / "data"
    quota.publish(path, b"hello", expected=None)
    assert quota.snapshot(path)[0] == b"hello"
    assert quota.usage()["used"] == 5


def remote_fixture(tmp_path, name="proxy", *, limit=None, block=10000):
    data = np.random.default_rng(1).integers(0, 256, 30000, dtype="u1")
    array = blosc2.asarray(data, chunks=(10000,), blocks=(block,))
    url = f"memory://quota-{name}.b2nd"
    fsspec.filesystem("memory").pipe_file(f"quota-{name}.b2nd", array.to_cframe())
    path = tmp_path / "public" / f"{name}.b2nd"
    path.parent.mkdir(exist_ok=True)
    creator = blosc2.RemoteProxy(
        url, cache_policy=blosc2.CachePolicy.DISK, cache_path=path, max_cache_bytes=limit
    )
    creator.schunk.vlmeta["user-note"] = "preserve me"
    carrier, payload = remote_proxy.inspect(path)
    proxy = remote_proxy.ServerRemoteProxy(
        creator.src, (array.shape, array.dtype, array.chunks, array.blocks), carrier, payload
    )
    return proxy, path, data


@pytest.mark.parametrize("limit", [None, 15000])
def test_proxy_grows_with_quota_and_reuses_data(tmp_path, limit):
    proxy, path, data = remote_fixture(tmp_path, limit=limit)
    quota = storage_quota.StorageQuota(tmp_path, 100000)
    before = path.stat().st_size
    np.testing.assert_array_equal(proxy.quota_read(quota, slice(0, 10000)), data[:10000])
    assert path.stat().st_size > before
    assert quota.usage()["used"] == path.stat().st_size
    proxy.src.traffic.reset()
    np.testing.assert_array_equal(proxy.quota_read(quota, slice(0, 10000)), data[:10000])
    assert proxy.src.traffic.requests == 0
    proxy.quota_read(quota, nchunk=1)
    assert quota.usage()["used"] <= quota.quota


def test_proxy_denied_retention_still_returns_data(tmp_path):
    proxy, path, data = remote_fixture(tmp_path)
    before = path.read_bytes()
    quota = storage_quota.StorageQuota(tmp_path, len(before) + 10032)
    # A compressed chunk fits this allowance, its full carrier metadata does not.
    np.testing.assert_array_equal(proxy.quota_read(quota, slice(0, 10000)), data[:10000])
    assert path.read_bytes() == before
    assert quota.usage()["used"] == len(before)


def test_partial_block_fill_then_whole_chunk_is_consistent(tmp_path):
    proxy, path, data = remote_fixture(tmp_path, block=2500)
    quota = storage_quota.StorageQuota(tmp_path, 100000)
    np.testing.assert_array_equal(proxy.quota_read(quota, slice(0, 200)), data[:200])
    proxy.src.traffic.reset()
    np.testing.assert_array_equal(proxy.quota_read(quota, slice(0, 200)), data[:200])
    assert proxy.src.traffic.requests == 0
    result = proxy.quota_read(quota, nchunk=0)
    np.testing.assert_array_equal(np.frombuffer(blosc2.decompress(result), dtype="u1"), data[:10000])
    assert quota.usage()["used"] == path.stat().st_size


def test_cross_proxy_pruning_preserves_descriptor_and_metadata(tmp_path):
    left, lp, data = remote_fixture(tmp_path, "left")
    right, rp, _ = remote_fixture(tmp_path, "right")
    quota = storage_quota.StorageQuota(tmp_path, lp.stat().st_size + rp.stat().st_size + 12000)
    left.quota_read(quota, slice(0, 10000))
    left.src.traffic.reset()
    right.quota_read(quota, slice(0, 10000))
    assert quota.usage()["used"] <= quota.quota
    raw = remote_proxy.raw_carrier(lp)
    assert raw.schunk.vlmeta["user-note"] == "preserve me"
    assert raw.schunk.vlmeta["b2o"]["cache_policy"] == "disk"
    assert not raw.schunk.vlmeta.get("proxy-fetched")
    np.testing.assert_array_equal(left.quota_read(quota, slice(0, 10000)), data[:10000])
    assert left.src.traffic.requests > 0


def test_parallel_proxy_and_upload_share_quota(tmp_path):
    proxy, path, data = remote_fixture(tmp_path)
    quota = storage_quota.StorageQuota(tmp_path, path.stat().st_size + 12000)
    upload = tmp_path / "shared" / "upload"

    def write():
        with contextlib.suppress(storage_quota.QuotaExceeded):
            quota.publish(upload, b"x" * 11000, expected=None, prune=False)

    with concurrent.futures.ThreadPoolExecutor(2) as pool:
        future = pool.submit(write)
        np.testing.assert_array_equal(proxy.quota_read(quota, slice(0, 10000)), data[:10000])
        future.result()
    measured = path.stat().st_size + (upload.stat().st_size if upload.exists() else 0)
    assert quota.usage()["used"] == measured <= quota.quota


def test_sqlite_failure_does_not_turn_cache_miss_into_read_failure(tmp_path, monkeypatch):
    proxy, _, data = remote_fixture(tmp_path)
    quota = storage_quota.StorageQuota(tmp_path, 100000)

    def fail(*args, **kwargs):
        raise sqlite3.OperationalError("busy")

    monkeypatch.setattr(quota, "publish", fail)
    np.testing.assert_array_equal(proxy.quota_read(quota, slice(0, 10000)), data[:10000])
