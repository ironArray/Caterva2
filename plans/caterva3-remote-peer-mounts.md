# Caterva3: remote peer mounts (multi-host)

Status: design verified / ready to implement — the step-by-step build plan is
`plans/caterva3-remote-peer-mounts-impl.md`. Consolidates the architecture
discussion for forking Caterva3 from the `new-table` branch. Scope below is deliberately split
into an MVP that ships and a set of clearly-deferred extensions. File/line
references are against `new-table` as read during design and should be
re-checked before coding.

## Goal

Let a Caterva3 server (A) **mount** another Caterva3 server (B) and expose B's
datasets as a virtual root, browsable and fetchable exactly like a local root.
Remote data is accessed through Blosc2 `Proxy`/`C2Array` so only the chunks that
are actually touched transfer, and those chunks are cached locally under a quota.

MVP restricts this to B's `@public` root only. Authentication, ACLs, and
non-public roots are deferred (see "Deferred").

## Key framing

A mount is **server A acting as a client of server B.** This is not a new
subsystem bolted on; it reuses primitives that already exist:

- Remote transport already exists as `blosc2.C2Array` (an array whose chunks live
  behind B's REST API) and the caching layer as `blosc2.Proxy` (fetch-on-access,
  chunk-granular local cache).
- The `new-table` "virtual roots" work already generalized path resolution over
  non-directory backings (TreeStore `.b2z`, HDF5) via an adapter protocol. A
  remote peer is just another adapter.

**Lineage: this is the old pubsub subscriber, resurrected.** Caterva2's original
broker/publisher/subscriber architecture did exactly this — the subscriber
mounted publisher roots via `Proxy(C2Array)` with a local chunk cache. The
`afetch` call in `partial_download` and `open_b2`'s proxy-source branches are
its leftovers. Before writing `RemotePeerAdapter` from scratch, mine the
pre-removal tree (`git show ebd3909^1:caterva2/services/{bro,pub,sub}.py`) for
cache layout, root models, and etag handling.

Two things that share the word "mount" but must be kept apart:

1. **Server-side adapter + path resolution** — reused. This is the seam a remote
   peer slots into.
2. **`new-table`'s client-side localStorage mount UX** — *not* reused for peers.
   That model is per-browser, zero server state, no endpoints (by design, see
   `plans/b2z-virtual-roots.md`, "Deliberately NOT done"). A peer mount is
   irreducibly server-side: a persistent record shared across A's users, with a
   cache pool. Local `.b2z`/`.h5` container mounts keep the localStorage UX;
   peer mounts do not.

## What is reused (grounded in current code)

- **Adapter protocol** — `caterva2/services/srv_utils.py`: `_TreeStoreAdapter` /
  `_HDF5Adapter` expose `leaves(prefix)`, `get(key)`, `size(prefix)`,
  `leaf_size(key)`, `is_group(node)`, `close()`, dispatched by
  `split_container_path`, `open_container`. The interface already tolerates
  `size()` returning `None` (unknown/expensive), which is exactly the remote
  case. A `RemotePeerAdapter` implements the same protocol.
- **Proxy leaf template** — `caterva2/hdf5.py:354` `HDF5Proxy(blosc2.Operand)` is a
  proxy leaf whose backing resource (the open `h5py.File`) must outlive `get()`.
  A remote leaf has the identical lifecycle, with an HTTP session in place of the
  file. (Note `HDF5Proxy` itself does *no* caching — its `.b2arr` holds metadata
  only and `__getitem__` reads the local file live; it is a *structural* template,
  not the caching mechanism.)
- **Fetch pipeline** — `caterva2/services/server.py:428` `partial_download` takes a
  per-path `asyncio.Lock` and calls `await proxy.afetch(slice_)`. Any object with
  `afetch` drops into this unchanged.
- **Caching mechanism** — `blosc2.Proxy` over a `C2Array`, persisted to a local
  urlpath: `afetch(slice)` fills only touched chunks; `blosc2.open` later returns
  the proxy with a partially-initialized cache container. This is the intended
  Caterva3 remote-cache primitive.
- **Root advertisement seam** — `server.py:331` `get_roots` returns `@public`
  (always) plus `@personal`/`@shared` (if authenticated). Peer roots are appended
  here.
