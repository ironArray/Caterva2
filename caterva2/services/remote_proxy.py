###############################################################################
# Caterva2 - On demand access to remote Blosc2 data repositories
#
# Copyright (c) 2023 ironArray SLU <contact@ironarray.io>
# https://www.blosc.org
# License: GNU Affero General Public License v3.0
# See LICENSE.txt for details about copyright and rights to use.
###############################################################################

"""Policy boundary for persisted remote-array references.

The carrier is inspected without resolving it.  Resolution is default-deny and
the first supported server backend is HTTPS with an explicit host allowlist,
publicly routable pinned addresses, and redirects disabled.
"""

from __future__ import annotations

import ipaddress
import math
import socket
import threading
import weakref
from dataclasses import dataclass
from urllib.parse import urlsplit

import aiohttp
import blosc2
import numpy as np
from blosc2.b2objects import make_b2object_carrier, write_b2object_payload
from fsspec.implementations.http import HTTPFileSystem


class RemoteProxyDenied(ValueError):
    """The server policy refuses a remote reference."""


@dataclass(frozen=True)
class Policy:
    enabled: bool = False
    allowed_hosts: tuple[str, ...] = ()
    timeout: float = 30.0
    max_nbytes: int = 1 << 30
    max_rank: int = 16
    max_chunks: int = 10_000_000
    max_concurrency: int = 8


policy = Policy()

_carrier_locks: weakref.WeakValueDictionary[str, threading.Lock] = weakref.WeakValueDictionary()
_carrier_locks_guard = threading.Lock()


def carrier_thread_lock(path) -> threading.Lock:
    """Return the process-local guard paired with the carrier's file lock."""
    key = str(path)
    with _carrier_locks_guard:
        lock = _carrier_locks.get(key)
        if lock is None:
            lock = threading.Lock()
            _carrier_locks[key] = lock
        return lock


def configure(conf) -> None:
    """Load the remote-reference policy from the server configuration."""
    global policy

    enabled = conf.get(".remote_proxy.enabled", False)
    hosts = conf.get(".remote_proxy.allowed_hosts", ())
    timeout = conf.get(".remote_proxy.timeout", 30.0)
    max_nbytes = conf.get(".remote_proxy.max_nbytes", 1 << 30)
    max_rank = conf.get(".remote_proxy.max_rank", 16)
    max_chunks = conf.get(".remote_proxy.max_chunks", 10_000_000)
    max_concurrency = conf.get(".remote_proxy.max_concurrency", 8)

    if not isinstance(enabled, bool):
        raise ValueError("remote_proxy.enabled must be true or false")
    if enabled and not hasattr(blosc2, "RemoteProxy"):
        raise ValueError("remote_proxy.enabled requires a Python-Blosc2 version with RemoteProxy support")
    if not isinstance(hosts, list | tuple) or any(not isinstance(host, str) for host in hosts):
        raise ValueError("remote_proxy.allowed_hosts must be a list of host names")
    if not isinstance(timeout, int | float) or isinstance(timeout, bool) or timeout <= 0:
        raise ValueError("remote_proxy.timeout must be positive")
    for name, value in {
        "max_nbytes": max_nbytes,
        "max_rank": max_rank,
        "max_chunks": max_chunks,
        "max_concurrency": max_concurrency,
    }.items():
        if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
            raise ValueError(f"remote_proxy.{name} must be a positive integer")

    policy = Policy(
        enabled=enabled,
        allowed_hosts=tuple(_normalize_allowed_host(host) for host in hosts),
        timeout=float(timeout),
        max_nbytes=max_nbytes,
        max_rank=max_rank,
        max_chunks=max_chunks,
        max_concurrency=max_concurrency,
    )


def _normalize_allowed_host(value: str) -> str:
    parsed = urlsplit(f"//{value}")
    if parsed.username is not None or parsed.password is not None or parsed.path not in {"", "/"}:
        raise ValueError(f"invalid remote_proxy allowed host: {value!r}")
    if parsed.hostname is None:
        raise ValueError(f"invalid remote_proxy allowed host: {value!r}")
    try:
        host = parsed.hostname.encode("idna").decode("ascii").lower()
        port = parsed.port
    except (UnicodeError, ValueError) as exc:
        raise ValueError(f"invalid remote_proxy allowed host: {value!r}") from exc
    return f"{host}:{port}" if port is not None else host


def raw_carrier(path, mode="r", *, locking=False):
    """Open a local Blosc2 carrier without dispatching its B2 object."""
    kwargs = {"dparams": blosc2.DParams(nthreads=1), "locking": locking}
    return blosc2.blosc2_ext.open(str(path), mode, 0, **kwargs)


