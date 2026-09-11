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
import logging
import math
import socket
import sqlite3
import threading
import weakref
from dataclasses import dataclass
from inspect import signature
from urllib.parse import urlsplit

import aiohttp
import blosc2
import numpy as np
from blosc2.b2objects import make_b2object_carrier, write_b2object_payload
from fsspec.implementations.http import HTTPFileSystem

from caterva2.services import storage_quota

log = logging.getLogger(__name__)


class RemoteArrayDenied(ValueError):
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
    cache_maintenance_seconds: float = 60.0
    cache_backend: str = "sparse"


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
    cache_maintenance_seconds = conf.get(".remote_proxy.cache_maintenance_seconds", 60.0)

    if not isinstance(enabled, bool):
        raise ValueError("remote_proxy.enabled must be true or false")
    if enabled and (
        not hasattr(blosc2, "RemoteArray")
        or "assume_immutable" not in signature(blosc2.RemoteArray).parameters
    ):
        raise ValueError("remote_proxy.enabled requires a compatible Python-Blosc2 RemoteArray")
    if not isinstance(hosts, list | tuple) or any(not isinstance(host, str) for host in hosts):
        raise ValueError("remote_proxy.allowed_hosts must be a list of host names")
    if not isinstance(timeout, int | float) or isinstance(timeout, bool) or timeout <= 0:
        raise ValueError("remote_proxy.timeout must be positive")
    if (
        not isinstance(cache_maintenance_seconds, int | float)
        or isinstance(cache_maintenance_seconds, bool)
        or cache_maintenance_seconds <= 0
    ):
        raise ValueError("remote_proxy.cache_maintenance_seconds must be positive")
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
        cache_maintenance_seconds=float(cache_maintenance_seconds),
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
    """Return ``(raw carrier, payload)`` for a RemoteArray, otherwise ``None``."""
    if not hasattr(blosc2, "RemoteArray"):
        return None
    with carrier_thread_lock(path):
        try:
            carrier = raw_carrier(path)
        except (RuntimeError, ValueError):
            return None
        schunk = getattr(carrier, "schunk", carrier)
        marker = schunk.meta.get("b2o")
        if not isinstance(marker, dict) or marker.get("kind") != "remote_array":
            return None
        # Only RemoteArray carriers need a sidecar lock. Reopen after
        # discrimination so inspecting ordinary datasets has no filesystem
        # side effect, then re-read the marker and payload under that lock.
        carrier = raw_carrier(path, locking=True)
        schunk = getattr(carrier, "schunk", carrier)
        marker = schunk.meta.get("b2o")
        if not isinstance(marker, dict) or marker.get("kind") != "remote_array":
            return None
        payload = schunk.vlmeta.get("b2o")
        if not isinstance(payload, dict):
            raise RemoteArrayDenied("RemoteArray carrier has no valid payload")
        return carrier, payload


def guard_embedded(path) -> None:
    """Reject remote references hidden in another persisted B2 object.

    Structured LazyExpr/LazyUDF decoding resolves operand references while the
    object is opened. Until the server can inject this module's secure source
    factory into that decoder, refusing those operands closes an otherwise
    easy way around the direct-carrier policy check.
    """
    if not hasattr(blosc2, "RemoteArray"):
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
        raise RemoteArrayDenied(
            "remote references embedded in persisted expressions are disabled by server policy"
        )


def _contains_remote_reference(value) -> bool:
    if isinstance(value, dict):
        if value.get("kind") in {"fsspec", "remote_array"}:
            return True
        return any(_contains_remote_reference(item) for item in value.values())
    if isinstance(value, list | tuple):
        return any(_contains_remote_reference(item) for item in value)
    return False


def is_metadata(meta) -> bool:
    """Whether an api/info model describes a RemoteArray carrier."""
    vlmeta = getattr(getattr(meta, "schunk", None), "vlmeta", None) or {}
    payload = vlmeta.get("b2o")
    return isinstance(payload, dict) and payload.get("kind") == "remote_array"