- **TOML config pattern** — `caterva2/services/settings.py` already loads all
  config from `caterva2-server.toml`. A `[[peer]]` array of tables fits directly.
- **Peer-auth primitives (not needed for MVP but present)** — `c2array.py`
  `login()` → `auth/jwt/login` cookie; `Client._get_auth_cookie`; `C2Array`/`_xget`
  accept the cookie as `auth_token`. Available when auth is added later.

## Confirmed facts that shape the design

- **`@public` is served unauthenticated on every read endpoint A needs.**
  `api/roots` (`server.py:331`), `api/list` (355), `api/info` (399), `api/fetch`
  (537), `api/chunk` (753) all use `Depends(optional_user)`, and `optional_user`
  degrades to `lambda: None` when login is disabled (210-213). Write/mutation
  endpoints (from 879) require `current_active_user`. => A can mount B's `@public`
  with **zero credentials**.
- **The existing "quota" is not a cache manager.** `get_disk_usage()`
  (`server.py:81`) sums `st_size` over the whole state dir; `settings.quota` is
  checked *only* on uploads (1112, 1181, 1247, 1323) as admission control
  ("Upload failed…"). There is no eviction anywhere, and the fetch path is not
  quota-checked. A remote cache needs the opposite behaviour (evict cold chunks
  and keep fetching), so the cache manager is new work, not reuse.
- **`Proxy.afetch` is serial and `C2Array` has no async path.** `Proxy.afetch`
  (`python-blosc2/src/blosc2/proxy.py:325`) is a plain `await` loop — no `gather`,
  one chunk fully round-tripped and written before the next request issues. It
  requires the *source* to implement `aget_chunk` and raises `NotImplementedError`
  otherwise (proxy.py:399). `C2Array` (`c2array.py:189`) implements only a
  synchronous `get_chunk` (one blocking `requests` GET per chunk against
  `api/chunk/{path}?nchunk=N`) and has **no `aget_chunk`**. The httpx→requests
  migration (for Pyodide compatibility) removed the async transport. Consequence:
  concurrent chunk fetching does not exist today; the MVP sidesteps this with a
  local `to_thread` shim (§3) and the true async path is an upstream fast-follow.
- **`api/chunk` serves only plain datasets — container members 404** (verified
  live 2026-07-04): the endpoint resolves via `get_abspath` alone, with no
  `split_container_path` handling, so `.b2z` and `.h5` member paths 404 before
  any container logic runs (`api/info` and `api/fetch` on the same paths return
  200 with correct data). `.h5` leaves have a second, deeper gap:
  `HDF5Proxy.schunk` is the metadata-only all-zeros `b2arr` (hdf5.py:483), so
  even with resolution fixed, `api/chunk` would serve zeros until the real
  `HDF5Proxy.get_chunk` (commented out at hdf5.py:628; direct-chunk-read
  helpers exist at hdf5.py:229-292) is implemented. Consequence: the peer
  caching path needs a per-leaf fetch strategy (see §2 and §5).
- **httpx now runs under Pyodide on recent runtimes.** Via JSPI (JavaScript
  Promise Integration), integrated in Pyodide's event loop since 0.27.7; fully
  supported in Chrome 137 and Node 24 (flagged elsewhere). So the Pyodide
  constraint that forced `requests` has largely expired — and it never applied to
  A's CPython server at all.

## Verified by experiment (2026-07-04)

All run against blosc2 4.7.1.dev0 (conda env `blosc2`) and a live `new-table`
server on `localhost:8010`. Repro scripts in the repo root (untracked):
`evict_test2.py` (contiguous eviction), `evict_sparse_test.py` (sparse
eviction), `e2e_peer_test.py` (live HTTP end-to-end).

**The MVP data path, end-to-end over real HTTP:**

- `Proxy` over a `C2Array` subclass whose `aget_chunk` is
  `asyncio.to_thread(get_chunk)` fills a cache from B's `api/chunk` — the shim
  strategy works with today's blosc2, no upstream changes.
- Partial `afetch` of rows 2..3 of an 8-chunk dataset issued exactly 2 GETs
  (chunks 2, 3); data bit-exact. Chunk selection (`get_slice_nchunks`) works
  over the wire.
