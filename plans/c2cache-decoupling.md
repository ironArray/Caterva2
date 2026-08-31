# C2Cache decoupling — Phase 1: in-repo provider seam

**Packaging direction superseded (2026-08-31):** C2Cache remains in the
Caterva2 repository, wheel, version, and release lifecycle as the internal
`caterva2.c2cache` subpackage. It is registered directly as a built-in through
the generic provider seam (entry-point discovery remains for external
providers), but its factory is inert unless `[[server.peer]]` configuration
activates it. References below to a top-level `c2cache/` package or a future
separate C2Cache distribution are historical.

**Superseded (2026-07-08):** every reference below to the module-global
`peercache.io_lock` describes the pre-locking design. It has been replaced
by per-cache locks (`peercache.cache_lock(cpath)`); see
`plans/peercache-locking.md` for the current mechanism and rationale. This
document is otherwise left as-is as the historical record of the seam
refactor — except §8, which has been amended with the current Phase-2
sequencing (§8.1, 2026-07-08 review).

Status: ready to implement. This document is a self-contained handover: it contains every
decision, code sketch, line reference, and guardrail needed to execute the refactor without
re-deriving the design. Line numbers are against the `caterva3-remote-peer` branch as of
2026-07-06 (after `peers_panel.html` and the rebuilt static bundles were added) — **re-check
line numbers before editing; match on the quoted code, not the numbers.**

## 1. Context and goal

The `caterva3-remote-peer` branch implements the remote-peer-mounts MVP (design:
`plans/caterva3-remote-peer-mounts.md`) directly inside the Caterva2 server: server A "mounts"
server B's `@public` root as a virtual root `@<name>`, fetching chunks on demand through
`blosc2.Proxy`/`C2Array` into a quota-managed local cache.

Decision: **drop the Caterva3 fork; keep maintaining Caterva2.** The peer-mounting capability
will become a separate package, **C2Cache** — a plugin that turns any Caterva2 server into a
"super-server" that mounts remote Caterva2 servers with local chunk caching. Caterva2 core keeps
the `GET /api/peer` identity endpoint and `peer_id` generation so that **any vanilla Caterva2
server remains discoverable/mountable** by a C2Cache-enabled server (the mounted side needs
nothing installed).

Two-phase approach (agreed):

- **Phase 1 (this work)**: in-repo refactor. All peer code moves behind a clean
  provider/plugin seam into a top-level `c2cache/` package with zero ad-hoc coupling to
  `server.py`. Caterva2 gains a small, generic "root provider" extension mechanism.
- **Phase 2 (later, mechanical, NOT this work)**: `c2cache/` becomes a second PyPI package in
  the same repo (monorepo/uv-workspace). No code changes then — import name, entry-point group,
  and seam are final after Phase 1.

Why not split now: async `aget_chunk` (blosc2 upstream), auth/non-public roots, and dynamic
mounts are all planned churn that will exercise the seam. Let it prove itself in-repo first.

## 2. Current state map (what exists, where)

### Peer modules (self-contained, ~540 lines, will move verbatim where possible)

| File | Contents |
|---|---|
| `caterva2/services/peers.py` (179 l) | `Peer` dataclass (name, urlbase, cache_quota, peer_id, online, catalog, sizes); `PeerRegistry` (config `load`, threaded `handshake_all`/`_handshake` against B's `/api/peer` with self-mount guard + api_version check + dedupe, lazy `maybe_reprobe`, `get_known`, `mark_offline`, TTL'd `catalog`); module-global singleton `registry`; `API_VERSION = 1` |
| `caterva2/services/remote.py` (244 l) | `OFFLINE_ERRORS` tuple (httpx.TransportError, requests ConnectionError/Timeout, TimeoutError); `NotAFetchableDataset`; `client_for` (pooled `caterva2.Client` per urlbase); `RemoteSource(blosc2.C2Array)` with `aget_chunk` = `asyncio.to_thread` shim and per-leaf strategy (api/chunk for plain datasets, chunk-aligned api/fetch fallback for container members); `cache_path` (sha256-hashed — injection-safe); `open_cached_proxy` (sparse cache, mtime invalidation); `slice_fully_cached`; `RemotePeerAdapter` (leaves/size/leaf_size/is_group/close/get/get_cached_only/_info). Imports `split_container_path` from `caterva2.services.srv_utils` (line 21) |
| `caterva2/services/peercache.py` (117 l) | module globals `io_lock` (asyncio.Lock serializing ALL peer-cache IO), `pool_dir`, `budget`; `touch()` (atomic `.atime.npy` sidecar); `ensure_budget()` → `_evict_sync` (pool-wide chunk-granular LRU via UNINIT special chunks) |
| `caterva2/services/templates/peers_panel.html` | fragment looping over `peers` entries with keys `name`, `urlbase`, `online`, `cached`; `{% else %}` "No peers configured" |
| `caterva2/tests/test_peers.py` (179 l) | 9 end-to-end tests, two real server subprocesses on ports 8031/8032 (own harness, not the port-8000 pytest one); subprocesses run with `PYTHONPATH=os.getcwd()` and `cwd=statedir` |

### Coupling points in core

- `caterva2/services/server.py` (~3300 l): 11 branches, listed exhaustively in §5.
- `caterva2/services/settings.py`: lines 41-42 `peers = conf.get(".peer") or []`,
  `peer_cache_quota = parse_size(conf.get(".peer_cache_quota", "1G"))`; line 51 `peer_id = None`.
  Note line 30: `conf = utils.get_server_conf()` — the raw TOML is a module attribute, which is
  how the plugin will read its own config.
