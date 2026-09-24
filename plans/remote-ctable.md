# Full RemoteCTable integration

## Implementation status and API consistency review (2026-09-24)

The first implementation is committed in Caterva2 as `dfd5528` and `4be107c`,
with python-blosc2 improvements in `5f92a80d` and `d6554ad5`. It covers root,
nested, and linked tables, table browsing and queries, secondary-source policy
hooks, batch/linked cache migration and trimming, and explicit reference refresh.
The dependency floor is now `blosc2>=4.14.0`.

The last implementation runs reported 437 passed / 173 skipped in Caterva2 and
487 passed / 5 skipped in the relevant upstream suites, with pre-commit passing.
These are historical results, not a fresh run for this documentation review.
They establish a useful integration baseline, but do not establish every item
in the original acceptance matrix. The implementation needs the following
follow-up work before claiming complete API parity or release readiness.

The findings below come from current source inspection and small local probes.
The original sections below remain the implementation specification; the
decisions here supersede conflicting field-response guidance there.

### P0: Make refresh authorization and replacement consistent with other writers

**Observed:** `server.current_active_user` becomes a dependency returning `None`
when login is disabled. Upload and remove explicitly reject a missing user;
`refresh_remote_reference()` does not. `get_writable_path()` permits the public
root with that value, so refresh can proceed anonymously in this configuration.

- Add the same explicit authentication guard used by existing writers, before
  inspecting the artifact or performing outbound I/O. Test both enabled and
  disabled login configurations and assert denied calls leave source traffic,
  carrier bytes, and quota state unchanged.
- Capture the inspected descriptor and its replacement signature from the same
  snapshot. Currently inspection happens before `replace()` obtains a new
  signature; a replacement in between can let stale discovery overwrite the
  newer reference. Reuse `StorageQuota.snapshot()` and the existing publication
  comparison rather than introducing another locking protocol. A racing write
  must result in the existing 409 response and preserve the winner's bytes.
- Keep successful refresh atomic and preserve the existing reference on failed
  discovery, policy denial, quota denial, or publication conflict. The current
  failure test checks carrier bytes; also check generation and accounting state.

### P1: Define one table projection and decoding contract

**Observed:** `ServerStoreTable.fetch(field=...)` always returns a one-column
CTable. Local table field requests instead execute `container[field]` and enter
the general array/SChunk dispatch. A fixed-width local field is a `Column`, not
an NDArray, SChunk, or CTable, so that route is not equivalent to remote fetch.
The client also chooses its decoder from a `Table` instance or a `.b2z` suffix;
a string such as `store.b2z/table` selects the array decoder.

- Make every table fetch, including a single-column projection, return a CTable
  cframe for local and remote tables. This preserves nulls, dictionaries, nested
  values, and schema, and avoids a dtype-dependent response type. Structured
  NDArray field fetches keep their NDArray contract. Document the correction to
  the previous local-table behavior rather than claiming backward compatibility.
- Resolve the response kind from dataset metadata for string paths; reuse
  metadata already held by `Table` objects. Remove suffix-based table guessing.
  Do not try unrelated binary decoders until one happens to succeed.
- Fix `Client.get_slice(..., key=..., field=...)`: it currently sends only
  `field`, dropping `key` (also described as ignored in its docstring). Table
  projection must preserve the requested row window; update documentation and
  test bounded transfers through the real Python client.
- Allow table filtering, row windows, and projection together, in that order.
  The HTTP route currently rejects `filter` plus `field`, although the remote
  adapter can apply both. Use the same table pipeline for GET and POST fetch,
  local and remote sources. Keep unrelated array rules explicit.
- Validate with standalone and nested paths, both `Table` objects and strings,
  fixed-width and nullable/batch/dictionary columns, and empty projections by
  row range. Assert decoded type, schema, nulls, and values, not only HTTP 200.

### P1: Normalize table input validation and error responses

