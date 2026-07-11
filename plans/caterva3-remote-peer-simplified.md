# Remote peer `.b2z` support, simplified: structured-dtype CTable cache + container navigation/mount UX

**Status (2026-07-11): IMPLEMENTED.** All of Part 1 + Part 2 landed on
`c2cache-monorepo`, tests in `caterva2/tests/test_peers.py` (25 pass, full
suite green). Three side-fixes shipped with it: `peercache._usage()` made
tolerant of files vanishing mid-scan (lock-free rglob raced `touch()`'s
atomic replace → 500 under load), `peercache.touch()` switched to unique
mkstemp tmp names (a cancelled `to_thread(touch)` orphan could collide on
the fixed name), and `client.Table.slice()` now passes the Table object to
`get_slice` (nested tables, e.g. `tree.b2z/dir/tbl`, were misread as
NDArray/SChunk — a pre-existing bug, local paths included). Replaces
`plans/ctable-remote-peer-mounts.md` (the per-column-cache revision of the
same day). Two changes relative to that plan:

1. **Simplification** — the CTable cache becomes **one sparse `.b2nd` with a
   compound (structured) numpy dtype per table**, instead of N per-column
   `Proxy` caches + a `.ctbl.json` family manifest + family locking + fan-out
   sibling fill. This collapses the CTable path into a one-source variant of
   the existing NDArray peer-cache path; the peercache changes (§3 of the old
   plan) are deleted entirely.
2. **Scope addition** — remote `.b2z` *navigation*: deep listing into peer
   TreeStore `.b2z` files (CLI), and the clickable plug-icon mount UX for
   peer paths in the web UI, mirroring local `.b2z` behavior. The old plan's
   premise "TreeStore peer mounting is already done" holds only at the data
   level (explicit member paths fetch fine); browsing/mounting them from A
   does not work today.

## Goal (restated)

Browse `.b2z` files living in remote peers from both the CLI (`cat2-client`)
and the web UI, mirroring local `.b2z` support:

- **CTables are accessible directly** (no mount): a standalone CTable `.b2z`
  in a peer root, or a CTable leaf inside a TreeStore, renders/fetches like a
  dataset.
- **TreeStore `.b2z` are mountable**: the plug icon shows on the remote row,
  mounting expands the container's leaves under a virtual root — exactly the
  local localStorage-mount UX, applied to `@peer/...` paths.

## Part 1 — CTable cache: one structured-dtype `.b2nd` per table

### Why the per-column design is over-built

The per-column plan reuses the NDArray cache machinery *mostly*, and pays for
the residue: a `.ctbl.json` family manifest, a family-lock scheme layered on
`cache_lock`, two-pass sibling wiring in `CTableColumnSource`, two-level
grouping in `ensure_budget`, N× `touch()` per read, and N-way offline
`slice_fully_cached` checks.

The columnar layout buys nothing in this design because **there is no
per-column access path anywhere**:

- The fetch unit — the peer's `api/fetch` CTable branch — returns a cframe
  with **all** columns for a row range.
- The synthesized response cframe contains all columns.
- The web-UI grid renders all columns.
- Even per-column eviction granularity is illusory: evicting one column's
  chunk saves bytes, but refetching that row range re-downloads every column
  anyway (one `api/fetch` call = all columns). Eviction at row-chunk
  granularity across all columns matches the actual refetch cost model.

### Verified (2026-07-10, installed blosc2 4.8.1.dev0)

A sparse structured-dtype frame works end-to-end through the exact
primitives the NDArray cache already uses:

```python
dt = np.dtype([("a", "i8"), ("b", "f4"), ("s", "S8")])
arr = blosc2.empty(
    (100,), dtype=dt, chunks=(16,), urlpath=p, contiguous=False, mode="w"
)  # sparse cache frame: OK
packed = blosc2.asarray(src[:16], chunks=(16,))  # pack one chunk: OK
arr.schunk.update_chunk(0, packed.schunk.get_chunk(0))  # fill: OK
re = blosc2.open(p)  # reopen: OK
re[0:3]
re[0:3]["a"]  # row + field reads: OK
```

So `open_cached_proxy` (`c2cache/remote.py:131-164`) works **verbatim** —
`blosc2.empty(shape, dtype, chunks, ..., contiguous=False)` accepts the
compound dtype, `Proxy` chunks are dtype-agnostic bytes, and
`slice_fully_cached` / `get_slice_nchunks` / `update_chunk` (eviction) are
all chunk-index-level, untouched.

