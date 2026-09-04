###############################################################################
# Caterva2 - On demand access to remote Blosc2 data repositories
#
# Copyright (c) 2023 ironArray SLU <contact@ironarray.io>
# https://www.blosc.org
# License: GNU Affero General Public License v3.0
# See LICENSE.txt for details about copyright and rights to use.
###############################################################################

import asyncio
import socket

import blosc2
import numpy as np
import pytest

from caterva2.services import remote_proxy


class _Conf:
    def __init__(self, values):
        self.values = values

    def get(self, key, default=None):
        return self.values.get(key, default)


def _payload(url):
    return {
        "kind": "remote_proxy",
        "version": 1,
        "source": {"kind": "fsspec", "version": 1, "urlpath": url},
        "cache_policy": "none",
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