**Observed:** `parse_segment()` accepts slice steps, but `ctable_row_range()`
ignores them and ignores extra tuple dimensions. The Python client rejects
non-unit steps, so raw HTTP and client calls disagree. Error handling also
differs: the remote adapter translates some filter errors and missing fields,
while local fields and filters take other branches; source errors are mapped
to 502 in refresh but not consistently in table fetch.

- For this release, reject non-unit steps (including zero and reverse steps)
  and extra table dimensions with 400 in the shared row-range helper. Preserve
  the established negative-index and clipped-window behavior. Full stride
  support can be a separate feature; do not silently widen selections.
- Validate field names, filter expressions, and sort columns through the same
  table operation path. Return 400 for invalid query parameters, 404 for a
  missing dataset/member, 403 for policy denial, 409 for generation/publication
  conflicts, and 502 for recognized upstream transport or malformed-source
  failures. Preserve existing quota/database handlers. Do not turn arbitrary
  programming exceptions into client errors.
- Add parity cases for GET/POST fetch and browser rendering, including unknown
  fields, malformed filters, unsupported selections, unavailable sources, and
  stale references. Browser errors should remain understandable HTML responses.

### P2: Complete refresh and query semantics in the client and adapter

- Add `Client.refresh(path)` for the existing reference-refresh endpoint and
  document that it accepts a hosted RemoteStore/RemoteCTable carrier. A path
  inside a store must explicitly identify the owning carrier to refresh; do not
  silently refresh siblings. RemoteArray refresh is not implemented by this
  endpoint and must receive a clear unsupported-target response. A missing
  path should receive 404, rather than the current generic 400.
- Define how callers reload cached `Table`/`Group` metadata after refresh; return
  the refreshed dataset object from the convenience method or explicitly reload
  its metadata. Avoid leaving `nrows` and schema silently stale.
- The internal adapter's `where()` replaces any previous filter, and
  `sort_by(..., view=False)` ignores `view`. Either implement the semantics of
  the CTable methods it imitates or narrow the internal interface to the browser
  operations actually needed. Do not expose misleading method compatibility.
- Avoid repeated execution: adapter construction reads metadata, `where()` and
  `sort_by()` execute queries to obtain metadata, and `slice()` executes them
  again. Reuse one operation to obtain the requested page and required counts.
  Move all blocking remote opens in fetch to the thread pool; currently the
  ordinary unfiltered root/member branches can still open on the event loop.

### P2: Finish the upstream integration contract and acceptance evidence

- Before 4.14.0, finalize supported python-blosc2 APIs for source authorization,
  batch/manifest validation, operation locking, and cold descriptor export.
  Caterva2 still uses underscored hooks, `runtime._owner`, and manifest/ZIP
  construction. Improve upstream APIs directly and then remove that dependency
  on private state; do not add Caterva2 compatibility wrappers around it.
- Audit the table-level concurrency limit, including batches and index reads;
  setting `max_concurrency` on physical array sources alone does not prove the
  whole table obeys server policy.
- Reproduce the exploratory HDF5 indexed-query case that refetched data after
  quota admission was suspended, even following warm reads. That assertion was
  not retained in the final suite. Determine whether the probe misses retained
  dependencies or the warm-up never retained them; count actual source bytes
  and bulk reads as well as `get_chunk()` calls. Fix cache behavior upstream if
  needed, and retain a regression for an established fully cached indexed query.
- Complete explicit remote-table Python-client and peer cases, process-shared
  indexed/batch cached-only reads, and interrupted refresh/export recovery.
  Record which skip conditions leave acceptance items untested. Measure cold
  and warm paging/query traffic and retained storage with competing workers
  before considering changes to lock granularity.

Implement P0 first, then projection/decoding and validation together, followed
by client conveniences and the remaining upstream/acceptance work. Extend the
existing tests; keep fixes in the repository that owns the behavior. This
review updates the plan only and does not implement these follow-up changes.

## Scope and baseline