- `caterva2/services/templates/root_list.html`: lines 32-36 `peer_roots` loop; lines 55-66
  hardcoded Peers dropdown (`{% if peers_configured %}`).

### Existing plugin precedent (reuse the pattern, not the mechanism)

`caterva2/services/plugins/{image,tomography}` are *display* plugins: each module exposes
`name`, `contenttype`, `app` (a sub-`FastAPI`), `guess()`, `init()`. They are imported and
mounted in `main()` (`server.py:3286-3299`, `app.mount(f"/plugins/{name}", plugin.app)`), and
they chain templates to the server's via `jinja2.ChoiceLoader` (see
`plugins/tomography/__init__.py:19-25`). The dict `plugins = {}` at `server.py:3250` is that
registry. **The new concept must be named `providers` everywhere to avoid collision with this
existing `plugins` dict.** Entry-point discovery is the only genuinely new mechanism.

## 3. New core module: `caterva2/services/providers.py`

This is the seam. Small, dependency-light (stdlib + the two re-export imports). It is the ONLY
`caterva2.services` module `c2cache` may import (besides `settings`, passed into the factory).

Full sketch (implement exactly this shape; docstrings abbreviated here):

```python
"""Root providers: extension seam for packages that contribute virtual roots
(e.g. c2cache peer mounts). See plans/c2cache-decoupling.md."""

import abc
import importlib.metadata
import logging
from collections.abc import AsyncIterator
from typing import Any, Protocol

import fastapi

# Re-exports: the stable surface provider authors may use instead of reaching
# into srv_utils/settings internals.
from caterva2.services.settings import parse_size  # noqa: F401
from caterva2.services.srv_utils import (  # noqa: F401
    BLOSC2_CONTAINER_SUFFIXES,
    split_container_path,
)

logger = logging.getLogger("providers")

# Peer-mount wire-protocol version. Lives in CORE (not c2cache) because the
# B-side /api/peer endpoint must serve it from a vanilla install; c2cache
# imports it for the A-side handshake check. Bump on any breaking change.
PEER_API_VERSION = 1

ENTRY_POINT_GROUP = "caterva2.providers"


# --- error taxonomy -----------------------------------------------------
# Plain exceptions (NOT fastapi.HTTPException): API endpoints convert them to
# HTTPException; htmx endpoints render them via htmx_error(request, detail).


class ProviderError(Exception):
    status_code = 500

    def __init__(self, detail=""):
        super().__init__(detail)
        self.detail = detail


class ProviderBadRequest(ProviderError):
    status_code = 400  # e.g. not a fetchable dataset, stepped slices


class ProviderUnavailable(ProviderError):
    status_code = 503  # e.g. peer offline and no usable cache


class ProviderRelayedStatus(ProviderError):
    """The remote answered with an HTTP status: relay it verbatim."""

    def __init__(self, status_code, detail=""):
        super().__init__(detail)
        self.status_code = status_code


# --- view handle (htmx path-view seam) ----------------------------------


class ViewHandle(Protocol):
    array: Any  # blosc2 Proxy-like: .shape / .fields / __getitem__

    async def prefetch(self, window) -> None:
        """Fill the local cache for `window` (tuple of slices/ints) so
        subsequent sync __getitem__ reads are local. Raises ProviderError
        subclasses on failure. Also records the window for access-time
        accounting on clean context exit."""


# --- the provider interface ----------------------------------------------


class RootProvider(abc.ABC):
    """A source of virtual roots. ALL orchestration (locking, cache
    accounting, eviction, offline fallbacks, error mapping to
    ProviderError subclasses) happens INSIDE the provider; server.py only
    routes and converts ProviderError to HTTP/htmx responses."""

    name: str  # e.g. "c2cache"; router mounted at /provider/{name}
    router: fastapi.APIRouter | None = None

    # -- control plane (sync, non-blocking; may kick background probes) --
    @abc.abstractmethod
    def roots(self) -> list[str]:
        """Advertisable roots, e.g. ["@labb"]. Only routable ones."""

    @abc.abstractmethod
    def owns(self, root: str) -> bool:
        """True if `root` is served by this provider — including while it is
        transiently unreachable (data-plane calls then raise
        ProviderUnavailable or serve from cache), so a known root never
        404s just because the remote is down."""

    def widgets(self) -> list[dict]:
        """UI contributions for the sidebar: [{"label", "icon", "panel_url"}]."""
        return []

    async def startup(self) -> None:  # lifespan hook
        pass

    async def shutdown(self) -> None:  # lifespan hook
        pass

    # -- data plane (async; thread-shift blocking work internally) --------
    @abc.abstractmethod
    async def list(self, root: str, prefix: str) -> list[str]:
        """api/list contract: names relative to the requested path, sorted."""

    @abc.abstractmethod
    async def rows(self, root: str) -> list[tuple[str, int]]:
        """(key, size) pairs for the whole root (htmx path-list). Per-row
        size failures are swallowed to size 0 — never kill the listing."""

    @abc.abstractmethod
    async def info(self, root: str, key: str) -> dict:
        """api/info contract (the remote's JSON dict)."""

    @abc.abstractmethod
    async def fetch(self, root: str, key: str, slice_) -> Any:
        """Return an in-memory ndarray-like for `slice_` (None = whole),
        fully orchestrated (lock, prefetch, read, accounting, eviction,
        offline fallback)."""

    @abc.abstractmethod
    def open_view(self, root: str, key: str) -> AsyncIterator[ViewHandle]:
        """Async context manager yielding a ViewHandle whose .array can be
        sliced synchronously (after .prefetch) while the context is held.
        The context guarantees any internal lock is released on ALL exits."""


# --- registry + discovery ------------------------------------------------

active: list[RootProvider] = []


def provider_for(root: str) -> RootProvider | None:
    return next((p for p in active if p.owns(root)), None)


def discover(settings_module) -> list[RootProvider]:
    """Load entry points in group `caterva2.providers`. Each resolves to a
    factory f(settings_module) -> RootProvider | None (None = installed but
    not configured -> inert). Failures are logged and skipped: a broken
    provider must never prevent server boot."""
    found = []
    try:
        eps = importlib.metadata.entry_points(group=ENTRY_POINT_GROUP)
    except Exception:
        logger.exception("entry-point scan failed")
        return found
    for ep in eps:
        try:
            factory = ep.load()
            provider = factory(settings_module)
        except Exception:
            logger.exception("provider %s failed to load; skipping", ep.name)
            continue
        if provider is not None:
            found.append(provider)
            logger.info(
                "provider %s active (roots: %s)", provider.name, provider.roots()
            )
    return found
```