### Design

**Cache artifact**: `pool_dir/<peer.name>/<hash>.b2nd`, where the hash is the
*existing* `cache_path(pool_dir, peer_id, remote_path)` recipe
(`remote.py:121-128`) — one artifact per key, no CTable-specific prefix, no
collision concern. The frame is a sparse NDArray:

- `shape = (nrows,)` (from `CTableMetadata.nrows`)
- `dtype = np.dtype([(name, dt) for name, dt in fixed_dtypes.items()])`
  in schema order, from `_ctable_fixed_dtypes(schema_dict)` (compile via
  `blosc2.schema_compiler.schema_from_dict`; `None` ⇒ non-cacheable)
- `chunks = info["chunks"]` (the table's shared 1-D `(rows_per_chunk,)` grid)
- `blocks`: let blosc2 default (per-column blocks don't map to a compound
  row; irrelevant for a cache)

**Vlmeta**: extend the existing `_peer_src` JSON with
`kind: "ctable"`, `schema` (the `schema_dict`, needed offline by
`_synth_ctable_cframe`), alongside the existing `path`/`mtime`. The schema
travels *inside* the cache — the `.ctbl.json` manifest file disappears, and
staleness/rebuild is the existing per-file mtime check in
`open_cached_proxy`, unchanged.

**Source**: one duck-typed `CTableSource` per **table** (not per column; not
a `C2Array` subclass — `C2Array.__init__` fires a sync HTTP `info()`).
Exposes `shape/chunks/blocks/dtype/cparams/aget_chunk` per `Proxy`'s
duck-typing contract:

```python
async def aget_chunk(self, nchunk):
    start = nchunk * self.chunks[0]
    stop = min(start + self.chunks[0], self.shape[0])
    resp = await self._aclient.get(
        f"{self.urlbase}api/fetch/{table_path}", params={"slice_": f"{start}:{stop}"}
    )
    resp.raise_for_status()
    table = blosc2.ctable_from_cframe(resp.content)
    rows = np.zeros(self.chunks[0], dtype=self.dtype)  # zero-pad trailing chunk
    for name in self.dtype.names:
        rows[name][: stop - start] = table[name][:]
    # keep `packed` referenced while reading the chunk: NDArray.schunk does
    # NOT keep its parent alive, so the one-liner
    # `blosc2.asarray(...).schunk.get_chunk(0)` is a use-after-free that
    # nondeterministically returns b"" or raises RuntimeError (verified on
    # 4.8.0 and 4.8.1.dev0, 2026-07-11; upstream fix pending).
    packed = blosc2.asarray(rows, chunks=self.chunks)
    return packed.schunk.get_chunk(0)
```

No siblings, no driver column, no two-pass wiring, no fan-out writes into
other frames. `Proxy.afetch(slice(start, stop), max_concurrency=...)` must
still pass `max_concurrency` explicitly (default is 1 for non-`C2Array`
sources, `proxy.py:434`).

**Locking / eviction / touch**: *exactly* the NDArray path.
`cache_lock(cpath)` per artifact; `touch()` once per read;
`peercache.py` **completely unchanged** — no family concept, no vlmeta peek
in `_gather_candidates`, no two-level grouping in `ensure_budget`, no new
glob (the artifact is already a `*/*.b2nd`).

**Online flow** (`fetch_ctable_slice`, under `cache_lock(cpath)`):
1. Open/rebuild the cache via `open_cached_proxy(CTableSource(...), cpath,
   mtime)` — one call, no new parameters.
2. Non-cacheable (varlen/list/dictionary/ndarray-spec columns, `chunks`
   falsy, invalid numpy field names, oversized row-chunks — see guard below)
   ⇒ **pass-through** (milestone 0, unchanged from the old plan): relay
   `api/fetch`'s cframe for the aligned `start:stop`.
3. Else `await proxy.afetch(slice(start, stop), max_concurrency=k)`;
   `rows = proxy[start:stop]` (one structured read);
   `touch(proxy, ...)` once.
4. `cols = {name: rows[name] for name in dtype.names}` →
   `_synth_ctable_cframe(schema_dict, cols, stop - start)` (unchanged from
   the old plan: EmbedStore with `/_meta`, all-True `/_valid_rows`,
   `/_cols/<relpath>`; `.to_cframe()`).

**Offline flow**: *exactly* today's NDArray discipline
(`get_cached_only` + `slice_fully_cached`, `remote.py:167-178, 256-264`),
then unpack fields → synth. One frame, one check. The old plan's
"initial-`_info`-failure fallback picks by on-disk artifact (`.b2nd` vs
`.ctbl.json`)" discriminator disappears: open the one cache, read
`_peer_src["kind"]`.