- `api/chunk` returns the raw compressed chunk (`schunk.get_chunk`), i.e.
  cache-aligned — confirmed both by code (server.py:761-767) and by the live
  proxy fill.
- `LazyExpr` over the remote-backed proxy: slicing `2*a+1` at row 4 fetched
  exactly chunk 4, result correct — "LazyExpr for free" holds. Caveat: it goes
  through Proxy's **sync** fetch path (blocking HTTP); see open item 4.

**Sparse cache + chunk-granular eviction:**

- `blosc2.empty(..., contiguous=False)` yields a directory cache (one file per
  chunk) that `Proxy` accepts via `_cache=`; `Proxy.__init__` does not forward
  `contiguous` (hence the blosc2 MVP task in §3).
- Eviction = `update_chunk(nchunk, UNINIT special chunk)`: full chunk
  reclaimed at any position (head/mid/tail), ~2 ms each, persists across
  reopen; the proxy refetched exactly the evicted chunks (incl. over HTTP);
  data bit-exact. Contiguous frames instead leave holes for non-tail
  evictions — not suitable for the cache.
- `blosc2.open` on a proxy cache auto-reconstructs the `C2Array` source from
  its `proxy-source` meta (incl. `auth_token`); `caterva2_env=True` defers
  reattachment to the server — the reopen story `RemotePeerAdapter.get()`
  needs already exists (schunk.py:1713-1723).

**Limits found (drove design changes):**

- `api/chunk` 404s on **all container-member paths** (`.b2z` and `.h5`
  members): it resolves via `get_abspath` only, no `split_container_path`.
  `api/info`/`api/fetch` return 200 with correct data on the same paths.
  Hence the per-leaf fetch strategy in §2.
- `.h5` leaves additionally lack a real `HDF5Proxy.get_chunk`
  (`HDF5Proxy.schunk` is the all-zeros metadata `b2arr`; real implementation
  commented out at hdf5.py:628) — B-side fix deferred.
- `Proxy.__getitem__` (and LazyExpr reads) fill missing chunks via sync HTTP —
  endpoints that slice containers directly must be audited (open item 4).
- The `partial_download`/`afetch` path is **latent** in `new-table`: nothing
  creates `Proxy(C2Array)` caches since the pubsub subscriber was removed —
  Caterva3 re-activates it rather than extending a working path.

## What must be built

### 1. Adapter-first path resolution

Current resolution is filesystem-centric: `get_abspath`/`split_and_resolve` map a
path to a local abspath (with `.resolve()` traversal guards), and `open_b2`
(`server.py:119`) hardcodes `root in {@personal,@shared,@public}` and raises
otherwise. A peer root has no abspath.

Change resolution to return `(adapter, key)` and let the adapter decide
filesystem vs. remote. Everything downstream (`partial_download`, the path-list
virtual-root loop) already needs only "something with `afetch`/`leaves()`", so
once resolution yields the right adapter, the pipeline is largely agnostic.

### 2. `RemotePeerAdapter`

Implements the adapter protocol against B's REST API:

- `leaves()` / `size()` / metadata → `api/list/@public`, `api/info` on B
  (via an embedded `caterva2.Client`, run in a threadpool since the Client is a
  synchronous `httpx.Client`; catalog ops are infrequent).
- `get(key)` → `blosc2.Proxy` over a `blosc2.C2Array(peer_urlbase, path)`, with
  a **sparse** cache in this peer's cache pool (built via
  `blosc2.empty(..., contiguous=False)` and passed as `_cache=` until Proxy
  forwards `contiguous`; see §3/§4). No `auth_token` in MVP.
- **Per-leaf fetch strategy** (because `api/chunk` 404s on container members,
  see Confirmed facts): plain `.b2nd` leaves use `api/chunk` (verified e2e);
  container-member leaves (`.b2z`/`.h5` internals) fall back to
  **chunk-aligned `api/fetch`** — `aget_chunk(nchunk)` computes that chunk's
  slice, fetches it via `api/fetch` (which works for all leaf types), and
  recompresses into the cache chunk. Works today with zero B-side changes, at
  a decompress/recompress cost. B-side fixes that later retire the fallback:
  container-path resolution in `api/chunk` (makes `.b2z` members chunk-served
  directly — their schunks are real), and a real `HDF5Proxy.get_chunk` for
  `.h5` (direct chunk read for blosc2-in-HDF5, transcode otherwise).