Also in this step: `peers.py`'s `API_VERSION` (line 17) is deleted and every use switches to
`providers.PEER_API_VERSION`; `server.py:353`'s `/api/peer` manifest reads
`providers.PEER_API_VERSION` too.

## 4. New top-level package: `c2cache/`

```
c2cache/
  __init__.py     # provider_factory(settings) -> C2CacheProvider | None
  provider.py     # C2CacheProvider(RootProvider): orchestration lifted from server.py
  peers.py        # git mv caterva2/services/peers.py
  remote.py       # git mv caterva2/services/remote.py
  peercache.py    # git mv caterva2/services/peercache.py  (UNCHANGED apart from module path)
  panel.py        # APIRouter serving GET /panel (the peers status fragment)
  templates/
    peers_panel.html   # git mv caterva2/services/templates/peers_panel.html
  main.py         # placeholder for the Phase-2 console script:
                  #   from caterva2.services.server import main; main()
```

Use `git mv` for the three modules and the template so history follows.

### 4.1 `c2cache/__init__.py`

```python
def provider_factory(settings):
    """Entry point `caterva2.providers`. Returns None when no [[server.peer]]
    is configured, so an installed-but-unconfigured c2cache is inert."""
    peer_confs = settings.conf.get(".peer") or []
    if not peer_confs:
        return None
    from c2cache.provider import C2CacheProvider

    return C2CacheProvider(settings, peer_confs)
```

(The import is deferred so a config-less server never imports blosc2-heavy modules for nothing.)

### 4.2 `c2cache/peers.py` changes (after `git mv`)

- Delete `API_VERSION = 1` (line 17); `_handshake`'s version check (line 103) imports
  `PEER_API_VERSION` from `caterva2.services.providers`.
- Delete the module-global `registry: PeerRegistry | None = None` singleton (last line). The
  registry becomes an attribute of `C2CacheProvider`.
- The local import `from caterva2.services.settings import parse_size` (line 62) becomes
  `from caterva2.services.providers import parse_size`.
- Everything else is untouched.

### 4.3 `c2cache/remote.py` changes (after `git mv`)

- Line 21 `from caterva2.services.srv_utils import split_container_path` becomes
  `from caterva2.services.providers import split_container_path`.
- Everything else is untouched (OFFLINE_ERRORS, NotAFetchableDataset, client_for, RemoteSource,
  cache_path, open_cached_proxy, slice_fully_cached, RemotePeerAdapter).

### 4.4 `c2cache/peercache.py`: UNCHANGED

Keep the module-global `io_lock`/`pool_dir`/`budget`. Rationale: there is one provider instance
per process and the lock's entire purpose is process-global serialization of sparse-frame IO
(concurrent handles or the evictor corrupt reads — see the comment block at lines 22-29). Do not
refactor this into instance state.

### 4.5 `c2cache/provider.py` — the centerpiece

Bodies are **lifted from server.py branches (code motion, not rewrite)**. Sketch:

```python
"""C2CacheProvider: Caterva2 root provider mounting remote peers' @public
roots with a local chunk cache. Orchestration moved verbatim from the
pre-decoupling server.py branches — see plans/c2cache-decoupling.md §5 for
the provenance of each method."""

import asyncio
import contextlib

import httpx

from caterva2.services import providers
from c2cache import peercache, peers, remote


class C2CacheProvider(providers.RootProvider):
    name = "c2cache"

    def __init__(self, settings, peer_confs):
        self.settings = settings
        self.peer_confs = peer_confs
        self.registry = None  # created in startup (needs settings.peer_id)
        from c2cache.panel import make_router  # late: avoids import cycle

        self.router = make_router(self)

    # -- lifespan ---------------------------------------------------------
    async def startup(self):
        # was server.py lifespan 248-255 (peer_id file creation STAYS in core)
        self.registry = peers.PeerRegistry(self.settings.peer_id)
        self.registry.load(self.peer_confs)
        await asyncio.to_thread(self.registry.handshake_all)
        peercache.pool_dir = self.settings.statedir / "peercache"
        peercache.pool_dir.mkdir(parents=True, exist_ok=True)
        peercache.budget = providers.parse_size(
            self.settings.conf.get(".peer_cache_quota", "1G")
        )

    # -- control plane ------------------------------------------------------
    def roots(self):
        # was get_roots 379-387 / htmx_root_list 1809-1813: advertise only
        # peers that completed a handshake (peer_id set); kick re-probes.
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
        return [
            {
                "label": "Peers",
                "icon": "fa-solid fa-server",
                "panel_url": f"provider/{self.name}/panel",
            }
        ]

    def _adapter(self, root):
        peer = self.registry.get_known(root)
        if peer is None:  # caller checked owns(); defensive
            raise providers.ProviderUnavailable(f"unknown root {root}")
        return remote.RemotePeerAdapter(peer, self.registry, peercache.pool_dir)

    # -- data plane ---------------------------------------------------------
    async def list(
        self, root, prefix
    ): ...  # body = server.py 413-427 (peer_list closure)
    async def rows(self, root): ...  # body = server.py 1996-2006 (peer_rows closure)
    async def info(self, root, key): ...  # body = server.py 477-483, mapped (see below)
    async def fetch(self, root, key, slice_): ...  # body = server.py 650-688, mapped

    open_view = ...  # body = server.py 2330-2346/2497-2501/2551-2563
```