**Chunk-size guard**: compound chunk bytes = `rows_per_chunk ×
sum(itemsizes)`, which a wide table can inflate. If it exceeds a threshold
(e.g. 512 MiB, comfortably under blosc2's 2 GiB chunk cap), treat as
non-cacheable → pass-through. One line, reuses the milestone-0 arm.

**Trade-offs accepted**:
- Row-interleaved mixed dtypes compress worse than columnar. Acceptable for
  a quota-evicted cache; fidelity is unaffected, disk cost is bounded by the
  budget/eviction that already exists.
- Column names that aren't valid numpy structured field names ⇒
  non-cacheable, pass-through (new caveat, replaces the old plan's absence
  of one — the per-column design had a TreeStore-key caveat in its first
  revision for the same reason).

**Unchanged from the old plan** (all survive verbatim):
- Milestone 0 pass-through fetch (permanent fallback for non-cacheable).
- §1 `srv_utils.ctable_row_range` extraction from `server.py:711-731`,
  re-exported via `providers.py`.
- `_synth_ctable_cframe` (schema + per-column numpy arrays → cframe).
- Server streaming branch: `if isinstance(data, bytes): return
  StreamingResponse(srv_utils.iterchunk(data), ...)` before the numpy wrap
  (`server.py:628-633`); `caterva2.Client.Table` works unchanged.
- §5 web-UI view wiring: `_is_ctable_like` duck check at `server.py:2257`,
  `PeerCTableView` (built from `api/info`'s `nrows`/`schema_dict`, `.slice()`
  → `fetch_ctable_slice` → `ctable_from_cframe`), the `open_view()` CTable
  branch, the missing `handle.prefetch()` in the CTable render branch, the
  sort-desc unreachability argument (`server.py:2200-2203`), the error
  mapping. Item 5 is entirely independent of the cache layout.
- `RemotePeerAdapter.get(key, info=None)` accepting pre-fetched info;
  `leaf_size` adding `or info.get("cbytes")`.

**Deleted from the old plan**:
- `.ctbl.json` family manifest (+ atomic-write idiom, + `_usage()` note).
- Family locking (`family:` parameter on `open_cached_proxy`, family-keyed
  `cache_lock`, the per-column-lock deadlock discussion).
- `CTableColumnSource` sibling wiring / two-pass construction / fan-out
  `update_chunk` into sibling frames / driver-column selection.
- All §3 peercache changes (`_gather_candidates` vlmeta peek,
  `ensure_budget` two-level grouping, N× `touch`).
- Per-column offline checks; the two-artifact offline discriminator.
- `ctable_column_cache_path` / `ctable_family_path` (plain `cache_path`
  suffices).

## Part 2 — Remote `.b2z` navigation + mount UX (new scope)

### Gap analysis (verified in code, 2026-07-10)

The data path for members of a peer `.b2z` **already works**:
`RemotePeerAdapter.get` detects container-member keys
(`split_container_path`) and uses the chunk-aligned `api/fetch` fallback
(`remote.py:242-254`); proxied `api/info` resolves member paths on B. What
doesn't work is *discovering* those members from A:

- **CLI deep listing.** `C2CacheProvider.list()` (`c2cache/provider.py:
  83-99`) filters the peer's **flat catalog** (B's `api/list/@public`
  directory walk, which lists `tree.b2z` as one opaque entry). So
  `cat2 list @peerb/tree.b2z` returns just the filename. B's own `get_list`
  already deep-lists container paths (`server.py:409-426`) — A just never
  asks it.
- **Web UI plug icon.** The provider-roots loop in `htmx_path_list` hardcodes
  `"mountable": False` (`server.py:1887`).
