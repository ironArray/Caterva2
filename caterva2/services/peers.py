"""Caterva3 peer registry: config, handshake, liveness.

A "peer" is another Caterva3/Caterva2 server whose @public root this server
mounts as a virtual root @<name>.  See plans/caterva3-remote-peer-mounts.md.
"""

import dataclasses
import logging
import re
import threading
import time

import httpx

logger = logging.getLogger("peers")

API_VERSION = 1  # must match server.API_VERSION on the remote side
HTTP_TIMEOUT = 5  # seconds, every peer request
CATALOG_TTL = 60  # seconds before a cached remote listing is stale
OFFLINE_RETRY = 15  # seconds before re-probing an offline peer
MAX_CATALOG = 10_000  # hard cap on ingested remote catalog entries

_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]*$")
_RESERVED = {"personal", "shared", "public"}


@dataclasses.dataclass
class Peer:
    name: str  # local alias; root is "@" + name
    urlbase: str  # e.g. http://serverB:8000
    # ponytail: parsed but not enforced; peercache only knows one global
    # budget (settings.peer_cache_quota). Upgrade: give peercache a
    # per-pool_dir/<peer.name> budget instead of one pool-wide number.
    cache_quota: int | None = None
    # filled by handshake:
    peer_id: str | None = None
    api_version: int | None = None
    capabilities: dict = dataclasses.field(default_factory=dict)
    online: bool = False
    last_probe: float = 0.0
    # catalog cache: list of dataset paths relative to B's @public
    catalog: list[str] | None = None
    catalog_ts: float = 0.0
    # per-leaf sizes (api/info cbytes), memoized until the catalog refreshes
    sizes: dict = dataclasses.field(default_factory=dict)

    @property
    def root(self):
        return "@" + self.name


class PeerRegistry:
    def __init__(self, own_peer_id):
        self.own_peer_id = own_peer_id
        self.peers: dict[str, Peer] = {}  # root name ("@lab-b") -> Peer

    # -- setup ------------------------------------------------------------

    def load(self, peer_confs):
        """Ingest [[server.peer]] config entries. Invalid ones are logged
        and skipped — never raise (startup must be tolerant)."""
        from caterva2.services.settings import parse_size  # avoid cycle

        for conf in peer_confs:
            name = conf.get("name")
            urlbase = (conf.get("urlbase") or "").rstrip("/")
            if not name or not urlbase or not _NAME_RE.match(name) or name in _RESERVED:
                logger.warning("skipping invalid [[server.peer]] entry: %r", conf)
                continue
            peer = Peer(
                name=name,
                urlbase=urlbase,
                cache_quota=parse_size(conf.get("cache_quota")),
            )
            if peer.root in self.peers:
                logger.warning("duplicate peer name %s, skipping", name)
                continue
            self.peers[peer.root] = peer

    def handshake_all(self):
        # Probe in parallel: N dead peers cost one HTTP_TIMEOUT, not N of them.
        threads = [threading.Thread(target=self._handshake, args=(peer,)) for peer in self.peers.values()]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

    def _handshake(self, peer):
        """Probe B/api/peer. Sets online/offline; never raises."""
        peer.last_probe = time.monotonic()
        try:
            r = httpx.get(peer.urlbase + "/api/peer", timeout=HTTP_TIMEOUT)
            r.raise_for_status()
            m = r.json()
        except Exception as exc:
            logger.warning("peer %s offline: %s", peer.name, exc)
            peer.online = False
            return
        if m.get("peer_id") == self.own_peer_id:
            logger.warning("peer %s is myself; disabling (self-mount guard)", peer.name)
            peer.online = False
            return
        if m.get("api_version") != API_VERSION:
            logger.warning(
                "peer %s api_version %s != %s; disabling",
                peer.name,
                m.get("api_version"),
                API_VERSION,
            )
            peer.online = False
            return
        # dedupe: same peer_id reached through two config entries
        for other in self.peers.values():
            if other is not peer and other.peer_id == m["peer_id"]:
                logger.warning(
                    "peer %s duplicates %s (same peer_id); disabling",
                    peer.name,
                    other.name,
                )
                peer.online = False
                return
        peer.peer_id = m["peer_id"]
        peer.api_version = m["api_version"]
        peer.capabilities = m.get("capabilities") or {}
        peer.online = True
        logger.info("peer %s online (%s)", peer.name, peer.peer_id)

    # -- runtime ----------------------------------------------------------

    def maybe_reprobe(self, peer):
        """Lazy liveness: if `peer` is offline and the retry window elapsed,
        re-handshake in a background thread. Never blocks the caller (this
        runs on the event loop via get_known/get_roots); bumping last_probe
        up front keeps concurrent callers from stampeding probes."""
        if not peer.online and time.monotonic() - peer.last_probe > OFFLINE_RETRY:
            peer.last_probe = time.monotonic()
            threading.Thread(target=self._handshake, args=(peer,), daemon=True).start()

    def get_known(self, root):
        """Return the Peer for @root if it is a legitimately mounted peer —
        one that has completed at least one real handshake — even while
        currently (transiently) offline, so callers can fall back to cached
        data instead of treating @root as an unknown root (404). Kicks a
        non-blocking re-probe for offline peers. Returns None for unknown
        roots and for peers permanently rejected by the self-mount guard,
        version mismatch, or dedupe check (those never get a peer_id)."""
        peer = self.peers.get(root)
        if peer is None:
            return None
        self.maybe_reprobe(peer)
        return peer if peer.peer_id is not None else None

    def mark_offline(self, peer):
        """Called by the adapter when a request to the peer fails."""
        peer.online = False
        peer.last_probe = time.monotonic()

    def catalog(self, peer):
        """Cached listing of B's @public (list of relative paths)."""
        now = time.monotonic()
        if peer.catalog is None or now - peer.catalog_ts > CATALOG_TTL:
            try:
                r = httpx.get(peer.urlbase + "/api/list/@public", timeout=HTTP_TIMEOUT)
                r.raise_for_status()
                listing = r.json()
            except Exception as exc:
                logger.warning("peer %s listing failed: %s", peer.name, exc)
                self.mark_offline(peer)
                return peer.catalog or []  # serve stale if we have it
            if len(listing) > MAX_CATALOG:  # untrusted input: cap it
                logger.warning("peer %s catalog truncated (%d entries)", peer.name, len(listing))
                listing = listing[:MAX_CATALOG]
            peer.catalog = [str(p) for p in listing]
            peer.catalog_ts = now
            peer.sizes.clear()  # sizes may be stale along with the listing
        return peer.catalog


registry: PeerRegistry | None = None  # singleton, created in server lifespan