Extend the RemoteArray/RemoteStore integration described in
[remote-store.md](remote-store.md) to support read-only RemoteCTable datasets
through Caterva2's existing table API, browser, and Python client. Cover both
standalone references and tables inside stores, including all column formats
and remote index reads supported by the installed python-blosc2.

Baseline inspected on 2026-09-24:

- Caterva2 commit `501c108` already serves HDF5 table members using
  `ServerStoreTable`, with a regression test for native index reuse.
- The local python-blosc2 checkout is at `286b7da9`, the merge of
  [PR #725](https://github.com/Blosc/python-blosc2/pull/725). The `blosc2`
  conda environment imports that checkout and reports `4.13.2.dev0`.
- Inspection used the installed source and tests; the GitHub PR page/API was
  unavailable during planning.
- A local memory-filesystem probe created and saved a RemoteCTable, then
  confirmed that Caterva2 currently reports it as `Directory` and opens it
  through `ServerRemoteStore`.

Full support means parity with Caterva2's existing CTable read operations and
preservation of upstream remote semantics. Source writes and remote index
creation are not supported by RemoteCTable. Query results remain materialized
CTable cframes; physical downloads remain portable remote-reference artifacts.
Arbitrary upstream Python methods do not each need a new HTTP endpoint.

## Upstream changes and release target

RemoteCTable has not been released. Its first release will be python-blosc2
**4.14.0**; the inspected `4.13.2.dev0` version is a development identifier,
not the intended release version.

Improve python-blosc2 directly in `/Users/faltet/blosc/python-blosc2` wherever
its API or implementation prevents this integration. Fix the underlying
behavior and add suitable server-facing APIs before integrating them into
Caterva2. Do not introduce Caterva2 monkey patches, copied upstream internals,
parallel cache/query implementations, or compatibility workarounds for the
unreleased RemoteCTable API. Adjust that API as needed before 4.14.0.

During implementation, make focused commits in python-blosc2 as appropriate,
with regression tests and documentation for each upstream change. This is
authorized as part of the integration work. Follow that repository's agent
instructions and use the `blosc2` conda environment. Keep upstream and
Caterva2 commits separate and record the upstream commits required by the
integration. Develop against the editable checkout; require `blosc2>=4.14.0`
for the released Caterva2 integration.

The following upstream gaps were identified by source inspection. Begin with
reproducing tests, then implement and verify the fixes:

1. **RemoteArray-backed table columns:**
   `RemoteArray._from_carrier_with_owner()` opens the secondary source without
   forwarding the owner's authorized filesystem or invoking its source
   validator. Add authorization/transport selection before source I/O and
   preserve geometry/resource validation.
2. **Linked RemoteStores:** deferred linked-store opening does not propagate
   the parent's filesystem and validation hooks, including shared-cache and
   artifact restoration paths. Provide authorization for each destination;
   cross-host references need separately authorized transports, not blind
   reuse of the parent's host-pinned filesystem. Preserve these hooks through
   refresh and nested references.
3. **Batch resource validation:** `open_ctable_batch()` has no equivalent of
   the array source-validation hook. Add validation suitable for batch-backed
   columns and related payloads, early enough to enforce limits before payload
   reads or unbounded allocation. Define the hook contract upstream.
4. **Cached-only table operations:** add a supported operation covering a
   table query's columns, masks, batches, dictionaries, and indexes. Return a
   clear cache miss without fetching missing data; coordinate the check/read
   with shared-cache locking and generation changes. Caterva2 can then serve
   warm queries under quota pressure and apply its normal fallback on a miss.

Also improve upstream inspection, attachment, cold-export, or lifecycle APIs
if implementation reveals that Caterva2 would otherwise need to manipulate
private owner state or reproduce format logic. Keep deployment policy, HTTP
responses, browser integration, and customer-quota decisions in Caterva2;
keep source resolution, storage formats, and cache correctness in python-blosc2.

## Existing machinery and concrete gaps

RemoteCTable inherits from CTable and uses the RemoteStore owner, manifest,
transport, and aggregate cache. Saved references carry `b2remote_store` and
`b2remote_manifest`; the selected root node has kind `ctable`. There is no
separate persisted table marker to invent.

Reuse `services/remote_store.py`, `ServerStoreTable`, the `store_operation`
quota path, and existing CTable metadata/client/rendering conventions. Keep
one generation per hosted reference, with table members sharing their store's
generation and budget.

Observed gaps:

1. `open_b2`, `open_container`, and `read_metadata` classify every remote-store
   artifact as a directory. Directory counts include only array nodes, and
   live recursive discovery also omits table leaves.
2. The non-DISK constructor does not allow a table root. Its post-operation
   validation also replaces CTable metadata with `None`, which the current
   upstream manifest validator rejects.
3. `ServerStoreTable` implements metadata and basic fetch only. Standalone
   filtering, browser paging/sorting, and generic table dispatch do not share
   that operation-scoped path.
4. Field fetch uses `blosc2.asarray(view[field][...])`; this requires a format
   audit for nullable, variable-length, nested, and dictionary columns.
5. Cold export and warm-carrier cleanup consider only `caches`. Current
   manifests also contain `batch_caches` and nested `linked` artifacts.
6. Array geometry validation does not by itself cover table batch sources,
   dictionary vocabularies, index payloads, or secondary remote references.
7. The upstream cached-only `RemoteStore.read_cached` primitive addresses
   array leaves. Table fetch has no equivalent callback before quota admission.

## 1. Recognize and open table references safely

Add one shared dispatch decision based on the validated manifest root:
`manifest['nodes'][manifest['source'].get('dataset', '')][0]`.

- A table root returns the operation-scoped table adapter at relative key `''`.
  A group root returns the existing store adapter. Keep root-array handling
  explicit and consistent with RemoteArray behavior.
- Apply the decision to metadata, fetch/filter, mountability, browser opening,
  downloads/publication, and embedded-reference checks. A standalone table
  appears as a table leaf; internal column/index files are not browsable datasets.
- Count and discover both array and table leaves in group listings. Support a
  reference selecting a nested table, not just a source whose table is at root.
- Preserve table and linked-reference metadata when validating runtime state.
- Use a supported upstream attachment/operation interface for standalone and
  nested tables, reusing the common RemoteStore cache machinery. Improve that
  interface upstream where necessary so Caterva2 does not need private root
  flags or additional private-owner manipulation to treat a table as a store.
  Finalize the interface with the upstream changes before wiring dispatch.
- Verify NONE and effective no-retention MEMORY as well as managed DISK.
  Keep table/column/view lifetimes inside each operation and materialize the
  response before closing them. Never cache live remote views in web state.

Primary files: `remote_store.py`, `srv_utils.py`, `server.py`, `sparse_cache.py`.

## 2. Complete the policy boundary for all table data

Perform this alongside dispatch, before enabling additional source paths.
Keep outbound access disabled by default and reuse `[server.remote_proxy]`.

- Audit transport creation for B2Z tables, PyTables/HDF5 tables, column
  RemoteArray carriers, linked remote references, indexes, dictionaries,
  validity/deletion masks, and any HDF5 metadata sidecars. Every destination
  must pass the allowlist, public-address validation, pinning, redirect,
  timeout, and credential rules before I/O, including refresh and restoration.
- Use the upstream authorization/transport hooks completed above for secondary
  sources. Do not allow an unrestricted fallback to `blosc2.open` or fsspec.
- Validate schemas, member paths, companions, index descriptors, archive
  entries, nested manifests, and warm-cache geometry before using uploaded
  state. Bound nested metadata in aggregate and guard reference cycles/depth.
  Preserve upstream rejection of unsafe object serializers.
- Extend resource validation to table row/column counts and non-array payload
  units. Keep existing array limits on physical column components and bound
  batch/index metadata before allocation. Choose documented defaults from
  representative supported fixtures, with explicit oversized-unit behavior.
- Apply server concurrency limits to the table itself. Upstream defaults are
  8 concurrent reads, an 8 MiB metadata buffer, and a 64 MiB row buffer; those
  buffers are transport batching targets, not hard process-memory limits.
- Inspect persisted schema/discovery without outbound access where available;
  fetching missing metadata still requires authorization. Policy failures must
  produce consistent client errors through every entry point.

Primary files: `remote_store.py`, `remote_proxy.py`, policy documentation/tests;
upstream `remote_store.py`, `remote_array.py`, `remote_ctable.py`,
`ctable_storage.py`, and `remote_batch.py` for the required policy hooks.

## 3. Integrate complete table reads

Use a single table operation to apply the query, select the requested row
window/columns, materialize, and serialize while its owner is alive.

- Serve schema, row/column counts, attributes, user metadata, sizes, and
  compression information using `CTableMetadata`, without downloading columns
  just to identify the dataset. Define unavailable size values consistently.
- Match local CTable slicing, integer/negative/clipped/empty windows, filtering,
  and supported field selection. Apply filters before selecting the result
  window. Preserve the API's existing parameter validation.
- Exercise fixed-width scalars/vectors, fixed strings, nullable columns, UTF-8,
  batch strings/lists, nested lists, structs/objects with safe serializers,
  dictionaries, and deleted rows. Preserve schema and null semantics in cframes.
- Keep the existing NDArray field-response contract where representable. For
  columns that cannot preserve their type/null semantics in an NDArray, define
  an explicit one-column CTable response and update client decoding together;
  do not silently coerce values through NumPy. Verify the corresponding local
  CTable behavior so local and remote tables agree.
- Delegate filtering/index selection to upstream, including summary, ordered,
  positional, membership, and imported PyTables indexes where supported.
  Verify selective queries avoid unrelated payload reads and warm queries
  reuse retained index data. Do not implement a second query engine.
- Return a clear unsupported-operation response for table-level compressed
  chunk requests. A table is not one chunked array; only explicitly supported
  physical array-column operations may use the array chunk contract.
- Map missing fields, invalid filters, unsupported column layouts, stale
  generations, and source failures to existing API error conventions.

Primary files: `remote_store.py`, fetch/filter/chunk routes in `server.py`,
`srv_utils.py`, and `client.py` where response decoding needs adjustment.

## 4. Complete cache, quota, and artifact lifecycle

Keep the existing per-store serialization, ledger, generation locks, recovery,
and export ownership. Extend concrete assumptions instead of adding another
cache manager or a separate table ledger kind.

- Account for all retained column chunks, batch payloads, dictionary/index
  data, HDF5 shared-record/source caches, and linked payloads under one owner
  budget. Charge metadata and allocated filesystem storage to customer quota.
- Use the new upstream table cached-only operation so a warm table read
  can succeed without reserving space for another fill. It must cover the
  query's index, masks, and payload dependencies atomically. On a genuine miss
  and denied retention, use the established no-retention fallback.
- Normalize cold manifests completely: clear array caches, batch caches, and
  retained linked artifacts while keeping enough discovery/reference metadata
  to reopen. Test a batch-only warm artifact explicitly.
- Restore validated warm state once, then remove its retained payload from the
  portable carrier using the existing publication protocol. Do not repeatedly
  resurrect evicted batch, index, or linked caches from that carrier.
- Verify trimming/recovery recognizes every table storage component and can
  operate without network access. Exercise concurrent workers, interrupted
  fills/exports, replacement/deletion, and restart reconciliation.
- Preserve source snapshots until explicit replacement/refresh. Integrate
  table refresh with generation retirement; old views must become stale and
  failed refresh must preserve the previous usable generation. First establish
  the server refresh entry point, which is not currently exposed by these
  routes, rather than accidentally refreshing on normal reads.
- Export warm/cold table references through the existing download/publication
  lifecycle, including staging admission, response cleanup, metadata, and
  mutability flags. Reopening must return RemoteCTable and expose neither
  private runtime paths nor credentials.

Primary files: `sparse_cache.py`, `remote_store.py`, `storage_quota.py` only if
needed, and download/publication routes in `server.py`.

## 5. Browser, Python client, and peer compatibility

- Route both standalone and nested remote tables through the existing CTable
  grid: selected columns, row paging, ascending/descending sort, and the
  existing table filter behavior. Execute remote work in the thread pool and
  materialize only the displayed result window. Sorting/filtering may still
  need wider upstream reads; avoid promising constant-cost queries.
- Extend the table adapter's operation interface for browser needs rather than
  pretending it is a live CTable outside the owner lock.
- Ensure metadata selects `caterva2.Table`, and verify `slice`, `where`,
  `rows`, `head`, and downloads for standalone and container-member paths.
- Verify peers consume the same table metadata and cframes, including formats
  already handled by their pass-through path. Reuse existing peer caching;
  this work does not replace the peer transport with RemoteCTable.
- Document reference creation/upload, supported B2Z and PyTables/HDF5 sources,
  policy configuration, cache behavior, indexes, refresh, and warm/cold
  downloads. Zarr remains a store/array source unless upstream adds tables.
- Set the release dependency floor to `blosc2>=4.14.0`; the current
  `>=4.13.0.dev0` does not guarantee RemoteCTable or the required improvements.

## Verification and delivery

Extend existing pytest suites and fixtures; no new framework. Use the Python
executable in the `blosc2` conda environment. `conda run` currently emits an
activation error in this shell, while the environment's Python works directly.

| Area | Required evidence |
| --- | --- |
| Dispatch | Standalone root and selected nested-table references report `ctable`; group listings include tables and hide internals. |
| Read parity | Local and remote table values, schemas, nulls, deleted rows, slices, filters, projections, and browser sorting agree. |
| Sources | B2Z root/nested tables and HDF5/PyTables tables; mixed array/table stores. |
| Policies | DISK plus effective no-retention NONE/MEMORY, including quota fallback. |
| Security | No unauthorized I/O through discovery, secondary carriers, indexes, linked references, restoration, or refresh. |
| Cache | Count upstream requests/bytes, verify index reuse and cross-process hits, and enforce one aggregate budget. |
| Lifecycle | Batch-only and mixed warm exports, cold reopen, trim, process death, replacement, refresh failure, and cleanup retain consistent accounting. |
| User surfaces | API, Python Table client, browser, and existing peer paths work for standalone and nested tables. |

Start with `test_remote_store.py`, `test_remote_proxy.py`, `test_sparse_cache.py`,
`test_storage_quota.py`, `test_storage_quota_api.py`, and `test_ctable.py`; add
targeted peer cases in `test_peers.py`. Use upstream RemoteCTable fixtures as
references without duplicating its entire test suite. Run applicable pre-commit
hooks and the broader regression suite once integration is complete.

Upstream acceptance must include denied secondary destinations with zero
unauthorized requests, hook propagation through restoration/refresh, batch
limit rejection, and cached-only hits/misses for indexed and batch-backed
queries across processes. Commit these regressions with their python-blosc2
fixes and run the relevant upstream remote-array/store/table suites before
depending on the changes in Caterva2.

Delivery order:

1. Reproduce and fix the four upstream gaps, finalize supported integration
   APIs, and commit the python-blosc2 changes with tests and documentation for
   4.14.0. Resolve further upstream gaps there as they are discovered.
2. Add Caterva2 regressions for dispatch, no-retention validation, and batch
   cold export; fix those paths using the improved upstream APIs.
3. Complete table read/serialization semantics and metadata for all column types.
4. Complete quota, cached-only reads, warm/cold lifecycle, and refresh behavior.
5. Wire browser/client/peer parity, documentation, and the 4.14.0 dependency floor.
6. Run end-to-end and multiprocessing acceptance tests. Measure cold/warm
   paging and indexed queries, upstream traffic, retained storage, and competing
   workers. Change lock granularity only if these measurements justify it.

Done means the matrix above passes for standalone and nested tables, with no
RemoteArray/RemoteStore regressions. Recognition alone is not completion.