- **Web UI mounted-roots state.** `htmx_root_list` validates mounted paths
  with `get_rootdir_or_none(prefix)` (`server.py:1739-1743`), which returns
  `None` for `@peerb/...` — a peer mount is silently dropped from the
  localStorage round-trip.
- **Web UI virtual-root expansion.** The mounted-container loop
  (`server.py:1845-1868`) resolves a local abspath + `open_container`; peer
  roots skip. (The "add current path" fallback at `:1891-1921` is also
  local-only — minor, same treatment.)

### Changes

1. **Deep-list primitive.** In `C2CacheProvider.list(root, prefix)`: when
   `prefix` is or descends into a `.b2z` (`split_container_path`), forward
   the call to B — one `api/list` GET on
   `@public/<prefix>` via `client_for(...)` (B deep-lists container paths
   natively) — instead of filtering the flat catalog. Same error mapping as
   `info()` (`HTTPStatusError` → relayed status, `OFFLINE_ERRORS` →
   `mark_offline` + `ProviderUnavailable`). This alone fixes the CLI.
2. **Kind detection, zero extra HTTP.** `leaf_size()` already does one
   memoized `api/info` per row (`remote.py:206-214`). Memoize the *kind*
   from the same response: B's info for a bare TreeStore `.b2z` is a
   `models.Directory` (`srv_utils.read_metadata`, `srv_utils.py:337-338`);
   for a standalone CTable `.b2z` it is `CTableMetadata`. Expose e.g.
   `adapter.leaf_kind(key)` → `"container" | "ctable" | "dataset"`, cleared
   with each catalog refresh like `sizes`.
3. **Plug icon on peer rows.** Provider-roots loop in `htmx_path_list`
   (`server.py:1870-1889`): `mountable = key.endswith(".b2z") and
   leaf_kind == "container"`. CTable `.b2z` rows stay plain (direct view) —
   matching the local rule where `open_container` returns `None` for a
   non-TreeStore `.b2z` (`srv_utils.py:173-188`).
4. **Accept peer mounts in `htmx_root_list`** (`server.py:1739-1743`):
   a mounted path whose first segment is a provider root
   (`providers.provider_for(prefix) is not None`) is kept (optionally
   validated as `"container"` via the memoized kind; stale/bad entries must
   skip silently, same rule as the local comment at `:1856-1858`).
5. **Expand mounted peer containers in `htmx_path_list`.** In the
   virtual-roots loop (`server.py:1845-1868`), branch when
   `proot.parts[0]` is a provider root: list members via the deep-list
   primitive (1), add a row per leaf (`f"{root}{key}"`), size via
   `adapter.leaf_size` (memoized; `None`/0 tolerated). CTable leaves inside
   the TreeStore list as single leaves already (hidden object roots on B's
   side), so they show as plain rows and open directly — satisfying the
   "CTables need no mount" rule inside mounted containers too.
6. **Nothing new for viewing/fetching members**: NDArray members go through
   the existing `api/fetch` fallback path today; CTable members (standalone
   or nested) go through Part 1. `api/info` on member paths already proxies.

## Tests

**`caterva2/tests/test_peers.py`** (existing two-subprocess pattern):

- Seed peer B's `@public` with: a fixed-width CTable `.b2z` (int/float/
  fixed-str, ≥4 chunks), a varlen-column table, and a TreeStore `.b2z`
  containing NDArray leaves + a nested CTable.
- `test_ctable_slice_fetch`: slice / whole / single-int fetch through A ⇒
  `ctable_from_cframe` values equal source.
- `test_ctable_cache_hit_and_layout`: fetch twice ⇒ exactly **one**
  `<hash>.b2nd` (structured dtype, `_peer_src.kind == "ctable"`, schema in
  vlmeta) + one atime sidecar; second response equal; no `.ctbl.json`.
- `test_ctable_offline_reads_cached_range`: fetch a range, SIGKILL B,
  refetch same range ⇒ 200 + equal; disjoint uncached range ⇒ 503.
- `test_ctable_varlen_table_passthrough`: fetch works, no cache artifact.
- Extend the tiny-quota concurrency test with CTable fetches (single lock,
  single artifact — the old family-deadlock test is moot).
- `test_peer_container_deep_list`: `api/list/@peerb/tree.b2z` returns member
  names (CLI path).
