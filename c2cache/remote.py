"""Remote dataset access for peer mounts: C2Array shim + sparse Proxy cache.

Verified mechanics: see "Verified by experiment" in
plans/caterva3-remote-peer-mounts.md and repo-root e2e_peer_test.py.
"""

import asyncio
import hashlib
import json
import math
import pathlib
import shutil
import threading

import blosc2
import httpx
import numpy as np

import caterva2
from caterva2 import api_utils
from caterva2.services.providers import split_container_path

# Errors that mean "the peer is unreachable" (connection/timeout), as opposed
# to an HTTP status the peer deliberately returned or a local bug. Every peer
# data-path call (caterva2.Client, blosc2.C2Array.get_chunk/aget_chunk, and
# our own fallback fetch below) is httpx-based, so httpx.TransportError alone
# covers connection/timeout failures. Client._post also wraps ReadTimeout in a
# bare TimeoutError.
OFFLINE_ERRORS = (
    httpx.TransportError,
    TimeoutError,
)


class NotAFetchableDataset(Exception):
    """The remote catalog entry is not a fetchable dataset (e.g. a bare
    container file / group, whose info carries no shape/schunk)."""


# One long-lived Client per peer urlbase: connection pooling across requests
# and chunks, and one single place that normalizes the trailing slash
# (C2Array slash-terminates urlbase, which trips a PurePosixPath quirk in
# Client._format_paths that collapses "http://" to "http:/").
_clients: dict[str, caterva2.Client] = {}
_clients_lock = threading.Lock()


def client_for(urlbase):
    urlbase = urlbase.rstrip("/")
    with _clients_lock:
        client = _clients.get(urlbase)
        if client is None:
            client = _clients[urlbase] = caterva2.Client(urlbase, timeout=5)
        return client


class RemoteSource(blosc2.C2Array):
    """C2Array with an async chunk getter and a per-leaf fetch strategy.

    use_chunk_api=True  -> per-chunk GET api/chunk (plain .b2nd datasets),
                           via blosc2's own real async aget_chunk (httpx
                           AsyncClient, gathered/bounded by Proxy.afetch).
    use_chunk_api=False -> chunk-aligned GET api/fetch fallback (container
                           members: api/chunk 404s on those, verified), via
                           our own lazy async client below.
    """

    def __init__(self, path, urlbase, use_chunk_api=True):
        super().__init__(path, urlbase=urlbase)
        self.use_chunk_api = use_chunk_api
        self._fetch_aclient = None  # lazy async client for the api/fetch fallback path

    async def aget_chunk(self, nchunk):
        if self.use_chunk_api:
            return await super().aget_chunk(nchunk)  # blosc2.C2Array: real async api/chunk GET
        return await self._chunk_via_fetch(nchunk)

    async def aclose(self):
        """Close both lazy async HTTP clients this source may have opened:
        the api/chunk one from the base class, and our own api/fetch one
        for the container-member fallback. Callers must call this once
        they're done with a RemoteSource -- it is not closed automatically
        (matches blosc2.C2Array.aget_chunk's own contract)."""
        await super().aclose()
        if self._fetch_aclient is not None:
            await self._fetch_aclient.aclose()
            self._fetch_aclient = None

    def _chunk_slice(self, nchunk):
        """C-order chunk grid coordinates -> tuple of slices for `nchunk`."""
        grid = [math.ceil(s / c) for s, c in zip(self.shape, self.chunks, strict=True)]
        coords = np.unravel_index(nchunk, grid)
        return tuple(
            slice(int(i) * c, min((int(i) + 1) * c, s))
            for i, c, s in zip(coords, self.chunks, self.shape, strict=True)
        )

    async def _chunk_via_fetch(self, nchunk):
        """Fetch this chunk's exact slice via api/fetch and recompress it
        into a cache-shaped chunk (padded to full chunkshape)."""
        slice_ = self._chunk_slice(nchunk)
        params = {"slice_": api_utils.slice_to_string(slice_)}
        if self._fetch_aclient is None:
            self._fetch_aclient = httpx.AsyncClient(timeout=5)
        # self.urlbase is slash-terminated (blosc2.C2Array.__init__ enforces it).
        response = await self._fetch_aclient.get(f"{self.urlbase}api/fetch/{self.path}", params=params)
        response.raise_for_status()
        try:
            data = blosc2.ndarray_from_cframe(response.content)
        except RuntimeError:
            data = blosc2.schunk_from_cframe(response.content)
        full = np.zeros(self.chunks, dtype=self.dtype)
        region = tuple(slice(0, s.stop - s.start) for s in slice_)
        full[region] = data[...]
        packed = blosc2.asarray(full, chunks=self.chunks, blocks=self.blocks, cparams=self.cparams)
        return packed.schunk.get_chunk(0)


