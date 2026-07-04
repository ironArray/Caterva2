# Caterva3 remote peer mounts — implementation plan (MVP)

Companion to `plans/caterva3-remote-peer-mounts.md` (the design doc — read its
"Verified by experiment" section first; every mechanism below was tested there).
This document is the step-by-step build plan. Follow the phases in order; each
phase ends with an acceptance check that must pass before moving on.

## Ground rules for the implementing agent

- **Base branch**: `new-table`. All file references below are against it.
  Line numbers are hints only — **always locate code by the grep pattern
  given**, never by line number alone.
- **Python env**: use the `blosc2` conda env
  (`/Users/faltet/miniforge3/envs/blosc2/bin/python`). It has blosc2
  4.7.1.dev0 and the `[server,hdf5]` deps. Do NOT use the `cat2` env.
- **Running a dev server**: the env's `cat2-server` entry point is stale; run
  the module instead, from the repo root:

  ```sh
  CATERVA2_SECRET=c3test PYTHONPATH=. \
    /Users/faltet/miniforge3/envs/blosc2/bin/python -m caterva2.services.server \
    --statedir=/tmp/c3A --listen localhost:8020
  ```

  `--listen` requires `host:port` (a bare number is misread as a unix socket).
  Avoid port 8000 (often occupied by the user's own server; the pytest harness
  also assumes it).
- **Tests**: run `pytest caterva2/tests/ -x -q` in the env after every phase.
  Do not break existing tests. Only run the HTTP test suite when port 8000 is
  free — never kill an existing server on 8000.
- Reference experiments live in the repo root (untracked):
  `e2e_peer_test.py` (live HTTP proxy fill + eviction + LazyExpr),
  `evict_sparse_test.py` (sparse cache eviction). Read them; Phase 3 code is
  essentially a productionized version of what they do.
- Mark deliberate MVP shortcuts with a `# ponytail:` comment naming the
  ceiling and the upgrade path (repo convention).
- Do NOT touch: the upload quota logic (`settings.quota`, grep
  `if settings.quota`), the localStorage container-mount UX, user auth.

## Architecture in one paragraph

Server A lists peers from its TOML config. For each peer B it handshakes
(`GET B/api/peer`), then exposes a root `@<peername>` backed by a
`RemotePeerAdapter`. Listing/info go through B's REST API (sync
`caterva2.Client`, run in threads). Data reads open a `blosc2.Proxy` whose
source is a `C2Array` subclass with an async `aget_chunk` (thread shim) and
whose cache is a **sparse** blosc2 array on A's disk; `await proxy.afetch()`
pulls only the chunks a request touches. A per-chunk-LRU evictor keeps the
cache pool under a byte budget by rewriting cold chunks as UNINIT special
chunks (which the Proxy then treats as "not fetched" and re-downloads on
demand).

---

## Phase 1 — peer identity endpoint (`/api/peer`)

**Goal**: every server can identify itself; needed for the handshake,
self-mount guard, and dedupe.

### 1.1 Persist a `peer_id`

File: `caterva2/services/server.py`. Find the lifespan function
(grep `async def lifespan`). Add, before `yield`:

```python
# Peer identity (Caterva3): a stable UUID for this server instance.
idfile = settings.statedir / "peer_id"
if not idfile.exists():
    idfile.write_text(uuid.uuid4().hex)
settings.peer_id = idfile.read_text().strip()
```

Add `import uuid` at the top if missing. In
`caterva2/services/settings.py`, add module-level defaults near the existing
`statedir = None` block:

```python
peer_id = None  # set at startup from <statedir>/peer_id
peers = []  # list of peer config dicts, see Phase 2
```

### 1.2 The endpoint

In `server.py`, next to `get_roots` (grep `async def get_roots`), add:

```python
API_VERSION = 1  # bump on any breaking change to the peer-facing API


@app.get("/api/peer")
async def get_peer_manifest() -> dict:
    """Identity manifest used by the Caterva3 peer handshake."""
    return {
        "peer_id": settings.peer_id,
        "name": settings.urlbase,
        "api_version": API_VERSION,
        "roots": ["@public"],  # only locally-owned public data: never mounts
        "capabilities": {"chunk_api": "plain"},  # api/chunk works for plain
        # datasets only (see design doc)
    }
```

### 1.3 Acceptance

Start a dev server (see ground rules) and:

```sh
curl -s http://localhost:8020/api/peer
# -> JSON with a 32-hex peer_id, api_version 1, roots ["@public"]
# restart the server; peer_id must be THE SAME (persisted)
```

---

## Phase 2 — peer config, registry, startup handshake

**Goal**: parse `[[server.peer]]` from the TOML, hold an in-memory registry,
handshake at startup, lazy liveness.

### 2.1 Config parsing

`caterva2/services/settings.py` already loads the TOML via
`conf = utils.get_server_conf()` and reads keys as `conf.get(".key")` (the
leading dot prepends the `server` prefix — see `class Conf` in
`caterva2/utils.py`). Add:

```python
# [[server.peer]] array of tables:
#   [[server.peer]]
#   name = "lab-b"                  # root will be "@lab-b"
#   urlbase = "http://serverB:8000"
#   cache_quota = "2G"              # optional per-peer override
peers = conf.get(".peer") or []
# one global remote-cache budget by default
peer_cache_quota = parse_size(conf.get(".peer_cache_quota", "1G"))
```

### 2.2 The registry

New file: `caterva2/services/peers.py`. Complete content:

```python
"""Caterva3 peer registry: config, handshake, liveness.

A "peer" is another Caterva3/Caterva2 server whose @public root this server
mounts as a virtual root @<name>.  See plans/caterva3-remote-peer-mounts.md.
"""

import dataclasses
import logging
import re
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
        for peer in self.peers.values():
            self._handshake(peer)

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

    def get_online(self, root):
        """Return the Peer for @root if online, retrying offline peers
        lazily. Return None for unknown/offline peers."""
        peer = self.peers.get(root)
        if peer is None:
            return None
        if not peer.online and time.monotonic() - peer.last_probe > OFFLINE_RETRY:
            self._handshake(peer)  # lazy liveness: re-probe on access
        return peer if peer.online else None

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
                logger.warning(
                    "peer %s catalog truncated (%d entries)", peer.name, len(listing)
                )
                listing = listing[:MAX_CATALOG]
            peer.catalog = [str(p) for p in listing]
            peer.catalog_ts = now
        return peer.catalog


registry: PeerRegistry | None = None  # singleton, created in server lifespan
```

Note: `registry.catalog()` and `_handshake()` do **blocking** HTTP. Callers in
async endpoints must wrap them: `await asyncio.to_thread(...)`. This is stated
again at each call site below.

### 2.3 Wire into startup

In `server.py` lifespan, after the peer_id block from Phase 1:

```python
# Peer registry (Caterva3)
from caterva2.services import peers as peers_mod

peers_mod.registry = peers_mod.PeerRegistry(settings.peer_id)
peers_mod.registry.load(settings.peers)
await asyncio.to_thread(peers_mod.registry.handshake_all)
```

A peer that is down or version-mismatched must only log a warning — verify the
server still boots with a bogus peer configured.

### 2.4 Acceptance

1. `pytest caterva2/tests -x -q` still green (no peers configured → no-op).
2. Start server B on :8021 (own statedir). Write for A a
   `caterva2-server.toml` containing:

   ```toml
   [server]
   ...existing keys...
   [[server.peer]]
   name = "labb"
   urlbase = "http://localhost:8021"
   ```

   Start A on :8020 → log shows `peer labb online (...)`.
3. Point a third entry at A's own urlbase → log shows the self-mount guard
   firing, server boots anyway.
4. Stop B, restart A → `peer labb offline`, server boots anyway.

---