def _validated_source(payload: dict) -> str:
    if not policy.enabled:
        raise RemoteArrayDenied("RemoteArray resolution is disabled by server policy")
    if set(payload) - {"mutable"} != {"kind", "version", "source", "cache_policy", "max_cache_bytes"}:
        raise RemoteArrayDenied("RemoteArray payload contains unsupported fields")
    if not isinstance(payload.get("mutable", False), bool):
        raise RemoteArrayDenied("RemoteArray mutable must be true or false")
    if payload.get("kind") != "remote_array" or payload.get("version") != 1:
        raise RemoteArrayDenied("unsupported RemoteArray payload")
    cache_policy = payload.get("cache_policy")
    max_cache_bytes = payload.get("max_cache_bytes")
    if cache_policy == "none":
        if max_cache_bytes is not None:
            raise RemoteArrayDenied("RemoteArray cache policy 'none' cannot have max_cache_bytes")
    elif cache_policy == "disk":
        if max_cache_bytes is not None and (
            isinstance(max_cache_bytes, bool) or not isinstance(max_cache_bytes, int) or max_cache_bytes <= 0
        ):
            raise RemoteArrayDenied(
                "RemoteArray cache policy 'disk' requires positive max_cache_bytes or None"
            )
    elif cache_policy == "memory":
        if isinstance(max_cache_bytes, bool) or not isinstance(max_cache_bytes, int) or max_cache_bytes <= 0:
            raise RemoteArrayDenied(
                f"RemoteArray cache policy {cache_policy!r} requires positive max_cache_bytes"
            )
    else:
        raise RemoteArrayDenied(
            "server RemoteArray supports only cache policies 'none', 'memory', and 'disk'"
        )
    source = payload.get("source")
    if not isinstance(source, dict) or set(source) != {
        "kind",
        "version",
        "urlpath",
        "assume_immutable",
    }:
        raise RemoteArrayDenied("server RemoteArray supports only a versioned fsspec URL source")
    if source.get("kind") != "fsspec" or source.get("version") != 1:
        raise RemoteArrayDenied("server RemoteArray supports only fsspec source version 1")
    if not isinstance(source.get("assume_immutable"), bool):
        raise RemoteArrayDenied("RemoteArray source assume_immutable must be true or false")
    url = source.get("urlpath")
    if not isinstance(url, str):
        raise RemoteArrayDenied("RemoteArray source URL must be a string")

    parsed = urlsplit(url)
    if parsed.scheme.lower() != "https":
        raise RemoteArrayDenied("server RemoteArray currently permits only HTTPS sources")
    if parsed.username is not None or parsed.password is not None:
        raise RemoteArrayDenied("RemoteArray source URLs cannot contain user information")
    if parsed.query or parsed.fragment:
        raise RemoteArrayDenied("RemoteArray source URLs cannot contain a query or fragment")
    if parsed.hostname is None:
        raise RemoteArrayDenied("RemoteArray source URL has no host")
    try:
        host = parsed.hostname.encode("idna").decode("ascii").lower()
        port = parsed.port or 443
    except (UnicodeError, ValueError) as exc:
        raise RemoteArrayDenied("RemoteArray source URL has an invalid host or port") from exc
    authority = host if port == 443 else f"{host}:{port}"
    if authority not in policy.allowed_hosts:
        raise RemoteArrayDenied(f"RemoteArray destination {authority!r} is not allowed")
    return url


def _public_addresses(host: str, port: int) -> tuple[str, ...]:
    try:
        answers = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except OSError as exc:
        raise RemoteArrayDenied(f"RemoteArray destination {host!r} cannot be resolved") from exc
    addresses = tuple(dict.fromkeys(answer[4][0] for answer in answers))
    if not addresses:
        raise RemoteArrayDenied(f"RemoteArray destination {host!r} has no addresses")
    denied = [address for address in addresses if not ipaddress.ip_address(address).is_global]
    if denied:
        raise RemoteArrayDenied(f"RemoteArray destination {host!r} resolves to a non-public address")
    return addresses


class _PinnedResolver(aiohttp.abc.AbstractResolver):
    def __init__(self, host: str, addresses: tuple[str, ...]):
        self.host = host
        self.addresses = addresses

    async def resolve(self, host, port=0, family=socket.AF_UNSPEC):
        if host.encode("idna").decode("ascii").lower() != self.host:
            raise OSError("redirected hosts are not allowed for RemoteArray sources")
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
        raise RemoteArrayDenied(
            f"RemoteArray source geometry does not match its carrier: carrier={expected}, source={actual}"
        )
    if len(source.shape) > policy.max_rank:
        raise RemoteArrayDenied(f"RemoteArray rank exceeds the configured limit of {policy.max_rank}")
    nbytes = math.prod(source.shape) * source.dtype.itemsize
    if nbytes > policy.max_nbytes:
        raise RemoteArrayDenied(
            f"RemoteArray logical size exceeds the configured limit of {policy.max_nbytes}"
        )
    chunks = math.prod(
        math.ceil(size / chunk) for size, chunk in zip(source.shape, source.chunks, strict=True)
    )
    if chunks > policy.max_chunks:
        raise RemoteArrayDenied(
            f"RemoteArray chunk count exceeds the configured limit of {policy.max_chunks}"
        )
    return ServerRemoteArray(source, expected, carrier, payload)


def _effective_cache_policy(requested: str) -> str:
    """Return the runtime cache policy executed by Caterva2 ('none' or 'disk')."""
    if requested in {"none", "memory"}:
        return "none"
    if requested == "disk":
        return "disk"
    raise ValueError(f"unknown cache policy: {requested!r}")