# --- sparse cache handling ---------------------------------------------


def cache_path(pool_dir, peer_id, remote_path):
    """Local cache location for one remote dataset.

    SECURITY: remote_path comes from the peer's catalog (untrusted). It is
    hashed, never spliced into the filesystem path.
    """
    h = hashlib.sha256(f"{peer_id}:{remote_path}".encode()).hexdigest()[:32]
    return pathlib.Path(pool_dir) / (h + ".b2nd")


def open_cached_proxy(source, cpath, remote_mtime):
    """Return a blosc2.Proxy over `source` with a persistent sparse cache at
    `cpath`. Creates the cache on first use; drops and recreates it when the
    remote dataset changed (mtime mismatch -> stale-chunk protection)."""
    cpath = pathlib.Path(cpath)
    cache = None
    if cpath.exists():
        try:
            cache = blosc2.open(str(cpath), mode="a", locking=True)
            meta = json.loads(cache.schunk.vlmeta.get("_peer_src", "{}"))
            valid = meta.get("mtime") == remote_mtime
        except Exception:
            # A cache that won't open (e.g. left half-written by a crashed
            # writer) is just an invalid cache: rebuild it, don't crash.
            valid = False
        if not valid:
            cache = None  # drop the open handle before removing the dir
            shutil.rmtree(cpath)
            pathlib.Path(str(cpath) + ".atime.npy").unlink(missing_ok=True)
    if cache is None:
        cpath.parent.mkdir(parents=True, exist_ok=True)
        cache = blosc2.empty(
            source.shape,
            source.dtype,
            chunks=source.chunks,
            blocks=source.blocks,
            cparams=source.cparams,
            urlpath=str(cpath),
            contiguous=False,
            mode="w",
            locking=True,
        )
        # src_meta lets a source stash extra keys (e.g. CTableSource's
        # kind/schema) so the offline path can rebuild responses from the
        # cache alone, without a fresh api/info.
        extra = getattr(source, "src_meta", None) or {}
        cache.schunk.vlmeta["_peer_src"] = json.dumps({"path": source.path, "mtime": remote_mtime, **extra})
    return blosc2.Proxy(source, _cache=cache)


def slice_fully_cached(cache, slice_):
    """True if every chunk touched by `slice_` is already filled (not an
    UNINIT/other special chunk). Reading a special chunk directly returns
    zeros silently (verified), so callers serving from a static, offline
    cache must check this first rather than trust the read."""
    touched = (
        range(cache.schunk.nchunks) if slice_ in (None, ()) else blosc2.get_slice_nchunks(cache, slice_)
    )
    specials = {info.nchunk: info.special for info in cache.schunk.iterchunks_info()}
    return all(
        specials.get(int(n), blosc2.SpecialValue.UNINIT) == blosc2.SpecialValue.NOT_SPECIAL for n in touched
    )


# --- CTable peer cache: one structured-dtype sparse .b2nd per table ------
# Design: plans/caterva3-remote-peer-simplified.md Part 1. The cache is a
# plain sparse NDArray whose compound dtype packs all columns of a row, so
# the whole NDArray peer-cache path (open_cached_proxy, slice_fully_cached,
# peercache locking/eviction/touch) applies verbatim.

# Compound chunk bytes = rows_per_chunk x sum(itemsizes); a wide table can
# inflate this. Cap comfortably under blosc2's 2 GiB chunk limit; beyond it
# the table is just non-cacheable (pass-through).
CTABLE_MAX_CHUNK_BYTES = 512 * 2**20

# CTableSource is not a C2Array, so Proxy.afetch defaults to 1 in-flight
# aget_chunk (proxy.py); pass this explicitly instead.
CTABLE_FETCH_CONCURRENCY = 4


