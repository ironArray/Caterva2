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
    pending = [manifest]
    nodes = 0
    while pending:
        current = pending.pop()
        nodes += len(current["nodes"])
        if nodes > remote_proxy.policy.max_nodes:
            raise remote_proxy.RemoteArrayDenied("RemoteStore node count exceeds the configured limit")
        pending.extend(entry["manifest"] for entry in current.get("linked", {}).values())
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


def root_kind(manifest):
    root = manifest["source"].get("dataset", "")
    return manifest["nodes"][root][0]


def filesystem_for_url(url):
    parsed = urlsplit(
        remote_proxy._validated_source(
            {
                "kind": "remote_array",
                "version": 1,
                "source": {
                    "kind": "fsspec",
                    "version": 1,
                    "urlpath": url,
                    "assume_immutable": True,
                },
                "cache_policy": "none",
                "max_cache_bytes": None,
            }
        )
    )
    host = parsed.hostname.encode("idna").decode("ascii").lower()
    addresses = remote_proxy._public_addresses(host, parsed.port or 443)
    return remote_proxy._https_filesystem(host, addresses)


def cold_export(manifest, destination):
    """Write a descriptor-only archive without resolving its source."""
    manifest = dict(manifest, caches=[], batch_caches=[], linked={})
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
        filesystems = []

        def authorized_filesystem(url):
            fs = filesystem_for_url(url)
            filesystems.append(fs)
            return fs

        source = self.manifest["source"]
        fs = authorized_filesystem(source["urlpath"])
        options = {
            "_filesystem": fs,
            "_filesystem_resolver": authorized_filesystem,
            "_source_validator": validate_array,
            "_batch_validator": validate_batch,
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
                    _manifest=dict(copy.deepcopy(self.manifest), caches=[], batch_caches=[], linked={}),
                    allow_table_root=True,
                    **options,
                )
            with store:
                yield store
        finally:
            for source_fs in filesystems:
                session = getattr(source_fs, "_session", None)
                if session is not None:
                    source_fs.close_session(source_fs.loop, session)

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
        try:
            full = self._key(prefix).rstrip("/")
        except ValueError:
            return []
        listed = self.manifest["listed"]
        has_links = any(kind == "remote_store" for kind, _ in self.manifest["nodes"].values())
        if (self.manifest["source"]["kind"] in {"b2z", "hdf5"} or full in listed) and not has_links:
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
                    if kind in {"ndarray", "ctable"} and (not full or key.startswith(full + "/"))
                )

        def discover(store):
            result = []
            pending = [prefix.strip("/")]
            while pending:
                key = pending.pop()
                node = store.get_info(key)
                if node.kind in {"ndarray", "ctable"}:
                    result.append("/" + key)
                elif node.kind in {"group", "remote_store"}:
                    with store[key] as group:
                        if node.kind == "remote_store" and group.kind("") == "ctable":
                            result.append("/" + key)
                        else:
                            pending.extend("/".join(p for p in (key, child) if p) for child in group)
                if len(result) + len(pending) > remote_proxy.policy.max_nodes:
                    raise remote_proxy.RemoteArrayDenied("RemoteStore listing exceeds the node limit")
            return sorted(result)

        return self.operation(discover)

    def get(self, key):
        from caterva2.services.srv_utils import GROUP

        try:
            full = self._key(key)
            known = self.manifest["nodes"].get(full)
            linked = any(
                kind == "remote_store" and full.startswith(path + "/")
                for path, (kind, _) in self.manifest["nodes"].items()
            )
            if known is None and self.manifest["source"]["kind"] in {"b2z", "hdf5"} and not linked:
                return None
            kind = known[0] if known else self.operation(lambda store: store.kind(key.strip("/")))
            if kind == "remote_store":

                def linked_kind(store):
                    with store[key.strip("/")] as linked_store:
                        return "ctable" if linked_store.kind("") == "ctable" else "remote_store"

                kind = self.operation(linked_kind)
            if kind in {"group", "remote_store"}:
                return GROUP
            if kind == "ndarray":
                return ServerStoreArray(self, key.strip("/"))
            if kind == "ctable":
                return ServerStoreTable(self, key.strip("/"))
            return None
        except (KeyError, ValueError):
            return None

    def is_group(self, node):
        from caterva2.services.srv_utils import GROUP

        return node is GROUP

    def is_leaf(self, key):
        try:
            known = self.manifest["nodes"].get(self._key(key))
        except ValueError:
            return False
        if known is not None:
            return known[0] in {"ndarray", "ctable"}
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