## Phase 3 — remote source + sparse cache (`RemoteDataset`)

**Goal**: the data-path building block, a productionized `e2e_peer_test.py`.

New file: `caterva2/services/remote.py`. Complete content:

```python
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

import blosc2
import numpy as np

import caterva2


class RemoteSource(blosc2.C2Array):
    """C2Array with an async chunk getter and a per-leaf fetch strategy.

    use_chunk_api=True  -> per-chunk GET api/chunk (plain .b2nd datasets).
    use_chunk_api=False -> chunk-aligned GET api/fetch fallback (container
                           members: api/chunk 404s on those, verified).
    """

    def __init__(self, path, urlbase, use_chunk_api=True):
        super().__init__(path, urlbase=urlbase)
        self.use_chunk_api = use_chunk_api

    async def aget_chunk(self, nchunk):
        # ponytail: serial to_thread shim; replaced by real async aget_chunk
        # + gathered afetch when that lands upstream in blosc2.
        return await asyncio.to_thread(self._get_chunk_sync, nchunk)

    def _get_chunk_sync(self, nchunk):
        if self.use_chunk_api:
            return self.get_chunk(nchunk)
        return self._chunk_via_fetch(nchunk)

    def _chunk_slice(self, nchunk):
        """C-order chunk grid coordinates -> tuple of slices for `nchunk`."""
        grid = [math.ceil(s / c) for s, c in zip(self.shape, self.chunks, strict=True)]
        coords = np.unravel_index(nchunk, grid)
        return tuple(
            slice(int(i) * c, min((int(i) + 1) * c, s))
            for i, c, s in zip(coords, self.chunks, self.shape, strict=True)
        )

    def _chunk_via_fetch(self, nchunk):
        """Fetch this chunk's exact slice via api/fetch and recompress it
        into a cache-shaped chunk (padded to full chunkshape)."""
        slice_ = self._chunk_slice(nchunk)
        # caterva2.Client.get_slice hits api/fetch and returns the slice
        # (sync httpx; we are already inside to_thread here).
        client = caterva2.Client(self.urlbase, timeout=5)
        try:
            data = client.get_slice(self.path, slice_, as_blosc2=True)
        finally:
            client.close()
        full = np.zeros(self.chunks, dtype=self.dtype)
        region = tuple(slice(0, s.stop - s.start) for s in slice_)
        full[region] = data[...]
        packed = blosc2.asarray(
            full, chunks=self.chunks, blocks=self.blocks, cparams=self.cparams
        )
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
        cache = blosc2.open(str(cpath), mode="a")
        meta = json.loads(cache.schunk.vlmeta.get("_peer_src", "{}"))
        if meta.get("mtime") != remote_mtime:
            del cache  # invalidation on remote change
            shutil.rmtree(cpath)
            cache = None
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
        )
        cache.schunk.vlmeta["_peer_src"] = json.dumps(
            {"path": source.path, "mtime": remote_mtime}
        )
    return blosc2.Proxy(source, _cache=cache)
```

Notes for the implementer:

- Because we create the cache ourselves with `blosc2.empty`, it carries **no
  `proxy-source` metalayer** (only `Proxy.__init__`-created caches do), so
  `blosc2.open(cpath)` returns it as a plain array — exactly what
  `open_cached_proxy` wants for re-wrapping with a fresh `RemoteSource`. Do
  not "fix" this by adding proxy metadata. (Verified: this is how the
  repo-root `evict_sparse_test.py` reopen works.)
- `caterva2.Client` is a synchronous `httpx.Client` wrapper
  (`caterva2/client.py`, grep `class Client`); its useful methods here are
  `get_list(path)`, `get_info(path)`, `get_slice(path, key, as_blosc2=True)`.
  Every call to it from server code must run inside `asyncio.to_thread`.

### Acceptance (Phase 3)

Adapt repo-root `e2e_peer_test.py` into
`caterva2/tests/test_remote_source.py` guarded by a running-server fixture
(see Phase 7 for the fixture; until then, run manually):