def inspect(path):
    """Return ``(raw carrier, payload)`` for a RemoteProxy, otherwise ``None``."""
    if not hasattr(blosc2, "RemoteProxy"):
        return None
    with carrier_thread_lock(path):
        try:
            carrier = raw_carrier(path)
        except (RuntimeError, ValueError):
            return None
        schunk = getattr(carrier, "schunk", carrier)
        marker = schunk.meta.get("b2o")
        if not isinstance(marker, dict) or marker.get("kind") != "remote_proxy":
            return None
        # Only RemoteProxy carriers need a sidecar lock. Reopen after
        # discrimination so inspecting ordinary datasets has no filesystem
        # side effect, then re-read the marker and payload under that lock.
        carrier = raw_carrier(path, locking=True)
        schunk = getattr(carrier, "schunk", carrier)
        marker = schunk.meta.get("b2o")
        if not isinstance(marker, dict) or marker.get("kind") != "remote_proxy":
            return None
        payload = schunk.vlmeta.get("b2o")
        if not isinstance(payload, dict):
            raise RemoteProxyDenied("RemoteProxy carrier has no valid payload")
        return carrier, payload


def guard_embedded(path) -> None:
    """Reject remote references hidden in another persisted B2 object.

    Structured LazyExpr/LazyUDF decoding resolves operand references while the
    object is opened. Until the server can inject this module's secure source
    factory into that decoder, refusing those operands closes an otherwise
    easy way around the direct-carrier policy check.
    """
    if not hasattr(blosc2, "RemoteProxy"):
        return
    try:
        carrier = raw_carrier(path)
    except (RuntimeError, ValueError):
        return
    schunk = getattr(carrier, "schunk", carrier)
    marker = schunk.meta.get("b2o")
    if not isinstance(marker, dict) or marker.get("kind") not in {"lazyexpr", "lazyudf"}:
        return
    payload = schunk.vlmeta.get("b2o")
    if _contains_remote_reference(payload):
        raise RemoteProxyDenied(
            "remote references embedded in persisted expressions are disabled by server policy"
        )


def _contains_remote_reference(value) -> bool:
    if isinstance(value, dict):
        if value.get("kind") in {"fsspec", "remote_proxy"}:
            return True
        return any(_contains_remote_reference(item) for item in value.values())
    if isinstance(value, list | tuple):
        return any(_contains_remote_reference(item) for item in value)
    return False


def is_metadata(meta) -> bool:
    """Whether an api/info model describes a RemoteProxy carrier."""
    vlmeta = getattr(getattr(meta, "schunk", None), "vlmeta", None) or {}
    payload = vlmeta.get("b2o")
    return isinstance(payload, dict) and payload.get("kind") == "remote_proxy"


def _validated_source(payload: dict) -> str:
    if not policy.enabled:
        raise RemoteProxyDenied("RemoteProxy resolution is disabled by server policy")
    if set(payload) != {"kind", "version", "source", "cache_policy", "max_cache_bytes"}:
        raise RemoteProxyDenied("RemoteProxy payload contains unsupported fields")
    if payload.get("kind") != "remote_proxy" or payload.get("version") != 1:
        raise RemoteProxyDenied("unsupported RemoteProxy payload")
    cache_policy = payload.get("cache_policy")
    max_cache_bytes = payload.get("max_cache_bytes")
    if cache_policy == "none":
        if max_cache_bytes is not None:
            raise RemoteProxyDenied("RemoteProxy cache policy 'none' cannot have max_cache_bytes")
    elif cache_policy == "disk":
        if max_cache_bytes is not None and (
            isinstance(max_cache_bytes, bool) or not isinstance(max_cache_bytes, int) or max_cache_bytes <= 0
        ):
            raise RemoteProxyDenied(
                "RemoteProxy cache policy 'disk' requires positive max_cache_bytes or None"
            )
    elif cache_policy == "memory":
        if isinstance(max_cache_bytes, bool) or not isinstance(max_cache_bytes, int) or max_cache_bytes <= 0:
            raise RemoteProxyDenied(
                f"RemoteProxy cache policy {cache_policy!r} requires positive max_cache_bytes"
            )
    else:
        raise RemoteProxyDenied(
            "server RemoteProxy supports only cache policies 'none', 'memory', and 'disk'"
        )
    source = payload.get("source")
    if not isinstance(source, dict) or set(source) != {"kind", "version", "urlpath"}:
        raise RemoteProxyDenied("server RemoteProxy supports only a versioned fsspec URL source")
    if source.get("kind") != "fsspec" or source.get("version") != 1:
        raise RemoteProxyDenied("server RemoteProxy supports only fsspec source version 1")
    url = source.get("urlpath")
    if not isinstance(url, str):
        raise RemoteProxyDenied("RemoteProxy source URL must be a string")

    parsed = urlsplit(url)
    if parsed.scheme.lower() != "https":
        raise RemoteProxyDenied("server RemoteProxy currently permits only HTTPS sources")
    if parsed.username is not None or parsed.password is not None:
        raise RemoteProxyDenied("RemoteProxy source URLs cannot contain user information")
    if parsed.query or parsed.fragment:
        raise RemoteProxyDenied("RemoteProxy source URLs cannot contain a query or fragment")
    if parsed.hostname is None:
        raise RemoteProxyDenied("RemoteProxy source URL has no host")
    try:
        host = parsed.hostname.encode("idna").decode("ascii").lower()
        port = parsed.port or 443
    except (UnicodeError, ValueError) as exc:
        raise RemoteProxyDenied("RemoteProxy source URL has an invalid host or port") from exc
    authority = host if port == 443 else f"{host}:{port}"
    if authority not in policy.allowed_hosts:
        raise RemoteProxyDenied(f"RemoteProxy destination {authority!r} is not allowed")
    return url