Because leaves become Blosc2 operands, server-side `LazyExpr` over a mounted
remote dataset works for free (`open_b2` already reopens proxy-source operands).

**The whole §2/§3 data path is verified live** (2026-07-04, against a running
`new-table` server; repro: repo-root `e2e_peer_test.py`, expects a server on
`localhost:8010`): `C2Array` over real HTTP + the `to_thread` `aget_chunk`
shim + a sparse `Proxy` cache. Partial `afetch` of rows 2..3 issued exactly
two `api/chunk` GETs (chunks 2, 3); evicting chunk 2 and refetching issued
exactly one; a `LazyExpr` (`2*a+1`) sliced at row 4 pulled exactly chunk 4 and
computed bit-exact. One caveat found: `LazyExpr`/`__getitem__` reads go
through Proxy's **sync** `fetch` (blocking HTTP on the caller's thread) — see
open item 6.

### 3. Async chunk fetch: local shim in MVP, upstream as fast-follow

`Proxy.afetch` only checks `callable(getattr(src, "aget_chunk"))`
(proxy.py:399). So the MVP doesn't *gate* on a blosc2 release: a
Caterva3-local `C2Array` subclass whose `aget_chunk` is
`await asyncio.to_thread(self.get_chunk, nchunk)` works with today's blosc2
and already gives the operationally important property — A's event loop never
blocks on a cache fill. The fetch is serial, but correct. Since we own blosc2,
the upstream items below proceed in parallel and replace the shim whenever
they land; the shim is a de-risking stopgap, not the destination.

**Blosc2 tasks (small, can land with the MVP)**:

- Forward `contiguous` (and other storage kwargs) through `Proxy.__init__` to
  the cache constructor, so sparse caches don't require the `_cache=` detour.
- A convenience API for the eviction primitive, e.g.
  `SChunk.update_special(nchunk, SpecialValue.UNINIT)`, so callers don't
  hand-craft special chunk bytes (including the trailing-partial-chunk size
  matching). Caterva3 can ship crafting the bytes itself if this lags.

**Blosc2 tasks (fast-follow)** — the latency win:

- Add a real `C2Array.aget_chunk(nchunk)` doing an **async** GET
  (`httpx.AsyncClient`) against `api/chunk/{path}?nchunk=N`.
- Make `Proxy.afetch` **gather** its chunk requests instead of awaiting serially.
  Guardrails: (a) **opt-in / adaptive** — local `NDArray`/`SChunk` sources gain
  nothing from concurrency; expose `max_concurrency` defaulting conservative,
  higher for remote sources; (b) **semaphore-bounded** — an unbounded gather over
  a 10k-chunk slice would fire 10k GETs at the origin (self-DoS). A cap in the
  tens captures most of the win; HTTP/2 (`httpx[http2]`, already a dep)
  multiplexes them over one connection.
- Keep a fallback for non-JSPI browser runtimes (JupyterLite): a Pyodide-native
  async transport via `pyodide.http.pyfetch`, or the sync `get_chunk`. Do not let
  the server path depend on it.
- Optional: a batch `api/chunks?nchunks=...` endpoint on the origin + matching
  `aget_chunks([...])` to collapse round-trips at the source. Nice-to-have on top
  of concurrent `afetch`, not a prerequisite.

Why this matters (async accelerates caching): filling a cache is dominated by
network round-trips, not CPU. Concurrency across chunks turns `N × RTT` into
`~1 × RTT`; the `await` also yields A's event loop so one slow fill doesn't stall
other requests; and it enables speculative prefetch. Serial `afetch` gives only
the event-loop benefit, not the latency win.

### 4. Remote-cache manager

Separate from the durable-upload quota — two pools, two policies:

- **Durable storage** (`@personal`/`@shared` uploads): keep today's admission
  quota; never evict.
