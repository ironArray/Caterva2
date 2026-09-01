# CTable support for remote peer mounts: fetch + per-column `.b2nd` cache

**Status (2026-07-10): planned, not implemented. Revised same day** — cache
container changed from a `.b2d` TreeStore to one `blosc2.Proxy`-backed sparse
`.b2nd` per column, and web-UI browsing (previously out of scope) is now
included. See "Revision note" below. Closes the last gap of
`plans/caterva3-remote-peer-mounts.md`: peer CTables are browsable but not
fetchable. TreeStore peer mounting is already done (NDArray leaves inside a
`.b2z` cache chunk-granularly via the `api/fetch` fallback); this plan makes
CTable leaves — standalone `.b2z` tables and CTables inside TreeStores —
fetchable, cached under the peercache budget, readable offline, and
browsable in the web UI.

## Context

The peer chunk cache (`c2cache/`) rejects CTables at the fetchability gate:
`api/info` on a CTable returns `models.CTableMetadata`, which has no
`shape`/`schunk`, so `RemotePeerAdapter.get` (`c2cache/remote.py:245`) raises
`NotAFetchableDataset` → 400. No fetch ⇒ no cache ⇒ no offline serving.

Approach: **per-column caches filled from one row-slice fetch.** The peer's
existing `api/fetch` CTable branch (`caterva2/services/server.py:710-733`)
serves `table.slice(start, stop).to_cframe()` — one cframe containing all
columns. The provider fetches a chunk-aligned row range once, splits it into
columns, and caches each column locally. One fetch warms every column; no
peer wire-protocol change (`PEER_API_VERSION` stays 1); works against
unmodified peers.

**Milestone 0 (immediately functional): pass-through fetch.** Relay the
peer's row-slice cframe without caching. This alone makes peer CTables usable
online, and remains the permanent fallback for non-cacheable tables
(varlen/list/dictionary/ndarray-spec columns, `chunks is None`, computed
columns). Caching and offline reads land on top.

Enabling facts (verified against python-blosc2, floor already `>=4.8.0`):

- A CTable's cframe is an EmbedStore with `/_meta` (SChunk vlmeta:
  `kind="ctable"`, `version=1`, `schema` json incl. `n_rows`),
  `/_valid_rows` (bool NDArray; all-True after `slice()`), and
  `/_cols/<relpath>` per column — so a valid CTable cframe can be
  **synthesized** from per-column numpy arrays plus the schema dict
  (~20 lines, mirrors `CTable.to_cframe`, `ctable.py:4879-4946`).
- `server.py:631` wraps provider fetch results numpy-only
  (`blosc2.asarray(...).to_cframe()`), so the provider returns **raw cframe
  bytes** for CTables and the server streams them as-is.
- `CTableMetadata.chunks` is the table's shared 1-D grid
  `(rows_per_chunk,)`; it is `None` for tables without fixed-size scalar
  columns → treat as non-cacheable.
- CTables inside a TreeStore are hidden object roots
  (`tree_store.py:254-311`): they list as single leaves with
  `CTableMetadata`, so one code path covers standalone and nested tables.

## Revision note (2026-07-10)

The cache container originally designed here was a `.b2d` TreeStore (one
member per `(column, chunk)`, kept below in "Rejected alternatives" for the
record). Discussed and replaced with **one sparse `.b2nd` `blosc2.Proxy`
cache per column** instead: it reuses `RemoteSource`/`Proxy`/
`open_cached_proxy` (`c2cache/remote.py`) verbatim, and needs **no new
eviction/atime code** in `c2cache/peercache.py` — it already globs
`pool_dir/*/*.b2nd` and treats every hit as a plain sparse NDArray cache via
`update_chunk`/`iterchunks_info`. The TreeStore member format, its parallel
msgpack atime-sidecar variant, its second eviction glob path, and the
"column name must be a valid TreeStore key" non-cacheable caveat all
disappear. Web-UI browsing of peer CTables, previously called out as out of
scope, is now included ("Changes" item 5 below) since the per-column cache
makes it cheap to wire up.

