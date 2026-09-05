###############################################################################
# Caterva2 - On demand access to remote Blosc2 data repositories
#
# Copyright (c) 2023 ironArray SLU <contact@ironarray.io>
# https://www.blosc.org
# License: GNU Affero General Public License v3.0
# See LICENSE.txt for details about copyright and rights to use.
###############################################################################

import asyncio
import concurrent.futures
import socket

import blosc2
import fsspec
import numpy as np
import pytest

from caterva2.services import remote_proxy


class _Conf:
    def __init__(self, values):
        self.values = values

    def get(self, key, default=None):
        return self.values.get(key, default)


def _payload(url, *, cache_policy="none", max_cache_bytes=None):
    return {
        "kind": "remote_proxy",
        "version": 1,
        "source": {"kind": "fsspec", "version": 1, "urlpath": url},
        "cache_policy": cache_policy,
        "max_cache_bytes": max_cache_bytes,
    }


@pytest.fixture(autouse=True)
def reset_policy():
    previous = remote_proxy.policy
    yield
    remote_proxy.policy = previous


@pytest.mark.parametrize(
    ("cache_policy", "max_cache_bytes"),
    [
        ("none", None),
        ("memory", 268435456),
        ("disk", 1_000_000),
        ("disk", None),
    ],
)
def test_resolution_is_default_deny(cache_policy, max_cache_bytes):
    remote_proxy.policy = remote_proxy.Policy()
    with pytest.raises(remote_proxy.RemoteProxyDenied, match="disabled"):
        remote_proxy._validated_source(
            _payload(
                "https://data.example/array.b2nd", cache_policy=cache_policy, max_cache_bytes=max_cache_bytes
            )
        )


@pytest.mark.parametrize(
    "url",
    [
        "http://data.example/array.b2nd",
        "https://user@data.example/array.b2nd",
        "https://data.example/array.b2nd?token=secret",
        "https://data.example:bad/array.b2nd",
        "https://other.example/array.b2nd",
    ],
)
@pytest.mark.parametrize(
    ("cache_policy", "max_cache_bytes"),
    [
        ("none", None),
        ("memory", 268435456),
        ("disk", 1_000_000),
    ],
)
def test_enabled_policy_still_rejects_unsafe_destinations(url, cache_policy, max_cache_bytes):
    remote_proxy.policy = remote_proxy.Policy(enabled=True, allowed_hosts=("data.example",))
    with pytest.raises(remote_proxy.RemoteProxyDenied):
        remote_proxy._validated_source(
            _payload(url, cache_policy=cache_policy, max_cache_bytes=max_cache_bytes)
        )


@pytest.mark.parametrize(
    ("cache_policy", "max_cache_bytes"),
    [
        ("none", None),
        ("memory", 268435456),
        ("memory", 500_000),
        ("disk", 1_000_000),
        ("disk", None),
    ],
)
def test_configured_https_destination_is_accepted(cache_policy, max_cache_bytes):
    remote_proxy.configure(
        _Conf(
            {
                ".remote_proxy.enabled": True,
                ".remote_proxy.allowed_hosts": ["DATA.example", "data.example:8443"],
            }
        )
    )
    assert remote_proxy._validated_source(
        _payload(
            "https://data.example/array.b2nd", cache_policy=cache_policy, max_cache_bytes=max_cache_bytes
        )
    )
    assert remote_proxy._validated_source(
        _payload(
            "https://data.example:8443/array.b2nd",
            cache_policy=cache_policy,
            max_cache_bytes=max_cache_bytes,
        )
    )