`info` mapping (order of excepts is behavior — HTTPStatusError BEFORE OFFLINE_ERRORS):

```python
async def info(self, root, key):
    adapter = self._adapter(root)
    try:
        return await asyncio.to_thread(adapter._info, key)
    except httpx.HTTPStatusError as exc:
        # The peer answered (e.g. 404 for a bad path): relay its status.
        raise providers.ProviderRelayedStatus(exc.response.status_code) from exc
    except remote.OFFLINE_ERRORS as exc:
        raise providers.ProviderUnavailable(f"peer {root} is offline") from exc
```

(Note: `adapter._info` already calls `registry.mark_offline` internally on transport errors —
see `remote.py:214-219` — so `info()` does not mark offline again.)

`fetch` (verbatim body of server.py 650-688 with HTTPException→ProviderError substitutions;
returns the materialized `data` ndarray, the cframe/streaming stays in core):

```python
async def fetch(self, root, key, slice_):
    adapter = self._adapter(root)
    real_slice = () if slice_ is None else slice_
    parts = real_slice if isinstance(real_slice, tuple) else (real_slice,)
    if any(isinstance(p, slice) and p.step not in (None, 1) for p in parts):
        raise providers.ProviderBadRequest(
            "stepped slices are not supported on peer datasets"
        )
    try:
        # Everything that touches the cache frame under peercache.io_lock
        # (open, fetch, read): concurrent handles/eviction corrupt reads.
        # Data is read out before ensure_budget (evict-after-read).
        async with peercache.io_lock:
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
        async with peercache.io_lock:  # offline reads touch the frame too
            cached = await asyncio.to_thread(adapter.get_cached_only, key)
            if cached is None:
                raise providers.ProviderUnavailable(f"peer {root} is offline") from exc
            data = await asyncio.to_thread(lambda: cached[real_slice])
            # Check AFTER the read: a positive check now means every
            # touched chunk was present during the read.
            if not remote.slice_fully_cached(cached, real_slice):
                raise providers.ProviderUnavailable(f"peer {root} is offline") from exc
    return data
```

`open_view` — the io_lock-fidelity-critical piece. This asynccontextmanager reproduces, exactly,
the manual acquire/release choreography of htmx_path_view (2330-2346, 2497-2501, 2551-2563):

```python
class _Handle:
    def __init__(self, outer, adapter, array):
        self._outer, self._adapter = outer, adapter
        self.array = array
        self.window = None  # set by prefetch; touch on clean exit only

    async def prefetch(self, window):
        try:
            await self.array.afetch(window)  # io_lock held by open_view
        except remote.OFFLINE_ERRORS as exc:
            self._adapter.registry.mark_offline(self._adapter.peer)
            raise providers.ProviderUnavailable(
                f"Peer {self._outer._root} is offline."
            ) from exc
        self.window = window


@contextlib.asynccontextmanager
async def open_view(self, root, key):
    adapter = self._adapter(root)
    async with peercache.io_lock:  # released on EVERY exit path
        try:
            arr = await asyncio.to_thread(adapter.get, key)
        except remote.NotAFetchableDataset as exc:
            raise providers.ProviderBadRequest(str(exc)) from exc
        except httpx.HTTPStatusError as exc:
            raise providers.ProviderRelayedStatus(
                exc.response.status_code,
                f"Peer error: HTTP {exc.response.status_code}.",
            ) from exc
        except remote.OFFLINE_ERRORS as exc:
            adapter.registry.mark_offline(adapter.peer)
            raise providers.ProviderUnavailable(f"Peer {root} is offline.") from exc
        handle = self._Handle(self, adapter, arr)
        yield handle
        # Clean exit only (an exception skips this, matching the old
        # BaseException path that released without touching):
        if handle.window is not None:
            await asyncio.to_thread(peercache.touch, arr, handle.window)
    await peercache.ensure_budget()  # AFTER lock release (it locks itself)
```

(Implementation detail: `_Handle` may live at module level instead of as a nested class; keep
the touch-on-clean-exit and ensure_budget-after-release semantics exactly as above. Note
`open_view` needs `root` for the offline message — pass it into `_Handle` or store per-call,
do not store mutable per-request state on the provider instance.)

### 4.6 `c2cache/panel.py`

Moves `htmx_peers` (server.py 1825-1856) onto a provider-owned router, with templates chained to
the server's using the tomography pattern (`plugins/tomography/__init__.py:19-25`):