1. Against a live server with a multi-chunk `@public/mc.b2nd`:
   `RemoteSource(..., use_chunk_api=True)` + `open_cached_proxy` +
   `asyncio.run(proxy.afetch((slice(2,4), ...)))` fetches exactly chunks 2,3
   and data equals the origin.
2. Against `@public/test.h5/dset` (container member):
   `use_chunk_api=False` path returns bit-exact data too.
3. Touch/replace the origin dataset, reopen via `open_cached_proxy` with the
   new mtime → cache directory is recreated (old chunks gone).

---

## Phase 4 — `RemotePeerAdapter` + server routing

**Goal**: `@<peer>` roots resolve, list, info, and fetch through the API.

### 4.1 The adapter

Append to `caterva2/services/remote.py`. It mirrors the protocol of
`_TreeStoreAdapter` / `_HDF5Adapter` in `caterva2/services/srv_utils.py`
(grep `class _TreeStoreAdapter`) — same method names so downstream code that
already speaks "adapter" keeps working:

```python
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
        info = self._info(key)
        return info.get("schunk", {}).get("cbytes")

    def is_group(self, node):
        return False  # remote catalog entries are always leaves

    def close(self):
        pass

    # -- data -------------------------------------------------------------

    def _remote_path(self, key):
        return "@public/" + key.strip("/")

    def _info(self, key):
        try:
            client = caterva2.Client(self.peer.urlbase, timeout=5)
            try:
                return client.get_info(self._remote_path(key))
            finally:
                client.close()
        except Exception:
            self.registry.mark_offline(self.peer)
            raise

    def get(self, key):
        """Return a blosc2.Proxy for the remote dataset behind `key`."""
        info = self._info(key)
        mtime = info.get("schunk", {}).get("mtime")
        # container members can't use api/chunk (verified: 404) -> fallback
        plain = "/" not in key or not any(
            part.endswith((".b2z", ".h5", ".hdf5")) for part in key.split("/")[:-1]
        )
        src = RemoteSource(
            self._remote_path(key), self.peer.urlbase, use_chunk_api=plain
        )
        cpath = cache_path(self.pool_dir / self.peer.name, self.peer.peer_id, key)
        return open_cached_proxy(src, cpath, mtime)
```

(`Client.get_info` may return a model object rather than a plain dict — grep
`def get_info` in `caterva2/client.py` and check; if so, convert with its
model `.model_dump()` or read attributes accordingly. If `Client` proves
awkward here, plain `httpx.get(f"{urlbase}/api/info/{path}", timeout=5)` +
`raise_for_status()` + `.json()` is an acceptable substitute.)

The container-member detection duplicates
`srv_utils.BLOSC2_CONTAINER_SUFFIXES` — import and use that set instead of
hardcoding extensions.

### 4.2 Root advertisement

In `server.py` `get_roots` (grep `async def get_roots`), after the `@public`
entry is added:

```python
from caterva2.services import peers as peers_mod

if peers_mod.registry is not None:
    for peer in peers_mod.registry.peers.values():
        roots[peer.root] = models.Root(name=peer.root)
```

Peer roots are public-only, so they are added for anonymous users too
(mirroring `@public`). Do NOT condition on `user`.

### 4.3 Endpoint routing

Add one helper near `get_rootdir_or_error` (grep `def get_rootdir_or_error`):

```python
def get_peer_adapter_or_none(root):
    """Return a RemotePeerAdapter if `root` names an online peer, else None."""
    from caterva2.services import peers as peers_mod
    from caterva2.services import remote

    if peers_mod.registry is None:
        return None
    peer = peers_mod.registry.get_online(root)
    if peer is None:
        return None
    return remote.RemotePeerAdapter(
        peer, peers_mod.registry, settings.statedir / "peercache"
    )
```

Then, in each of the following endpoints, add an early branch **before** the
existing `get_abspath`/`get_rootdir_or_error` call. Pattern (shown for
`get_list`; grep `@app.get("/api/list/`):