@pytest.mark.parametrize(
    ("payload", "match"),
    [
        (
            _payload("https://data.example/a.b2nd", cache_policy="unknown", max_cache_bytes=1000),
            "only cache policies",
        ),
        (
            _payload("https://data.example/a.b2nd", cache_policy="none", max_cache_bytes=1),
            "cannot have max_cache_bytes",
        ),
        (
            _payload("https://data.example/a.b2nd", cache_policy="disk", max_cache_bytes=True),
            "requires positive",
        ),
        (
            _payload("https://data.example/a.b2nd", cache_policy="disk", max_cache_bytes=False),
            "requires positive",
        ),
        (
            _payload("https://data.example/a.b2nd", cache_policy="disk", max_cache_bytes=0),
            "requires positive",
        ),
        (
            _payload("https://data.example/a.b2nd", cache_policy="disk", max_cache_bytes=-10),
            "requires positive",
        ),
        (
            _payload("https://data.example/a.b2nd", cache_policy="disk", max_cache_bytes=10.5),
            "requires positive",
        ),
        (
            _payload("https://data.example/a.b2nd", cache_policy="disk", max_cache_bytes="1000"),
            "requires positive",
        ),
        (_payload("https://data.example/a.b2nd", cache_policy="memory"), "requires positive"),
        (
            _payload("https://data.example/a.b2nd", cache_policy="memory", max_cache_bytes=True),
            "requires positive",
        ),
        (
            _payload("https://data.example/a.b2nd", cache_policy="memory", max_cache_bytes=False),
            "requires positive",
        ),
        (
            _payload("https://data.example/a.b2nd", cache_policy="memory", max_cache_bytes=0),
            "requires positive",
        ),
        (
            _payload("https://data.example/a.b2nd", cache_policy="memory", max_cache_bytes=-10),
            "requires positive",
        ),
        (
            _payload("https://data.example/a.b2nd", cache_policy="memory", max_cache_bytes=10.5),
            "requires positive",
        ),
        (
            _payload("https://data.example/a.b2nd", cache_policy="memory", max_cache_bytes="1000"),
            "requires positive",
        ),
    ],
)
def test_cache_specification_is_strict(payload, match):
    remote_proxy.policy = remote_proxy.Policy(enabled=True, allowed_hosts=("data.example",))
    with pytest.raises(remote_proxy.RemoteProxyDenied, match=match):
        remote_proxy._validated_source(payload)


def test_private_resolution_is_rejected(monkeypatch):
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda *args, **kwargs: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 443))],
    )
    with pytest.raises(remote_proxy.RemoteProxyDenied, match="non-public"):
        remote_proxy._public_addresses("data.example", 443)


def test_pinned_resolver_rejects_redirected_host():
    resolver = remote_proxy._PinnedResolver("data.example", ("203.0.113.10",))
    with pytest.raises(OSError, match="redirected hosts"):
        asyncio.run(resolver.resolve("other.example", 443))


def test_embedded_fsspec_reference_is_recognized():
    payload = {
        "kind": "lazyexpr",
        "operands": {"a": {"kind": "fsspec", "version": 1, "urlpath": "https://data.example/a"}},
    }
    assert remote_proxy._contains_remote_reference(payload)


@pytest.mark.parametrize(
    ("cache_policy", "max_cache_bytes", "expected_eff_policy", "expected_eff_limit"),
    [
        ("none", None, "none", None),
        ("memory", 268435456, "none", None),
        ("memory", 500_000, "none", None),
        ("disk", 1_000_000, "disk", 1_000_000),
        ("disk", None, "disk", None),
    ],
)
def test_allowed_source_is_resolved_with_the_secure_filesystem(
    monkeypatch, cache_policy, max_cache_bytes, expected_eff_policy, expected_eff_limit
):
    class Carrier:
        shape = (10,)
        dtype = np.dtype(np.int32)
        chunks = (5,)
        blocks = (5,)

        class schunk:
            urlpath = "/tmp/reference.b2nd"

    class Source:
        shape = Carrier.shape
        dtype = Carrier.dtype
        chunks = Carrier.chunks
        blocks = Carrier.blocks
        cparams = blosc2.CParams()

    filesystem = object()
    seen = {}

    def fake_source(url, max_concurrency, *, _filesystem):
        seen.update(url=url, max_concurrency=max_concurrency, filesystem=_filesystem)
        return Source()

    remote_proxy.policy = remote_proxy.Policy(
        enabled=True,
        allowed_hosts=("data.example",),
        max_concurrency=3,
    )
    monkeypatch.setattr(remote_proxy, "_public_addresses", lambda host, port: ("93.184.216.34",))
    monkeypatch.setattr(remote_proxy, "_https_filesystem", lambda host, addresses: filesystem)
    monkeypatch.setattr(blosc2, "FsspecNDSource", fake_source)

    payload = _payload(
        "https://data.example/array.b2nd",
        cache_policy=cache_policy,
        max_cache_bytes=max_cache_bytes,
    )
    resolved = remote_proxy.resolve(Carrier(), payload)
    assert isinstance(resolved, remote_proxy.ServerRemoteProxy)
    assert resolved.requested_cache_policy == cache_policy
    assert resolved.requested_max_cache_bytes == max_cache_bytes
    assert resolved.cache_policy == expected_eff_policy
    assert resolved.effective_cache_policy == expected_eff_policy
    assert resolved.max_cache_bytes == expected_eff_limit
    assert seen == {
        "url": "https://data.example/array.b2nd",
        "max_concurrency": 3,
        "filesystem": filesystem,
    }


