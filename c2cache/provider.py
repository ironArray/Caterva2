"""C2CacheProvider: Caterva2 root provider mounting remote peers' @public
roots with a local chunk cache. Orchestration moved verbatim from the
pre-decoupling server.py branches — see plans/c2cache-decoupling.md §5 for
the provenance of each method."""

import asyncio
import contextlib

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
            await self.array.afetch(window)  # this cache's lock held by open_view
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

        def peer_list():
            # Names relative to the requested path (the endpoint's contract,
            # matching the local branches), not full catalog keys.
            strip = prefix.strip("/")
            names = []
            for key in adapter.leaves(prefix):
                if key == strip:  # listing the dataset itself -> its name
                    names.append(key.rsplit("/", 1)[-1])
                else:
                    names.append(key[len(strip) + 1 :] if strip else key)
            return sorted(names)

        return await asyncio.to_thread(peer_list)

    async def rows(self, root):
        # was htmx_path_list's peer_rows() closure (server.py 1996-2006)
        adapter = self._adapter(root)

        def peer_rows():
            out = []
            for key in adapter.leaves():
                try:
                    size = adapter.leaf_size(key)
                except Exception:
                    size = 0
                out.append((key, size))
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
        try:
            # Everything that touches this cache's frame, under its own lock
            # (not the whole pool's): concurrent fetch/eviction of this same
            # cache corrupt reads. Fetches of *other* caches are unaffected.
            # Data is read out before ensure_budget (evict-after-read).
            async with peercache.cache_lock(cpath):
                proxy = await asyncio.to_thread(adapter.get, key)
                await proxy.afetch(real_slice)
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
                data = await asyncio.to_thread(lambda: cached[real_slice])
                # Check AFTER the read: a positive check now means every
                # touched chunk was present during the read.
                if not remote.slice_fully_cached(cached, real_slice):
                    raise providers.ProviderUnavailable(f"peer {root} is offline") from exc
        return data

    @contextlib.asynccontextmanager
    async def open_view(self, root, key):
        adapter = self._adapter(root)
        cpath = adapter.cache_path(key)
        async with peercache.cache_lock(cpath):  # released on EVERY exit path
            try:
                arr = await asyncio.to_thread(adapter.get, key)
            except remote.NotAFetchableDataset as exc:
                raise providers.ProviderBadRequest(str(exc)) from exc
            except httpx.HTTPStatusError as exc:
                raise providers.ProviderRelayedStatus(
                    exc.response.status_code, f"Peer error: HTTP {exc.response.status_code}."
                ) from exc
            except remote.OFFLINE_ERRORS as exc:
                adapter.registry.mark_offline(adapter.peer)
                raise providers.ProviderUnavailable(f"Peer {root} is offline.") from exc
            handle = _Handle(adapter, arr, root)
            yield handle
            # Clean exit only (an exception skips this, matching the old
            # BaseException path that released without touching):
            if handle.window is not None:
                await asyncio.to_thread(peercache.touch, arr, handle.window)
        await peercache.ensure_budget()  # AFTER lock release (it locks itself)
