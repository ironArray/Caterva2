# RemoteArray update and RemoteStore support

## Objective and agreed scope

Update Caterva2 for the current python-blosc2 API and support persisted
`RemoteStore` references with disk caches shared by multiple server processes.
Implement the necessary upstream features directly in
`/Users/faltet/blosc/python-blosc2` before its upcoming release.

- Replace `RemoteProxy` with `RemoteArray`, including persisted object markers.
  `RemoteProxy` was never released upstream: no legacy carrier compatibility or
  migration layer is required.
- Use sparse `RemoteArray` storage for the leaves of a server-managed
  `RemoteStore` under `CachePolicy.DISK`.
- Share discovery metadata and enforce one aggregate compressed-payload limit
  across all leaves of a store.
- Keep portable `.b2z` artifacts separate from writable private runtime caches.
- Support simultaneous handles in multiple processes sharing one local state
  directory. Initially serialize operations within each store; separate stores
  can proceed independently. Multi-host/network-filesystem sharing is outside
  this implementation.
- Reuse Caterva2's existing policy boundary, OS locks, SQLite storage ledger,
  admission, recovery, pruning, and export lifecycle.

## Findings from the initial analysis

The inspected python-blosc2 checkout and editable installation report
`4.13.0.dev0`. They expose `RemoteArray` and `RemoteStore`, but no `RemoteProxy`.
The array persistence marker is now `remote_array`.

Caterva2's `services/remote_proxy.py` authorizes remote array sources, while
`services/sparse_cache.py` manages private sparse generations. Array requests
already serialize under dataset and generation locks, including cache fills.
`services/storage_quota.py` coordinates storage admission across workers.

Upstream `StoreDiskCache` currently takes a nonblocking exclusive OS lock for
the lifetime of the store and its dependent handles. A second owner, including
another process, cannot open that cache. Discovery state and the aggregate
`CacheCoordinator` are process-local. Shortening the lifetime lock alone would
allow stale manifest writes and incorrect aggregate accounting.

Store artifacts use a `.b2z` archive containing `embed.b2e`, a `b2remote_store`
marker, a discovery manifest, and optional leaf caches. Live storage uses a
`.b2d` directory. The existing Caterva2 cache manager assumes a single flat
sparse array directory in several build, sync, and export operations.

Security must be integrated before enabling store resolution. Caterva2's
container adapter currently calls `blosc2.open()` directly; upstream recognizes
store artifacts and may initiate remote discovery during opening. RemoteStore
also lacks the authorized-filesystem injection path used for Caterva2 arrays.
Authorized standalone B2Z sparse attachment is currently rejected upstream.

## 1. Update Caterva2 to RemoteArray

- Update Python API references, capability checks, persisted marker checks and
  writers, server type dispatch, tests, examples, and documentation.
- Keep the existing `[server.remote_proxy]` configuration usable; renaming the
  upstream class does not require deployment configuration churn.
- Remove assumptions that a missing `RemoteProxy` means remote-reference
  inspection can be skipped.
- Update embedded-expression guards to recognize current remote descriptors.
- Use the local editable python-blosc2 checkout during development. Set the
  dependency floor to the release containing the completed required APIs once
  that release version is established.
- Run the existing remote-array, sparse-cache, quota, and API tests before
  extending their behavior for stores.

Primary files: `caterva2/services/remote_proxy.py`, `sparse_cache.py`,
`srv_utils.py`, `server.py`, `caterva2/client.py`, and `pyproject.toml`, plus
their tests, examples, and documentation.

## 2. Add shared RemoteStore runtime support upstream

Provide a server-facing attachment interface modeled on
`RemoteArray.with_sparse_cache()`. It must accept authorized discovery/transport
and private runtime storage separately from the portable store artifact.
Finalize the exact API after tracing the existing constructors and callers.

### Ownership and operation ordering

Use a process-local thread guard paired with an operation-scoped OS lock per
store. Multiple processes may retain handles, but every operation that observes
or mutates shared cache state must synchronize through this protocol:

1. Acquire the store lock and read the active generation and current manifest.
2. Reject stale child handles after a generation change; reopen or refresh
   cached leaf state as needed within the current generation.
3. Reconstruct aggregate retained-payload accounting from shared leaf state.
4. Perform discovery, cached reads, fills, or eviction.
5. Persist leaf and manifest changes before releasing ownership.

Apply the protocol to root/group operations and child RemoteArray operations,
including cleanup/finalization that writes metadata. Establish one consistent
lock order with Caterva2's initialization, dataset, generation, and leaf locks.
Avoid holding SQLite transactions during remote I/O.

### Sparse leaf storage and aggregate limits

- Reuse RemoteArray sparse storage and Proxy chunk/block machinery for leaves.
- Support authorized B2Z leaf attachment as well as HDF5 and Zarr sources.
- Preserve a single store-wide payload allowance; do not give every leaf an
  independent copy of that allowance.
- Reload accounting and eviction state under the store lock so one process
  sees another process's fills and evictions.
- Preserve discovery metadata when payload chunks are evicted.
- Keep source discovery and format-specific decoding in python-blosc2 rather
  than reproducing them in Caterva2.

Primary upstream files: `src/blosc2/remote_store.py`, `remote_store_cache.py`,
`remote_array.py`, and the existing cache coordinator in `proxy.py`.

## 3. Complete recovery, refresh, and maintenance primitives

- Record interrupted mutations before changing persistent cache state.
- Make manifest publication recoverable. The current atomic replacement of
  `active_generation.json` does not make in-place updates of the manifest in
  `embed.b2e` atomic.