def test_cold_cframe_preserves_specification_but_not_cached_chunks(tmp_path):
    source = blosc2.asarray(np.arange(20), chunks=(10,), blocks=(5,))
    source_url = "memory://cold-cframe-source.b2nd"
    fsspec.filesystem("memory").pipe_file("cold-cframe-source.b2nd", source.to_cframe())
    carrier_path = tmp_path / "proxy.b2nd"
    proxy = blosc2.RemoteProxy(
        source_url,
        cache_policy=blosc2.CachePolicy.DISK,
        cache_path=carrier_path,
        max_cache_bytes=1_000_000,
    )
    np.testing.assert_array_equal(proxy[:10], np.arange(10))

    carrier, payload = remote_proxy.inspect(carrier_path)
    assert carrier.schunk.vlmeta.get("proxy-cache-sizes")
    cold = blosc2.ndarray_from_cframe(remote_proxy.cold_cframe(carrier, payload))

    assert cold.schunk.vlmeta["b2o"] == payload
    assert not cold.schunk.vlmeta.get("proxy-cache-sizes", {})
    assert cold.to_cframe() != carrier.to_cframe()


def _server_proxy(tmp_path, name="server-cache", cache_policy="disk", max_cache_bytes=1_000_000):
    data = np.arange(40, dtype=np.int32)
    source = blosc2.asarray(data, chunks=(10,), blocks=(5,))
    source_url = f"memory://{name}-source.b2nd"
    fsspec.filesystem("memory").pipe_file(f"{name}-source.b2nd", source.to_cframe())
    carrier_path = tmp_path / f"{name}.b2nd"
    if cache_policy == "disk":
        creator = blosc2.RemoteProxy(
            source_url,
            cache_policy=blosc2.CachePolicy.DISK,
            cache_path=carrier_path,
            max_cache_bytes=max_cache_bytes,
        )
    elif cache_policy == "memory":
        creator = blosc2.RemoteProxy(
            source_url,
            cache_policy=blosc2.CachePolicy.MEMORY,
            max_cache_bytes=max_cache_bytes,
        )
        creator.save(carrier_path)
    elif cache_policy == "none":
        creator = blosc2.RemoteProxy(
            source_url,
            cache_policy=blosc2.CachePolicy.NONE,
        )
        creator.save(carrier_path)
    else:
        raise ValueError(f"unknown cache_policy: {cache_policy}")

    carrier, payload = remote_proxy.inspect(carrier_path)
    geometry = (creator.shape, creator.dtype, creator.chunks, creator.blocks)
    return remote_proxy.ServerRemoteProxy(creator.src, geometry, carrier, payload), data, carrier_path