```python
import asyncio
import pathlib

import jinja2
from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

BASE_DIR = pathlib.Path(__file__).resolve().parent


def make_router(provider):
    from caterva2.services.server import custom_filesizeformat
    from caterva2.services.server import templates as srv_templates
    from c2cache import peercache

    templates = Jinja2Templates(directory=BASE_DIR / "templates")
    templates.env.loader = jinja2.ChoiceLoader(
        [templates.env.loader, srv_templates.env.loader]
    )
    router = APIRouter()

    @router.get("/panel", response_class=HTMLResponse)
    async def panel(request: Request):
        """Read-only peers status fragment. Re-probes on each render (no
        background timer, MVP scope); probes run in parallel threads."""

        def peer_entry(peer):
            provider.registry._handshake(peer)
            cachedir = peercache.pool_dir / peer.name if peercache.pool_dir else None
            cached_bytes = (
                sum(f.stat().st_size for f in cachedir.rglob("*") if f.is_file())
                if cachedir and cachedir.is_dir()
                else 0
            )
            return {
                "name": peer.name,
                "urlbase": peer.urlbase,
                "online": peer.online,
                "cached": custom_filesizeformat(cached_bytes),
            }

        entries = []
        if provider.registry is not None:
            entries = await asyncio.gather(
                *(
                    asyncio.to_thread(peer_entry, p)
                    for p in provider.registry.peers.values()
                )
            )
        return templates.TemplateResponse(
            request, "peers_panel.html", {"peers": entries}
        )

    return router
```

The context keys `name`/`urlbase`/`online`/`cached` are what the existing `peers_panel.html`
renders — do not rename them. The `from caterva2.services.server import ...` inside
`make_router` mirrors what tomography already does (plugins may import server; server must not
import providers' packages) and is late so module import stays cheap.

## 5. server.py rewiring — every branch, before → after

General substitutions used below:

- API endpoints: `except providers.ProviderError as exc: raise fastapi.HTTPException(status_code=exc.status_code, detail=exc.detail or None) from exc`
- htmx endpoints: `except providers.ProviderError as exc: return htmx_error(request, exc.detail or f"provider error {exc.status_code}")`
- Line 59 import becomes: `from caterva2.services import db, providers, schemas, settings, srv_utils, users` (drop `peercache`, `remote`; drop the `peers as peers_mod` import wherever it appears).

| # | Site (current lines) | Current behavior | After |
|---|---|---|---|
| 1 | lifespan 242-255 | peer_id file; `peers_mod.registry = PeerRegistry(...)`; `load(settings.peers)`; `to_thread(handshake_all)`; peercache pool_dir/budget | **Keep 242-246 (peer_id) in core.** Replace 248-255 with `for p in providers.active: await p.startup()`. After the `yield` add `for p in providers.active: await p.shutdown()` |
| 2 | `/api/peer` 347-357 | manifest, `api_version` from `peers_mod.API_VERSION` | stays in core; `"api_version": providers.PEER_API_VERSION` |
| 3 | get_roots 379-387 | loop `peers_mod.registry.peers`, maybe_reprobe, add roots with peer_id | `for p in providers.active:` `for name in p.roots(): roots[name] = models.Root(name=name)` |
| 4 | api/list 410-427 | `get_peer_adapter_or_none`; `peer_list()` closure (relativize + sort) in to_thread | `p = providers.provider_for(root)` → `if p: return await p.list(root, "/".join(path.parts[1:]))` wrapped in the API except. The closure body moves into `C2CacheProvider.list` |
| 5 | api/info 473-483 | adapter._info in to_thread; HTTPStatusError relay; OFFLINE→503 | `if p: try: return await p.info(root, rel) except ProviderError → HTTPException`. Error mapping moves into `C2CacheProvider.info` |
| 6 | api/fetch 640-691 | filter/field 400 guard (643-648); stepped-slice 400 (650-657); io_lock block (658-667); error taxonomy (668-688); cframe+stream (689-691) | Keep the **filter/field 400 guard in core** (generic policy: provider slices are raw; params never reach the provider). Then `data = await p.fetch(root, rel, slice_)` in the API except wrapper; keep 689-691 (cframe/StreamingResponse) in core operating on `data`. Stepped-slice guard moves INTO `fetch` |
| 7 | api/download 827-830 | 404 "peer roots are non-transitive" | generic: `if providers.provider_for(path.parts[0]) is not None: raise fastapi.HTTPException(status_code=404, detail="external roots are non-transitive")` |
| 8 | api/chunk 885-887 | same 404 guard | same generic guard as #7 |
| 9 | html_home 1750-1758 | anonymous-user root whitelist incl. peer roots | `provider_roots = {r for p in providers.active for r in p.roots()}`; keep the filter expression otherwise identical |
| 10 | htmx_root_list 1790-1822 | `peer_roots` list + `peers_configured` context | context: `"provider_roots": [r for p in providers.active for r in p.roots()]`, `"provider_widgets": [w for p in providers.active for w in p.widgets()]`; delete `peers_configured` |
| 11 | htmx_peers 1825-1856 | peers status panel | **delete the endpoint** — it moves to `c2cache/panel.py` (router mounted at `/provider/c2cache`, so the fragment URL becomes `provider/c2cache/panel`) |
| 12 | get_peer_adapter_or_none 1859-1869 | dispatcher | **delete** — replaced by `providers.provider_for` |
| 13 | htmx_path_list 1987-2018 | `peer_rows()` closure per root; build dataset dicts | `p = providers.provider_for(root); if p is None: continue;` `for key, size in await p.rows(root): ...` keep the dataset-dict construction (2007-2018) in core unchanged. Closure body moves into `C2CacheProvider.rows` |
| 14 | htmx_path_info 2112-2130 | adapter._info + model round-trip via `get_model_from_obj` | `if p: try: info = await p.info(root, rel) except ProviderError → htmx-appropriate HTTPException (this endpoint currently raises HTTPException, keep that); meta = _model_from_info(info)`. Add core helper `_model_from_info(info)` containing exactly the current 2123-2130 dispatch (`shape`→Metadata, `cparams`→SChunk, `nfiles`→Directory, else File) |
| 15 | htmx_path_view 2322-2346, 2354-2355, 2489-2501, 2551-2563, 2577 | manual io_lock acquire/release choreography | see §5.1 |
| 16 | main() ~3286-3299 | display-plugin registration | after it, add: `providers.active[:] = providers.discover(settings)`; `for p in providers.active: if p.router is not None: app.include_router(p.router, prefix=f"/provider/{p.name}")` |

### 5.1 htmx_path_view conversion (the one delicate edit)

Current choreography: acquire `peercache.io_lock` at 2334 before `adapter.get`; release on each
early-error return (2338/2341/2344); `await arr.afetch(window)` at 2497 (still under lock; on
OFFLINE release + htmx_error at 2498-2501); reads at 2504-2550 with a `BaseException` guard that
releases (2551-2556); on success touch + release + ensure_budget (2558-2563). Also: `filterable`
context at 2577 is `not hdf5_member and adapter is None`, and the peer branch rejects
filter/sortby up front (2326-2329).

After — using `contextlib.AsyncExitStack` so every path releases:

```python
provider = providers.provider_for(path.parts[0])
async with contextlib.AsyncExitStack() as stack:
    if provider is not None:
        hdf5_member = False
        idx = None
        if filter or sortby:
            return htmx_error(
                request, "Filtering/sorting is not supported on external roots yet."
            )
        try:
            handle = await stack.enter_async_context(
                provider.open_view(path.parts[0], "/".join(path.parts[1:]))
            )
        except providers.ProviderError as exc:
            return htmx_error(request, exc.detail or "provider error")
        arr = handle.array
    else:
        ...  # existing local branches 2347-2391 unchanged

    ...  # CTable branch + inputs/tags computation unchanged (2393-2487)

    if provider is not None:
        window = tuple(...)  # unchanged from 2492-2495
        try:
            await handle.prefetch(window)
        except providers.ProviderError as exc:
            return htmx_error(request, exc.detail or "provider error")

    ...  # the read body 2504-2550 unchanged, WITHOUT the
    # BaseException/release guard (the stack releases via open_view)
    # and WITHOUT the touch/release/ensure_budget block 2558-2563
    # (open_view does touch-on-clean-exit + ensure_budget)

    # context: filterable = not hdf5_member and provider is None   (2577)
    return templates.TemplateResponse(request, "info_view.html", context)
```

Two things to preserve carefully:

- The read body reassigns the local name `arr` (e.g. 2516 `arr = arr[...]`); the proxy needed
  for touch is `handle.array`, held by the handle — that is why touch lives in `open_view`, keyed
  on `handle.window`, not in the endpoint. (Today's code keeps `peer_proxy = arr` at 2502 for the
  same reason; that line disappears.)
- `ensure_budget` runs after the lock is released and, in the new structure, still before the
  template renders only on the clean-exit path — acceptable and equivalent: today it also only
  runs on the success path (2563).
- The `return` of the TemplateResponse happens **inside** the `async with stack` block; exiting
  the block on return triggers `open_view`'s clean-exit path (touch + release + ensure_budget)
  *before* the response object is sent. That matches today's ordering (touch/release/evict at
  2558-2563 happen before the render at 2565+). Note the CTable early-return at 2442 also exits
  the stack cleanly — today a peer-backed CTable can't reach there (peer leaves are arrays), and
  `handle.window` would be None anyway, so no touch: same behavior.

