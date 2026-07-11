"""C2CacheProvider: Caterva2 root provider mounting remote peers' @public
roots with a local chunk cache. Orchestration moved verbatim from the
pre-decoupling server.py branches — see plans/c2cache-decoupling.md §5 for
the provenance of each method."""

import asyncio
import contextlib
import json
import pathlib

import httpx

from c2cache import peercache, peers, remote
from caterva2.services import providers


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