def test_server_proxy_reuses_its_carrier_cache(tmp_path):
    proxy, data, carrier_path = _server_proxy(tmp_path)
    proxy.src.traffic.reset()
    np.testing.assert_array_equal(proxy.read(slice(0, 10)), data[:10])
    assert proxy.src.traffic.requests > 0

    carrier, payload = remote_proxy.inspect(carrier_path)
    fresh_source = blosc2.FsspecNDSource(payload["source"]["urlpath"])
    reopened = remote_proxy.ServerRemoteProxy(
        fresh_source,
        (proxy.shape, proxy.dtype, proxy.chunks, proxy.blocks),
        carrier,
        payload,
    )
    fresh_source.traffic.reset()
    np.testing.assert_array_equal(reopened.read(slice(0, 10)), data[:10])
    assert fresh_source.traffic.requests == 0


def test_server_proxy_memory_retains_no_cache_and_repeats_upstream_fetches(tmp_path):
    proxy, data, carrier_path = _server_proxy(
        tmp_path, "memory-proxy", cache_policy="memory", max_cache_bytes=500_000
    )
    assert proxy.requested_cache_policy == "memory"
    assert proxy.requested_max_cache_bytes == 500_000
    assert proxy.cache_policy == "none"
    assert proxy.effective_cache_policy == "none"
    assert proxy.max_cache_bytes is None
    assert proxy.current_cache_bytes() == 0

    before_bytes = carrier_path.read_bytes()
    before_size = carrier_path.stat().st_size
    before_mtime = carrier_path.stat().st_mtime_ns

    # Instrument data calls on proxy.src
    chunk_calls = []
    range_calls = []
    orig_get_chunk = proxy.src.get_chunk
    orig_read_range = proxy.src.read_range

    def traced_get_chunk(n):
        chunk_calls.append(n)
        return orig_get_chunk(n)

    def traced_read_range(*args, **kwargs):
        range_calls.append(args)
        return orig_read_range(*args, **kwargs)

    proxy.src.get_chunk = traced_get_chunk
    proxy.src.read_range = traced_read_range

    # First slice read
    slice_item = slice(0, 10)
    np.testing.assert_array_equal(proxy.read(slice_item), data[:10])
    first_chunk_count = len(chunk_calls)
    first_range_count = len(range_calls)
    assert first_chunk_count > 0 or first_range_count > 0

    # Second slice read on the same runtime: no retained data, must fetch again
    np.testing.assert_array_equal(proxy.read(slice_item), data[:10])
    assert len(chunk_calls) > first_chunk_count or len(range_calls) > first_range_count

    # Reconstruct the runtime and read slice again
    carrier, payload = remote_proxy.inspect(carrier_path)
    fresh_source = blosc2.FsspecNDSource(payload["source"]["urlpath"])
    fresh_chunk_calls = []
    fresh_orig_get_chunk = fresh_source.get_chunk

    def fresh_traced_get_chunk(n):
        fresh_chunk_calls.append(n)
        return fresh_orig_get_chunk(n)

    fresh_source.get_chunk = fresh_traced_get_chunk
    reopened = remote_proxy.ServerRemoteProxy(
        fresh_source,
        (proxy.shape, proxy.dtype, proxy.chunks, proxy.blocks),
        carrier,
        payload,
    )
    np.testing.assert_array_equal(reopened.read(slice_item), data[:10])
    assert len(fresh_chunk_calls) > 0 or fresh_source.traffic.requests > 0

    # Repeat for get_chunk
    chunk_calls.clear()
    chunk0_first = proxy.get_chunk(0)
    assert len(chunk_calls) == 1
    assert chunk_calls[0] == 0
    chunk0_second = proxy.get_chunk(0)
    assert len(chunk_calls) == 2
    assert chunk_calls[1] == 0
    assert chunk0_first == chunk0_second

    # Invariants on carrier file
    assert carrier_path.read_bytes() == before_bytes
    assert carrier_path.stat().st_size == before_size
    assert carrier_path.stat().st_mtime_ns == before_mtime
    assert proxy.current_cache_bytes() == 0