### 5.2 templates

`root_list.html`:

- Line 32: `{% for name in peer_roots %}` → `{% for name in provider_roots %}` (context key
  renamed in htmx_root_list).
- Lines 55-66: replace the hardcoded `{% if peers_configured %}` dropdown with a generic loop,
  preserving the exact htmx attributes:

```html
{% for w in provider_widgets %}
<div class="dropdown mt-3">
    <button class="btn btn-sm btn-outline-secondary" type="button" title="{{ w.label }}"
            data-bs-toggle="dropdown" aria-expanded="false"
            hx-get="{{ url(w.panel_url) }}" hx-target="#provider-dropdown-{{ loop.index }}"
            hx-trigger="click">
        <i class="{{ w.icon }}"></i> {{ w.label }}
    </button>
    <div class="dropdown-menu p-2" id="provider-dropdown-{{ loop.index }}">
        <span class="text-muted small">Loading…</span>
    </div>
</div>
{% endfor %}
```

`peers_panel.html`: `git mv` to `c2cache/templates/peers_panel.html`, content unchanged.

### 5.3 settings.py

Delete lines 40-42 (`peers`, `peer_cache_quota`) — the plugin factory reads
`settings.conf.get(".peer")` and `.peer_cache_quota` itself. **Keep** line 51 `peer_id = None`
(B-side identity, set in lifespan).

### 5.4 pyproject.toml

- Ensure the `c2cache` package ships in the wheel: with hatchling, add/extend the wheel target
  (e.g. `[tool.hatch.build.targets.wheel] packages = ["caterva2", "c2cache"]` — check the
  current build config first and match its style; `c2cache/templates/*.html` must be included
  as package data).
- Add the entry point (on the caterva2 dist for Phase 1; moves to the c2cache dist in Phase 2):

```toml
[project.entry-points."caterva2.providers"]
c2cache = "c2cache:provider_factory"
```