- `test_peer_container_mount_ui`: `htmx_path_list` with the peer root shows
  the TreeStore row `mountable: true` and the CTable `.b2z` row
  `mountable: false`; with `roots=@peerb/tree.b2z` it lists member rows;
  `htmx_root_list` keeps the mounted peer path.
- `test_ctable_web_view`: `POST /htmx/path-view/{path}` for a standalone
  peer CTable *and* a nested one renders rows; paging via `index`/`sizes`
  reflects the right window; filter/sortby still error.

**Units**: `ctable_row_range` edges; `_synth_ctable_cframe` round-trip;
`CTableSource.aget_chunk` packs a correct structured chunk from one HTTP
call (mock/count httpx calls), incl. zero-padded trailing chunk; structured
round-trip preserves null sentinels; non-cacheable detection (varlen,
`chunks=None`, bad field name, oversized chunk).

## Verification

1. REPL-check before coding: ~~structured sparse frame + update_chunk +
   reopen + field reads~~ **done 2026-07-10** (blosc2 4.8.1.dev0, see
   Part 1). **Re-verified 2026-07-11 on a clean `blosc2==4.8.0` install**
   (no version bump needed): all of the above, plus the full
   `open_cached_proxy` idiom (`blosc2.empty` + vlmeta + `Proxy(duck_source,
   _cache=...)` + `afetch(max_concurrency=)`), duck-typed source accepted,
   reopen + `get_slice_nchunks` + `iterchunks_info` specials on the
   structured frame, `schema_from_dict(t.schema_dict())`,
   `to_cframe`/`ctable_from_cframe` round-trip, `EmbedStore`. Remaining:
   null sentinels through the structured round-trip; synth cframe reopens
   via `ctable_from_cframe` with correct nrows/dtypes.
2. `pytest caterva2/tests/test_peers.py -v` repeatedly, then the full suite.
3. Manual two-peer run: fetch CTable slices through A; kill B; refetch the
   cached range offline; inspect `pool_dir` (one `.b2nd` per table).
4. Manual web-UI: plug icon on the remote TreeStore row; mount it; browse
   leaves; open the nested CTable directly; page through rows; unmount.
   Confirm the CTable `.b2z` row has no plug icon and opens as a table.

## Sequencing

1. §1 helper (`ctable_row_range`, no behavior change)
2. Milestone 0 pass-through (gate branch + bytes streaming) — peer CTables
   usable online, CLI + `Client.Table`
3. Part 2 items 1–2 (deep list + kind memoization) — CLI `.b2z` navigation
4. Part 1 cache (structured `.b2nd`, offline path)
5. Old-plan §5 web-UI CTable view (`PeerCTableView` etc.)
6. Part 2 items 3–5 (plug icon, mounted-roots acceptance, expansion loop)
7. Tests alongside each step

## Long-term direction (recorded 2026-07-11, not in scope here)

The eventual goal — full CTable functionality (`sort_by`, `group_by`,
indexing, filters) over a remote table, à la `Proxy`/`C2Array` — does **not**
run through this cache, so the structured-dtype layout doesn't constrain it.
The cache is a disposable relay artifact on A with no external contract
(wire format is the synthesized cframe; eviction already rebuilds it).

The long-term design lives in blosc2, at the existing `TableStorage` seam:
a `RemoteTableStorage` whose `open_column(name)` returns
`Proxy(C2Array(column_path))` — CTable's expensive ops are already columnar
and lazy (`sort_by(view=True)` materializes only the key columns), so
`CTable` works unchanged on top, with per-column chunk-level fetch + local
Proxy caching. A thin `C2CTable` wrapper for ergonomics at most. What it
needs server-side is per-column addressability (`api/info`/`api/fetch` on
column virtual paths, or a `field=` param — CTables are opaque object roots
today), and optionally index-anchor exposure. Per-column paths routed
through A would hit the existing NDArray member-fallback cache path
(`remote.py:242-254`), independent of this plan's CTable cache. Worst case
is bounded double-caching on A (row-structured + per-column), covered by the
existing quota; the structured cache is the disposable one.

## Out of scope (unchanged from the old plan)

- Whole-file `api/download` for peer paths (stays 404).
- DictStore recognition server-side (flat `.b2z` stores stay opaque leaves).
- Per-peer `cache_quota` enforcement (parsed, unused).
- Auth, dynamic mounts, batch `api/chunks` — per the deferred list in
  `plans/caterva3-remote-peer-mounts.md`.
