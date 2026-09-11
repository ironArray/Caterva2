"""Policy-checked portable stores backed by private shared sparse generations."""

from __future__ import annotations

import copy
import math
import zipfile
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import urlsplit

import blosc2
import fastapi
from blosc2.msgpack_utils import msgpack_packb

from caterva2.services import remote_proxy, storage_quota


def inspect(path):
    """Inspect a store archive without opening any remote source."""
    path = Path(path)
    if path.suffix != ".b2z" or not path.is_file():
        return None
    from blosc2.remote_store import get_zip_offsets

    try:
        offsets = get_zip_offsets(str(path))
    except zipfile.BadZipFile:
        return None
    entry = offsets.get("embed.b2e")
    if entry is None:
        return None
    if not entry["stored"] or entry["length"] > remote_proxy.policy.max_metadata_bytes:
        raise remote_proxy.RemoteArrayDenied("Store metadata exceeds the configured limit or is compressed")
    embed = blosc2.blosc2_ext.open(str(path), "r", entry["offset"])
    if "b2remote_store" not in embed.meta:
        return None
    try:
        manifest, _ = blosc2.RemoteStore._load_artifact_manifest(str(path))
        validate_manifest(manifest)
    except (KeyError, TypeError, ValueError) as exc:
        raise remote_proxy.RemoteArrayDenied(f"Invalid RemoteStore manifest: {exc}") from exc
    return manifest


def validate_manifest(manifest):
    blosc2.RemoteStore._validate_artifact_manifest(manifest)
    if set(manifest["source"]) != {"urlpath", "dataset", "kind"}:
        raise remote_proxy.RemoteArrayDenied("RemoteStore source contains unsupported fields")
    if len(msgpack_packb(manifest)) > remote_proxy.policy.max_metadata_bytes:
        raise remote_proxy.RemoteArrayDenied("RemoteStore metadata exceeds the configured limit")
    if len(manifest["nodes"]) > remote_proxy.policy.max_nodes:
        raise remote_proxy.RemoteArrayDenied("RemoteStore node count exceeds the configured limit")
    # Reuse array policy validation, including the strict cache-policy schema.
    policy = manifest.get("cache_policy", "none")
    limit = manifest.get("max_cache_bytes")
    if (
        policy not in {"none", "memory", "disk"}
        or (policy != "none" and limit is not None and (type(limit) is not int or limit <= 0))
        or (policy == "none" and limit is not None)
        or (policy == "memory" and limit is None)
    ):
        raise remote_proxy.RemoteArrayDenied("Invalid RemoteStore cache policy or allowance")


def source_url(manifest):
    source = manifest["source"]
    return remote_proxy._validated_source(
        {
            "kind": "remote_array",
            "version": 1,
            "source": {
                "kind": "fsspec",
                "version": 1,
                "urlpath": source["urlpath"],
                "assume_immutable": True,
            },
            "cache_policy": "none",
            "max_cache_bytes": None,
        }
    )


def filesystem(manifest):
    parsed = urlsplit(source_url(manifest))
    host = parsed.hostname.encode("idna").decode("ascii").lower()
    addresses = remote_proxy._public_addresses(host, parsed.port or 443)
    return remote_proxy._https_filesystem(host, addresses)


def cold_export(manifest, destination):
    """Write a descriptor-only archive without resolving its source."""
    manifest = dict(manifest, caches=[])
    storage = blosc2.Storage(contiguous=True)
    storage.meta = {"b2tree": {"version": 1}, "b2remote_store": {"version": 1}}
    embed = blosc2.SChunk(chunksize=8192, data=None, storage=storage)
    embed.vlmeta["b2remote_manifest"] = manifest
    with zipfile.ZipFile(destination, "x", compression=zipfile.ZIP_STORED) as archive:
        archive.writestr("embed.b2e", embed.to_cframe())


