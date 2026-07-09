"""Remote dataset access for peer mounts: C2Array shim + sparse Proxy cache.

Verified mechanics: see "Verified by experiment" in
plans/caterva3-remote-peer-mounts.md and repo-root e2e_peer_test.py.
"""

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
        cache.schunk.vlmeta["_peer_src"] = json.dumps({"path": source.path, "mtime": remote_mtime})
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
            sizes[key] = info.get("schunk", {}).get("cbytes") or info.get("size") or 0
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

    def get(self, key):
        """Return a blosc2.Proxy for the remote dataset behind `key`."""
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