```python
root = path.parts[0]
adapter = get_peer_adapter_or_none(root)
if adapter is not None:
    rel = "/".join(path.parts[1:])
    return await asyncio.to_thread(lambda: sorted(adapter.leaves(rel)))
```

Endpoints to touch, and what the peer branch returns:

| endpoint (grep)                | peer-root behavior                            |
|--------------------------------|-----------------------------------------------|
| `/api/list/{path:path}`        | `adapter.leaves(rel)` as a list (to_thread)    |
| `/api/info/{path:path}`        | `adapter._info(rel)` passthrough (to_thread)   |
| `/api/fetch/{path:path}`       | see 4.4                                        |
| `/api/chunk/{path:path}`       | `raise HTTPException(404, "peer roots are non-transitive")` |
| `/api/download/{path:path}`    | same 404 for MVP (`# ponytail:` note)          |

Anything not listed (upload, move, lazyexpr-create, etc.) must fail on peer
roots naturally because `get_rootdir_or_error` raises 404 for unknown roots —
verify one of them (e.g. upload to `@labb/x`) actually 404s and leave them be.

If an adapter call raises (peer went down mid-request), return
`HTTPException(503, f"peer {root} is offline")`; the registry has already
marked it offline via `mark_offline`.

### 4.4 The fetch path

Locate the `api/fetch` endpoint (grep `"/api/fetch/`) and read it to the end:
it resolves the path, ensures data is local, opens the container, slices, and
builds a streaming response. Refactor the tail (from "container is open and
local" to the response) into a helper `slice_to_response(container, slice_,
...)` if it is not already one, then implement the peer branch as:

```python
adapter = get_peer_adapter_or_none(root)
if adapter is not None:
    rel = "/".join(path.parts[1:])
    proxy = await asyncio.to_thread(adapter.get, rel)
    lock = locks.setdefault(str(path), asyncio.Lock())
    async with lock:  # same discipline as partial_download
        await proxy.afetch(slice_)
    peercache.touch(proxy, slice_)  # Phase 5 (atime stamping)
    await peercache.ensure_budget()  # Phase 5 (eviction)
    return slice_to_response(proxy, slice_, ...)
```

Until Phase 5 exists, leave the two `peercache.` lines as TODO comments so
this phase stays runnable.

`proxy[...]` (sync `__getitem__`) blocks on HTTP for unfetched chunks —
**always `await proxy.afetch(slice_)` before slicing** in any server code
that touches a peer-backed container (design doc, open item 4).

### 4.5 Acceptance

Two dev servers: B on :8021 with a multi-chunk `@public/mc.b2nd` (create it
with the snippet from `e2e_peer_test.py`), A on :8020 with B configured as
peer `labb`. Then:

```sh
curl -s http://localhost:8020/api/roots            # contains "@labb"
curl -s http://localhost:8020/api/list/@labb       # contains "mc.b2nd"
curl -s http://localhost:8020/api/info/@labb/mc.b2nd   # B's info JSON
curl -s "http://localhost:8020/api/fetch/@labb/mc.b2nd?slice_=2:4" -o /tmp/s.b2
python -c "import blosc2; print(blosc2.ndarray_from_cframe(open('/tmp/s.b2','rb').read()).shape)"
# -> (2, 1000000); compare values against B's data
```

Also verify: only chunk files for the touched chunks exist under
`<statedirA>/peercache/labb/<hash>.b2nd/` (chunk-granular transfer), a second
identical fetch does not re-download (check B's access log / add a counter),
and stopping B then re-fetching the same slice still succeeds from cache.

---

## Phase 5 — cache manager (chunk-granular LRU)

**Goal**: keep `<statedir>/peercache` under `settings.peer_cache_quota`.

New file: `caterva2/services/peercache.py`. Complete content:

```python
"""Remote-cache pool manager: per-chunk LRU under a byte budget.

Mechanism (verified, see design doc): evicting a chunk = replacing it with an
UNINIT special chunk via update_chunk; the sparse frame reclaims the chunk
file space immediately and the Proxy refetches on next access.
"""