class ServerStoreTable:
    """Operation-scoped view of a CTable inside a portable RemoteStore."""

    def __init__(self, store, key, *, filter=None, sortby=None):
        self.store, self.key, self.path = store, key, store.path
        self.filter, self.sortby = filter, sortby

        def metadata(runtime):
            with self._open_table(runtime) as table:
                if filter:
                    table = table.where(filter)
                if sortby:
                    table = table.sort_by(sortby, view=True)
                schema = table.schema_dict()
                nbytes, cbytes = table.nbytes, table.cbytes
                return {
                    "nrows": table.nrows,
                    "ncols": table.ncols,
                    "chunks": table.chunks,
                    "blocks": table.blocks,
                    "schema_dict": schema,
                    "columns": [column["name"] for column in schema.get("columns", [])],
                    "nbytes": nbytes,
                    "cbytes": cbytes,
                    "cratio": nbytes / cbytes if cbytes else 0,
                    "vlmeta": dict(table.vlmeta[:]) if table.vlmeta[:] else {},
                    "attrs": dict(table.attrs),
                }

        self.metadata = store.operation(metadata)
        self.nrows = self.metadata["nrows"]

    @contextmanager
    def _open_table(self, runtime):
        with runtime[self.key] as node:
            if isinstance(node, blosc2.RemoteStore):
                with node[""] as table:
                    yield table
            else:
                yield node

    def schema_dict(self):
        return self.metadata["schema_dict"]

    def where(self, expression):
        return type(self)(self.store, self.key, filter=expression, sortby=self.sortby)

    def sort_by(self, column, *, view=False):
        return type(self)(self.store, self.key, filter=self.filter, sortby=column)

    def slice(self, start, stop):
        def read(runtime):
            with self._open_table(runtime) as table:
                if self.filter:
                    table = table.where(self.filter)
                if self.sortby:
                    table = table.sort_by(self.sortby, view=True)
                return table.slice(start, stop)

        return self.store.operation(read, cached=lambda runtime: runtime.read_cached_table(read))

    def fetch(self, slice_=None, *, filter=None, field=None):
        from caterva2.services.srv_utils import ctable_row_range

        if field is not None and field not in self.metadata["columns"]:
            raise fastapi.HTTPException(status_code=400, detail=f"Unknown table field: {field}")

        def read(runtime):
            with self._open_table(runtime) as table:
                expression = filter or self.filter
                try:
                    view = table.where(expression) if expression else table
                except (NameError, SyntaxError) as exc:
                    raise ValueError(f"Invalid table filter: {exc}") from exc
                if self.sortby:
                    view = view.sort_by(self.sortby, view=True)
                start, stop = ctable_row_range(slice_, view.nrows)
                if field is not None:
                    return view.select([field]).slice(start, stop).to_cframe()
                return view.slice(start, stop).to_cframe()

        return self.store.operation(read, cached=lambda runtime: runtime.read_cached_table(read))


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


def validate_batch(batch):
    policy = remote_proxy.policy
    if (
        len(batch.offsets) > policy.max_chunks
        or batch.member_length > policy.max_nbytes
        or len(msgpack_packb((batch.meta, batch.vlmeta))) > policy.max_metadata_bytes
    ):
        raise remote_proxy.RemoteArrayDenied("RemoteStore batch exceeds resource limits")