def _public_addresses(host: str, port: int) -> tuple[str, ...]:
    try:
        answers = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except OSError as exc:
        raise RemoteProxyDenied(f"RemoteProxy destination {host!r} cannot be resolved") from exc
    addresses = tuple(dict.fromkeys(answer[4][0] for answer in answers))
    if not addresses:
        raise RemoteProxyDenied(f"RemoteProxy destination {host!r} has no addresses")
    denied = [address for address in addresses if not ipaddress.ip_address(address).is_global]
    if denied:
        raise RemoteProxyDenied(f"RemoteProxy destination {host!r} resolves to a non-public address")
    return addresses


class _PinnedResolver(aiohttp.abc.AbstractResolver):
    def __init__(self, host: str, addresses: tuple[str, ...]):
        self.host = host
        self.addresses = addresses

    async def resolve(self, host, port=0, family=socket.AF_UNSPEC):
        if host.encode("idna").decode("ascii").lower() != self.host:
            raise OSError("redirected hosts are not allowed for RemoteProxy sources")
        records = []
        for address in self.addresses:
            address_family = socket.AF_INET6 if ":" in address else socket.AF_INET
            if family not in {socket.AF_UNSPEC, address_family}:
                continue
            records.append(
                {
                    "hostname": host,
                    "host": address,
                    "port": port,
                    "family": address_family,
                    "proto": 0,
                    "flags": 0,
                }
            )
        return records

    async def close(self):
        return None


def _https_filesystem(host: str, addresses: tuple[str, ...]):
    async def get_client(**kwargs):
        connector = aiohttp.TCPConnector(resolver=_PinnedResolver(host, addresses))
        timeout = aiohttp.ClientTimeout(total=policy.timeout)
        return aiohttp.ClientSession(connector=connector, timeout=timeout, **kwargs)

    return HTTPFileSystem(
        get_client=get_client,
        allow_redirects=False,
        skip_instance_cache=True,
    )


def resolve(carrier, payload):
    """Resolve one allowed carrier as a policy-limited remote array."""
    url = _validated_source(payload)
    parsed = urlsplit(url)
    host = parsed.hostname.encode("idna").decode("ascii").lower()
    addresses = _public_addresses(host, parsed.port or 443)
    fs = _https_filesystem(host, addresses)
    source = blosc2.FsspecNDSource(
        url,
        max_concurrency=policy.max_concurrency,
        _filesystem=fs,
    )

    expected = (tuple(carrier.shape), carrier.dtype, tuple(carrier.chunks), tuple(carrier.blocks))
    actual = (tuple(source.shape), source.dtype, tuple(source.chunks), tuple(source.blocks))
    if actual != expected:
        raise RemoteProxyDenied(
            f"RemoteProxy source geometry does not match its carrier: carrier={expected}, source={actual}"
        )
    if len(source.shape) > policy.max_rank:
        raise RemoteProxyDenied(f"RemoteProxy rank exceeds the configured limit of {policy.max_rank}")
    nbytes = math.prod(source.shape) * source.dtype.itemsize
    if nbytes > policy.max_nbytes:
        raise RemoteProxyDenied(
            f"RemoteProxy logical size exceeds the configured limit of {policy.max_nbytes}"
        )
    chunks = math.prod(
        math.ceil(size / chunk) for size, chunk in zip(source.shape, source.chunks, strict=True)
    )
    if chunks > policy.max_chunks:
        raise RemoteProxyDenied(
            f"RemoteProxy chunk count exceeds the configured limit of {policy.max_chunks}"
        )
    return ServerRemoteProxy(source, expected, carrier, payload)


def _effective_cache_policy(requested: str) -> str:
    """Return the runtime cache policy executed by Caterva2 ('none' or 'disk')."""
    if requested in {"none", "memory"}:
        return "none"
    if requested == "disk":
        return "disk"
    raise ValueError(f"unknown cache policy: {requested!r}")