def _ctable_fixed_dtypes(schema_dict):
    """Compound numpy dtype covering every column of `schema_dict` in schema
    order, or None when the table is non-cacheable: any list/varlen-scalar/
    dictionary/ndarray column, or column names numpy rejects as structured
    field names."""
    # Internal blosc2 APIs (schema_compiler, CTable._is_* predicates),
    # pinned to the blosc2>=4.8.0 floor in pyproject.
    from blosc2.schema_compiler import schema_from_dict

    try:
        compiled = schema_from_dict(schema_dict)
        for col in compiled.columns:
            if (
                blosc2.CTable._is_list_column(col)
                or blosc2.CTable._is_varlen_scalar_column(col)
                or blosc2.CTable._is_dictionary_column(col)
                or blosc2.CTable._is_ndarray_column(col)
            ):
                return None
        return np.dtype([(col.name, col.dtype) for col in compiled.columns])
    except Exception:
        return None


def _synth_ctable_cframe(schema_dict, cols, n):
    """A valid CTable cframe from per-column numpy arrays: an EmbedStore with
    /_meta, an all-True /_valid_rows and /_cols/<relpath> per column,
    mirroring CTable.to_cframe (ctable.py) for the fixed-width scalar case."""
    from blosc2.ctable_storage import _column_name_to_relpath  # blosc2>=4.8.0 internal

    estore = blosc2.EmbedStore(urlpath=None, mode="w")
    meta = blosc2.SChunk()
    meta.vlmeta["kind"] = "ctable"
    meta.vlmeta["version"] = 1
    meta.vlmeta["schema"] = json.dumps(schema_dict)
    estore["/_meta"] = meta
    estore["/_valid_rows"] = blosc2.asarray(np.ones(n, dtype=np.bool_))
    for name, arr in cols.items():
        estore[f"/_cols/{_column_name_to_relpath(name)}"] = blosc2.asarray(arr)
    return estore.to_cframe()


class CTableSource:
    """Duck-typed blosc2.Proxy source for one remote CTable: 1-D structured
    rows, one api/fetch round trip per row-chunk (all columns at once, which
    is the peer's fetch unit anyway)."""

    def __init__(self, remote_path, urlbase, info, dtype):
        self.path = remote_path
        self.urlbase = urlbase.rstrip("/") + "/"
        self.shape = (info["nrows"],)
        self.chunks = tuple(info["chunks"])
        self.blocks = None  # let blosc2 pick; per-column blocks don't map to compound rows
        self.dtype = dtype
        self.cparams = blosc2.CParams()
        # Travels into the cache's _peer_src vlmeta (open_cached_proxy) so
        # the offline path can synthesize cframes without a live api/info.
        self.src_meta = {"kind": "ctable", "schema": info["schema_dict"]}
        self._aclient = None
        self._client = None

    def _pack_rows(self, table, start, stop):
        rows = np.zeros(self.chunks[0], dtype=self.dtype)  # zero-pad trailing chunk
        for name in self.dtype.names:
            rows[name][: stop - start] = table[name][:]
        # Keep `packed` referenced while reading the chunk: NDArray.schunk
        # does NOT keep its parent alive, so a one-liner here is a
        # use-after-free (verified; see plan).
        packed = blosc2.asarray(rows, chunks=self.chunks)
        return packed.schunk.get_chunk(0)

    def _chunk_range(self, nchunk):
        start = nchunk * self.chunks[0]
        return start, min(start + self.chunks[0], self.shape[0])

    async def aget_chunk(self, nchunk):
        start, stop = self._chunk_range(nchunk)
        if self._aclient is None:
            self._aclient = httpx.AsyncClient(timeout=5)
        resp = await self._aclient.get(
            f"{self.urlbase}api/fetch/{self.path}", params={"slice_": f"{start}:{stop}"}
        )
        resp.raise_for_status()
        table = blosc2.ctable_from_cframe(resp.content)
        return self._pack_rows(table, start, stop)

    def get_chunk(self, nchunk):
        # Sync twin of aget_chunk: Proxy.__getitem__ falls back to it for any
        # chunk afetch didn't fill (shouldn't happen in our flow, but Proxy's
        # contract expects it).
        start, stop = self._chunk_range(nchunk)
        if self._client is None:
            self._client = httpx.Client(timeout=5)
        resp = self._client.get(f"{self.urlbase}api/fetch/{self.path}", params={"slice_": f"{start}:{stop}"})
        resp.raise_for_status()
        table = blosc2.ctable_from_cframe(resp.content)
        return self._pack_rows(table, start, stop)

    async def aclose(self):
        if self._aclient is not None:
            await self._aclient.aclose()
            self._aclient = None
        if self._client is not None:
            self._client.close()
            self._client = None