class ServerRemoteStore:
    """Container adapter; leaf handles contain descriptors, not live disk owners."""

    def __init__(self, path, manifest):
        self.path = str(path)
        self.manifest = manifest
        self.carrier_generation = storage_quota.signature(path)
        self.max_cache_bytes = manifest.get("max_cache_bytes")
        self.cache_policy = remote_proxy._effective_cache_policy(manifest.get("cache_policy", "none"))

    @contextmanager
    def open(self, runtime=None):
        fs = filesystem(self.manifest)
        source = self.manifest["source"]
        options = {
            "_filesystem": fs,
            "_source_validator": validate_array,
            "_manifest_validator": validate_manifest,
            "_max_nodes": remote_proxy.policy.max_nodes,
        }
        try:
            if runtime is not None:
                store = blosc2.RemoteStore.with_sparse_cache(
                    source["urlpath"],
                    runtime,
                    dataset=source.get("dataset"),
                    manifest=copy.deepcopy(self.manifest),
                    max_cache_bytes=self.max_cache_bytes,
                    carrier=self.path,
                    **options,
                )
            else:
                store = blosc2.RemoteStore(
                    source["urlpath"],
                    dataset=source.get("dataset"),
                    cache_policy=blosc2.CachePolicy.NONE,
                    _manifest=dict(copy.deepcopy(self.manifest), caches=[]),
                    **options,
                )
            with store:
                yield store
        finally:
            session = getattr(fs, "_session", None)
            if session is not None:
                fs.close_session(fs.loop, session)

    def operation(self, callback, *, cached=None):
        from caterva2.services.server import quota_coordinator

        try:
            source_url(self.manifest)
            return quota_coordinator().remote.store_operation(self, callback, cached=cached)
        except remote_proxy.RemoteArrayDenied as exc:
            raise fastapi.HTTPException(status_code=403, detail=str(exc)) from exc
        except ValueError as exc:
            raise fastapi.HTTPException(status_code=400, detail=str(exc)) from exc

    def _key(self, key):
        relative = key.strip("/")
        blosc2.remote_store.RemoteDiscovery._validate(relative)
        root = self.manifest["source"].get("dataset", "")
        return "/".join(part for part in (root, relative) if part)

    def leaves(self, prefix="/"):
        root = self.manifest["source"].get("dataset", "")
        full = self._key(prefix).rstrip("/")
        listed = self.manifest["listed"]
        if self.manifest["source"]["kind"] in {"b2z", "hdf5"} or full in listed:
            # Use known discovery offline only when all descendant groups are listed.
            complete = self.manifest["source"]["kind"] in {"b2z", "hdf5"} or all(
                key in listed
                for key, (kind, _) in self.manifest["nodes"].items()
                if kind == "group" and (key == full or key.startswith(full + "/") or not full)
            )
            if complete:
                return sorted(
                    "/" + (key[len(root) + 1 :] if root else key)
                    for key, (kind, _) in self.manifest["nodes"].items()
                    if kind == "ndarray" and (not full or key.startswith(full + "/"))
                )

        def discover(store):
            result = []
            pending = [prefix.strip("/")]
            while pending:
                key = pending.pop()
                node = store.get_info(key)
                if node.kind == "ndarray":
                    result.append("/" + key)
                elif node.kind == "group":
                    with store[key] as group:
                        pending.extend("/".join(p for p in (key, child) if p) for child in group)
                if len(result) + len(pending) > remote_proxy.policy.max_nodes:
                    raise remote_proxy.RemoteArrayDenied("RemoteStore listing exceeds the node limit")
            return sorted(result)

        return self.operation(discover)

    def get(self, key):
        from caterva2.services.srv_utils import GROUP

        try:
            known = self.manifest["nodes"].get(self._key(key))
            kind = known[0] if known else self.operation(lambda store: store.kind(key.strip("/")))
            if kind == "group":
                return GROUP
            if kind != "ndarray":
                return None
            return ServerStoreArray(self, key.strip("/"))
        except KeyError:
            return None

    def is_group(self, node):
        from caterva2.services.srv_utils import GROUP

        return node is GROUP

    def is_leaf(self, key):
        known = self.manifest["nodes"].get(self._key(key))
        if known is not None:
            return known[0] == "ndarray"
        node = self.get(key)
        return node is not None and not self.is_group(node)

    def leaf_size(self, key):
        return None

    def size(self, prefix="/"):
        return Path(self.path).stat().st_size if prefix == "/" else None

    def close(self):
        pass  # Every operation closes its own runtime and child handles.


class ServerStoreArray(remote_proxy.ServerRemoteArray):
    def __init__(self, store, key):
        self.store, self.key, self.path = store, key, store.path
        self.cache_policy, self.max_cache_bytes = store.cache_policy, store.max_cache_bytes

        def geometry(runtime):
            with runtime[key] as array:
                validate_array(array)
                return array.shape, array.dtype, array.chunks, array.blocks, array.cparams, dict(array.attrs)

        self.shape, self.dtype, self.chunks, self.blocks, self.cparams, self.attrs = store.operation(
            geometry
        )

    def read(self, item=(), *, cache_limit=None):
        return self.quota_read(None, item)

    def get_chunk(self, nchunk, *, cache_limit=None):
        return self.quota_read(None, nchunk=nchunk)

    def quota_read(self, quota, item=(), *, nchunk=None):
        def read(runtime):
            with runtime[self.key] as array:
                validate_array(array)
                if (array.shape, array.dtype, array.chunks, array.blocks) != (
                    self.shape,
                    self.dtype,
                    self.chunks,
                    self.blocks,
                ):
                    raise storage_quota.StorageBusy("RemoteStore leaf geometry changed")
                return array[item] if nchunk is None else array.get_chunk(nchunk)

        return self.store.operation(
            read, cached=lambda runtime: runtime.read_cached(self.key, item, nchunk=nchunk)
        )


def validate_array(array):
    policy = remote_proxy.policy
    if hasattr(array, "max_concurrency"):
        array.max_concurrency = policy.max_concurrency
    if (
        len(array.shape) > policy.max_rank
        or math.prod(array.shape) * array.dtype.itemsize > policy.max_nbytes
    ):
        raise remote_proxy.RemoteArrayDenied("RemoteStore leaf exceeds array geometry limits")
    if (
        math.prod(math.ceil(s / c) for s, c in zip(array.shape, array.chunks, strict=True))
        > policy.max_chunks
    ):
        raise remote_proxy.RemoteArrayDenied("RemoteStore leaf exceeds the chunk limit")
