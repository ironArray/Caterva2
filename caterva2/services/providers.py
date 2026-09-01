"""Root providers: extension seam for components that contribute virtual roots
(e.g. the bundled C2Cache peer mounts). See plans/c2cache-decoupling.md."""

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
    BLOSC2_NATIVE_SUFFIXES,
    ctable_row_range,
    split_container_path,
)

logger = logging.getLogger("providers")

# Peer-mount wire-protocol version. Lives in the generic provider seam rather
# than the C2Cache implementation because the B-side /api/peer endpoint must
# serve it even when no peers are configured. Bump on any breaking change.
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

    async def startup(self) -> None:  # noqa: B027 -- lifespan hook, optional to override
        pass

    async def shutdown(self) -> None:  # noqa: B027 -- lifespan hook, optional to override
        pass

    # -- data plane (async; thread-shift blocking work internally) --------
    @abc.abstractmethod
    async def list(self, root: str, prefix: str) -> list[str]:
        """api/list contract: names relative to the requested path, sorted."""

    @abc.abstractmethod
    # Quoted, and it has to be: `list` here is the method above, not the
    # builtin -- these are named after the endpoints they serve -- so an
    # annotation evaluated where it stands subscripts a function and raises at
    # import, taking the whole server with it.  Python 3.14 defers annotations
    # (PEP 649) and never looks, which is why this ran anywhere at all
    async def rows(self, root: str, prefix: str = "") -> "list[tuple[str, int, str | None]]":
        """(key, size, kind) triples for the htmx path-list: the whole root
        when `prefix` is empty, else the members of the container at
        `prefix` (keys relative to it). kind is "container" (mountable),
        "ctable", "dataset", or None (unknown). Per-row size/kind failures
        are swallowed (size 0 / kind None) — never kill the listing."""

    @abc.abstractmethod
    async def info(self, root: str, key: str) -> dict:
        """api/info contract (the remote's JSON dict)."""

    @abc.abstractmethod
    async def fetch(self, root: str, key: str, slice_) -> Any:
        """Return an in-memory ndarray-like for `slice_` (None = whole), OR
        raw cframe `bytes` to be streamed as-is (e.g. a CTable row range);
        fully orchestrated (lock, prefetch, read, accounting, eviction,
        offline fallback)."""

    async def download(self, root: str, key: str, accept_encoding: str | None = None):
        """(async byte iterator, media_type, headers) streaming the whole
        file behind `key`, for api/download. Optional: the default refuses."""
        raise ProviderRelayedStatus(404, "download is not supported on this root")

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
    """Discover bundled and third-party root providers.

    C2Cache is part of Caterva2, so it is registered directly and works from a
    source checkout without relying on installed distribution metadata.  Truly
    external providers are loaded from the ``caterva2.providers`` entry-point
    group.  Every factory receives *settings_module* and returns either a
    provider or ``None`` when it is not configured.  A broken provider must
    never prevent server boot.
    """
    found = []

    # Built-ins are code, not packaging metadata.  Keeping this import inside
    # discovery preserves the cheap/inert import path when Caterva2 is used as
    # a client library, while making `PYTHONPATH=<checkout> cat2-server` behave
    # exactly like an installed wheel.
    try:
        from caterva2.c2cache import provider_factory as c2cache_factory
    except Exception:
        logger.exception("bundled provider c2cache failed to load; skipping")
    else:
        try:
            provider = c2cache_factory(settings_module)
        except Exception:
            logger.exception("bundled provider c2cache failed to initialize; skipping")
        else:
            if provider is not None:
                found.append(provider)
                logger.info("provider %s active (roots: %s)", provider.name, provider.roots())

    try:
        eps = importlib.metadata.entry_points(group=ENTRY_POINT_GROUP)
    except Exception:
        logger.exception("entry-point scan failed")
        eps = ()
    for ep in eps:
        # Ignore stale metadata left by an editable install from before
        # C2Cache became a bundled provider, and prevent double activation if
        # an already-built wheel with that old entry point remains installed.
        if ep.name == "c2cache":
            continue
        try:
            factory = ep.load()
            provider = factory(settings_module)
        except Exception:
            logger.exception("provider %s failed to load; skipping", ep.name)
            continue
        if provider is not None:
            found.append(provider)
            logger.info("provider %s active (roots: %s)", provider.name, provider.roots())
    return found