- **Remote cache**: one global byte budget by default (per-peer `cache_quota`
  as optional override); **chunk-granular LRU** on a high/low watermark,
  checked on the `afetch` path. Verified end-to-end (blosc2 4.7.1.dev,
  2026-07-04; repro: repo-root `evict_sparse_test.py`):
  - Caches are **sparse frames**: `blosc2.empty(..., contiguous=False)` — a
    directory with one file per chunk — handed to `Proxy` via `_cache=`.
  - The eviction primitive is
    `schunk.update_chunk(nchunk, uninit_special_chunk)` (not file deletion —
    Proxy treats any `special != NOT_SPECIAL` chunk as unfetched and refetches
    it). On the sparse layout the full chunk is reclaimed at any position
    (head/mid/tail), ~2 ms per eviction; persists across reopen; refetched
    data is bit-exact. No compaction needed (contiguous frames, by contrast,
    leave holes for non-tail evictions — don't use them for the cache).
  - **LRU ordering needs a sidecar per-chunk atime store** (Blosc2 gives no
    chunk atime): a small per-dataset array/file next to the cache dir,
    updated on fetch, scanned by the evictor.
  - Implementation details: trailing partial chunks need a size-matched
    special chunk; whole-dataset drop (for invalidation, below) is just
    removing the cache dir.
  - Pinning of in-use datasets stays deferred; the per-path `asyncio.Lock` in
    `partial_download` already serializes fetch-vs-evict for MVP purposes.
- **Invalidation on remote change**: record B's mtime/etag per dataset when the
  proxy cache is created; on catalog refresh, drop the whole cache dir if it
  changed. Otherwise a partially-filled cache silently serves mixed stale/fresh
  chunks. (Prior art: old pub kept
  `database.etags` + `If-None-Match`; old sub stored etags from response
  headers and invalidated on change events — see `ebd3909^1`.)

Do not leave both in one pool: remote-cache churn would otherwise starve real
uploads or wedge against a ceiling with no eviction to relieve it.

### 5. Server identity manifest

New endpoint `GET /api/peer` (or `/.well-known/caterva3`) returning
`{peer_id, name, api_version, capabilities, public_root}`. `peer_id` is a UUID B
generates once and persists in its state dir. Used for:

- **Self-mount guard** — reject if the returned `peer_id` equals A's own.
- **Dedupe** — the same B reached via two routes (config + later mDNS) is one
  peer, keyed by id not URL.
- **Version negotiation** — a single integer `api_version`, exact match; bump
  on any breaking change. Fail the handshake with a clear message rather than
  a confusing mid-browse 404.
- **Capabilities** — e.g. whether B exposes per-chunk `api/chunk` (what the
  caching proxy wants), whole-slice `api/fetch` only, or the batch endpoint. The
  adapter picks its fetch strategy per peer from this — and **per leaf**: today
  `api/chunk` only works for plain datasets, so container members need the
  chunk-aligned `api/fetch` fallback (§2) until B advertises
  container-chunk support.

### 6. Static discovery + startup handshake

MVP discovery is static configuration, not network discovery. A `[[peer]]` seed
list in `caterva2-server.toml`:

```toml
[[peer]]
name    = "lab-b"          # local alias -> root "@lab-b"
urlbase = "http://serverB:8000"
# cache_quota = "2G"       # optional per-peer override of the global remote-cache budget
```

At startup, for each seeded peer run the handshake even though discovery is
static (skipping it degrades "static config" into "blindly trust a pasted URL"):

1. `GET B/api/peer` — confirm it's a Caterva3 server, read `peer_id`, check
   `api_version`, apply the self-mount guard.
2. Record capabilities.
3. `GET B/api/list/@public` — confirm `@public` is reachable and snapshot the
   initial catalog. Enforce non-transitivity here: ingest only B's own `@public`;
   B advertises only its local roots, never its mounts.
4. Hold the entry in the in-memory registry.

**Startup must be tolerant**: a peer that is down, or whose version mismatches, is
logged and marked offline/skipped — never a boot failure. Offline peers are
re-probed lazily on next access (§8).

### 7. `get_roots` + WebUI

- `get_roots` appends configured peer roots. Public-only ⇒ visible to everyone,
  unconditionally, mirroring how `@public` is added.
- WebUI: an HTMX fragment (`GET htmx/peers/`) listing configured peers with a
  status badge. **Read-only in MVP** (no mount/unmount buttons — mounts are
  config). Peer leaves render through the same path-list machinery as local
  virtual-root leaves.

### 8. Liveness & catalog refresh

MVP liveness is **lazy**: mark a peer offline when a request to it fails,
re-probe on the next access — no background timer task. When B is offline, keep
serving already-cached chunks (offline property of the proxy cache) and badge
the root stale. Distinguish the cheap **catalog cache** (metadata) from the
expensive **chunk cache** (quota-governed).

Deferred: timer-based re-probe/refresh, and beyond that **push-based** catalog
refresh — the old stack used `fastapi_websocket_pubsub` (an `@new` channel for
new roots, per-root topics where pub published dataset changes with etags and
sub invalidated on receipt). Post-MVP, B can expose a pubsub endpoint and A
subscribes per mounted root, replacing polling entirely.

## MVP scope

In:

- Static `[[peer]]` seed list; startup handshake (`api/peer` probe, version check,
  self-mount guard, dedupe).
- Public-only; **no credentials**, **no database**, no user auth required to run a
  mounting server.
- In-memory registry; lazy liveness (no background tasks).
- `RemotePeerAdapter` + adapter-first resolution refactor.
- Remote-cache pool: sparse per-dataset caches, chunk-granular LRU eviction
  with sidecar atime store (separate from upload quota) + mtime/etag
  invalidation.
- Local `aget_chunk` shim (`asyncio.to_thread` over sync `get_chunk`) so the
  MVP doesn't gate on a blosc2 release; small blosc2 conveniences
  (`contiguous` passthrough, `update_special`) land in parallel (see §3).
- Read-only peers panel in the WebUI.

Deferred (all additive; none require unwinding the MVP):

- Blosc2 fast-follow: real async `C2Array.aget_chunk` + gathered, bounded
  `afetch` (the latency win; see §3); replaces the shim when it lands.
- Timer/push-based liveness & catalog refresh (see §8).
- mDNS/Zeroconf auto-discovery; manual add-by-URL UI; broker-style rendezvous
  directory; gossip/peer-exchange.
- Dynamic registry (`POST`/`DELETE /api/mounts`, sqlite persistence).
- Credentials / auth: service-account vs. per-user delegation, ACLs, non-public
  roots, per-user mounts. Additive because the read endpoints already thread
  `optional_user` — later you pass a cookie into the same `C2Array`/`_xget`
  `auth_token` slot.
- Transitive mounts.
- Batch `api/chunks` endpoint.
- B-side container-chunk support (retires the `api/fetch` fallback):
  `split_container_path` resolution in `api/chunk`, and a real
  `HDF5Proxy.get_chunk` (uncomment/finish hdf5.py:628 using the
  direct-chunk-read helpers) — the latter is the same HDF5Proxy work the
  filter/sort-inside-containers plan already wants.

## Discovery vs. handshake (why they're separated)

Discovery = how A becomes aware B exists (transport). Handshake = turning a URL
into a registry entry (application protocol). The handshake is transport-agnostic
and identical whether the URL was typed, configured, or discovered. This is why
starting with a static seed costs nothing later: mDNS/manual-add simply become
new *sources* of candidate URLs that flow into the same handshake.

- **mDNS/Zeroconf** (deferred): `_caterva3._tcp.local.` service with TXT records
  (`urlbase`, name, version). Link-local only; breaks under Docker bridge
  networking (no multicast) — needs host networking, macvlan, or an Avahi
  reflector. The zero-config demo layer, not the backbone.
- **Static config** (MVP): deterministic, no multicast. In Docker Compose, peers
  reach each other by service name via built-in DNS (`http://serverB:8000`),
  which pairs perfectly with the seed list.
- **Manual add-by-URL** (deferred UI): the universal path for WAN/NAT/cloud.
- **Rendezvous directory** (deferred): resurrect the old broker pattern — it
  was ~70 lines (`bro.py` at `ebd3909^1`): `POST /api/roots` to announce,
  `GET /api/roots` to enumerate, state in a JSON file. A broker-style directory
  is the WAN/cloud discovery answer where multicast can't reach: peers announce
  to a well-known directory, candidates flow into the same handshake, the
  operator still promotes.

Rule that survives all layers: **discovery surfaces candidates; a human/operator
promotes them.** Never auto-mount everything discovered (trust hole + caching
blow-up).

## Non-transitivity & loops

Mounts are non-transitive: A sees B's local `@public` only, never B's mounts.
Enforce on both sides — B's manifest/listing advertises only locally-owned roots.
With that plus the `peer_id` self-guard and dedupe, A↔B cycles are harmless
(neither re-exposes the other's borrowed view), and metadata refreshes can't
ping-pong.

## Security posture

- Mounting fetches an operator-supplied URL ⇒ SSRF-shaped. In MVP this is
  config-only (already privileged); when dynamic mounts arrive, admin-gate them
  and consider a URL allowlist.
- Treat every B response as untrusted input, even public: cap catalog size, set
  timeouts on the `api/peer`/list probes, and reuse the defensive posture
  `new-table` already applies to corrupt containers (swallow-and-skip, never 500
  the listing).
- **Cache-path injection**: dataset names from B's catalog become local cache
  urlpaths on A; a malicious or compromised B returning `../../…` entries gets
  a file write outside the cache pool. Derive cache filenames as
  `hash(peer_id, remote_path)` plus a small manifest mapping (or strictly
  sanitize) — never splice remote names into local paths.
- Public-only cleanly sidesteps the cross-peer privacy tension: "public" carries
  no privacy expectation, so caching B's public chunks on A's disk is by
  definition acceptable. In the MVP, the Docker/privacy motivation reduces to
  isolating A's *own* state, not policing peer-to-peer leakage.

## Deployment

- Single-host / dev: `uvx caterva3-server`. Runtime is "just the Python service."
- Production / privacy-sensitive: Docker image + Compose. Service-name DNS gives
  deterministic peer URLs with no multicast (fits the static seed).
- Frontend is server-rendered Jinja + HTMX (htmx vendored as a static file). npm
  is build-time only (Vite bundles `src/main.js` + Bootstrap/SCSS); no runtime JS
  toolchain either way.

## Open items to verify before / during implementation

1. ~~Which proxy source `partial_download` actually reaches in `new-table`.~~
   **Answered**: the path is latent. Nothing in the standalone server creates
   `Proxy(C2Array)` caches anymore — the `afetch` call and `open_b2`'s
   proxy-source branches are leftovers of the removed pubsub subscriber. We are
   rebuilding that path, with the old subscriber code as prior art.
2. ~~`api/chunk` chunk alignment.~~ **Resolved**: `api/chunk` returns the raw
   compressed chunk via `schunk.get_chunk(nchunk)` (server.py:761-767), so it
   is cache-aligned and suitable for `update_chunk`. (`api/fetch` remains a
   repacked slice — not for the caching path.)
3. **Blosc2 upstream landing** (fast-follow, not MVP): `C2Array.aget_chunk` and
   the gathered, semaphore-bounded, opt-in `afetch`, plus the non-JSPI fallback
   decision driven by the JupyterLite browser matrix.
4. **Sync reads on the event loop.** Verified: `Proxy.__getitem__` (and thus
   LazyExpr operand reads) fills missing chunks via the **sync** `fetch` path —
   for a remote-backed leaf that means blocking HTTP GETs on whatever thread
   runs it. `api/fetch` is safe (routes through `partial_download`/`afetch`
   first), but audit every other endpoint that slices containers directly
   (display panels, `__getitem__`-style handlers): for peer-backed paths they
   must either await `partial_download` first or run in a threadpool.
5. **DB vs. TOML coupling.** The SQLite DB currently only initializes when
   `CATERVA2_SECRET` is set (it's tied to user management, `db.py`). The
   public-only MVP runs without a secret, so keep the registry in-memory/TOML and
   avoid a DB dependency; only graduate to a sqlite registry once a deployment
   already has the secret/DB for user auth.
6. ~~Sparse-frame chunk eviction.~~ **Resolved** (verified 2026-07-04 on blosc2
   4.7.1.dev): works end-to-end through the public API. Sparse cache =
   `blosc2.empty(..., contiguous=False)` passed to `Proxy(_cache=...)`;
   eviction = `update_chunk` with an UNINIT special chunk. Full chunk reclaimed
   at any position, ~2 ms, persists, refetches exactly the evicted chunks,
   bit-exact. See §4. Also verified: `blosc2.open` on a proxy cache
   auto-reconstructs a `C2Array` source from `proxy-source` meta (incl.
   `auth_token`), and a `caterva2_env=True` flag defers source reattachment to
   the server — the reopen story needed by `RemotePeerAdapter.get()` already
   exists.
