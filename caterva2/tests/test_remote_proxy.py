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


def test_resolution_is_default_deny():
    remote_proxy.policy = remote_proxy.Policy()
    with pytest.raises(remote_proxy.RemoteProxyDenied, match="disabled"):
        remote_proxy._validated_source(_payload("https://data.example/array.b2nd"))


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
def test_enabled_policy_still_rejects_unsafe_destinations(url):
    remote_proxy.policy = remote_proxy.Policy(enabled=True, allowed_hosts=("data.example",))
    with pytest.raises(remote_proxy.RemoteProxyDenied):
        remote_proxy._validated_source(_payload(url))


def test_configured_https_destination_is_accepted():
    remote_proxy.configure(
        _Conf(
            {
                ".remote_proxy.enabled": True,
                ".remote_proxy.allowed_hosts": ["DATA.example", "data.example:8443"],
            }
        )
    )
    assert remote_proxy._validated_source(_payload("https://data.example/array.b2nd"))
    assert remote_proxy._validated_source(_payload("https://data.example:8443/array.b2nd"))


@pytest.mark.parametrize(
    ("payload", "match"),
    [
        (_payload("https://data.example/a.b2nd", cache_policy="memory"), "only cache policies"),
        (
            _payload("https://data.example/a.b2nd", cache_policy="none", max_cache_bytes=1),
            "cannot have max_cache_bytes",
        ),
        (_payload("https://data.example/a.b2nd", cache_policy="disk"), "requires positive"),
        (
            _payload("https://data.example/a.b2nd", cache_policy="disk", max_cache_bytes=True),
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


def test_allowed_source_is_resolved_with_the_secure_filesystem(monkeypatch):
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

    resolved = remote_proxy.resolve(Carrier(), _payload("https://data.example/array.b2nd"))
    assert isinstance(resolved, remote_proxy.ServerRemoteProxy)
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


def _server_proxy(tmp_path, name="server-cache"):
    data = np.arange(40, dtype=np.int32)
    source = blosc2.asarray(data, chunks=(10,), blocks=(5,))
    source_url = f"memory://{name}-source.b2nd"
    fsspec.filesystem("memory").pipe_file(f"{name}-source.b2nd", source.to_cframe())
    carrier_path = tmp_path / f"{name}.b2nd"
    creator = blosc2.RemoteProxy(
        source_url,
        cache_policy=blosc2.CachePolicy.DISK,
        cache_path=carrier_path,
        max_cache_bytes=1_000_000,
    )
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


def test_zero_effective_quota_reads_without_retaining(tmp_path):
    proxy, data, carrier_path = _server_proxy(tmp_path, "zero-quota")
    before = carrier_path.read_bytes()
    np.testing.assert_array_equal(proxy.read(slice(0, 10), cache_limit=0), data[:10])
    assert carrier_path.read_bytes() == before


def test_customer_quota_reduces_the_proxy_cache_limit(monkeypatch):
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
    assert server.remote_proxy_cache_limit(Proxy()) == 300

    monkeypatch.setattr(server, "get_disk_usage_written", lambda pending: 1_000)
    assert server.remote_proxy_cache_limit(Proxy()) == 200


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
