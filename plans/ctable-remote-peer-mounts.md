# CTable support for remote peer mounts: fetch + `.b2d` column cache

**Status (2026-07-10): planned, not implemented.** Closes the last gap of
`plans/caterva3-remote-peer-mounts.md`: peer CTables are browsable but not
fetchable. TreeStore peer mounting is already done (NDArray leaves inside a
`.b2z` cache chunk-granularly via the `api/fetch` fallback); this plan makes
CTable leaves — standalone `.b2z` tables and CTables inside TreeStores —
fetchable, cached under the peercache budget, and readable offline.

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

## Cache container: a `.b2d` TreeStore per table

One decision drove this design: cache into a **standard blosc2 container**,
not another ad-hoc layout. `pool_dir/<peer.name>/<sha256(peer_id:path)[:32]>.b2d`
(same hashing recipe as `cache_path`, `remote.py:121-128` — peer-controlled
names are hashed, never spliced), opened as
`blosc2.TreeStore(cdir, mode="a", locking=True, threshold=0)`:

- **Metadata in vlmeta** (peer path/mtime, schema_dict, nrows, chunks) — no
  sidecar manifest. Staleness: vlmeta mtime vs `info["mtime"]`; mismatch or
  corruption ⇒ `shutil.rmtree(cdir)` + rebuild (mirrors `open_cached_proxy`,
  `remote.py:131-164`).
- **One member per (column, chunk)**: key `/c/<colname>/<nchunk>`, a small
  contiguous 1-D NDArray holding that chunk's rows (the last one shorter).
  Column names must pass TreeStore key validation; tables with names that
  don't are non-cacheable (pass-through), not a crash.
- **Fill** = `ts[key] = chunk_rows` (threshold=0 ⇒ external file per member).
  **Eviction = `del ts[key]`** — deletes the member's file, space actually
  reclaimed (`dict_store.py:626-639`). "Fully cached" = key presence; none of
  the UNINIT/`iterchunks_info` machinery applies here.
- **Atimes**: one sidecar `<cdir>.atime.msgpack` per family (dict
  key→atime, atomic tmp+`os.replace`), read by the evictor like the existing
  `.npy` sidecars.
- **Concurrency**: blosc2's store-wide frame lock (the `embed.b2e` handle
  doubles as the store lock, `dict_store.py:377-393`, requires
  `locking=True` — repo discipline: every open/create site) + ONE asyncio
  lock per family for fetch→read→touch vs evict atomicity (same discipline
  as `plans/peercache-locking.md`, no multi-lock ordering).
- Known ceiling (mark with a `ponytail:` comment): each member set/del
  rewrites the whole msgpack key map in `embed.b2e` — O(members) per chunk
  write. Upgrade path if it bites on huge tables: coarser members, one per
  (column, fetch-tile of k chunks).

Rejected alternatives:

- **Sparse `.b2t/` dir + `table.json`**: maximal reuse of the existing
  UNINIT evictor, but an ad-hoc format variant — the reason `.b2d` won.
- **`.b2e` (EmbedStore) over a sparse frame**: EmbedStore is an append-only
  byte pack — `__setitem__` appends whole serialized cframes
  (`embed_store.py:276-286`), `__delitem__` reclaims nothing (`:315-323`),
  and member boundaries don't align with backing chunks. No in-place update,
  no per-chunk eviction.
- Caching into a `.b2z` is out by construction: zip stores are read-only
  shared + atomic-replace; `locking=True` is rejected for them.

## Changes

### 1. Shared row-range helper

Extract the CTable slice normalization at `server.py:711-731` verbatim into
`srv_utils.ctable_row_range(slice_, nrows) -> (start, stop)` (None/int/
negative wrap/clamp semantics); call it from the server branch; re-export via
`caterva2/services/providers.py` so c2cache clamps identically to the peer's
own branch.

### 2. CTable cache machinery (`c2cache/remote.py`, sync, run via to_thread)

- `ctable_cache_dir(pool_dir, peer_id, remote_path)` — `.b2d` path per the
  layout above; `RemotePeerAdapter.ctable_cache_dir(key)` companion.
- `_ctable_fixed_dtypes(schema_dict) -> dict[name, dtype] | None` — compile
  via `blosc2.schema_compiler.schema_from_dict`; `None` if any column is
  list/varlen-scalar/dictionary/ndarray-spec (`blosc2.CTable._is_*_column`
  staticmethods). Internal blosc2 APIs: comment each import site, pinned to
  the `blosc2>=4.8.0` floor.
- `open_ctable_store(cdir, info) -> TreeStore | None` — `None` when
  non-cacheable (`_ctable_fixed_dtypes` fails, `chunks` falsy, nrows == 0,
  bad column key names); staleness/rebuild per the layout above; creation
  failure ⇒ `None` (degrade to pass-through, don't crash).
- `fetch_ctable_slice(adapter, key, info, start, stop) -> bytes` — the
  online path, family lock held by the caller:
  1. Cache hit: all `(column, chunk)` keys for the range present ⇒ read,
     touch, synthesize, no HTTP.
  2. Else GET `api/fetch/{path}?slice_=a:b` with `a,b` = the range aligned
     to `rows_per_chunk` (exact `start:stop` when non-cacheable), via a
     module-level shared `httpx.Client`; `ctable_from_cframe`.
  3. If cacheable and the fetched frame has no computed columns: store each
     column's chunks as members, touch, serve from the store (same builder
     as the hit path).
  4. Else: return `t.slice(start - a, stop - a).to_cframe()` pass-through.
