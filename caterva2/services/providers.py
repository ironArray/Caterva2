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

    async def startup(self) -> None:  # noqa: B027 -- lifespan hook, optional to override
        pass

    async def shutdown(self) -> None:  # noqa: B027 -- lifespan hook, optional to override
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
            logger.info("provider %s active (roots: %s)", provider.name, provider.roots())
    return found