async def _passthrough_ctable_cframe(urlbase, remote_path, start, stop):
    """Milestone-0 arm: relay the peer's own api/fetch cframe for the row
    range, no caching. Permanent fallback for non-cacheable tables."""
    async with httpx.AsyncClient(timeout=5) as client:
        resp = await client.get(
            f"{urlbase.rstrip('/')}/api/fetch/{remote_path}", params={"slice_": f"{start}:{stop}"}
        )
        resp.raise_for_status()
        return resp.content


def ctable_cacheable(info):
    """The compound dtype for a cacheable peer CTable, or None (pass-through):
    non-fixed columns, no shared chunk grid, or oversized compound chunks."""
    dtype = _ctable_fixed_dtypes(info.get("schema_dict") or {})
    chunks = info.get("chunks")
    if dtype is None or not chunks or chunks[0] * dtype.itemsize > CTABLE_MAX_CHUNK_BYTES:
        return None
    return dtype


async def fetch_ctable_slice(adapter, key, info, start, stop):
    """Cframe bytes for rows [start, stop) of the peer CTable behind `key`.
    Caller holds cache_lock(adapter.cache_path(key)). Cacheable tables go
    through the structured sparse cache (offline-capable); the rest pass
    through to the peer's api/fetch."""
    from c2cache import peercache  # late: keeps remote.py importable standalone in tests

    remote_path = adapter._remote_path(key)
    dtype = ctable_cacheable(info)
    if dtype is None:
        return await _passthrough_ctable_cframe(adapter.peer.urlbase, remote_path, start, stop)
    src = CTableSource(remote_path, adapter.peer.urlbase, info, dtype)
    try:
        proxy = await asyncio.to_thread(open_cached_proxy, src, adapter.cache_path(key), info.get("mtime"))
        await proxy.afetch(slice(start, stop), max_concurrency=CTABLE_FETCH_CONCURRENCY)
        rows = await asyncio.to_thread(lambda: proxy[start:stop])
        await asyncio.to_thread(peercache.touch, proxy, (slice(start, stop),))
    finally:
        await src.aclose()
    cols = {name: rows[name] for name in dtype.names}
    return await asyncio.to_thread(_synth_ctable_cframe, info["schema_dict"], cols, stop - start)


class PeerCTableView:
    """Duck CTable for the htmx data-grid (nrows/schema_dict/slice), built
    straight from api/info — no cache open just to render. `aload` (driven
    by the view handle's prefetch) materializes one row window through
    fetch_ctable_slice; `slice` then serves from that window."""

    def __init__(self, adapter, key, info):
        self._adapter, self._key, self._info = adapter, key, info
        self.nrows = info["nrows"]
        self._window = None  # (start, stop, local CTable), set by aload

    def schema_dict(self):
        return self._info["schema_dict"]

    async def aload(self, start, stop):
        """Fetch rows [start, stop) into a local CTable window. Caller holds
        cache_lock(adapter.cache_path(key)) (fetch_ctable_slice's contract)."""
        data = await fetch_ctable_slice(self._adapter, self._key, self._info, start, stop)
        self._window = (start, stop, blosc2.ctable_from_cframe(data))

    def slice(self, start, stop):
        wstart, wstop, table = self._window  # aload'ed via handle.prefetch
        if (start, stop) == (wstart, wstop):
            return table
        if wstart <= start and stop <= wstop:  # sub-window: cheap local re-slice
            return table.slice(start - wstart, stop - wstart)
        raise RuntimeError(f"rows [{start}, {stop}) not prefetched (have [{wstart}, {wstop}))")


def slice_ctable_cached(cache, start, stop):
    """Offline read: synthesized cframe for rows [start, stop) from a
    ctable-kind structured cache, or None when the range isn't fully cached
    (or the cache carries no schema). Caller holds the cache's lock."""
    meta = json.loads(cache.schunk.vlmeta.get("_peer_src", "{}"))
    schema = meta.get("schema")
    if schema is None:
        return None
    rows = cache[start:stop]
    # Check AFTER the read, same discipline as the NDArray offline path: a
    # positive check now means every touched chunk was present during it.
    if not slice_fully_cached(cache, (slice(start, stop),)):
        return None
    cols = {name: rows[name] for name in rows.dtype.names}
    return _synth_ctable_cframe(schema, cols, stop - start)