- `requests` is a real (currently transitive) dependency of `remote.py`'s OFFLINE_ERRORS: add it
  explicitly to the `server` extra.

## 6. Guardrails — do NOT change these behaviors

1. **io_lock discipline** (`c2cache/peercache.py` comment block, lines 22-29 pre-move):
   every open/fetch/read/eviction of a sparse cache frame happens under `peercache.io_lock`;
   `ensure_budget()` acquires it itself so it must be called only AFTER release. A leaked lock
   wedges all peer IO for the life of the process.
2. **touch-on-clean-exit only**: today's path-view releases the lock WITHOUT touch on the
   `BaseException` read path (2551-2556). `open_view` must reproduce this (exception skips the
   post-`yield` touch).
3. **Error-catch ordering**: `httpx.HTTPStatusError` (peer answered — relay status, peer stays
   online) must be caught BEFORE `remote.OFFLINE_ERRORS` (transport failure — mark offline).
   Covered end-to-end by `test_info_404_relayed_and_peer_stays_online`.
4. **Offline fallback check-after-read**: in `fetch`, `slice_fully_cached` runs AFTER the data
   read (comment at 685-687): a positive check then proves every touched chunk was present
   during the read. Do not "optimize" it to check-before-read.
5. **filter/field guard stays in core; stepped-slice guard moves into the provider** — the
   former is generic policy (params never reach providers), the latter is a
   `get_slice_nchunks` limitation of this provider.
6. **Non-transitivity 404s** on api/download + api/chunk stay in core.
7. **peers_panel.html context keys** are `name`, `urlbase`, `online`, `cached` — the committed
   template renders exactly these.
8. **`/api/peer` + peer_id creation stay in core** — a vanilla Caterva2 server must remain
   mountable with no c2cache installed.
9. **Module-global `io_lock`/`pool_dir`/`budget` in peercache.py stay module-global.**
10. **`remote.py`/`peers.py` logic is untouched** apart from the three import/constant edits in
    §4.2/§4.3.

## 7. Milestones and verification

Work on a branch off `caterva3-remote-peer`. Each milestone leaves the tree green.

**M1 — Seam module (no behavior change).**
Add `caterva2/services/providers.py` (§3). Switch `server.py:353` and `peers.py:103` to
`providers.PEER_API_VERSION`; delete `peers.py:17`.
Verify: `python -m pytest caterva2/tests/test_peers.py -x -q` (needs `CATERVA2_SECRET` unset —
the harness sets its own env; just run it) — all 9 tests green.