class ServerRemoteArray:
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
        self.carrier_generation = storage_quota.signature(self.path)
        self.requested_cache_policy = payload["cache_policy"]
        self.requested_payload = dict(payload)
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
                # The physical payload is authoritative. Uploaded size tables
                # can be stale after older unbounded writers (or user supplied).
                return carrier.schunk.cbytes

    def quota_read(self, quota, item=(), *, nchunk=None):
        """Assemble on an immutable candidate and admit its exact physical size."""
        if quota.cache_backend == "sparse":
            return quota.remote.read(self, item, nchunk=nchunk)
        if self.cache_policy != "disk" or getattr(self.src, "stamp", None) is None:
            return (
                self.get_chunk(nchunk, cache_limit=0)
                if nchunk is not None
                else self.read(item, cache_limit=0)
            )
        try:
            frame, generation = quota.snapshot(self.path)
        except (OSError, RuntimeError, ValueError, sqlite3.Error):
            return (
                self.get_chunk(nchunk, cache_limit=0)
                if nchunk is not None
                else self.read(item, cache_limit=0)
            )
        if frame is None or len(frame) > quota.work_bytes:
            return (
                self.get_chunk(nchunk, cache_limit=0)
                if nchunk is not None
                else self.read(item, cache_limit=0)
            )
        carrier = blosc2.ndarray_from_cframe(frame, copy=True)
        if carrier.schunk.vlmeta.get("b2o") != self.requested_payload:
            return (
                self.src.get_chunk(nchunk)
                if nchunk is not None
                else blosc2.Proxy(self.src, _refresh_source=False)[item]
            )
        backend = blosc2.Proxy(
            self.src, _cache=carrier, _refresh_source=False, _max_cache_bytes=self.max_cache_bytes
        )
        if nchunk is None:
            result = backend[item]
        else:
            grid = tuple(math.ceil(s / c) for s, c in zip(self.shape, self.chunks, strict=True))
            item = tuple(
                slice(int(i) * c, min((int(i) + 1) * c, s))
                for i, c, s in zip(np.unravel_index(nchunk, grid), self.chunks, self.shape, strict=True)
            )
            backend.fetch(item)
            result = backend.schunk.get_chunk(nchunk)
            backend._enforce_cache_limit(item)
        candidate = carrier.to_cframe()
        try:
            if candidate != frame:
                quota.publish(self.path, candidate, expected=generation, cache=True)
            else:
                quota.touch(self.path)
        except (storage_quota.QuotaExceeded, storage_quota.StorageBusy, OSError, sqlite3.Error):
            # The logical result already exists. Retention is strictly optional.
            pass
        return result

    def _backend(self, cache_limit=None, *, carrier=None):
        if self.cache_policy != "disk":
            return blosc2.Proxy(self.src, _refresh_source=False)
        mode = "r" if cache_limit == 0 else "a"
        carrier = raw_carrier(self.path, mode=mode, locking=True) if carrier is None else carrier
        if cache_limit == 0:
            # Consume warm data without writing metadata or retaining misses.
            return blosc2.Proxy(self.src, _cache=carrier, _refresh_source=False)
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
        if self.cache_policy != "disk":
            return self._backend(cache_limit)[item]
        with carrier_thread_lock(self.path):
            carrier = raw_carrier(self.path, mode="r" if cache_limit == 0 else "a", locking=True)
            with carrier.schunk.holding_lock():
                backend = self._backend(cache_limit, carrier=carrier)
                return backend[item]

    def __getitem__(self, item):
        return self.read(item)

    def get_chunk(self, nchunk, *, cache_limit=None):
        if self.cache_policy != "disk":
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
            carrier = raw_carrier(self.path, mode="r" if cache_limit == 0 else "a", locking=True)
            with carrier.schunk.holding_lock():
                backend = self._backend(cache_limit, carrier=carrier)
                try:
                    backend.fetch(item)
                except ValueError as exc:
                    if cache_limit != 0 or "reading mode" not in str(exc):
                        raise
                    return self.src.get_chunk(nchunk)
                chunk = backend.schunk.get_chunk(nchunk)
                backend._enforce_cache_limit(item)
        return chunk


def cold_cframe(carrier, payload) -> bytes:
    """Return a cache-free carrier without resolving or mutating its source."""
    cold = make_b2object_carrier(
        "remote_array",
        carrier.shape,
        carrier.dtype,
        chunks=carrier.chunks,
        blocks=carrier.blocks,
        cparams=carrier.cparams,
        meta={
            key: carrier.schunk.meta[key]
            for key in carrier.schunk.meta
            if key not in {"b2nd", "b2o", "proxy"}
        },
    )
    from blosc2.proxy import _RESERVED_VLMETA

    for key in carrier.schunk.vlmeta:
        if key not in _RESERVED_VLMETA and key != "b2o":
            cold.schunk.vlmeta[key] = carrier.schunk.vlmeta[key]
    write_b2object_payload(cold, payload)
    return cold.to_cframe()


def export_cframe(carrier, payload, *, include_cache: bool) -> bytes:
    """Snapshot a warm or cold carrier while excluding concurrent mutations."""
    path = carrier.schunk.urlpath
    with carrier_thread_lock(path), carrier.schunk.holding_lock():
        if include_cache:
            return carrier.to_cframe()
        return cold_cframe(carrier, payload)