def test_memory_carrier_ignores_synthetic_cached_chunks(tmp_path):
    # Create DISK proxy and warm chunks 0 and 1
    disk_proxy, data, carrier_path = _server_proxy(tmp_path, "synthetic", cache_policy="disk")
    disk_proxy.read(slice(0, 20))
    raw = remote_proxy.raw_carrier(carrier_path, mode="a", locking=True)
    with raw.schunk.holding_lock():
        assert raw.schunk.vlmeta.get("proxy-cache-sizes")
        # Mutate carrier's payload to MEMORY
        payload = dict(raw.schunk.vlmeta["b2o"])
        payload["cache_policy"] = "memory"
        payload["max_cache_bytes"] = 500_000
        raw.schunk.vlmeta["b2o"] = payload

    # Update upstream source with new data
    new_data = data + 1000
    fsspec.filesystem("memory").pipe_file(
        "synthetic-source.b2nd",
        blosc2.asarray(new_data, chunks=(10,), blocks=(5,)).to_cframe(),
    )

    carrier, payload = remote_proxy.inspect(carrier_path)
    assert payload["cache_policy"] == "memory"
    fresh_src = blosc2.FsspecNDSource("memory://synthetic-source.b2nd")
    mem_proxy = remote_proxy.ServerRemoteProxy(
        fresh_src,
        (disk_proxy.shape, disk_proxy.dtype, disk_proxy.chunks, disk_proxy.blocks),
        carrier,
        payload,
    )
    assert mem_proxy.cache_policy == "none"
    assert mem_proxy.effective_cache_policy == "none"
    assert mem_proxy.current_cache_bytes() == 0

    # Logical reads must return source data, ignoring the carrier's cached chunks
    np.testing.assert_array_equal(mem_proxy.read(slice(0, 20)), new_data[:20])
    chunk = mem_proxy.get_chunk(0)
    assert chunk == fresh_src.get_chunk(0)


def test_memory_carrier_exports_preserve_policy_and_reopen_with_client_cache(tmp_path):
    _proxy, data, carrier_path = _server_proxy(
        tmp_path, "export-mem", cache_policy="memory", max_cache_bytes=500_000
    )
    carrier, payload = remote_proxy.inspect(carrier_path)

    warm_bytes = remote_proxy.export_cframe(carrier, payload, include_cache=True)
    cold_bytes = remote_proxy.export_cframe(carrier, payload, include_cache=False)

    for kind, b in [("warm", warm_bytes), ("cold", cold_bytes)]:
        out_path = tmp_path / f"export_{kind}.b2nd"
        out_path.write_bytes(b)
        reopened = blosc2.open(str(out_path))
        assert isinstance(reopened, blosc2.RemoteProxy)
        assert reopened.cache_policy == blosc2.CachePolicy.MEMORY
        assert reopened.max_cache_bytes == 500_000
        np.testing.assert_array_equal(reopened[:10], data[:10])

        # Client memory cache reuse: uncached slice fetches then hits cache
        reopened.src.traffic.reset()
        res1 = reopened[10:20]
        assert reopened.src.traffic.requests > 0
        np.testing.assert_array_equal(res1, data[10:20])
        reopened.src.traffic.reset()
        res2 = reopened[10:20]
        assert reopened.src.traffic.requests == 0
        np.testing.assert_array_equal(res2, data[10:20])