Verified against the installed blosc2 4.8.0 source
(`/Users/faltet/blosc/python-blosc2/src/blosc2/`) while revising:
- `C2Array.__init__` fires a synchronous HTTP `info()` call against its
  `path` (`c2array.py:248-249`) — so the per-column source must NOT subclass
  `C2Array` (there's no per-column peer endpoint to call). It's a plain
  duck-typed source instead (`Proxy` accepts anything with
  `shape`/`chunks`/`blocks`/`dtype`/`cparams`/`aget_chunk`, per its own
  docstring example, `proxy.py:372-397`).
- `Proxy.afetch` (`proxy.py:337-446`): missing chunks come from
  `iterchunks_info()`, fetched via `self.src.aget_chunk(nchunk)`, written via
  `self._schunk_cache.update_chunk(nchunk, chunk)` — each fetch's write runs
  synchronously between awaits, so concurrent `_fetch_one` tasks can't
  interleave. This same guarantee covers writing into *sibling* columns'
  schunks from within one column's `aget_chunk`, not just self. Default
  `max_concurrency` is 1 unless `isinstance(self.src, C2Array)`
  (`proxy.py:434`) — our source isn't, so the fan-out call must pass
  `max_concurrency=` explicitly.
- `Proxy` exposes `.dtype`, `.blocks`, `.cparams`, `.schunk` (→ the
  underlying `SChunk`, whose `.update_chunk(nchunk, bytes)` is the same
  primitive `peercache._evict_from_cache` already uses to blank a chunk;
  here it fills one with real compressed data instead).
- `CTableMetadata` (`caterva2/models.py:77-89`) has `nrows`, `chunks`,
  `blocks`, `schema_dict`, `mtime` — everything needed to build a column's
  cache shape/grid up front.

## Cache container: one `.b2nd` `Proxy` cache per column

Two artifact kinds live in `pool_dir/<peer.name>/`, alongside today's
per-dataset `<hash>.b2nd` caches:

- **Per-column cache**: `<column_hash>.b2nd`, a sparse NDArray,
  `shape=(nrows,)`, `chunks=info["chunks"]`, `blocks=info["blocks"]`, dtype =
  that column's compiled dtype, wrapped in `blosc2.Proxy` via the existing
  `open_cached_proxy()` (`remote.py:131-164`). `column_hash` = sha256 over
  `b"ctable-col\0" + peer_id + b"\0" + remote_path + b"\0" + colname`
  (`\0`-delimited, distinct literal prefix from the existing `cache_path()`
  recipe, `remote.py:121-128` — no collision with plain NDArray caches or
  ambiguity across tables/columns with `:`-like names). Same directory depth
  as existing `.b2nd` caches, so `peercache.py`'s existing
  `pool_dir.glob("*/*.b2nd")` picks these up automatically — **no new glob
  pattern needed**.
- **Family manifest**: `<table_hash>.ctbl.json` (different suffix — invisible
  to the `*.b2nd` glob, never touched by eviction). Holds `path`, `mtime`,
  `nrows`, `chunks`, `schema_dict`, and a `columns: {name: filename}` map —
  the only local record of the table's schema (needed by
  `_synth_ctable_cframe`) and of which cache file belongs to which column,
  available without contacting the peer. Required for the offline path,
  which by definition can't re-derive it from a fresh `info()` call. Written
  atomically (tmp + `os.replace`, same idiom as `peercache.touch`).

**Locking: one asyncio lock per table family, not per column.** Reuse
`peercache.cache_lock()` unmodified, keyed by the manifest's path string. A
CTable fetch/evict operation holds exactly one lock — no per-column locks,
so no lock-ordering hazard (two concurrent requests against the same table
via different trigger columns can't deadlock acquiring each other's locks,
which is exactly the failure mode a per-column-lock scheme would introduce).
`open_cached_proxy` gains one optional parameter, `family: str | None =
None`, folded into the existing `_peer_src` vlmeta JSON; plain NDArray call
sites pass nothing, so their vlmeta is unchanged. blosc2's own
`locking=True` per `.b2nd` frame is unaffected — same frame-integrity role
it plays today, orthogonal to the asyncio lock.

**Staleness/rebuild**: one check at family granularity, before opening any
column proxy — load the manifest, compare `mtime` to the live
`info["mtime"]`; mismatch (or missing/corrupt manifest) ⇒ delete every file
in `manifest["columns"]` plus their atime sidecars, then rebuild. The
*existing*, untouched per-file mtime/corruption check inside
`open_cached_proxy` still self-heals a single crashed/half-written column
file without forcing a whole-family rebuild.

**Fan-out fill**: a new `CTableColumnSource` in `c2cache/remote.py` — plain
duck-typed source (not a `C2Array` subclass), one per column, holding
references to its sibling columns' `Proxy` objects (wired in a second pass
after all N proxies exist, since siblings can't be referenced before they're
created):