**M2 — Plugin package + API endpoints.**
`git mv` the three modules + template into `c2cache/` (§4), apply §4.2/§4.3 edits, write
`c2cache/__init__.py`, `provider.py` (list/rows/info/fetch/roots/owns/startup/widgets; open_view
can land in M3 but writing it now is fine), `panel.py`. Wire `server.py`: imports, lifespan
(#1), `/api/peer` (#2), get_roots (#3), api/list (#4), api/info (#5), api/fetch (#6), download
(#7), chunk (#8), html_home (#9), main() discovery (#16). Add the entry point + wheel config
(§5.4). Reinstall editable (`pip install -e .`) so the entry point registers.
Verify: `python -m pytest caterva2/tests/test_peers.py -x -q` — this suite covers exactly these
endpoints including the error taxonomy (404 relay, 503 offline fallback, 400 filter/steps,
non-transitivity guards). Note the test subprocesses run with `PYTHONPATH=os.getcwd()` from the
repo root, so top-level `c2cache/` is importable, but the **entry point** requires the editable
install — if peer roots don't appear, check `importlib.metadata.entry_points(group="caterva2.providers")`.

**M3 — UI endpoints.**
htmx_root_list (#10), delete htmx_peers (#11) + get_peer_adapter_or_none (#12), htmx_path_list
(#13), htmx_path_info (#14, incl. `_model_from_info` helper), htmx_path_view (#15/§5.1),
root_list.html (§5.2).
Verify: `python -m pytest caterva2/tests/ -x -q` (full suite), plus a manual two-server run:
start B (`cat2-server --statedir /tmp/peerB --listen localhost:8031` with some public data),
start A with `[[server.peer]] name="labb" urlbase="http://localhost:8031"` in its
`caterva2-server.toml`; in A's web UI check: `@labb` appears in the roots column, its datasets
list with sizes, clicking one shows metadata and the data view renders a window, the Peers
dropdown opens and shows labb online with a cache size, killing B and re-viewing a cached
window still works while an uncached dataset shows the offline error. (Docker tooling from
commit eab1d62 automates a similar setup.)

**M4 — Cleanup + decoupling tests.**
settings.py deletions (§5.3). New `caterva2/tests/test_providers.py`:
(a) boot a server with NO `[[server.peer]]` config (reuse the `_start` helper pattern from
test_peers.py): `/api/roots` contains only `@public` (anon), `/api/peer` still serves a manifest
with `peer_id` and `api_version == 1`;
(b) seam-cleanliness guard: read `caterva2/services/server.py` source and assert no matches for
`re.compile(r"\b(c2cache|peers_mod|peercache|remote\.)\b")` — a cheap regression tripwire;
(c) unit test for `open_view` lock release: enter the context with a monkeypatched adapter,
raise inside the `async with`, assert `peercache.io_lock.locked()` is False afterwards.
Run: `python -m pytest caterva2/tests/ -q` and `ruff check . && ruff format --check .`.

## 8. Phase 2 (later — recorded for completeness, do not do now)

- Move `c2cache/` + `caterva2/tests/test_peers.py` → `packages/c2cache/{c2cache/, tests/}` with
  its own `pyproject.toml`: `dependencies = ["caterva2[base-services]>=<first release with the
  seam>"]`, the `caterva2.providers` entry point, `[project.scripts] c2cache = "c2cache.main:main"`,
  templates as package data. Repo root becomes a uv workspace
  (`[tool.uv.workspace] members = [".", "packages/c2cache"]`).
- Remove `c2cache` from the caterva2 wheel includes and the entry point from caterva2's
  pyproject. No code changes.
- Trigger: the seam has survived the planned follow-ups (blosc2 async `aget_chunk` replacing the
  to_thread shim, auth/non-public roots, dynamic mounts) without needing new core hooks.
- The `providers.py` surface + `PEER_API_VERSION` then constitute a compatibility contract;
  document it in the caterva2 docs and version-gate c2cache releases on it.

### 8.1 Phase-2 sequencing amendments (2026-07-08 review)

Status check against the trigger above: the seam has survived the decoupling itself
(`e4d9ac2`) **and** the per-cache locking rework (`2f8eacb`) with zero new core hooks — good
evidence. But async `aget_chunk` adoption has not happened, auth/dynamic mounts haven't either,
and the branch (`c2cache-monorepo`) signals intent to split now. Amended plan: Phase 2 stays
valid and mechanical as written, with the following ordering and detail changes.

**Do BEFORE the split (in this order):**

1. **Adopt real async in c2cache.** Upstream python-blosc2 now ships `C2Array.aget_chunk` +
   gathered, bounded `Proxy.afetch` (python-blosc2 `35a162ab`), yet `c2cache/remote.py:70-73`
   still carries the serial `to_thread` shim whose own ponytail says it exists "until that
   lands upstream". Replace the shim with the upstream async path for plain-dataset leaves;
   the chunk-aligned `api/fetch` fallback for container members needs a small async variant of
   its own. Rationale for doing it pre-split: this is the last planned seam-exercising churn
   and the one most likely to touch the seam (`ViewHandle.prefetch`, the `to_thread`
   choreography in `fetch`/`open_view`) — do it while a seam change is a same-repo edit, not a
   cross-package version dance.
2. **Ride the release train.** c-blosc2 3.2.0 tag → python-blosc2 4.8.0 → bump caterva2's
   `blosc2>=` floor. The floor bump is **done (2026-07-09)**: `pyproject.toml` now reads
   `blosc2>=4.8.0`, ahead of the actual tags per explicit user instruction — the code already
   passes `locking=True`, which only exists in unreleased 4.8.0.dev, so a PyPI install of
   current HEAD (`pip install -e '.[tests,hdf5]'` in CI) will fail to resolve until c-blosc2
   3.2.0 / python-blosc2 4.8.0 are actually tagged and published. Phase 2 produces a *published*
   c2cache package whose dependency floors must name real releases; splitting first creates a
   package that cannot ship — so the split itself still waits on the actual tags, only the floor
   number is pre-bumped. The release-cutting decision itself is the maintainer's call; only the
   ordering is prescribed here. Tracking: python-blosc2 `todo/locking-mwmr.md` "Release
   coupling", caterva2 `plans/peercache-locking.md` "Changes" §5.

**Amendments to the §8 split itself:**

- The c2cache dist's dependencies must carry `blosc2>=4.8.0` (the planned release that ships
  `locking`) explicitly — not just the `caterva2[base-services]>=` floor.
- `caterva2/tests/test_providers.py` **stays** in the caterva2 package (it tests the seam, not
  the plugin); only `test_peers.py` moves to `packages/c2cache/tests/`.
- Housekeeping: a stale `packages/c2cache/tests/__pycache__/` from an earlier aborted start is
  lying around untracked; remove it when doing the real move.

**Do AFTER the split:**

- Auth/non-public roots and dynamic mounts, as originally listed (provider-internal; safe to do
  cross-package).
- **New roadmap item: multi-worker Caterva2 sharing one peercache pool.** Previously only an
  out-of-scope note in `plans/peercache-locking.md`; promoted to a deliberate item because it
  is where the blosc2 MWMR work converges with Caterva2 (multiple server processes fetching
  into the same cache frames *is* the multi-writer use case, and the frames are already safe
  for it via `locking=True`). What's missing is Caterva2-side: the fetch→read→touch critical
  section is an asyncio lock (process-local) and would need to become cross-process —
  blosc2's `holding_lock()` bracket is the natural primitive — plus shared atime/budget
  accounting. Prerequisites and the blosc2-side steps: python-blosc2
  `todo/locking-mwmr.md`. If super-server deployments are expected to run more than one
  worker, this outranks any remaining polish.

## 9. Risks

- **io_lock fidelity** (highest): entirely concentrated in `open_view` + the §5.1 endpoint
  conversion. Guarded by M4 test (c) and the manual offline test.
- **Error-order fidelity**: guardrail 3; covered by `test_info_404_relayed_and_peer_stays_online`.
- **Entry-point discovery in `main()`**: providers are invisible under a bare
  `uvicorn caterva2.services.server:app` (same pre-existing limitation as the display plugins).
  Add a note to the README/server docs.
- **Import cycle**: `c2cache/panel.py` imports from `caterva2.services.server` (templates,
  custom_filesizeformat) — this is safe only at call time (inside `make_router`, called from
  `provider.__init__` during `discover()` in `main()`, after server.py is fully imported). Do
  not move those imports to module top level.
- **Wheel packaging**: `c2cache/templates/*.html` must land in the wheel (verify with
  `python -m build && unzip -l dist/*.whl | grep peers_panel`).