def test_memory_carrier_detects_geometry_replacement_and_observes_data_replacement(tmp_path):
    _proxy, data, carrier_path = _server_proxy(
        tmp_path, "replacement", cache_policy="memory", max_cache_bytes=500_000
    )
    carrier, payload = remote_proxy.inspect(carrier_path)

    remote_proxy.configure(
        _Conf(
            {
                ".remote_proxy.enabled": True,
                ".remote_proxy.allowed_hosts": ["data.example"],
            }
        )
    )

    url = "https://data.example/replacement-source.b2nd"
    payload = dict(payload)
    payload["source"] = dict(payload["source"])
    payload["source"]["urlpath"] = url

    mem_fs = fsspec.filesystem("memory")
    mem_fs.pipe_file(url, blosc2.asarray(data, chunks=(10,), blocks=(5,)).to_cframe())

    orig_public_addr = remote_proxy._public_addresses
    orig_fs = remote_proxy._https_filesystem
    remote_proxy._public_addresses = lambda host, port: ("93.184.216.34",)
    remote_proxy._https_filesystem = lambda host, addr: mem_fs

    try:
        # Case A: Source geometry changed
        mismatched_source = blosc2.asarray(np.arange(60, dtype=np.int32), chunks=(10,), blocks=(5,))
        mem_fs.pipe_file(url, mismatched_source.to_cframe())
        with pytest.raises(remote_proxy.RemoteProxyDenied, match="geometry does not match"):
            remote_proxy.resolve(carrier, payload)

        # Case B: Source data changed (geometry identical)
        new_data = np.arange(100, 140, dtype=np.int32)
        mem_fs.pipe_file(url, blosc2.asarray(new_data, chunks=(10,), blocks=(5,)).to_cframe())
        resolved = remote_proxy.resolve(carrier, payload)
        np.testing.assert_array_equal(resolved.read(slice(0, 10)), new_data[:10])
    finally:
        remote_proxy._public_addresses = orig_public_addr
        remote_proxy._https_filesystem = orig_fs


def test_zero_effective_quota_reads_without_retaining(tmp_path):
    proxy, data, carrier_path = _server_proxy(tmp_path, "zero-quota")
    before = carrier_path.read_bytes()
    np.testing.assert_array_equal(proxy.read(slice(0, 10), cache_limit=0), data[:10])
    assert carrier_path.read_bytes() == before


@pytest.mark.parametrize("limit", [None, 1_000_000])
def test_read_only_cache_reuses_warm_chunks_and_does_not_retain_misses(tmp_path, limit):
    proxy, data, path = _server_proxy(tmp_path, "readonly-cache", max_cache_bytes=limit)
    proxy.get_chunk(0)
    before = path.read_bytes()
    mtime = path.stat().st_mtime_ns
    proxy.src.traffic.reset()
    np.testing.assert_array_equal(proxy.read(slice(0, 10), cache_limit=0), data[:10])
    proxy.get_chunk(0, cache_limit=0)
    assert proxy.src.traffic.requests == 0
    np.testing.assert_array_equal(proxy.read(slice(10, 20), cache_limit=0), data[10:20])
    assert proxy.src.traffic.requests > 0
    proxy.src.traffic.reset()
    proxy.get_chunk(1, cache_limit=0)
    assert proxy.src.traffic.requests > 0
    assert path.read_bytes() == before
    assert path.stat().st_mtime_ns == mtime


def test_concurrent_quota_reads_do_not_grow_distinct_carriers(tmp_path, monkeypatch):
    monkeypatch.setenv("CATERVA2_SECRET", "remote-proxy-test-secret")
    from caterva2.services import server

    monkeypatch.setattr(server.settings, "quota", 1)
    entries = [_server_proxy(tmp_path, name, max_cache_bytes=None) for name in ("left", "right")]
    before = [path.read_bytes() for _, _, path in entries]
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        futures = [
            pool.submit(proxy.read, (), cache_limit=server.remote_proxy_cache_limit(proxy))
            for proxy, _, _ in entries
        ]
        for future, (_, data, _) in zip(futures, entries, strict=True):
            np.testing.assert_array_equal(future.result(timeout=10), data)
    assert [path.read_bytes() for _, _, path in entries] == before


def test_cache_accounting_ignores_stale_size_table(tmp_path):
    proxy, _, path = _server_proxy(tmp_path, "stale-table", max_cache_bytes=None)
    proxy.read(())
    carrier = remote_proxy.raw_carrier(path, mode="a")
    carrier.schunk.vlmeta["proxy-cache-sizes"] = {"0": 1}
    assert proxy.current_cache_bytes() == carrier.schunk.cbytes


