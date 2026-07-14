"""C2CacheProvider: Caterva2 root provider mounting remote peers' @public
roots with a local chunk cache. Orchestration moved verbatim from the
pre-decoupling server.py branches — see plans/c2cache-decoupling.md §5 for
the provenance of each method."""

import asyncio
import contextlib
import json
import os
import pathlib
import tempfile

import blosc2
import httpx

from c2cache import peercache, peers, remote
from caterva2.services import providers


def _read_sidecar(entry):
    """The JSON sidecar of a whole-file cache entry, or None if missing or
    unreadable (a torn write is just a cache miss, never a 500)."""
    try:
        with open(str(entry) + ".json") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def _read_entry(entry):
    """Sidecar + body bytes of a whole-file entry; caller holds its
    cache_lock. Stamps the entry's atime (the eviction LRU signal)."""
    sidecar = _read_sidecar(entry) or {}
    data = entry.read_bytes()
    peercache.touch_file(entry)
    return sidecar, data


class _Handle:
    def __init__(self, adapter, array, root):
        self._adapter = adapter
        self.array = array
        self._root = root
        self.window = None  # set by prefetch; touch on clean exit only

    async def prefetch(self, window):
        try:
            # this cache's lock held by open_view
            await remote.afetch_retry_once(self.array, window)
        except remote.OFFLINE_ERRORS as exc:
            self._adapter.registry.mark_offline(self._adapter.peer)
            raise providers.ProviderUnavailable(f"Peer {self._root} is offline.") from exc
        self.window = window


class _CTableHandle:
    """View handle for a peer CTable: prefetch materializes the row window
    via the view's aload (fetch_ctable_slice touches internally, so there is
    no Proxy cleanup/touch on exit — open_view's CTable branch relies on
    that)."""

    def __init__(self, adapter, view, root, cpath):
        self._adapter = adapter
        self.array = view
        self._root = root
        self._cpath = cpath
        self.window = None

    async def prefetch(self, window):
        sl = window[0] if isinstance(window, tuple) and len(window) > 0 else window
        start, stop = providers.ctable_row_range(sl, self.array.nrows)
        try:
            async with peercache.cache_lock(self._cpath):
                await self.array.aload(start, stop)
        except httpx.HTTPStatusError as exc:
            raise providers.ProviderRelayedStatus(
                exc.response.status_code, f"Peer error: HTTP {exc.response.status_code}."
            ) from exc
        except remote.OFFLINE_ERRORS as exc:
            self._adapter.registry.mark_offline(self._adapter.peer)
            raise providers.ProviderUnavailable(f"Peer {self._root} is offline.") from exc
        self.window = window


