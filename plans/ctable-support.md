# Caterva2 CTable support plan

> **Status (2026-07-01): implemented, committed, and hardened.** All MVP work in
> this plan is done — see "Implementation status" near the end for the as-built
> summary (including deviations from the sketches below and post-review fixes).
> The design sections that follow are kept as the rationale/record; where a
> sketch and the shipped code differ, the code wins.

## Revision 2026-07-01

Four decisions supersede the original text below; where a later section still
reads the old way, this block wins:

1. **Whole-table `/api/fetch` must not short-circuit to `FileResponse`.** A whole
   `.b2nd` file *is* a cframe, so the existing `FileResponse` short-circuit
   decodes fine on the client. A whole `.b2z` file is a **zip** (`PK\x03\x04`),
   which is *not* a cframe — the client's cframe decoder raises
   `RuntimeError: Could not get the schunk from the cframe`. Reproduced against
   the committed code: `table[:]` (and any `slice` with `stop >= nrows`) fails.
   Fix: exclude `CTable` from the whole short-circuit so whole tables also go
   through `to_cframe()`. See "`/api/fetch` behavior".

2. **Client decode dispatches on known kind, not trial-and-except.** The
   committed `_fetch_data` attempts `ctable_from_cframe` first and catches
   `ValueError`, so every (common) array fetch pays a failed CTable parse, and
   the chain is coupled to which exact exception each decoder raises (that
   coupling is why the bug in #1 propagates uncaught). The client already knows
   the kind (`Table` vs `Dataset`, `meta["kind"]`); dispatch on it.

3. **Remove the JSON `/api/table` row-window endpoint entirely — not deferred,
   dropped.** It is a lighter-weight *duplicate of the slice fetch* with no
   consumer: the Python client already yields rows off the cframe
   (`[tuple(row) for row in data[:]]`), so CLI `show` reuses it; the web UI
   renders **server-side** in `htmx_path_view` from the live `CTable`; and
   JupyterLite is Pyodide, so it has blosc2 and decodes cframes directly. The
   only consumers it would serve — a non-Pyodide JS client or `curl` eyeballing
   — are hypothetical, and if one appears the endpoint is ~20 trivial lines. A
   specced-but-deferred endpoint is an attractive nuisance (invites a second
   row-preview path), so it is removed with a short "rejected alternative" note
   rather than parked.

4. **Transport is orthogonal to operation; table ops ride two homes, neither is
   `/api/table`.** `where`/`sort`/`group_by` all *produce a `CTable`*, which the
   cframe path already serializes — so operations need a way to be *expressed in
   the request*, not a JSON transport. Split by what the op does to the schema:
   - **Schema-preserving** (`where`, `sort`) → `filter=`/`sortby=` params on
     `/api/fetch`, cframe out (mirrors the array `get_filtered_array` path).
     Near-term. Enabled now that the lazy-view `to_cframe()` copy() bug is fixed.
   - **Schema-changing** (`group_by`, `aggregate`, `join`, `describe`) → a
     future `POST /api/query/{path}` with a structured query body, cframe
     response. Compute-not-fetch; a genuinely different resource. Post-MVP,
     only when an analytical use case lands.

5. **Client class hierarchy: `Table` is-a `Dataset`, not a sibling.** Reparent
   the client leaf classes to `File → Dataset → {Array, Table}` (details in
   "Client class hierarchy"). `Table(File)` as a sibling of the array class was
   a workaround to keep `blosc2.Operand` off tables; the correct fix is to push
   `Operand` **down onto `Array` only**, which gives symmetry (`Table` is-a
   `Dataset`) *and* correctness (`Table` is-not-an `Operand`). Also relocates
   `File`'s array-flavored `__getitem__` (it already references `self.dtype`)
   down to `Array`, leaving `File` generic.

## Goal

Add first-class support for Blosc2 heterogeneous tables (`blosc2.CTable`), mainly compact single-file `.b2z` files, alongside the existing `NDArray`/`.b2nd` support.

The first version should make tables discoverable, downloadable, inspectable, and previewable through the REST API, Python client, CLI, and web UI.

## Non-goals for the first pass

Keep the first implementation boring and small. Do **not** build a dataframe service yet.

Skip initially:

- `.b2d` directory-backed tables
- table append/edit/delete over Caterva2
- joins/groupby/describe/cov server APIs
- Arrow/Parquet/CSV transport APIs
- lazy expressions involving `CTable`
- treating `CTable` as a `blosc2.Operand`
- full query language/UI
- server-side pagination cache beyond simple `start`/`stop`

Add these only after a real use case needs them.

## Current assumptions in Caterva2

Caterva2 currently mostly assumes Blosc2 datasets are homogeneous arrays:

- `.b2nd`: `blosc2.NDArray`
- `.b2frame`: `blosc2.NDArray` or `SChunk`-style frame
- `.b2`: compressed regular file / `SChunk`

Important places with array assumptions:

- `caterva2/services/srv_utils.py`
  - `read_metadata()` only accepts `.b2frame`, `.b2nd`, `.b2`
  - metadata model assumes `shape`, `chunks`, `blocks`, `dtype`, `schunk`

- `caterva2/services/server.py`
  - suffix checks in `get_abspath()`, `/api/fetch`, upload, htmx upload/download paths
  - `open_b2()` assumes objects have either `schunk` or `vlmeta`, then later sets `cparams/dparams`
  - `/api/fetch` returns Blosc2 binary frames and decodes naturally as arrays/schunks
  - web preview path assumes `arr.shape`

- `caterva2/models.py`
  - no table metadata model

- `caterva2/client.py`
  - `Root.__getitem__()` returns `Dataset` only for `.b2nd`/`.b2frame`
  - `Dataset` assumes `shape`, `dtype`, `chunks`, `blocks`
  - `Client._fetch_data()` assumes binary cframe response

- `caterva2/clients/cli.py`
  - `show` currently calls `client.fetch()`
  - `info` formatting assumes array/schunk metadata

## Design principles

1. **CTable is a sibling data kind, not an NDArray variant.**
   - Use `nrows`, `ncols`, `columns`, `schema`.
   - Do not invent fake `shape`/`dtype` for API metadata.
   - Consistent with the client hierarchy (Revision 2026-07-01 #5): `Array` and
     `Table` are *siblings* under a shared `Dataset` base — a table is a kind of
     dataset, but not a kind of array.

2. **Do not put JSON in `/api/fetch`.**
   - `/api/fetch` means Blosc2 binary frame data: cframes for arrays/schunks,
     and (via `CTable.to_cframe()`) cframes for tables. Slices come
     back as blosc2 objects on the client, mirroring the array workflow.
   - A separate JSON row-window endpoint (`/api/table`) *was* planned for
     previews, but is **removed** — see Revision 2026-07-01 #3. Preview is
     served off the cframe path (CLI) and server-side rendering (web UI), so no
     JSON-over-HTTP endpoint is needed. Table *operations* live on `/api/fetch`
     params (schema-preserving) or a future `POST /api/query` (schema-changing),
     never on a JSON row endpoint — see Revision 2026-07-01 #4.

3. **Start with compact `.b2z`.**
   - It is a single file and fits Caterva2's current file model.
   - `.b2d` can come later when directory-backed datasets are needed.

4. **Centralize suffix logic.**
   - Avoid adding `.b2z` manually in many places without a shared constant.

5. **Small preview first.**
   - A row window (`start`/`stop`, optional columns) is enough for UI, CLI, and
     Python client. It is produced from the cframe slice on the client and by
     server-side row iteration in the web UI — no dedicated JSON endpoint (see
     Revision 2026-07-01 #3).
   - Large exports can use `/api/download` first.

## Proposed suffix constants

Add shared constants, preferably in `caterva2/services/srv_utils.py` or a small shared module if client/server both need them.

Server-side minimum:

```python
BLOSC2_ARRAY_SUFFIXES = {".b2nd", ".b2frame"}
BLOSC2_TABLE_SUFFIXES = {".b2z"}
BLOSC2_FRAME_SUFFIXES = {".b2"}
BLOSC2_NATIVE_SUFFIXES = (
    BLOSC2_ARRAY_SUFFIXES | BLOSC2_TABLE_SUFFIXES | BLOSC2_FRAME_SUFFIXES
)
HDF5_SUFFIXES = {".h5", ".hdf5"}
```

Use these in:

- `read_metadata()` suffix assertions
- `get_abspath()` native-file detection
- upload/load-from-url native-file detection
- htmx upload compression decisions
- download handling
- path-list handling for compressed regular files

Client-side can use a tiny local constant:

```python
DATASET_SUFFIXES = (".b2nd", ".b2frame")
TABLE_SUFFIXES = (".b2z",)
```

No need to over-share constants across package boundaries unless already easy.

## Metadata model

Add a new Pydantic model in `caterva2/models.py`:

```python
class CTableMetadata(pydantic.BaseModel):
    kind: str = "ctable"
    nrows: int
    ncols: int
    chunks: tuple
    blocks: tuple
    schema: dict
    columns: list[str]
    nbytes: int
    cbytes: int
    cratio: float
    vlmeta: dict = {}
    mtime: datetime.datetime | None
```

Notes:

- `schema` should come from `table.schema_dict()`.
- `columns` should be derived from the schema for convenience.
- `vlmeta` should use `table.vlmeta`.
- `kind` lets clients distinguish tables without relying on missing `shape`.

Possible future fields:

- `indexes`
- `computed_columns`
- per-column compression stats
- per-column logical/physical dtype info

Skip them initially unless they are already trivial and stable in Blosc2.

## `read_metadata()` changes

In `caterva2/services/srv_utils.py`:

1. Allow `.b2z` in the suffix check.
2. After `obj = blosc2.open(path)`, detect `blosc2.CTable`.
3. Return `models.CTableMetadata`.

Sketch:

```python
if path.suffix not in BLOSC2_NATIVE_SUFFIXES:
    ...

obj = blosc2.open(path)
...
if isinstance(obj, blosc2.CTable):
    schema = obj.schema_dict()
    return models.CTableMetadata(
        nrows=obj.nrows,
        ncols=obj.ncols,
        chunks=obj.chunks,
        blocks=obj.blocks,
        schema=schema,
        columns=[col["name"] for col in schema.get("columns", [])],
        nbytes=obj.nbytes,
        cbytes=obj.cbytes,
        cratio=obj.cratio,
        vlmeta=obj.vlmeta,
        mtime=mtime,
    )
```

Keep the existing `NDArray`, `SChunk`, `LazyArray` branches unchanged.

## `open_b2()` changes

In `caterva2/services/server.py`, `open_b2()` currently opens any Blosc2 object and then eventually tunes `container.cparams.nthreads` and `container.dparams.nthreads`.

`CTable` does not expose table-level `cparams/dparams` like `NDArray`.

Add an early branch after `blosc2.open()` and special-file detection setup:

```python
container = blosc2.open(abspath)

if isinstance(container, blosc2.CTable):
    return container
```

This should happen before trying to set `container.cparams` or `container.dparams`.

If `vlmeta` access is needed before this branch, keep it safe:

```python
vlmeta = (
    container.schunk.vlmeta
    if hasattr(container, "schunk")
    else getattr(container, "vlmeta", {})
)
```

## Path resolution and upload/download

### `get_abspath()`

Currently regular files are auto-compressed to `.b2` unless the suffix is native.

Change native suffix check from:

```
if filepath.suffix not in {".b2frame", ".b2nd", ".h5"}:
```

to include `.b2z` and `.hdf5` via constants:

```
if filepath.suffix not in BLOSC2_ARRAY_SUFFIXES | BLOSC2_TABLE_SUFFIXES | HDF5_SUFFIXES:
```

This prevents uploaded/existing `.b2z` files from being wrapped into `.b2`.

### Upload APIs

Include `.b2z` wherever native Blosc2 files should be stored as-is:

- `/api/upload/{path:path}`
- `/api/load_from_url/{path:path}`
- `/htmx/upload/{name}`

Existing logic:

```python
if abspath.suffix not in {".b2", ".b2frame", ".b2nd"}:
    schunk = blosc2.SChunk(data=data)
```

Should become:

```python
if abspath.suffix not in BLOSC2_NATIVE_SUFFIXES:
    schunk = blosc2.SChunk(data=data)
```

And final write-as-cframe checks should include `.b2z`, `.h5`, `.hdf5` as native.

### Download API

`/api/download/{path:path}` should work for `.b2z` without special conversion.

`get_file_content()` should return stored bytes for `.b2z`. Do not try `to_cframe()`.

If current code opens `.b2nd`/`.b2frame` specially for HDF5Proxy, leave `.b2z` on the normal file path.

## Rejected alternative: a JSON `/api/table` row-window endpoint

An earlier draft proposed `GET /api/table/{path}?start&stop&columns` returning
`{"kind": "ctable", "rows": [...]}` JSON. **Rejected — removed, not deferred**
(Revision 2026-07-01 #3).

Why it does not earn its place:

- It is a lighter-weight *duplicate of the slice fetch*. Every would-be MVP
  consumer is already covered without it:
  - CLI `show` → rows off the cframe via the Python client.
  - Web UI → rows rendered server-side in `htmx_path_view` from the live
    `CTable`.
  - JupyterLite → Pyodide has blosc2, so it decodes cframes directly.
- The only consumers it would uniquely serve are a **non-Pyodide JS client** or
  **`curl` eyeballing** — both hypothetical today. If one appears, the endpoint
  is ~20 trivial lines (JSON-ify a `slice()`), and the design is easy to
  re-derive — unlike the cframe transport decision below, which is subtle and
  worth preserving.
- A specced-but-deferred endpoint is an attractive nuisance: it invites a second
  row-preview path alongside the cframe one. Better to have one path.

If a real external JSON consumer ever lands, add it then. Note that table
*operations* (`where`/`sort`/`group_by`) do **not** motivate this endpoint —
transport (JSON vs cframe) is orthogonal to operation, and every operation
returns a `CTable` the cframe path already serializes. See "Future extensions →
table operations" for where operations actually live.

## Transport format for table slices (decision)

This section records the analysis of how to ship a slice of a `CTable` over the
wire as a Blosc2 object, mirroring how `/api/fetch` ships `NDArray` slices as
`to_cframe()` bytes. It is the most consequential design choice in this plan,
so the options and reasoning are kept here in full.

### The core problem

A cframe serializes **one** `SChunk`. An `NDArray`/`SChunk` is one schunk, so
`to_cframe()`/`ndarray_from_cframe()` is trivial. A `CTable` is **a collection**
of blosc2 objects: one `NDArray` per column, a `_valid_rows` mask, a `_meta`
manifest, `_vlmeta`, and a schema dict. There is no single schunk to serialize,
so `CTable.to_cframe()` does not exist today. The native container is `.b2z`, a
zip archive bundling the column leaves plus an `embed.b2e` `EmbedStore` — but
`.b2z` is a **file-based** format, not bytes.

### Options considered

**Option A: temp-file `.b2z` roundtrip (no new blosc2 API).**

```python
# server (slice)
view = table.slice(start, stop)
view.to_b2z("/tmp/slice.b2z", overwrite=True)
# stream /tmp/slice.b2z bytes to the HTTP response

# client
open("/tmp/slice.b2z", "wb").write(data)
rt = blosc2.open("/tmp/slice.b2z", mode="r")
```

Tested — it works. Findings:

- `to_b2z()` rejects file-like objects (`os.fspath` + `endswith(".b2z")` +
  `os.path.exists`); a `BytesIO` fails with `urlpath must have a .b2z extension`.
  So the server must touch disk.
- `.b2z` read mode reads column leaves **lazily, by byte offset, from a file
  path** at the C level (`blosc2_ext.open(path, offset=...)`). The client temp
  file must stay alive: opening a `.b2z`, deleting the file, then touching a
  row raises `RuntimeError: Error while getting the lazychunk`.
- For a slice/view, `to_b2z()` falls back to the logical `save()` path and
  **recompresses** the live rows; the zero-recompression `ZIP_STORED` physical
  pack only fires for a whole persistent `.b2d`/`.b2z` source.

**Pyodide viability (revisited).** Earlier this plan flagged the path-based lazy
reader as a Pyodide blocker. That was wrong: Pyodide's **MEMFS** is a real
filesystem exposed to compiled C via Emscripten file syscalls, and blosc2's
default `open()` does **not** mmap (mmap is opt-in; the WASM path only forces
`nthreads=1`, not mmap). So `blosc2.open('/tmp/x.b2z')` on MEMFS works exactly
as on disk. The temp-file path is therefore viable on desktop **and** Pyodide.

**Footprint.** MEMFS RAM is part of the Pyodide heap budget, but this is not a
penalty: the CTable's compressed bytes live on the heap either way. Steady
state is identical for both paths — compressed slice (C bytes) on the heap plus
decompressed columns touched (D bytes). The only transient difference is a
brief 2C during ingestion (response bytes copied into MEMFS, or into a Python
buffer), identical in both. MEMFS is neither cheaper nor dearer than a Python
buffer; footprint is a wash.

**Cleanup — the real residual difference.** With MEMFS, the temp-file path is
viable, but cleanup is **extrinsic**: the MEMFS file holds C bytes until
explicitly deleted, tied to a `Table._local_b2z` attribute and a `__del__`
finalizer on `Table`. The cframe path is **intrinsic**: the bytes live in the
`CTable` object; freeing the object frees the bytes via ordinary refcounting.

  - With a `__del__`/`weakref.finalize` that does `os.remove(self._local_b2z)`,
    the temp-file path reaches effective equality on CPython (and Pyodide runs
    CPython, so refcounting is deterministic there). The user cannot leak.
  - The maintainer can: the invariant is "every temp `.b2z` created on behalf
    of this `Table` is recorded in `_local_b2z`" — a convention the codebase
    must enforce on itself. A future fetch path that does its own `mkstemp`
    without routing through one `_materialize()` method leaks silently. The
    cframe path has no such invariant: resource and object are the same thing,
    so new fetch methods cannot break cleanup.
  - To make the temp-file path bulletproof, route every materialization
    through one `_materialize()` method and forbid ad-hoc `mkstemp`.

| aspect | temp-file + `__del__` | cframe |
|---|---|---|
| Footprint | equal | equal |
| Pyodide (MEMFS) | works | works |
| User can leak | no | no |
| Maintainer can leak (new fetch path) | yes, if it bypasses `_materialize` | no |
| Discipline required | "all materialization via one method, forever" | none |
| Symmetry with `NDArray.to_cframe()` | no | yes |
| New blosc2 API | none | `CTable.to_cframe`/`ctable_from_cframe` (small) |

**Option B: `CTable.to_cframe()`/`ctable_from_cframe()` on `EmbedStore`.**

`EmbedStore` is the existing blosc2 mechanism for bundling **multiple** arrays
into one serializable store:

```python
es = blosc2.EmbedStore(urlpath=None, mode="w")
es["/a"] = blosc2.asarray(...)
es["/b"] = blosc2.asarray(...)
cf = es.to_cframe()  # -> bytes, one blob for the whole collection
back = blosc2.from_cframe(cf)  # -> EmbedStore, fully in-memory
```

Tested — pure bytes, no temp files, `from_cframe(bytes)` works today. A CTable
= `schema_dict()` + N column `NDArray`s + a `valid_rows` `NDArray`, so it
packs cleanly into an `EmbedStore`. An in-memory materialized CTable's columns
are plain `blosc2.NDArray` with working `to_cframe()` (tested).

A real `CTable.to_cframe()` is therefore a small, Python-level, well-scoped
feature on an existing primitive: serializer packs schema + each column's
`to_cframe()` + `valid_rows.to_cframe()` into one `EmbedStore` and returns
`es.to_cframe()` → `bytes`; `ctable_from_cframe(bytes)` rebuilds from an
`InMemoryTableStorage`. It mirrors `NDArray.to_cframe()`/`ndarray_from_cframe()`
exactly: pure bytes, no disk, lazy-safe (objects live in memory after
`from_cframe`).

The one design reason blosc2 currently keeps columns as external leaves
(`FileTableStorage` forces `threshold=0`) is a *persistent-store* concern so
large columns stay out of one schunk and small `_meta` overwrites persist
reliably. It does not apply to an in-memory cframe transport, where bundling
everything is the point.

**Option C: `DictStore.to_bytes()` (native `.b2z` bytes).**

`DictStore.to_b2z()` already builds the `.b2z` zip via `zipfile.ZipFile(path,
"w", ZIP_STORED)`. A `to_bytes()` into `BytesIO` would mirror it, producing
native `.b2z` bytes with zero recompression for whole persistent tables.

But on its own it's **half a feature**:

- The zip **reader** is path/offset-based (`blosc2_ext.open(path, offset=...)`).
  `to_bytes()` without a buffer-backed `from_bytes()` reader just shifts the
  temp-file problem from server to client — the consumer must write the bytes
  to a temp `.b2z` and `open()` it. That buffer-backed reader is a big C-level
  change, and it's the part that does the actual work.
- For a **slice**, `to_bytes()` falls to the logical `save()` path and
  recompresses — same cost as the cframe, but producing `.b2z` bytes with worse
  client ergonomics and no array symmetry.
- For a **whole table** that is already a file, `/api/download` already serves
  those exact bytes (`FileResponse` on the on-disk `.b2z`); `to_bytes()`
  duplicates it. It only differs for an in-memory/sliced table, which is the
  slice case above.

### Decision

**Build `CTable.to_cframe()`/`ctable_from_cframe()` on `EmbedStore` (Option B).
Do not build `DictStore.to_bytes()` now.**

Reasons:

1. **Symmetry with the existing array-slice workflow.** `/api/fetch` streams
   `array.slice(...).to_cframe()` and the client does
   `blosc2.ndarray_from_cframe(data)`. A table fetch streams
   `table.slice(...).to_cframe()` and the client does `ctable_from_cframe(data)`
   — one code shape, no JSON-for-small / download-for-whole special-casing,
   no second client-side pattern in `Client._fetch_data`.
2. **It closes a real gap in blosc2.** `CTable` is the only major container
   without a bytes serialization. Adding it is appealing on its own merits,
   independent of Caterva2.
3. **Intrinsic, invariant-free resource cleanup.** No client temp file, no
   `_local_b2z`/`__del__` lifecycle to maintain, no "all materialization via one
   method" convention for future maintainers to violate. Resource and object
   are the same thing; ordinary refcounting is the cleanup.
4. **Pyodide-safe and footprint-equal.** Pure bytes; works in the browser
   without MEMFS round-trips and with the same heap budget as the temp-file
   path.
5. **Small, Python-level blosc2 change** on an existing primitive
   (`EmbedStore`), unlike `DictStore.to_bytes()` which is gated behind a big
   buffer-backed C reader to be useful.

On the "`EmbedStore` is less battle-proven than `DictStore`/`TreeStore`" concern:
this is an argument **for** the cframe route, not against. As a *transport codec*
it gets real exercise in a simpler, more contained stress than being a
persistence backend, which hardens it.

### `DictStore.to_bytes()` — deferred, with a revisit condition

Not built now. It only becomes the best all-around answer **if and when a
buffer-backed zip reader** (`DictStore.from_bytes()` / `blosc2_ext.open` from a
buffer) lands in blosc2 — at which point `.b2z` bytes could do whole-table *and*
slice transport uniformly in one native format, and the cframe could even be
retired in favor of it. That is a blosc2-wide decision driven by "read `.b2z`
from a buffer" demand (e.g. S3/HTTP bytes, Pyodide `fetch()` without a MEMFS
round-trip), not a Caterva2 need. Revisit when such a reader exists; until then
`to_bytes()` is redundant with `/api/download` (whole files) and the cframe
(slices).

### Consequences for the endpoints

- `/api/fetch` is extended to tables via the cframe path (see next section),
  **not** left array-only. This supersedes the earlier "do not support `.b2z`
  in `/api/fetch`" stance: with `CTable.to_cframe()` available, the clean path
  is to support it.
- `/api/download` continues to serve whole `.b2z` files as raw bytes
  (`FileResponse`), unchanged.
- A JSON `/api/table` rows endpoint is **removed entirely** (Revision
  2026-07-01 #3), not just deferred: CLI `show` derives rows from the cframe via
  the Python client, and the web UI renders rows server-side in
  `htmx_path_view`, so neither needs a JSON-over-HTTP window. Table *operations*
  live on `/api/fetch` params or a future `POST /api/query`, not here.

## `/api/fetch` behavior (with cframe)

Support `.b2z` in `/api/fetch` using `CTable.to_cframe()`, mirroring the
`NDArray` path. This is the slice-as-blosc2-object transport.

**Critical (Revision 2026-07-01 #1): do NOT let a whole `CTable` fetch hit the
generic `FileResponse` short-circuit.** That short-circuit streams the raw
on-disk file. For `.b2nd` that file *is* a cframe, so the client decodes it. For
`.b2z` the file is a **zip** (`PK\x03\x04`), not a cframe — the client's
cframe decoder fails with `RuntimeError: Could not get the schunk from the
cframe`. So a whole `CTable` must also be serialized via `to_cframe()`.

Concretely, exclude `CTable` from the whole short-circuit condition:

```python
if (
    whole
    and not isinstance(
        array, blosc2.LazyArray | hdf5.HDF5Proxy | blosc2.NDField | blosc2.CTable
    )  # <-- add CTable
    and not filter
):
    return FileResponse(
        abspath, filename=abspath.name, media_type="application/octet-stream"
    )
```

Then the `CTable` branch always produces a cframe (whole or sliced):

```python
if isinstance(container, blosc2.CTable):
    row_start = 0 if whole else start
    row_stop = container.nrows if whole else stop
    view = container.slice(row_start, row_stop)  # materialized CTable
    data = view.to_cframe()  # bytes, via EmbedStore
    downloader = srv_utils.iterchunk(data)
    return responses.StreamingResponse(
        downloader, media_type="application/octet-stream"
    )
```

A whole-table fetch costs one recompress this way, but it is correct and keeps
the client's single decode path honest. `/api/download` still serves the raw
`.b2z` bytes (`FileResponse`) for actual file downloads — that path is fine
because the client writes bytes to disk rather than decoding a cframe.

(Where exactly `ctable_to_cframe`/`ctable_from_cframe` live — `blosc2.CTable`
methods or module functions mirroring `ndarray_from_cframe` — is a blosc2 API
choice; Caterva2 just calls them.)

Keep the accurate error message for non-supported suffixes:

```python
if abspath.suffix not in BLOSC2_ARRAY_SUFFIXES | BLOSC2_TABLE_SUFFIXES:
    srv_utils.raise_bad_request(
        "The fetch API only supports datasets (.b2nd, .b2frame, .b2z); "
        "use /api/download for other files"
    )
```

## Client class hierarchy

Reparent the client leaf classes so a table is a *kind of dataset*, not a
sibling of one (Revision 2026-07-01 #5). Target:

```
File(  )                            # any leaf: vlmeta / download / move / copy / remove
└── Dataset(File)                   # a fetchable blosc2 data leaf: generic slice() -> blosc2 object
    ├── Array(Dataset, blosc2.Operand)   # shape / dtype / chunks / blocks / ndim / append; lazyexpr operand
    └── Table(Dataset)                    # nrows / ncols / columns / schema
```

Current state (for reference) is asymmetric:

```
File
├── Dataset(File, blosc2.Operand)   # the array class
└── Table(File)                     # sibling, not a Dataset
```

### Why this shape (not just cosmetics)

- **`blosc2.Operand` must move down onto `Array` only.** `Dataset` inheriting
  `Operand` is what lets an array participate in lazy expressions; a `Table`
  must **not** be an `Operand` (a non-goal of this plan). The sibling design was
  a blunt way to keep `Operand` off `Table`. Pushing `Operand` down to `Array`
  gives both symmetry (`Table` is-a `Dataset`) and correctness (`Table`
  is-not-an `Operand`). This is the reason the base cannot simply be today's
  `Dataset`.
- **`File` stops leaking array semantics.** `File.__getitem__` already
  references `self.dtype` (only defined on the array class), so `File` is
  already array-flavored. Move that dtype/field-aware `__getitem__` down to
  `Array`; keep on `Dataset` only the generic `slice() -> blosc2 object`
  plumbing that both `Array` and `Table` share; keep `File` truly generic.
- **`Dataset` is effectively abstract.** `Root.__getitem__` always resolves to
  `Array`, `Table`, or `File` — a bare `Dataset` is never instantiated, so there
  is no ambiguous "what is a plain Dataset."

### Backward-compat ledger

- **`isinstance(x, Dataset)` broadens** from "array" to "array *or* table" — the
  originally intended meaning of `Dataset` as the generic data leaf. Behavior
  change: a `.b2z` was `isinstance … Dataset == False`, becomes `True`. Audit
  the 4 sites in `client.py` (`get()` ~998, `download`/`get_slice` ~1060/1126,
  `get_chunk` ~1190); the first three *simplify* (`Dataset ⊂ File`,
  `Table ⊂ Dataset`), and `get_chunk` (array-flavored) needs a check that it is
  not wrongly handed a `Table`. `test_api.py`'s `isinstance(myds, cat2.Dataset)`
  on a `.b2nd` keeps passing since `Array(Dataset)`.
- **Repr doctests**: 5 doctests print `<Dataset: …b2nd>` (client.py ~123, 234,
  524, 557, 586) → become `<Array: …>`. Mechanical update.
- **Exports**: keep `Dataset` in `__init__.py` / `__all__` (now the base, still
  importable — no import breakage); add `Array` and `Table`.
- **Naming**: use `Array`, not `NDArray`, to parallel `Table` and avoid
  colliding with the `NDArray` alias already imported for type hints
  (client.py:416).
- Blast radius is confined to `client.py` plus those doctests/tests; server, web
  UI, and CLI do not touch these client classes.

### `Root.__getitem__` dispatch

```python
if path.endswith((".b2nd", ".b2frame")):
    return Array(self, path)  # was Dataset(self, path)
if path.endswith(".b2z"):
    return Table(self, path)
return File(self, path)
```

## Python client API

With the hierarchy above, `Table` subclasses `Dataset` (not `File`). Note the
metadata field is `schema_dict`, not `schema` (a pydantic `BaseModel` already
defines `schema`, so naming the field `schema` shadows/deprecates it — the model
uses `schema_dict`). Keep `ncols` and a `schema` *property* for parity with the
metadata even though they are cheap conveniences:

```python
class Table(Dataset):
    def __repr__(self):
        return f"<Table: {self.path}>"

    @property
    def nrows(self):
        return self.meta["nrows"]

    @property
    def ncols(self):
        return self.meta["ncols"]

    @property
    def columns(self):
        return self.meta["columns"]

    @property
    def schema(self):
        return self.meta["schema_dict"]

    def slice(self, key):
        """Row slice as a blosc2.CTable (via the cframe /api/fetch path)."""
        return self.client.get_slice(self.path, key, as_blosc2=True)

    def __getitem__(self, key):
        return self.slice(key)

    def rows(self, start=0, stop=50):
        """Preview rows as a list of tuples, off the cframe slice."""
        ct = self.slice(slice(start, stop))
        return [tuple(row) for row in ct[:]]

    def head(self, n=10):
        return self.rows(0, n)
```

Rows come from the **cframe path** (`get_slice` → `/api/fetch` → a
`blosc2.CTable`), not a JSON endpoint. There is no `get_table_rows()` and no
`/api/table` — both removed (Revision 2026-07-01 #3).

`Root.__getitem__()` dispatch is shown in "Client class hierarchy" (returns
`Array` for `.b2nd`/`.b2frame`, `Table` for `.b2z`, `File` otherwise).

`Client.get()`'s type check simplifies with the new hierarchy — since
`Array`/`Table` are both `Dataset ⊂ File`, the tuple collapses:

```python
if isinstance(path, (File, Root)):  # was (File, Dataset, Table, Root)
    return path
```

### `_fetch_data` decode: dispatch on known kind (Revision 2026-07-01 #2)

Do **not** decode by trial-and-except (`ctable_from_cframe` first, catch
`ValueError`, then `ndarray_from_cframe`, catch `RuntimeError`, …). That makes
every array fetch pay a failed CTable parse and couples the code to each
decoder's exception type. The caller already knows the kind — an `Array` vs a
`Table` (both `Dataset`s), or `meta["kind"] == "ctable"` — so pass it down and
pick the decoder directly:

```python
def _fetch_data(self, path, urlbase, params, kind=None, as_blosc2=False, timeout=5):
    data = self._xget(...).content
    if kind == "ctable":
        obj = blosc2.ctable_from_cframe(data)
    else:
        try:
            obj = blosc2.ndarray_from_cframe(data)
        except RuntimeError:
            obj = blosc2.schunk_from_cframe(data)
    if as_blosc2:
        return obj
    if isinstance(obj, blosc2.CTable):
        return [tuple(row) for row in obj[:]]
    if hasattr(obj, "ndim"):
        return obj[()] if obj.ndim == 0 else obj[:]
    return obj[:]
```

The `Array`/`Table` wrappers already know their kind, so threading it through
`get_slice`/`_fetch_data` is local. (The remaining array `ndarray`/`schunk`
try/except is fine: both are cframes and the ambiguity is genuine.)

## CLI changes

### `info`

If metadata contains `kind == "ctable"`, print table-specific fields:

```text
kind   : ctable
nrows  : 1000000
ncols  : 12
chunks : (1048576,)
blocks : (32768,)
nbytes : 123 MB
cbytes : 12 MB
ratio  : 10.2x
columns: a, b, c, ...
mtime  : ...
```

For `--json`, no special work: dump metadata as returned.

### `show`

For `.b2z`, do not call `client.fetch()`.

Minimal behavior:

```sh
cat2-client show path/table.b2z
```

prints first 50 rows.

Optional first-pass slice support:

```sh
cat2-client show path/table.b2z[0:20]
```

maps to `start=0`, `stop=20`.

Optional columns could come later via `--columns a,b`. Skip unless asked.

Implementation:

- detect `.b2z` path
- parse single row slice only
- call `table.rows(start, stop)` (which uses the cframe path); there is no
  `get_table_rows()`/`/api/table` (removed, Revision 2026-07-01 #3)
- print rows as a simple table or JSON

Lazy first text output can use standard formatting:

```python
rows = client.get(path).rows(start, stop)  # list of tuples off the cframe
print(json.dumps(rows, indent=2, default=str))
```

Pretty tables can come later.

## Web UI changes

### Reuse of the existing structured-array visualizer

Caterva2 already has a table visualizer for **structured NDArrays** (dtypes with
fields). It splits into two layers, and CTable reuse differs per layer
(verified against the live `CTable`/`NDArray` APIs, 2026-07-01):

- **Template `info_view.html` — reusable as-is.** It is driven entirely by
  generic context keys (`rows`, `cols`, `fields`, `sortby`, `filter`, `shape`,
  `inputs`, `tags`); nothing in it is array-specific. The Fields dropdown,
  Sort-by select, Filter box, and the row/column grid all render from a plain
  table model. A CTable is exactly the "1-D, has-fields" case this template
  already handles, so no template change is needed to show rows.

- **Server extraction (`htmx_path_view` + `get_filtered_array`) — NOT reusable
  directly.** It is hard-wired to the structured-`NDArray` API, and a `CTable`
  is a different type exposing none of those attributes:

  | Extraction needs | structured `NDArray` | `CTable` | CTable equivalent |
  |---|---|---|---|
  | `.fields` (detect + col names) | yes | **no** | `schema_dict()["columns"]` → names |
  | `.shape` / `.ndim` (windowing) | yes | **no** | `nrows` / `ncols` (conceptually 1-D) |
  | `.tolist()` (materialize rows) | yes | **no** | iterate `slice(a,b)` → `CTableRow._asdict()` |
  | positional `row[i]` | yes | **no** (`CTableRow`, keyed) | `row._asdict()[field]` |
  | `.argsort()` / `.sort()` (filter/sort) | yes | **no** | `where()` / `sort_by()` / `sorted_slice()` |
  | column select | `arr.fields[name]` (NDField) | `t[name]`→`Column`, `t[[names]]`→`CTable` | usable, different type |

  **Pitfall:** `htmx_path_view` starts with `shape = arr.shape` — a CTable has
  no `.shape`, so this line `AttributeError`s. The CTable branch must come
  **before** it.

  **Rejected shortcut:** converting a CTable slice to a structured `NDArray`
  (its columns already are NDArrays) to feed the existing `has_ndfields` path
  verbatim — it breaks on **variable-length string columns**, which don't fit a
  fixed numpy structured dtype and are a first-class CTable case. Use a native
  branch.

### `htmx_path_info()`

Currently the array display tab is added when `hasattr(meta, "shape")`.

Add the table display tab when `kind == "ctable"`:

```python
if getattr(meta, "kind", None) == "ctable":
    context["data_url"] = make_url(request, "htmx_path_view", path=path)
    context["shape"] = (meta.nrows,)  # synthesize a 1-D shape; meta has no .shape
    tabs.append(
        {
            "name": "data",
            "label": "Display",
            "include": "includes/info_data.html",
        }
    )
```

### `htmx_path_view()`

Add an early CTable branch **before** `shape = arr.shape` (which would otherwise
fail). It fills the *same* context keys the template expects, from CTable APIs —
note **keyed** row access via `_asdict()`, not the structured array's positional
`row[i]`:

```python
if isinstance(arr, blosc2.CTable):
    cols = [c["name"] for c in arr.schema_dict()["columns"]]
    fields = fields or cols[:5]
    index = (0,) if index is None else tuple(index)
    size = (sizes or [10])[0]
    start = index[0]
    stop = min(start + size, arr.nrows)
    # keyed access (CTableRow._asdict()), NOT positional row[i]
    rows = [fields] + [
        [_cell(row._asdict()[f]) for f in fields] for row in arr.slice(start, stop)
    ]
    context = {
        "view_url": make_url(request, "htmx_path_view", path=path),
        "inputs": [
            {
                "start": start,
                "start_max": max(arr.nrows - size, 0),
                "size": size,
                "size_max": arr.nrows,
                "with_size": True,
            }
        ],
        "rows": rows,
        "cols": cols,
        "fields": fields,
        "filter": "",
        "sortby": "",
        "shape": (arr.nrows,),
        "tags": list(range(start, stop)),
        "filterable": False,  # see caveat below
    }
    return templates.TemplateResponse(request, "info_view.html", context)
```

`_cell()` coerces numpy scalars / bytes for HTML (mirrors the JSON edge cases:
`np.generic` → `.item()`, `bytes` → decode). Column subset could also use
`arr[fields]` (returns a CTable), but per-row `_asdict()` keyed access is simpler
and avoids re-slicing.

**Filter/sort caveat.** The template shows the Filter box and Sort-by select
whenever `cols` is truthy — but `get_filtered_array` is **not** reusable for
CTable (it opens with `assert has_ndfields` and uses NDArray lazy-expr
`argsort`/`sort`; a CTable needs a parallel path on `where()` +
`sort_by()`/`sorted_slice()`). Filtering/sorting is deferred (Revision
2026-07-01 #4), so for the MVP **render rows only and hide those two controls**:
add a `filterable` context flag (set `False` here, `True` on the array path) and
guard the Filter/Sort-by blocks in `info_view.html` with `{% if filterable %}`.
The Fields (column subset) dropdown can stay — it works with a plain slice.

When filtering does land, it rides the schema-preserving `/api/fetch`
`filter=`/`sortby=` path (Revision 2026-07-01 #4), backed by a CTable
`where()`/`sort_by()` branch — not `get_filtered_array` and not `/api/table`.

## Tests

Add one small CTable fixture.

Example creation helper:

```python
from dataclasses import dataclass
import blosc2


@dataclass
class Row:
    id: int = blosc2.field(blosc2.int32())
    name: str = blosc2.field(blosc2.string(max_length=20))


def make_ctable(path):
    table = blosc2.CTable(Row, urlpath=str(path), mode="w", compact=True)
    table.append((1, "alice"))
    table.append((2, "bob"))
    table.append((3, "carol"))
    table.close()
```

Tests:

1. `read_metadata()` on `.b2z`
   - `kind == "ctable"`
   - `nrows == 3`
   - `columns == ["id", "name"]`

2. `/api/info/...table.b2z`
   - same assertions through HTTP

3. `/api/download/...table.b2z`
   - write response to temp file
   - `blosc2.open(downloaded_path)` returns `blosc2.CTable`
   - `len(table) == 3`

4. `/api/fetch` cframe transport (the bug from Revision 2026-07-01 #1)
   - **whole** fetch (`table[:]`) decodes to a `blosc2.CTable` with `nrows == 3`
     — guards against the raw-zip regression
   - **slice** fetch (`table[1:3]`) decodes to bob/carol

5. Python client
   - `root["table.b2z"]` is `Table`
   - `table.nrows == 3`
   - `table.head(2)` has two rows (list of tuples, off the cframe)
   - `table[:]` / `table.slice(slice(1, 3))` return a `blosc2.CTable`

6. CLI smoke test
   - `cat2-client info table.b2z --json` contains `kind: ctable`
   - `cat2-client show table.b2z --json` returns first rows

7. Web preview smoke test (`htmx_path_view` CTable branch)
   - a `.b2z` renders rows without error (guards the `arr.shape` pitfall)
   - Filter/Sort-by controls are absent (`filterable=False`); Fields dropdown
     present

No `/api/table` test — that endpoint is removed (Revision 2026-07-01 #3).

Keep tests small. No fixtures for huge data, no performance suite.

## Edge cases

### NumPy scalar JSON encoding

CTable rows can contain NumPy scalar types. Convert with `.item()`.

### NumPy array cells

CTable can have nested/NDArray-like cells. For JSON preview, convert arrays to lists.

### Bytes cells

Decode as UTF-8 with replacement for preview. For exact bytes transport, use download/export later.

### Large pages

Cap preview rows (e.g. 1000/request) to prevent accidental huge responses from
the web UI or CLI. (Row-count caps and column validation as HTTP 400 were tied
to the removed `/api/table` endpoint; for the MVP the web UI/CLI simply bound
their own preview windows.)

### Unknown columns

Only relevant once column selection is exposed (as a `field=`/`columns=` param
on `/api/fetch`, not a JSON endpoint). When it lands, reject unknown requested
columns with 400 — do not silently ignore.

### `.b2d`

Directory-backed CTable should be rejected/not recognized initially. If uploaded as archive, it is just a regular file/archive. Native directory-backed support needs separate path-list and download semantics.

## Future extensions

Only add these once MVP is working and there is demand.

### Table operations: two homes, split by schema effect

Transport (cframe vs JSON) is orthogonal to operation. Every table op
(`where`/`sort`/`group_by`/…) *returns a `CTable`* (or an NDArray for scalar
reductions), which the cframe path already serializes. So operations never need
a JSON row endpoint — they need a way to be *expressed in the request*. Route
them by what the op does to the schema (Revision 2026-07-01 #4):

**Schema-preserving → params on `/api/fetch`, cframe response.**
`where`/`sort` return the same columns — a subset/reordering of the same rows —
so they are still "a region of this dataset." This is exactly the array
`get_filtered_array` path, now viable for tables since lazy-view `to_cframe()`
works.

```text
GET /api/fetch/{path}?filter=col%20%3E%2010&field=a,b&sortby=col&start=0&stop=50
```

```python
table.where("col > 10", columns=["a", "b"])  # -> CTable -> cframe
table.sort_by("col") / table.sorted_slice(...)  # -> CTable -> cframe
```

Needs careful expression validation/error handling. **Not** a JSON `/api/table`
variant.

**Schema-changing → a future `POST /api/query/{path}`, cframe response.**
`group_by`/`aggregate`/`join`/`describe` produce a *new* dataset with a
different schema/cardinality. Cramming these into `/api/fetch` GET params would
turn query strings into a stringly-typed query language and erode fetch's
"give me bytes of this dataset" contract. Give them a structured JSON *request
body* and a cframe *response*:

```text
POST /api/query/{path}
{ "where": "...", "groupby": ["a"], "aggregations": {"s": "sum(b)"},
  "orderby": "s", "limit": 100 }
```

This is compute-not-fetch — a genuinely different resource. Post-MVP, only when
an analytical use case lands. (The response is still a cframe `CTable`, so no
JSON-rows machinery is resurrected.)

### Export formats (Arrow / CSV / Parquet)

Distinct from both fetch and query: these re-serialize a table (or a query
result) into a foreign format for download. Hang them off `/api/download` (or a
future query result) with a `format=` param — **not** `/api/table`:

```text
GET /api/download/{path}?format=arrow   # table.to_arrow() / iter_arrow_batches()
GET /api/download/{path}?format=csv     # table.to_csv()
GET /api/download/{path}?format=parquet # table.to_parquet()
```

Arrow/Parquet likely need PyArrow and temp files — add only if PyArrow is
already an accepted dependency/extra, never as a core dependency just for first
support.

### `.b2d` support

Directory-backed tables need decisions:

- how to list them as one logical dataset
- how to upload/download them
- whether to tar/zip for download
- how to protect internal files from direct mutation

Do later.

## Suggested implementation order

1. Add suffix constants and `.b2z` native handling.
2. Add `CTableMetadata` and `read_metadata()` support.
3. Make `open_b2()` return `CTable` safely.
4. Ensure `/api/download` preserves `.b2z` bytes.
5. **blosc2 prerequisite:** implement `CTable.to_cframe()` /
   `ctable_from_cframe()` on `EmbedStore` (see the transport decision), then
   wire `/api/fetch` table whole+slices + `Client.get_slice`/`Table.slice` to
   it. This is the slice/whole-as-blosc2-object transport and the basis for
   preview.
6. Refactor the client leaf hierarchy to `File → Dataset → {Array, Table}`
   (see "Client class hierarchy"): introduce `Array`, push `blosc2.Operand`
   down onto it, reparent `Table` under `Dataset`, update `Root.__getitem__`,
   exports, the `isinstance(_, Dataset)` sites, and the repr doctests.
7. Add Python client `Table` behavior (`slice`/`rows`/`head` off the cframe
   path) on the reparented class.
8. Add web preview: a CTable branch in `htmx_path_view` (before `arr.shape`)
   reusing `info_view.html`, plus a `filterable` flag + `{% if filterable %}`
   guard to hide filter/sort (see "Reuse of the existing structured-array
   visualizer"). `get_filtered_array` is not reused.
9. Add CLI `info`/`show` support (rows off the cframe path).
10. Add the small CTable tests (including the whole-table cframe regression).

There is **no** `/api/table` JSON endpoint step — removed (Revision
2026-07-01 #3).

## Implementation status

**Complete (2026-07-01).** Everything in the MVP scope is implemented and
committed. Commits: `3f4748c` (metadata + fetch pipeline + initial `Table`),
`32947e8` (whole-fetch fix + `Array`/`Table` hierarchy + `.b2z` storage),
`6a45518` (web preview + CLI), plus a post-review hardening commit.

### blosc2 side (done)

- `CTable.to_cframe()` + `blosc2.ctable_from_cframe(bytes)` on `EmbedStore`
  (`EmbedStoreTableStorage` backend), exported; fails cleanly (`ValueError`) on
  non-CTable cframes. Handles scalar/string/list/dict/vlstring columns + vlmeta.
- Lazy-view `to_cframe()` copy() limitation fixed (`where()`/`view()` serialize).
- **Extra (found during Caterva2 QA):** `CTableRow.__getitem__` now resolves
  dotted paths into nested struct columns (`row["trip.sec"]`) via
  `split_field_path`, so the names `schema_dict()` advertises are all
  addressable. Fixed upstream with tests
  (`tests/ctable/test_nested_access_storage.py`); the temporary caterva2-side
  workaround was removed once it landed.

### Caterva2 side (done)

- **Constants/metadata:** `BLOSC2_NATIVE_SUFFIXES` (incl. `.b2z`) in
  `srv_utils.py`; `CTableMetadata` in `models.py` (field is **`schema_dict`**,
  not `schema` — the "Metadata model" sketch above predates the rename);
  `read_metadata()` → `CTableMetadata`; `open_b2()` returns `CTable` early;
  `get_abspath()` treats `.b2z` as native.
- **`/api/fetch` (Revision #1):** whole *and* sliced `.b2z` serialize via
  `CTable.slice(...).to_cframe()`; `CTable` excluded from the `FileResponse`
  whole short-circuit so `table[:]` no longer returns raw zip bytes.
- **`_fetch_data` (Revision #2):** `get_slice` computes `kind` before the request
  and `_fetch_data` dispatches `ctable_from_cframe` only for `kind=="ctable"`;
  no more trial-and-except format sniffing.
- **Client hierarchy (Revision #5):** `File → Dataset → {Array, Table}`;
  `blosc2.Operand` on `Array` only; array-flavored `__getitem__` moved off
  `File`; `Array`/`Table` exported; redundant `isinstance` tuples simplified;
  repr doctests updated.
- **`Table` client:** `nrows`, `ncols`, `columns`, `schema` (reads
  `schema_dict`), `slice`/`__getitem__` → `blosc2.CTable`, `rows`, `head`.
  (As-built defaults differ from the sketch: `rows(start=0, stop=50)` and
  `head(n=5)`.)
- **Upload/download:** switched hardcoded suffix sets to
  `BLOSC2_NATIVE_SUFFIXES` (incl. `.b2z`) across `/api/upload`,
  `/api/load_from_url`, `/htmx/upload`, and path-guessing;
  `get_file_content`/`/api/download` already served `.b2z` raw bytes correctly.
- **Web preview:** `htmx_path_info` Display tab for `kind=="ctable"`;
  `htmx_path_view` CTable branch before `arr.shape`, keyed `row[...]` access,
  `cell()` coercion, `filterable=False` + `{% if filterable %}` guard in
  `info_view.html`. **Extra:** fixed a pre-existing `info_metadata.html` crash
  (the Meta tab assumed `cparams` on any `nbytes`-bearing metadata, false for
  `CTableMetadata`).
- **CLI:** `info` prints table fields for `kind=="ctable"`; `show` parses
  `table.b2z[start:stop]` and prints `Table.rows()` off the cframe (no `fetch`).
- **Tests:** `caterva2/tests/test_ctable.py` (25 tests) covering all 7 planned
  items + nested/non-identifier column-name regressions.

### Post-review hardening (done)

- `Table.rows()` default bounded to `[0:50)` (was an unbounded whole-table fetch).
- `/api/fetch` CTable slice: `is None` instead of truthiness (so `[0:0]` is
  empty, not the whole table), plus negative-index normalization and clamping.
- CLI `show --json`: numpy/bytes cell coercion via a `json.dumps` default.
- Filtering/sorting an unsupported dataset (e.g. a `.b2z`) now returns a clean
  htmx error instead of an uncaught `AssertionError` (500).

### Not built (intentionally deferred — see "Future extensions")

- JSON `/api/table` row endpoint — **removed** (Revision #3).
- CTable filtering/sorting (`where`/`sort_by`) over `/api/fetch` params.
- `POST /api/query` (group_by/join/describe); Arrow/CSV/Parquet export; `.b2d`.

## Open questions

- Should web table preview support filters immediately?
  - Proposed first pass: no. (Transport is settled — cframe `/api/fetch` — so
    adding it later is a param, not a new endpoint.)

- Should `.b2z` be included in `/api/fetch`?
  - **Decided: yes**, via the `CTable.to_cframe()` cframe path, for **whole and
    sliced** fetches (see Revision 2026-07-01 #1). `/api/download` remains for
    whole-file raw bytes.

- Should there be a JSON `/api/table` endpoint?
  - **Decided: removed, not just deferred** (Revision 2026-07-01 #3) — a
    consumer-less duplicate of the cframe slice. Revisit only if a real external
    non-blosc2 JSON consumer appears. Table *operations* never motivate it.

- Where do table operations (`where`/`sort`/`group_by`) live?
  - **Decided** (Revision 2026-07-01 #4): schema-preserving (`where`/`sort`) →
    params on `/api/fetch` (cframe); schema-changing (`group_by`/`join`/
    `describe`) → a future `POST /api/query` (cframe). Neither is `/api/table`.

- Should `Table` be a sibling of the array class or a `Dataset`?
  - **Decided: a `Dataset`** (Revision 2026-07-01 #5). Reparent to
    `File → Dataset → {Array, Table}`, pushing `blosc2.Operand` down onto
    `Array` so `Table` is a dataset without being an operand. See "Client class
    hierarchy".

- Should Caterva2 expose Arrow if Blosc2 can export Arrow?
  - Proposed first pass: no; avoid dependency/API expansion. If added later, it
    is a `format=` param on `/api/download`, not a JSON endpoint.

- Should table metadata include per-column compression stats?
  - Proposed first pass: no, unless Blosc2 exposes a stable cheap dict.

## Definition of done for MVP

The cframe transport (`CTable.to_cframe()`, blosc2) is a **prerequisite for the
whole MVP**, not a final add-on: preview itself now rides it. With it in place,
a user can:

```python
import caterva2 as cat2

client = cat2.Client("https://server")
root = client.get("@public")
table = root["example.b2z"]

print(table.nrows)
print(table.columns)
print(table.head(5))
table.download()
```

And in CLI:

```sh
cat2-client info @public/example.b2z
cat2-client show @public/example.b2z
```

And in the web UI:

- click a `.b2z`
- see metadata
- see a small row/column preview

**Slice/whole fetch as a blosc2 object** (via `CTable.to_cframe()`):

```python
ds = root["example.b2z"]
view = ds.slice(slice(10, 20))  # blosc2.CTable, mirroring ds.slice() for arrays
whole = ds[:]  # blosc2.CTable too — must NOT return raw zip bytes
```

This mirrors the existing array workflow (`ds.slice(...)` -> `blosc2.NDArray`)
and closes the symmetry gap. Both slice **and** whole must decode as a
`blosc2.CTable` on the client (Revision 2026-07-01 #1).