```python
async def aget_chunk(self, nchunk):
    start, stop = nchunk * self.chunks[0], min(
        (nchunk + 1) * self.chunks[0], self.shape[0]
    )
    resp = await self._aclient.get(
        f"{self.urlbase}api/fetch/{table_path}", params={"slice_": f"{start}:{stop}"}
    )
    table = blosc2.ctable_from_cframe(
        resp.content
    )  # ALL cacheable columns, one HTTP call
    for name, proxy in self.siblings.items():
        proxy.schunk.update_chunk(nchunk, _pack_column_chunk(table[name][:], proxy))
    return _pack_column_chunk(table[self.colname][:], self)
```

`_pack_column_chunk` mirrors `RemoteSource._chunk_via_fetch`'s existing
pad-and-repack idiom (`remote.py:97-115`): zero-pad a short/trailing row
range to a full chunk, repack via `blosc2.asarray(...).schunk.get_chunk(0)`.

Per-request flow (`fetch_ctable_slice`, inside the family lock):
1. Open/rebuild the family, producing N `Proxy` objects.
2. Pick any one column as "driver" (first in schema order — arbitrary but
   deterministic); `await driver.afetch(slice(start, stop),
   max_concurrency=N)` — this is the only network activity; siblings are
   filled as a side effect of the driver's `aget_chunk` calls, never through
   their own `afetch`.
3. Read every column uniformly — `cols = {name: p[start:stop] for name, p in
   proxies.items()}` (no special-casing the driver: its data comes back via
   the same local re-read as everyone else's, same shape as the existing
   NDArray flow's `await proxy.afetch(...); data = proxy[...]`).
4. `touch()` each proxy (existing function, unchanged, called N times).
5. `_synth_ctable_cframe(schema_dict, cols, stop - start)` (unchanged, see
   below) — fed a `cols` dict sourced from N Proxy reads.

**Offline path**: direct N-column generalization of
`get_cached_only`/`slice_fully_cached` (`remote.py:167-178, 256-264`): open
each column's `.b2nd` file directly (no Proxy wrapper, same as today's
offline NDArray path), check `slice_fully_cached` on every one *before*
trusting any read (a UNINIT/special chunk reads back as silent zeros — same
discipline the existing docstring warns about), then synthesize the cframe.
Any column missing a touched chunk ⇒ whole read fails ⇒ caller returns 503,
matching existing NDArray offline semantics.

**`peercache.py` changes (small, not zero)**: `cache_lock`, `touch`,
`_evict_from_cache`, `_uninit_chunk`, `_atime_file`, `_load_atimes`,
`_usage` — all unchanged.
- `_gather_candidates` (`peercache.py:115-135`): while already opening each
  `.b2nd` to read `iterchunks_info()`, also peek the `_peer_src` vlmeta for
  an optional `"family"` string; carry it in the candidate tuple (`None` for
  plain NDArray caches).
- `ensure_budget` (`peercache.py:91-113`): group two levels — by lock key
  (`family or cpath`) first, then by actual `cpath` within that lock — so
  eviction of any sibling column happens under the table's single family
  lock.
- Callers now call `touch()` N times per read (once per column, all under
  the already-held family lock) instead of once.
- `_usage()`'s `rglob("*")` will also count the small `.ctbl.json` manifest
  files toward pool disk usage — negligible, not filtered out.

Rejected alternatives:

- **The `.b2d` TreeStore design** (this plan's original choice): correct and
  workable, but duplicates an entire eviction/atime/locking subsystem
  (`del ts[key]`, msgpack atime sidecar, `dict_store.py`-level store-wide
  frame lock) that the NDArray path already has, battle-tested, for free.
  The O(members)-per-chunk-write ceiling that design needed a `ponytail:`
  caveat for doesn't exist here — a `.b2nd` sparse frame's `update_chunk`
  never had that cost, which is why plain NDArray peer caching never needed
  a workaround for it either.
- **Per-column `cache_lock(cpath)` with a fixed acquisition order across
  siblings** (the natural first idea once you notice the fan-out writes N
  files): rejected in favor of one family lock — it introduces a real
  deadlock hazard (two concurrent requests hitting the same table via
  different trigger columns could acquire each other's locks in opposite
  order) for no benefit.
- **Deriving the column→hash mapping from a fresh `info()` call every time,
  no manifest file**: works online (info is already in hand) but breaks the
  offline path, which by definition has no fresh `info()`, and can't
  distinguish an old (evictable) family from a schema that changed without
  knowing what was cached under the old schema.
- **`.b2e` (EmbedStore) over a sparse frame**: EmbedStore is an append-only
  byte pack — `__setitem__` appends whole serialized cframes
  (`embed_store.py:276-286`), `__delitem__` reclaims nothing (`:315-323`),
  and member boundaries don't align with backing chunks. No in-place
  update, no per-chunk eviction. (Kept from the original plan's rejection
  list — applies equally against the `.b2nd`-per-column design.)
- Caching into a `.b2z` is out by construction: zip stores are read-only
  shared + atomic-replace; `locking=True` is rejected for them.

## Changes

### 1. Shared row-range helper

Extract the CTable slice normalization at `server.py:711-731` verbatim into
`srv_utils.ctable_row_range(slice_, nrows) -> (start, stop)` (None/int/
negative wrap/clamp semantics); call it from the server branch; re-export via
`caterva2/services/providers.py` so c2cache clamps identically to the peer's
own branch.

### 2. CTable cache machinery (`c2cache/remote.py`)

- `ctable_column_cache_path(pool_dir, peer_id, remote_path, colname)` /
  `ctable_family_path(pool_dir, peer_id, remote_path)` — hashed paths per
  the layout above.
- `_ctable_fixed_dtypes(schema_dict) -> dict[name, dtype] | None` — compile
  via `blosc2.schema_compiler.schema_from_dict`; `None` if any column is
  list/varlen-scalar/dictionary/ndarray-spec (`blosc2.CTable._is_*_column`
  staticmethods). Internal blosc2 APIs: comment each import site, pinned to
  the `blosc2>=4.8.0` floor.
- `CTableColumnSource` — duck-typed `Proxy` source, one per column (see
  "Fan-out fill" above for its `aget_chunk`).
- `_open_ctable_family(pool_dir, peer, key, info, fixed_dtypes) ->
  dict[name, Proxy] | None` — manifest load/staleness-check/rebuild, then
  two-pass construction: build every `CTableColumnSource` + `Proxy` first,
  then wire each source's `.siblings` to the others. `None` when
  non-cacheable (`_ctable_fixed_dtypes` fails, `chunks` falsy, nrows == 0)
  or creation fails (degrade to pass-through, don't crash).
- `fetch_ctable_slice(adapter, key, info, start, stop) -> bytes` — the
  online path, family lock held by the caller: open/rebuild the family; if
  non-cacheable, pass-through (`t.slice(a, b).to_cframe()` over one
  `api/fetch` GET, aligned `start:stop` exactly); else pick a driver column,
  `await driver.afetch(slice(start, stop), max_concurrency=len(proxies))`,
  read every column locally, `touch()` each, `_synth_ctable_cframe(...)`.
- `_synth_ctable_cframe(schema_dict, cols, n) -> bytes` — EmbedStore with
  `/_meta` (vlmeta kind/version/schema json with `n_rows=n`), all-True
  `/_valid_rows`, `/_cols/<ctable_storage._column_name_to_relpath(name)>`
  per column; `.to_cframe()`.
- `read_ctable_cached_only(pool_dir, peer, key) -> (manifest, caches) |
  None` + `slice_ctable_cached(manifest, caches, start, stop) -> bytes |
  None` — offline: open each column `.b2nd` directly (no `Proxy`), check
  `slice_fully_cached` on every one before trusting a read, synthesize.
- `PeerCTableView(adapter, key, info)` — web-UI view object (see §5 below).
- `RemotePeerAdapter.get(key, info=None)` — accept pre-fetched info (skip
  the duplicate `_info` GET). `leaf_size`: add `or info.get("cbytes")` so
  CTable leaves get sizes in listings.

### 3. peercache generalizations (`c2cache/peercache.py`)

- `_gather_candidates` (`peercache.py:115-135`): peek the `_peer_src` vlmeta
  of each already-opened `.b2nd` for an optional `"family"` string; carry it
  in the candidate tuple (`None` for plain NDArray caches). No new glob
  pattern — CTable column caches are already `*.b2nd` files at the existing
  depth.
- `ensure_budget` (`peercache.py:91-113`): group two levels — by lock key
  (`family or cpath`) first, then by actual `cpath` within that lock — so
  eviction of any sibling column happens under the table's single family
  lock, never a per-column lock.
- Callers call `touch()` once per column proxy per read (all under the
  already-held family lock), instead of once per read as for NDArrays.
- `cache_lock`, `touch`, `_evict_from_cache`, `_uninit_chunk`,
  `_atime_file`, `_load_atimes`, `_usage` — unchanged.

### 4. Provider + server wiring

- `c2cache/provider.py fetch()` (`provider.py:127-167`): fetch `info` first
  (existing error mapping); `info.get("kind") == "ctable"` ⇒ row range via
  `providers.ctable_row_range`, family lock (`cache_lock(family_key)`
  keyed by `adapter.ctable_family_path(key)`) around
  `to_thread(fetch_ctable_slice, ...)`; OFFLINE_ERRORS ⇒ `mark_offline` +
  `read_ctable_cached_only`/`slice_ctable_cached` under the same lock or
  `ProviderUnavailable`; `ensure_budget()` outside the lock; return bytes.
  NDArray path unchanged except `adapter.get(key, info=info)`. The
  initial-`_info`-failure offline fallback picks by on-disk artifact
  (`adapter.cache_path(key)` `.b2nd` vs `adapter.ctable_family_path(key)`
  `.ctbl.json`).
- `server.py:628-633`: `if isinstance(data, bytes): return
  StreamingResponse(srv_utils.iterchunk(data), ...)` before the numpy wrap;
  update the `providers.py` fetch docstring (ndarray-like OR cframe bytes).
  `caterva2.Client.Table` then works unchanged.

### 5. Web-UI view wiring (htmx data-grid) — newly in scope

Peer CTables should also be *browsable* in the web UI (`POST
/htmx/path-view/{path}`, `server.py:2172-2306`), not just fetchable via the
API — this is a separate code path from item 4: `open_view()` currently
calls `adapter.get(key)` unconditionally (`provider.py:169-196`), which
still raises `NotAFetchableDataset` for CTables (its `"shape"/"schunk" in
info` gate is untouched — only `fetch()`'s CTable branch bypasses it).

- **`isinstance(arr, blosc2.CTable)` (`server.py:2257`) becomes a duck-type
  check.** `ViewHandle.array` is already typed `Any` (duck-typed,
  `providers.py:63`) precisely so provider-backed views don't need to be
  real blosc2 objects:
  ```python
  def _is_ctable_like(arr):
      return isinstance(arr, blosc2.CTable) or (
          hasattr(arr, "nrows") and hasattr(arr, "schema_dict") and hasattr(arr, "slice")
      )
  ```
- **`PeerCTableView` (`c2cache/remote.py`)**, built straight from `api/info`
  (`CTableMetadata` already has `nrows`/`schema_dict` —
  `caterva2/models.py:77-89` — no manifest read needed just to construct
  the view):
  ```python
  class PeerCTableView:
      def __init__(self, adapter, key, info):
          self._adapter, self._key, self._info = adapter, key, info
          self.nrows = info["nrows"]

      def schema_dict(self):
          return self._info["schema_dict"]

      def slice(self, start, stop):
          data = fetch_ctable_slice(self._adapter, self._key, self._info, start, stop)
          return blosc2.ctable_from_cframe(data)  # genuine local CTable, just this window
  ```
  `.nrows` is the *true full* row count (from `info`, independent of any
  window), so the existing pagination math at `server.py:2261-2266` needs no
  change. `.slice()` re-derives a small local `blosc2.CTable` per page
  window via `fetch_ctable_slice` — no new CTable assembly logic.
- **`open_view()` (`provider.py:169-196`)**: add a branch mirroring
  `fetch()`'s `info.get("kind") == "ctable"` check — fetch `info` first, and
  if it's a CTable, skip `adapter.get` and build `PeerCTableView(adapter,
  key, info)` instead; lock key becomes the CTable family path. `_Handle`
  carries enough (adapter/key/info) for `prefetch(window)` to branch to
  `fetch_ctable_slice` instead of `proxy.afetch`; the Proxy-specific
  `arr.src.aclose()`/`peercache.touch` cleanup no-ops on this branch
  (`fetch_ctable_slice` already touches internally).
- **Pagination**: the CTable render branch in `server.py` currently has *no*
  `handle.prefetch()` call (unlike the NDArray branch just below it at
  `:2353-2364`) — add one, using the `start`/`stop` already computed at
  `:2263-2266`, before `arr.slice(start, stop)` at `:2290`. Each page POST
  then only fetches/cache-hits its own row window (chunk-aligned inside
  `fetch_ctable_slice`), never the whole table.
- **Descending sort**: provably unreachable for provider-backed CTables —
  `server.py:2200-2203` already errors any `provider is not None` request
  with `filter or sortby` set, *before* `open_view` is called, so
  `sort_desc` and `provider is not None` can never both hold at `:2285`. No
  sort-desc handling needed here.
- **Error mapping**: reuse `open_view`'s existing exception taxonomy
  (`provider.py:174-184`) inside the new CTable `prefetch()` arm —
  `httpx.HTTPStatusError` → `ProviderRelayedStatus`,
  `remote.OFFLINE_ERRORS` → `mark_offline` + `ProviderUnavailable`; already
  caught by `server.py:2361-2364`'s `except providers.ProviderError`.
- **Pass-through (non-cacheable) tables**: `fetch_ctable_slice`'s
  milestone-0 pass-through arm returns the same cframe shape regardless of
  cacheability, so `PeerCTableView` works unchanged for those — just an
  HTTP round-trip per page instead of a cache hit.

## Tests

**`caterva2/tests/test_peers.py`** (existing two-subprocess pattern):

- Seed peer B's `@public` with a fixed-width CTable (int/float/fixed-str
  columns, ≥4 chunks) and one varlen-column table.
- `test_ctable_slice_fetch`: slice / whole / single-int fetch through peer A
  ⇒ `ctable_from_cframe` values equal source.
- `test_ctable_cache_hit_and_layout`: fetch twice; N `<col>.b2nd` proxy
  caches + one `.ctbl.json` family manifest exist for the table, each with
  filled chunks and an atime sidecar; second response equal.
- `test_ctable_offline_reads_cached_range` (own server pair, like
  `test_peer_offline_tolerated`): fetch a range, SIGKILL B, refetch the same
  range ⇒ 200 + equal values; disjoint uncached range ⇒ 503.
- `test_ctable_varlen_table_passthrough`: fetch works, no `.b2nd`/`.ctbl.json`
  created.
- Extend the tiny-quota concurrency test with CTable fetches (no crashes,
  valid responses — the race detector); include concurrent fetches on
  *different* driver columns of the same table to exercise the family-lock
  no-deadlock property.
- `test_ctable_web_view`: `POST /htmx/path-view/{path}` for a peer CTable
  renders rows (200, not 400); page through with `index`/`sizes` form params
  and confirm each page reflects the right row window; confirm
  filter/sortby still error as they already do for peer NDArrays.

**Units** (`test_ctable.py` or alongside): `ctable_row_range` edge cases
(None/int/negative/clamp/stop==0); `_synth_ctable_cframe` round-trip through
`ctable_from_cframe` (values, dtypes, nrows); `CTableColumnSource.aget_chunk`
fan-out fills sibling `Proxy` caches from one HTTP call (mock/count the
`httpx` calls).

## Verification

1. REPL-check before coding (no guessing): build a `CTableColumnSource`-shaped
   duck object by hand, confirm `Proxy(source)` accepts it without needing
   `C2Array`; confirm `schunk.update_chunk` on a sibling's cache reflects in
   a fresh `blosc2.open` of that file; confirm eviction (`update_chunk` to
   UNINIT) on one column's file doesn't corrupt a concurrently-open
   sibling's file; synth cframe reopens via `ctable_from_cframe` with
   correct nrows; null sentinels survive the numpy round-trip.
2. `pytest caterva2/tests/test_peers.py -v` several times in a row, then the
   full suite.
3. Manual two-peer run: fetch CTable slices through A, kill B, refetch the
   cached range offline; inspect `pool_dir` for the expected `.b2nd` +
   `.ctbl.json` files.
4. Manual web-UI check: browse a peer CTable in the htmx view, page through
   rows (confirms `prefetch()`/`slice()` windowing), confirm filter/sort
   controls are absent/blocked as they already are for peer NDArrays.

## Sequencing

1 (helper, no behavior change) → milestone 0 (pass-through: gate branch +
bytes streaming — peer CTables usable online) → 2+3 (cache machinery) →
4 (full wiring, offline) → 5 (web-UI view wiring) → tests alongside each
step.

## Out of scope

- Whole-file `api/download` for peer paths (stays 404).
- DictStore recognition server-side (flat `.b2z` stores stay opaque leaves).
- Per-peer `cache_quota` enforcement (still parsed, still unused).
- Auth, dynamic mounts, batch `api/chunks` — per the deferred list in
  `plans/caterva3-remote-peer-mounts.md`.