def _leaf_kind(info):
    """Classify a catalog leaf from its api/info dict: a bare TreeStore .b2z
    reports as a Directory ("container", mountable), a CTable .b2z as
    CTableMetadata ("ctable", direct view), anything else is a plain
    "dataset" row."""
    if info.get("kind") == "ctable":
        return "ctable"
    if "nfiles" in info:  # models.Directory: mtime/size/nfiles
        return "container"
    return "dataset"


class RemotePeerAdapter:
    """Adapter protocol over a peer's @public root.

    All methods doing HTTP are synchronous — async endpoints must call them
    via asyncio.to_thread(). `size()` returns None (unknown/expensive), which
    the existing listing code already tolerates.
    """

    def __init__(self, peer, registry, pool_dir):
        self.peer = peer
        self.registry = registry
        self.pool_dir = pathlib.Path(pool_dir)

    # -- catalog ----------------------------------------------------------

    def leaves(self, prefix="/"):
        cat = self.registry.catalog(self.peer)
        prefix = prefix.strip("/")
        for p in cat:
            if not prefix or p.startswith(prefix + "/") or p == prefix:
                yield p

    def size(self, prefix="/"):
        return None

    def leaf_size(self, key):
        # Memoized on the Peer (cleared with each catalog refresh): the
        # listing calls this once per row, and one api/info round trip per
        # row on every render would add up.
        sizes = self.peer.sizes
        if key not in sizes:
            info = self._info(key)
            sizes[key] = info.get("schunk", {}).get("cbytes") or info.get("cbytes") or info.get("size") or 0
            # Same response also tells the leaf's kind; memoize it alongside
            # (zero extra HTTP for the listing's mountable/plain decision).
            self.peer.kinds[key] = _leaf_kind(info)
        return sizes[key]

    def is_group(self, node):
        return False  # remote catalog entries are always leaves

    def close(self):
        pass

    # -- data -------------------------------------------------------------

    def _remote_path(self, key):
        return "@public/" + key.strip("/")

    def _info(self, key):
        try:
            return client_for(self.peer.urlbase).get_info(self._remote_path(key))
        except OFFLINE_ERRORS:
            # Only transport failures mean the peer is down; an HTTP status
            # (e.g. 404 for a bad path) is the peer answering and must NOT
            # take the whole root offline.
            self.registry.mark_offline(self.peer)
            raise

    def cache_path(self, key):
        """Local cache path for `key`, computable up front (pure hashing, no
        I/O) so callers can take that cache's lock before opening anything."""
        return cache_path(self.pool_dir / self.peer.name, self.peer.peer_id, key)

    def get(self, key, info=None):
        """Return a blosc2.Proxy for the remote dataset behind `key`.
        `info` may be passed pre-fetched to skip a duplicate api/info GET."""
        if info is None:
            info = self._info(key)
        if "shape" not in info or "schunk" not in info:
            # e.g. a bare .h5/.b2z in the catalog: its info is a Directory
            # (group), not a dataset; there is nothing chunk-fetchable.
            raise NotAFetchableDataset(f"{key} is not a fetchable dataset (a container/group?)")
        mtime = info.get("mtime")  # top-level; info["schunk"]["mtime"] is always None
        # Container members can't use api/chunk (verified: 404) -> api/fetch
        # fallback. Reuse the canonical container-boundary rule.
        plain = split_container_path(key)[1] is None
        src = RemoteSource(self._remote_path(key), self.peer.urlbase, use_chunk_api=plain)
        return open_cached_proxy(src, self.cache_path(key), mtime)

    def get_cached_only(self, key):
        """The on-disk cache for `key` without any peer HTTP call, or None
        if nothing has been cached yet. Used when the peer is unreachable;
        callers must still check `slice_fully_cached` before trusting data,
        since not-yet-fetched chunks read back as silent zeros."""
        cpath = self.cache_path(key)
        if not cpath.exists():
            return None
        return blosc2.open(str(cpath), mode="a", locking=True)