- Recover or invalidate interrupted disposable cache data without resolving
  remote sources. Reuse existing array dirty-cache recovery where applicable.
- Preserve immutable-until-refresh source semantics. Build replacement
  discovery before publishing a new generation; failed refresh must preserve
  the current generation.
- Detect refresh from other processes and make old child handles stale.
- Coordinate retirement and deletion with active operations and exports.
- Expose cached-only reads, bounded offline trimming, payload accounting, and
  warm/cold export functionality needed by Caterva2, reusing array primitives.
- Ensure exported artifacts remain portable and never expose private cache
  paths or runtime credentials.

## 4. Integrate policy and container access in Caterva2

### Inspect before resolving

Recognize `b2remote_store` and inspect the manifest without dispatching through
an unrestricted `blosc2.open()`. Apply this boundary to listings, metadata,
mountability probes, fetches, chunks, downloads, and embedded references.

Validate uploaded manifest structure, node paths, leaf references, source
descriptors, and archive members before resolution or extraction. Treat
persisted metadata as untrusted. Validate remote leaf geometry against the
authorized source before using uploaded warm cache data.

### Authorize discovery and leaf reads

- Extend the existing HTTPS policy to store discovery and every leaf transport:
  explicit allowlist, public DNS addresses, address pinning, no redirects,
  credential-free URLs, timeouts, and bounded fetch concurrency.
- Add upstream authorized-filesystem injection and propagate it through B2Z,
  HDF5, Zarr, restored manifests, and refresh paths.
- Bound discovery metadata and node counts alongside per-array rank, logical
  size, and chunk limits. Choose documented defaults during implementation.
- Keep outbound resolution disabled by default. Known persisted discovery can
  be inspected without network access; additional discovery requires policy
  authorization.

### Serve stores through existing routes

Add a RemoteStore adapter beside the TreeStore/HDF5 adapters in `srv_utils.py`.
Support groups, listings, attributes, leaf metadata, sliced fetches, and
compressed chunk reads through existing server routes and browser mounts.
Report unsupported nodes consistently.

Keep child handles alive through reads and streamed responses, and close them
explicitly afterward. Update physical downloads to export warm or cold store
artifacts without mutating the hosted descriptor. Preserve the distinction
between logical leaf fetches and portable store downloads.

Preserve Caterva2's effective no-retention handling of requested MEMORY/NONE
policies; DISK uses the managed shared runtime cache.

## 5. Extend quota and generation lifecycle

Represent a store's manifest and sparse leaves as one managed store generation.
Extend the existing ledger only where its array assumptions require it.

- Enforce the aggregate compressed-payload cap separately from customer quota.
- Charge allocated filesystem storage, including metadata, directories, leaf
  caches, and retired generations awaiting cleanup.
- Admit discovery growth as well as payload fills; metadata is retained storage
  even though it is outside the evictable payload limit.
- Reuse operation records, generation guards, admission fallback, and startup
  reconciliation. A denied cache fill should still return successfully fetched
  data without retaining it.
- Adapt flat-directory sync/measurement and offline pruning for store layouts.
- Retire store generations on replacement, deletion, and source refresh.
- Reserve export working storage and retain ownership until response cleanup.
- Preserve metadata and safely account for valid uploaded warm caches when
  initializing private runtime storage; do not repeatedly restore evicted
  chunks from the portable artifact.

Primary files: `caterva2/services/sparse_cache.py`, `storage_quota.py`, and the
publication/download paths in `server.py`.

## 6. Verification and acceptance criteria

Extend existing test suites and multiprocessing patterns rather than adding a
new test framework. Use the `blosc2` conda environment for upstream Python,
builds, and tests, as required by its repository instructions.

### Upstream checks

- Two or more processes keep handles open against the same cache without an
  ownership error.
- Same-leaf and different-leaf reads return correct data and reuse previously
  retained chunks across processes.
- Concurrent discovery preserves both workers' discovered nodes.
- Aggregate eviction respects one store-wide limit across processes and leaves.
- Process death during leaf mutation or manifest publication leaves recoverable
  state; subsequent reads are correct.
- Refresh invalidates child handles across processes; failed refresh preserves
  the old generation.
- Offline trimming/recovery performs no network access.
- Warm and cold artifacts reopen correctly, with metadata preserved.
- Exercise B2Z, HDF5, and Zarr store sources with suitable deterministic fixtures.

### Caterva2 checks

- Existing RemoteArray, sparse cache, quota, and API tests pass with the renamed
  API and current persistence markers.
- Store listings, metadata, mounts, sliced fetches, chunks, and downloads work.
- Disabled/denied sources, malformed manifests, unsafe references, and alternate
  opening paths cannot bypass policy.
- Quota pressure, discovery growth, export cleanup, replacement/deletion, and
  worker death leave consistent accounting and no active untracked generation.
- Measure upstream request counts as well as values: correct data alone does
  not demonstrate cross-process cache reuse.
- Run repository formatting, lint, whitespace, and applicable pre-commit checks.
  Preserve comments/docstrings and exactly one trailing newline in edited files.

## Delivery order and performance boundary

Complete the RemoteArray update first, then upstream shared-store primitives,
then Caterva2 policy/container integration and quota lifecycle, followed by
end-to-end verification and documentation.

Benchmark competing workers reading the same and different leaves, recording
throughput, upstream traffic, and retained storage. Initial operations serialize
per store, matching the existing Caterva2 array-cache coordination model.
Parallel fills into different leaves require finer locks and coordinated
aggregate eviction; add them only if measurement shows store-level locking is
a bottleneck. This does not defer simultaneous open handles or shared-cache
correctness, both of which are required in the initial implementation.