class ServerRemoteProxy:
    """Authorized remote source backed by its own carrier cache.

    Attributes
    ----------
    requested_cache_policy : str
        The cache policy requested in the carrier payload ('none', 'memory', or 'disk').
    requested_max_cache_bytes : int | None
        The cache limit requested in the carrier payload.
    cache_policy : str
        The effective runtime cache policy executed by Caterva2 ('none' or 'disk').
        Persisted 'memory' carriers are executed using the same no-retention path
        as 'none', avoiding unmanaged server RAM caching across requests.
    effective_cache_policy : str
        Read-only alias for `cache_policy`.
    max_cache_bytes : int | None
        The effective runtime cache limit (positive integer for 'disk', None for 'none').
    """

    def __init__(self, source, geometry, carrier, payload):
        self.src = source
        self.shape, self.dtype, self.chunks, self.blocks = geometry
        self.cparams = source.cparams
        self.path = carrier.schunk.urlpath
        self.requested_cache_policy = payload["cache_policy"]
        self.requested_max_cache_bytes = payload["max_cache_bytes"]
        self.cache_policy = _effective_cache_policy(self.requested_cache_policy)
        self.max_cache_bytes = self.requested_max_cache_bytes if self.cache_policy == "disk" else None

    @property
    def effective_cache_policy(self) -> str:
        """Effective cache policy executed by the server runtime ('none' or 'disk')."""
        return self.cache_policy

    def current_cache_bytes(self) -> int:
        if self.cache_policy != "disk":
            return 0
        with carrier_thread_lock(self.path):
            carrier = raw_carrier(self.path, locking=True)
            with carrier.schunk.holding_lock():
                sizes = carrier.schunk.vlmeta.get("proxy-cache-sizes")
                if isinstance(sizes, dict):
                    return sum(size for size in sizes.values() if isinstance(size, int) and size >= 0)
                return carrier.schunk.cbytes

    def _backend(self, cache_limit=None, *, carrier=None):
        if self.cache_policy != "disk" or cache_limit == 0:
            return blosc2.Proxy(self.src, _refresh_source=False)
        carrier = raw_carrier(self.path, mode="a", locking=True) if carrier is None else carrier
        if cache_limit is None:
            limit = self.max_cache_bytes
        elif self.max_cache_bytes is None:
            limit = cache_limit
        else:
            limit = min(self.max_cache_bytes, cache_limit)
        return blosc2.Proxy(
            self.src,
            _cache=carrier,
            _refresh_source=False,
            _max_cache_bytes=limit,
        )

    def read(self, item, *, cache_limit=None):
        if self.cache_policy != "disk" or cache_limit == 0:
            return self._backend(cache_limit)[item]
        with carrier_thread_lock(self.path):
            carrier = raw_carrier(self.path, mode="a", locking=True)
            with carrier.schunk.holding_lock():
                backend = self._backend(cache_limit, carrier=carrier)
                return backend[item]

    def __getitem__(self, item):
        return self.read(item)

    def get_chunk(self, nchunk, *, cache_limit=None):
        if self.cache_policy != "disk" or cache_limit == 0:
            return self.src.get_chunk(nchunk)
        item = tuple(
            slice(coord * chunk, min((coord + 1) * chunk, size))
            for coord, chunk, size in zip(
                np.unravel_index(
                    nchunk,
                    tuple(
                        math.ceil(size / chunk) for size, chunk in zip(self.shape, self.chunks, strict=True)
                    ),
                ),
                self.chunks,
                self.shape,
                strict=True,
            )
        )
        with carrier_thread_lock(self.path):
            carrier = raw_carrier(self.path, mode="a", locking=True)
            with carrier.schunk.holding_lock():
                backend = self._backend(cache_limit, carrier=carrier)
                backend.fetch(item)
                chunk = backend.schunk.get_chunk(nchunk)
                backend._enforce_cache_limit(item)
        return chunk


def cold_cframe(carrier, payload) -> bytes:
    """Return a cache-free carrier without resolving or mutating its source."""
    cold = make_b2object_carrier(
        "remote_proxy",
        carrier.shape,
        carrier.dtype,
        chunks=carrier.chunks,
        blocks=carrier.blocks,
        cparams=carrier.cparams,
    )
    write_b2object_payload(cold, payload)
    return cold.to_cframe()


def export_cframe(carrier, payload, *, include_cache: bool) -> bytes:
    """Snapshot a warm or cold carrier while excluding concurrent mutations."""
    path = carrier.schunk.urlpath
    with carrier_thread_lock(path), carrier.schunk.holding_lock():
        if include_cache:
            return carrier.to_cframe()
        return cold_cframe(carrier, payload)