def test_customer_quota_disables_cache_growth(monkeypatch):
    monkeypatch.setenv("CATERVA2_SECRET", "remote-proxy-test-secret")
    from caterva2.services import server

    class Proxy:
        cache_policy = "disk"
        max_cache_bytes = 500

        @staticmethod
        def current_cache_bytes():
            return 200

    monkeypatch.setattr(server.settings, "quota", 1_000)
    monkeypatch.setattr(server, "get_disk_usage_written", lambda pending: 900)
    assert server.remote_proxy_cache_limit(Proxy()) == 0

    monkeypatch.setattr(server, "get_disk_usage_written", lambda pending: 1_000)
    assert server.remote_proxy_cache_limit(Proxy()) == 0

    class UnlimitedProxy:
        cache_policy = "disk"
        max_cache_bytes = None

        @staticmethod
        def current_cache_bytes():
            return 200

    # Any configured quota disables automatic growth, regardless of remaining space.
    monkeypatch.setattr(server, "get_disk_usage_written", lambda pending: 900)
    assert server.remote_proxy_cache_limit(UnlimitedProxy()) == 0

    # Exhausted quota also leaves the carrier read-only.
    monkeypatch.setattr(server, "get_disk_usage_written", lambda pending: 1_000)
    assert server.remote_proxy_cache_limit(UnlimitedProxy()) == 0

    # Without quota, unlimited disk cache returns None
    monkeypatch.setattr(server.settings, "quota", 0)
    assert server.remote_proxy_cache_limit(UnlimitedProxy()) is None

    class MemoryProxy:
        cache_policy = "none"
        max_cache_bytes = None

    assert server.remote_proxy_cache_limit(MemoryProxy()) == 0


def test_unlimited_disk_server_proxy_caches_without_eviction(tmp_path):
    proxy, data, carrier_path = _server_proxy(
        tmp_path, "unlimited-server", cache_policy="disk", max_cache_bytes=None
    )
    assert proxy.requested_max_cache_bytes is None
    assert proxy.max_cache_bytes is None
    assert proxy.current_cache_bytes() == 0

    # Read slices to trigger cache population
    np.testing.assert_array_equal(proxy.read(slice(0, 20)), data[:20])
    np.testing.assert_array_equal(proxy.read(slice(20, 40)), data[20:])
    cached_bytes = proxy.current_cache_bytes()
    assert cached_bytes > 0

    # Reopening carrier shows data is valid
    reopened = blosc2.open(carrier_path, mode="r")
    np.testing.assert_array_equal(reopened[:], data)


def test_concurrent_server_proxy_fills_do_not_corrupt_carrier(tmp_path):
    first, data, carrier_path = _server_proxy(tmp_path, "concurrent")
    carrier, payload = remote_proxy.inspect(carrier_path)
    second_source = blosc2.FsspecNDSource(payload["source"]["urlpath"])
    second = remote_proxy.ServerRemoteProxy(
        second_source,
        (first.shape, first.dtype, first.chunks, first.blocks),
        carrier,
        payload,
    )
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        left = pool.submit(first.read, slice(0, 20))
        right = pool.submit(second.read, slice(20, 40))
        np.testing.assert_array_equal(left.result(timeout=10), data[:20])
        np.testing.assert_array_equal(right.result(timeout=10), data[20:])

    reopened = blosc2.open(carrier_path, mode="r")
    np.testing.assert_array_equal(reopened[:], data)


def test_https_filesystem_disables_redirects_and_pins_resolution():
    remote_proxy.policy = remote_proxy.Policy(timeout=7)
    fs = remote_proxy._https_filesystem("data.example", ("93.184.216.34",))
    assert fs.kwargs["allow_redirects"] is False

    async def inspect_client():
        client = await fs.get_client()
        try:
            assert client.timeout.total == 7
            assert isinstance(client.connector._resolver, remote_proxy._PinnedResolver)
        finally:
            await client.close()

    asyncio.run(inspect_client())