import asyncio
import logging
import pathlib
import time

import blosc2
import numpy as np

logger = logging.getLogger("peercache")

HIGH = 1.0  # start evicting above budget
LOW = 0.8  # evict down to this fraction of budget

_lock = asyncio.Lock()  # ponytail: one global evictor lock; fine for MVP
pool_dir: pathlib.Path | None = None  # set at startup
budget: int | None = None  # bytes, set at startup


def _atime_file(cache_urlpath):
    return pathlib.Path(str(cache_urlpath) + ".atime.npy")


def touch(proxy, slice_):
    """Stamp access times for the chunks a slice touches."""
    cache = proxy._cache
    nchunks = cache.schunk.nchunks
    af = _atime_file(cache.schunk.urlpath)
    atimes = np.load(af) if af.exists() else np.zeros(nchunks)
    if len(atimes) != nchunks:
        atimes = np.zeros(nchunks)
    touched = (
        range(nchunks)
        if slice_ in (None, ())
        else blosc2.get_slice_nchunks(cache, slice_)
    )
    atimes[list(touched)] = time.time()
    np.save(af, atimes)


def _usage():
    return sum(f.stat().st_size for f in pool_dir.rglob("*") if f.is_file())


def _uninit_chunk(schunk, nchunk):
    """A compressed UNINIT special chunk sized for `nchunk` (handles the
    trailing partial chunk)."""
    # ponytail: full-chunksize special works for NDArray caches because all
    # NDArray chunks are padded; revisit if SChunk (1D, unpadded) caches appear.
    tmp = blosc2.SChunk(
        chunksize=schunk.chunksize, cparams=blosc2.CParams(typesize=schunk.typesize)
    )
    tmp.fill_special(schunk.chunksize // schunk.typesize, blosc2.SpecialValue.UNINIT)
    return tmp.get_chunk(0)


async def ensure_budget():
    """Evict least-recently-used chunks across the whole pool until usage is
    below LOW * budget. Called after each remote fetch."""
    if pool_dir is None or budget is None:
        return
    async with _lock:
        await asyncio.to_thread(_evict_sync)


def _evict_sync():
    usage = _usage()
    if usage <= HIGH * budget:
        return
    # gather (atime, cache_path, nchunk) for all filled chunks in the pool
    candidates = []
    for cdir in pool_dir.glob("*/*.b2nd"):
        af = _atime_file(cdir)
        atimes = np.load(af) if af.exists() else None
        try:
            arr = blosc2.open(str(cdir), mode="a")
        except Exception:
            continue  # corrupt cache: skip (never crash the fetch path)
        sc = getattr(arr, "schunk", arr)
        for info in sc.iterchunks_info():
            if info.special == blosc2.SpecialValue.NOT_SPECIAL:
                at = (
                    atimes[info.nchunk]
                    if atimes is not None and info.nchunk < len(atimes)
                    else 0.0
                )
                candidates.append((at, str(cdir), info.nchunk))
    candidates.sort()  # oldest first
    target = LOW * budget
    open_caches = {}
    for _, cpath, nchunk in candidates:
        if usage <= target:
            break
        if cpath not in open_caches:
            open_caches[cpath] = blosc2.open(cpath, mode="a")
        sc = getattr(open_caches[cpath], "schunk", open_caches[cpath])
        before = _usage()  # ponytail: O(pool) stat per eviction; batch later
        sc.update_chunk(nchunk, _uninit_chunk(sc, nchunk))
        usage = _usage()
        logger.info(
            "evicted chunk %d of %s (freed %d bytes)", nchunk, cpath, before - usage
        )
```

Wire-up:

- In `server.py` lifespan (after the registry init):
  ```python
  from caterva2.services import peercache

  peercache.pool_dir = settings.statedir / "peercache"
  peercache.pool_dir.mkdir(parents=True, exist_ok=True)
  peercache.budget = settings.peer_cache_quota
  ```
- In the Phase 4.4 fetch branch, activate the two `peercache.` calls.

Known MVP ceilings (leave the `ponytail:` comments in): `_usage()` rescans the
pool per eviction; per-peer `cache_quota` overrides are parsed but not yet
enforced (global budget only) — note both in the code.

### Acceptance

Set `peer_cache_quota = "10M"` in A's TOML. Put a ~50 MB incompressible
dataset on B. Fetch successive slices of it through A and observe in A's log:
evictions fire, `du -sh <statedirA>/peercache` stays ≈ 8-10 MB, and re-fetching
an evicted slice re-downloads and still returns correct data.

---

## Phase 6 — WebUI: read-only peers panel + root browsing

- `get_roots` already feeds the WebUI root list (Phase 4.2), so `@labb`
  appears in the sidebar. Browsing it goes through the same `api/list`-backed
  htmx path list — find `htmx_path_list` (grep `def htmx_path_list`) and add
  the same peer branch as `get_list` if it resolves paths itself rather than
  calling `get_list`.
- Add `GET /htmx/peers/` returning a fragment that lists
  `registry.peers.values()` with name, urlbase, online/offline badge, and
  cached-bytes-per-peer (`du` of `peercache/<name>`). Follow the pattern of an
  existing small htmx route (grep `@app.get("/htmx/root-list/")`) and an
  existing template under `caterva2/services/templates/`. Read-only: no
  buttons.
- Dataset display panels for peer paths: only wire the metadata/info panel
  (served by the info branch). If the data-display panel path slices
  containers directly (it does — grep the open item: sync reads), keep peer
  datasets out of it for MVP: show info + a "fetch via API" hint instead.
  `# ponytail: display panels for peer datasets need afetch-first plumbing`.

Acceptance: open A's web UI; `@labb` root visible and browsable; peers panel
shows online badge; stop B; after ~15 s a refresh shows offline badge and
already-fetched data still displays info.

---

## Phase 7 — tests

New file: `caterva2/tests/test_peers.py`. Spin two real servers via
subprocess (do NOT reuse the port-8000 harness):

```python
import os, shutil, signal, subprocess, sys, time
import blosc2, numpy as np, pytest, httpx

B_PORT, A_PORT = 8031, 8032


def _start(statedir, port, extra_toml=""):
    statedir = str(statedir)
    toml = f'[server]\nurlbase = "http://localhost:{port}"\nlogin = false\n{extra_toml}'
    conf = f"{statedir}/caterva2-server.toml"
    os.makedirs(statedir, exist_ok=True)
    with open(conf, "w") as f:
        f.write(toml)
    env = dict(os.environ, CATERVA2_SECRET="t", PYTHONPATH=os.getcwd())
    proc = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "caterva2.services.server",
            "--statedir",
            statedir,
            "--listen",
            f"localhost:{port}",
            "--conf",
            conf,
        ],
        env=env,
        cwd=os.getcwd(),
    )
    for _ in range(50):  # wait until it answers
        try:
            httpx.get(f"http://localhost:{port}/api/roots", timeout=1)
            return proc
        except Exception:
            time.sleep(0.2)
    proc.kill()
    raise RuntimeError("server did not start")


@pytest.fixture(scope="module")
def two_servers(tmp_path_factory):
    bdir = tmp_path_factory.mktemp("peerB")
    adir = tmp_path_factory.mktemp("peerA")
    # seed B's @public with a 4-chunk dataset
    pub = bdir / "public"
    pub.mkdir()
    data = np.random.default_rng(0).random((4, 100_000))
    blosc2.asarray(data, chunks=(1, 100_000), urlpath=str(pub / "mc.b2nd"))
    b = _start(bdir, B_PORT)
    peer_toml = (
        f'[[server.peer]]\nname = "labb"\nurlbase = "http://localhost:{B_PORT}"\n'
    )
    a = _start(adir, A_PORT, peer_toml)
    yield f"http://localhost:{A_PORT}", data, adir
    for p in (a, b):
        p.send_signal(signal.SIGTERM)
        p.wait(timeout=10)
```

(Adapt `--conf` to however the server actually receives a config path — grep
`--conf` / `get_parser` in `caterva2/utils.py`; if config is only read from
the CWD, set `cwd=statedir` for the subprocess and put the repo on
`PYTHONPATH` — the fixture above already does the latter.)

Tests to write against the fixture (each is a few lines with `httpx` +
`blosc2.ndarray_from_cframe`):

1. `test_roots_contains_peer` — `/api/roots` has `@labb`.
2. `test_list_and_info` — listing shows `mc.b2nd`; info shape == (4, 100000).
3. `test_fetch_slice_correct` — fetch `2:4`, compare to seeded data.
4. `test_cache_hit` — fetch same slice twice; second answer correct (and, if
   feasible, no new chunk files appeared between the two).
5. `test_peer_offline_tolerated` — kill B, `/api/roots` still answers and
   fetch of an *uncached* slice returns 503; restart not required for A to
   keep serving other roots.
6. `test_chunk_endpoint_is_404_on_peer_root` — non-transitivity guard.

Run: `pytest caterva2/tests/test_peers.py -x -q` (ports 8031/8032 assumed
free). Then the full suite.

---

## Phase 8 — blosc2 repo tasks (parallel, non-blocking)

Repo: `/Users/faltet/blosc/python-blosc2` (we own it; the `blosc2` conda env
imports it as an editable/dev install — verify with
`python -c "import blosc2; print(blosc2.__file__)"`).

1. **Forward storage kwargs in `Proxy.__init__`** (grep `class Proxy`,
   `blosc2.empty(` in `src/blosc2/proxy.py`): accept `contiguous` (and pass
   through to `blosc2.empty`). One-liner + test. Lets Phase 3 drop the manual
   `blosc2.empty(...)` + `_cache=` dance later; do not block on it.
2. **`SChunk.update_special(nchunk, special_value)`** convenience wrapping the
   craft-a-special-chunk + `update_chunk` sequence used in
   `peercache._uninit_chunk`. With a test mirroring repo-root
   `evict_sparse_test.py`.
3. *(fast-follow, separate PR, not part of this MVP)*: real async
   `C2Array.aget_chunk` (httpx.AsyncClient) + gathered semaphore-bounded
   `Proxy.afetch(max_concurrency=...)`. When it lands, delete
   `RemoteSource.aget_chunk`'s to_thread shim (the design doc §3 has the
   guardrails: opt-in, bounded, HTTP/2).

---

## Explicitly out of scope (do not build)

- mDNS/Zeroconf, manual add-by-URL UI, rendezvous directory, gossip.
- `POST/DELETE /api/mounts`, sqlite-backed registry.
- Credentials/auth toward peers; non-public roots; per-user mounts.
- Transitive mounts (peer roots must 404 on `api/chunk` — that IS the guard).
- Batch `api/chunks` endpoint.
- B-side `api/chunk` container-path support and real `HDF5Proxy.get_chunk`
  (the `api/fetch` fallback covers members; B-side fixes are a later PR).
- Timer-based liveness/refresh and websocket push (lazy re-probe only).
- Pinning; per-peer quota enforcement (global budget only).

## Final verification checklist

1. Full pytest suite green (with port 8000 free).
2. The Phase 7 two-server tests green.
3. Manual: two servers + WebUI walkthrough from Phase 6 acceptance.
4. Manual: quota scenario from Phase 5 acceptance (evict + refetch correct).
5. `git grep -n "ponytail:" caterva2/services/{peers,remote,peercache}.py`
   lists every deliberate shortcut, each naming its upgrade path.