- `_synth_ctable_cframe(schema_dict, cols, n) -> bytes` — EmbedStore with
  `/_meta` (vlmeta kind/version/schema json with `n_rows=n`), all-True
  `/_valid_rows`, `/_cols/<ctable_storage._column_name_to_relpath(name)>`
  per column; `.to_cframe()`.
- `read_ctable_cached_only(cdir, start, stop) -> bytes | None` — offline:
  open the store read-only; every touched key present ⇒ read + synthesize,
  else `None`.
- `RemotePeerAdapter.get(key, info=None)` — accept pre-fetched info (skip
  the duplicate `_info` GET). `leaf_size`: add `or info.get("cbytes")` so
  CTable leaves get sizes in listings.

### 3. peercache generalizations (`c2cache/peercache.py`)

- `touch()` grows a family variant (update the msgpack atime dict for a set
  of member keys) alongside the existing per-chunk npy one.
- `cache_lock` keying: the CTable path locks on the family dir path;
  `ensure_budget` maps a `.b2d` candidate to the same key. No `lock_key`
  gymnastics needed if candidates carry their family path explicitly.
- `_gather_candidates` learns a second source: `pool_dir.glob("*/*.b2d")`
  families ⇒ candidates `(family, member_key, atime, size)` (size via the
  member file's stat through the store's map; atime from the sidecar,
  missing atime sorts oldest, as today). Eviction groups by family and
  `del ts[key]` under the family lock; `_usage()` already rglobs everything.

### 4. Provider + server wiring

- `c2cache/provider.py fetch()` (`provider.py:127-167`): fetch `info` first
  (existing error mapping); `info.get("kind") == "ctable"` ⇒ row range via
  `providers.ctable_row_range`, family lock around
  `to_thread(fetch_ctable_slice, ...)`; OFFLINE_ERRORS ⇒ `mark_offline` +
  `read_ctable_cached_only` under the same lock or `ProviderUnavailable`;
  `ensure_budget()` outside the lock; return bytes. NDArray path unchanged
  except `adapter.get(key, info=info)`. The initial-`_info`-failure offline
  fallback picks by on-disk artifact (`<hash>.b2d` vs `<hash>.b2nd`).
- `server.py:628-633`: `if isinstance(data, bytes): return
  StreamingResponse(srv_utils.iterchunk(data), ...)` before the numpy wrap;
  update the `providers.py` fetch docstring (ndarray-like OR cframe bytes).
  `caterva2.Client.Table` then works unchanged.
- `open_view()` untouched: peer CTables in the htmx data view keep the 400
  (out of scope; note in code).

## Tests

**`caterva2/tests/test_peers.py`** (existing two-subprocess pattern):

- Seed peer B's `@public` with a fixed-width CTable (int/float/fixed-str
  columns, ≥4 chunks) and one varlen-column table.
- `test_ctable_slice_fetch`: slice / whole / single-int fetch through peer A
  ⇒ `ctable_from_cframe` values equal source.
- `test_ctable_cache_hit_and_layout`: fetch twice; `.b2d` family exists with
  per-(column, chunk) members and the atime sidecar; second response equal.
- `test_ctable_offline_reads_cached_range` (own server pair, like
  `test_peer_offline_tolerated`): fetch a range, SIGKILL B, refetch the same
  range ⇒ 200 + equal values; disjoint uncached range ⇒ 503.
- `test_ctable_varlen_table_passthrough`: fetch works, no `.b2d` created.
- Extend the tiny-quota concurrency test with CTable fetches (no crashes,
  valid responses — the race detector).

**Units** (`test_ctable.py` or alongside): `ctable_row_range` edge cases
(None/int/negative/clamp/stop==0); `_synth_ctable_cframe` round-trip through
`ctable_from_cframe` (values, dtypes, nrows).

## Verification

1. REPL-check before coding (no guessing): `.b2d` TreeStore set/del/vlmeta
   round-trip with `locking=True` and fixed-str dtypes; `del` removes the
   member file; synth cframe reopens via `ctable_from_cframe` with correct
   nrows; null sentinels survive the numpy round-trip.
2. `pytest caterva2/tests/test_peers.py -v` several times in a row, then the
   full suite.
3. Manual two-peer run: fetch CTable slices through A, kill B, refetch the
   cached range offline; check the `.b2d` browses with `blosc2.open`.

## Sequencing

1 (helper, no behavior change) → milestone 0 (pass-through: gate branch +
bytes streaming — peer CTables usable online) → 2+3 (cache machinery) →
4 (full wiring, offline) → tests alongside each step.

## Out of scope

- Whole-file `api/download` for peer paths (stays 404).
- DictStore recognition server-side (flat `.b2z` stores stay opaque leaves).
- Per-peer `cache_quota` enforcement (still parsed, still unused).
- htmx data view for peer CTables (stays 400).
- Auth, dynamic mounts, batch `api/chunks` — per the deferred list in
  `plans/caterva3-remote-peer-mounts.md`.