class C2CacheProvider(providers.RootProvider):
    name = "c2cache"

    def __init__(self, settings, peer_confs):
        self.settings = settings
        self.peer_confs = peer_confs
        self.registry = None  # created in startup (needs settings.peer_id)
        from c2cache.panel import make_router  # late: avoids import cycle

        self.router = make_router(self)

    # -- lifespan -----------------------------------------------------------
    async def startup(self):
        # was server.py lifespan 248-255 (peer_id file creation STAYS in core)
        self.registry = peers.PeerRegistry(self.settings.peer_id)
        self.registry.load(self.peer_confs)
        await asyncio.to_thread(self.registry.handshake_all)
        peercache.pool_dir = self.settings.statedir / "peercache"
        peercache.pool_dir.mkdir(parents=True, exist_ok=True)
        peercache.budget = providers.parse_size(self.settings.conf.get(".peer_cache_quota", "1G"))
        peercache.peer_quotas = {
            p.name: p.cache_quota for p in self.registry.peers.values() if p.cache_quota
        }

    # -- control plane --------------------------------------------------------
    def roots(self):
        # was get_roots 379-387 / htmx_root_list 1809-1813: advertise only
        # peers that completed a handshake (peer_id set); kick re-probes.
        # Callable before startup() (discover() logs it at load time), when
        # self.registry is still None.
        if self.registry is None:
            return []
        out = []
        for peer in self.registry.peers.values():
            self.registry.maybe_reprobe(peer)
            if peer.peer_id is not None:
                out.append(peer.root)
        return out

    def owns(self, root):
        # was get_peer_adapter_or_none 1859-1869 (predicate part)
        return self.registry is not None and self.registry.get_known(root) is not None

    def widgets(self):
        if not self.registry or not self.registry.peers:
            return []
        return [{"label": "Peers", "icon": "fa-solid fa-server", "panel_url": f"provider/{self.name}/panel"}]

    def _adapter(self, root):
        peer = self.registry.get_known(root)
        if peer is None:  # caller checked owns(); defensive
            raise providers.ProviderUnavailable(f"unknown root {root}")
        return remote.RemotePeerAdapter(peer, self.registry, peercache.pool_dir)

    # -- data plane -----------------------------------------------------------
    async def list(self, root, prefix):
        # was api/list's peer_list() closure (server.py 415-427)
        adapter = self._adapter(root)

        # A prefix that is (or descends into) a .b2z/.h5 container never
        # matches the flat catalog (B lists the container as one opaque
        # entry); B's own get_list deep-lists container paths natively, so
        # forward the call instead.
        strip = prefix.strip("/")
        in_container = (
            providers.split_container_path(strip)[1] is not None
            or pathlib.PurePosixPath(strip).suffix in providers.BLOSC2_CONTAINER_SUFFIXES
        )
        if in_container:

            def deep_list():
                client = remote.client_for(adapter.peer.urlbase)
                return client.get_list(adapter._remote_path(strip))

            try:
                return await asyncio.to_thread(deep_list)
            except httpx.HTTPStatusError as exc:
                raise providers.ProviderRelayedStatus(exc.response.status_code) from exc
            except remote.OFFLINE_ERRORS as exc:
                adapter.registry.mark_offline(adapter.peer)
                raise providers.ProviderUnavailable(f"peer {root} is offline") from exc

        def peer_list():
            # Names relative to the requested path (the endpoint's contract,
            # matching the local branches), not full catalog keys.
            names = []
            for key in adapter.leaves(prefix):
                if key == strip:  # listing the dataset itself -> its name
                    names.append(key.rsplit("/", 1)[-1])
                else:
                    names.append(key[len(strip) + 1 :] if strip else key)
            return sorted(names)

        return await asyncio.to_thread(peer_list)

    async def rows(self, root, prefix=""):
        # was htmx_path_list's peer_rows() closure (server.py 1996-2006)
        adapter = self._adapter(root)
        # `keys` relative to prefix: deep-listed from B for container
        # prefixes (a mounted peer .b2z), the flat catalog otherwise.
        keys = await self.list(root, prefix) if prefix else None

        def peer_rows():
            out = []
            for key in keys if keys is not None else adapter.leaves():
                full_key = f"{prefix.strip('/')}/{key}" if prefix else key
                try:
                    size = adapter.leaf_size(full_key)
                except Exception:
                    size = 0
                # kind memoized by leaf_size from the same api/info; None
                # (info failed) renders as a plain row.
                out.append((key, size, adapter.peer.kinds.get(full_key)))
            return out

        return await asyncio.to_thread(peer_rows)

    async def info(self, root, key):
        adapter = self._adapter(root)
        try:
            return await asyncio.to_thread(adapter._info, key)
        except httpx.HTTPStatusError as exc:
            # The peer answered (e.g. 404 for a bad path): relay its status.
            raise providers.ProviderRelayedStatus(exc.response.status_code) from exc
        except remote.OFFLINE_ERRORS as exc:
            raise providers.ProviderUnavailable(f"peer {root} is offline") from exc

    async def fetch(self, root, key, slice_):
        adapter = self._adapter(root)
        real_slice = () if slice_ is None else slice_
        parts = real_slice if isinstance(real_slice, tuple) else (real_slice,)
        if any(isinstance(p, slice) and p.step not in (None, 1) for p in parts):
            raise providers.ProviderBadRequest("stepped slices are not supported on peer datasets")
        cpath = adapter.cache_path(key)
        proxy = None
        try:
            info = await asyncio.to_thread(adapter._info, key)
            if info.get("kind") == "ctable":
                # CTable branch: row-range cframe bytes (cached through the
                # structured sparse frame, or passed through for
                # non-cacheable tables). fetch_ctable_slice touches
                # internally; same lock discipline as the NDArray path.
                start, stop = providers.ctable_row_range(real_slice, info["nrows"])
                async with peercache.cache_lock(cpath):
                    data = await remote.fetch_ctable_slice(adapter, key, info, start, stop)
                await peercache.ensure_budget()
                return data
            # Everything that touches this cache's frame, under its own lock
            # (not the whole pool's): concurrent fetch/eviction of this same
            # cache corrupt reads. Fetches of *other* caches are unaffected.
            # Data is read out before ensure_budget (evict-after-read).
            async with peercache.cache_lock(cpath):
                proxy = await asyncio.to_thread(adapter.get, key, info)
                await remote.afetch_retry_once(proxy, real_slice)
                data = await asyncio.to_thread(lambda: proxy[real_slice])
                await asyncio.to_thread(peercache.touch, proxy, real_slice)
            await peercache.ensure_budget()
        except remote.NotAFetchableDataset as exc:
            raise providers.ProviderBadRequest(str(exc)) from exc
        except httpx.HTTPStatusError as exc:
            raise providers.ProviderRelayedStatus(exc.response.status_code) from exc
        except remote.OFFLINE_ERRORS as exc:
            adapter.registry.mark_offline(adapter.peer)
            async with peercache.cache_lock(cpath):  # offline reads touch the frame too
                cached = await asyncio.to_thread(adapter.get_cached_only, key)
                if cached is None:
                    raise providers.ProviderUnavailable(f"peer {root} is offline") from exc
                # The one on-disk artifact says what it caches: a ctable-kind
                # frame synthesizes a cframe, a plain one reads the slice.
                meta = json.loads(cached.schunk.vlmeta.get("_peer_src", "{}"))
                if meta.get("kind") == "ctable":
                    start, stop = providers.ctable_row_range(real_slice, cached.shape[0])
                    data = await asyncio.to_thread(remote.slice_ctable_cached, cached, start, stop)
                    if data is None:
                        raise providers.ProviderUnavailable(f"peer {root} is offline") from exc
                else:
                    data = await asyncio.to_thread(lambda: cached[real_slice])
                    # Check AFTER the read: a positive check now means every
                    # touched chunk was present during the read.
                    if not remote.slice_fully_cached(cached, real_slice):
                        raise providers.ProviderUnavailable(f"peer {root} is offline") from exc
        finally:
            # RemoteSource.aget_chunk lazily opens async HTTP client(s) that
            # aren't closed automatically; this Proxy/source is fresh per
            # call (adapter.get), so close them here regardless of outcome.
            if proxy is not None:
                await proxy.src.aclose()
        return data

    async def download(self, root, key, accept_encoding=None):
        # Whole-file cache for plain files (the ones B stores as .b2 frames);
        # datasets and containers keep the pure relay below. Design:
        # plans/plain-files-caching.md.
        adapter = self._adapter(root)
        suffix = pathlib.PurePosixPath(key.strip("/")).suffix
        relay_only = (
            suffix in providers.BLOSC2_NATIVE_SUFFIXES | providers.BLOSC2_CONTAINER_SUFFIXES
            or providers.split_container_path(key)[1] is not None
        )
        if not relay_only:
            return await self._download_plain_file(adapter, root, key, accept_encoding)
        return await self._relay_download(adapter, root, key, accept_encoding)

    async def _download_plain_file(self, adapter, root, key, accept_encoding):
        entry = adapter.cache_path(key).with_suffix(".b2")
        try:
            info = await asyncio.to_thread(adapter._info, key)  # freshness token (mtime)
        except httpx.HTTPStatusError as exc:
            raise providers.ProviderRelayedStatus(exc.response.status_code) from exc
        except remote.OFFLINE_ERRORS as exc:
            # _info already marked the peer offline; serve-stale posture, same
            # as the dataset cache: a cached entry beats a 503.
            if entry.exists():
                return await self._serve_file_entry(entry, accept_encoding)
            raise providers.ProviderUnavailable(f"peer {root} is offline") from exc
        if "shape" in info or "nfiles" in info or info.get("kind") == "ctable":
            # a dataset or container after all: keep the plain relay
            return await self._relay_download(adapter, root, key, accept_encoding)
        # Same asyncio lock as the chunk caches gives single-flight: concurrent
        # first downloads make one upstream request; waiters find the entry.
        async with peercache.cache_lock(entry):
            sidecar = await asyncio.to_thread(_read_sidecar, entry)
            if sidecar is None or sidecar.get("mtime") != info.get("mtime"):
                try:
                    passthrough = await self._fill_file_entry(adapter, key, entry, info.get("mtime"))
                except remote.OFFLINE_ERRORS as exc:
                    adapter.registry.mark_offline(adapter.peer)
                    if not entry.exists():
                        raise providers.ProviderUnavailable(f"peer {root} is offline") from exc
                    passthrough = None  # stale entry, peer just went down: serve it
                if passthrough is not None:
                    return passthrough
            sidecar, data = await asyncio.to_thread(_read_entry, entry)
        await peercache.ensure_budget()
        return await self._entry_response(sidecar, data, accept_encoding)

    async def _fill_file_entry(self, adapter, key, entry, remote_mtime):
        """Fetch B's own .b2 artifact for `key` into the whole-file entry
        (tmp + rename + JSON sidecar + atime stamp); caller holds
        cache_lock(entry). Fill-then-serve: the first byte waits for the full
        download (ponytail: a streaming tee is the upgrade path if giant
        files make this annoying; reports/CSVs won't). Returns None on
        success, or — when B's response is not a blosc2 frame (not a
        compressed plain file after all) — a ready (body, media_type,
        headers) passthrough triple, caching nothing."""
        url = f"{adapter.peer.urlbase}/api/download/{adapter._remote_path(key)}"
        async with httpx.AsyncClient(timeout=5) as client:
            resp = await client.get(url, headers={"Accept-Encoding": "blosc2"})
        if resp.status_code != 200:
            raise providers.ProviderRelayedStatus(resp.status_code)
        if resp.headers.get("content-encoding") != "blosc2":
            content = resp.content

            async def body():
                yield content

            relay = ("content-disposition", "content-length")
            headers = {k: v for k, v in resp.headers.items() if k.lower() in relay}
            return body(), resp.headers.get("content-type", "application/octet-stream"), headers

        def write():
            entry.parent.mkdir(parents=True, exist_ok=True)
            fd, tmp = tempfile.mkstemp(dir=entry.parent, prefix=entry.name, suffix=".tmp")
            with os.fdopen(fd, "wb") as f:
                f.write(resp.content)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, entry)
            sidecar = {
                "mtime": remote_mtime,
                "content_type": resp.headers.get("content-type", "application/octet-stream"),
                "content_disposition": resp.headers.get("content-disposition"),
            }
            fd, tmp = tempfile.mkstemp(dir=entry.parent, prefix=entry.name, suffix=".tmp")
            with os.fdopen(fd, "w") as f:
                json.dump(sidecar, f)
            os.replace(tmp, str(entry) + ".json")
            # atime now, not just on serve: a freshly filled entry must not
            # look like the oldest candidate to ensure_budget.
            peercache.touch_file(entry)

        await asyncio.to_thread(write)
        return None

    async def _serve_file_entry(self, entry, accept_encoding):
        """Offline serve: replay the cached entry, 503 if it vanished
        (evicted between the exists() check and taking the lock)."""
        async with peercache.cache_lock(entry):
            try:
                sidecar, data = await asyncio.to_thread(_read_entry, entry)
            except FileNotFoundError as exc:
                raise providers.ProviderUnavailable("peer is offline") from exc
        return await self._entry_response(sidecar, data, accept_encoding)

    async def _entry_response(self, sidecar, data, accept_encoding):
        """(body, media_type, headers) from a whole-file entry's bytes: the
        .b2 frame verbatim for blosc2 clients, decompressed otherwise (same
        as B's own get_file_content)."""
        if accept_encoding == "blosc2":
            headers = {"Content-Encoding": "blosc2"}
        else:
            data = await asyncio.to_thread(lambda: blosc2.schunk_from_cframe(data)[:])
            headers = {}
        headers["Content-Length"] = str(len(data))
        if sidecar.get("content_disposition"):
            headers["Content-Disposition"] = sidecar["content_disposition"]

        async def body():
            yield data

        return body(), sidecar.get("content_type", "application/octet-stream"), headers

    async def _relay_download(self, adapter, root, key, accept_encoding):
        # Pure byte relay of the peer's own api/download: aiter_raw + verbatim
        # headers, so a blosc2-encoded body passes through untouched. No local
        # caching (a whole-file stream doesn't fit the sparse chunk cache).
        url = f"{adapter.peer.urlbase}/api/download/{adapter._remote_path(key)}"
        # "identity" otherwise: never let httpx advertise gzip on our behalf,
        # the relayed Content-Length/Content-Encoding must match the body.
        headers = {"Accept-Encoding": accept_encoding or "identity"}
        client = httpx.AsyncClient(timeout=5)
        try:
            resp = await client.send(client.build_request("GET", url, headers=headers), stream=True)
        except remote.OFFLINE_ERRORS as exc:
            await client.aclose()
            adapter.registry.mark_offline(adapter.peer)
            raise providers.ProviderUnavailable(f"peer {root} is offline") from exc
        if resp.status_code != 200:
            await resp.aclose()
            await client.aclose()
            raise providers.ProviderRelayedStatus(resp.status_code)

        async def body():
            try:
                async for chunk in resp.aiter_raw():
                    yield chunk
            finally:
                await resp.aclose()
                await client.aclose()

        relay = ("content-disposition", "content-encoding", "content-length")
        out_headers = {k: v for k, v in resp.headers.items() if k.lower() in relay}
        return body(), resp.headers.get("content-type", "application/octet-stream"), out_headers

    @contextlib.asynccontextmanager
    async def open_view(self, root, key):
        adapter = self._adapter(root)
        cpath = adapter.cache_path(key)
        try:
            info = await asyncio.to_thread(adapter._info, key)
        except httpx.HTTPStatusError as exc:
            raise providers.ProviderRelayedStatus(
                exc.response.status_code, f"Peer error: HTTP {exc.response.status_code}."
            ) from exc
        except remote.OFFLINE_ERRORS as exc:
            adapter.registry.mark_offline(adapter.peer)
            raise providers.ProviderUnavailable(f"Peer {root} is offline.") from exc
        if info.get("kind") == "ctable":
            # CTable branch: the view materializes row windows on prefetch
            # (taking the cache lock itself); nothing to lock or clean here.
            yield _CTableHandle(adapter, remote.PeerCTableView(adapter, key, info), root, cpath)
            await peercache.ensure_budget()
            return
        async with peercache.cache_lock(cpath):  # released on EVERY exit path
            try:
                arr = await asyncio.to_thread(adapter.get, key, info)
            except remote.NotAFetchableDataset as exc:
                raise providers.ProviderBadRequest(str(exc)) from exc
            except httpx.HTTPStatusError as exc:
                raise providers.ProviderRelayedStatus(
                    exc.response.status_code, f"Peer error: HTTP {exc.response.status_code}."
                ) from exc
            except remote.OFFLINE_ERRORS as exc:
                adapter.registry.mark_offline(adapter.peer)
                raise providers.ProviderUnavailable(f"Peer {root} is offline.") from exc
            try:
                handle = _Handle(adapter, arr, root)
                yield handle
                # Clean exit only (an exception skips this, matching the old
                # BaseException path that released without touching):
                if handle.window is not None:
                    await asyncio.to_thread(peercache.touch, arr, handle.window)
            finally:
                # Same lazy-async-client cleanup as fetch() -- arr.src is a
                # fresh RemoteSource per call.
                await arr.src.aclose()
        await peercache.ensure_budget()  # AFTER lock release (it locks itself)
