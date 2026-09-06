(cat2-server)=
# `cat2-server` -- Launching the Caterva2 server

This program launches the Caterva2 server, which handles API requests, serves the web interface, and manages data storage. To use it, the `server` extra needs to be installed:

```sh
python -m pip install caterva2[server]
```

## Usage

Running `cat2-server --help` provides information on its usage:

```
cat2-server [OPTION...]
```

### Options

-   `--listen <HOST:PORT>`: Sets the host and port where the server will listen for connections. The default is `localhost:8000`.
-   `--statedir <PATH>`: Specifies the directory where the server will store all its state, including datasets, user information, and other configuration. The default is `_caterva2/state` in the current working directory.
-   `--conf <PATH>`: Specifies the path to a TOML configuration file. Settings in this file will be used unless overridden by command-line options. If not provided, `cat2-server` looks for a `caterva2-server.toml` file in the current directory.

## Configuration

The server's behavior can be configured through a `caterva2-server.toml` file. Command-line arguments will always take precedence over settings defined in the configuration file. For example, you can define the listening address in your TOML file:

```toml
# caterva2.toml
listen = "0.0.0.0:8080"
```

And then simply run `cat2-server` to start it on all network interfaces on port 8080.

## Remote reference policy

A persisted `blosc2.RemoteProxy` is a B2ND carrier that asks Caterva2 to read
another dataset. Persisted `MEMORY` carriers are accepted under the same source
policy but execute without retained caching (using the same no-retention execution
path as `NONE`), avoiding unmanaged memory use on the server while preserving
the requested client limit for download. With a `DISK` cache, Caterva2 stores
fetched compressed chunks in a private sparse runtime generation. The public B2ND
carrier remains portable and contains only the descriptor and user metadata. The
default proxy limit is 256 MiB; `max_cache_bytes = None` remains unbounded per
proxy. With or without a customer quota, sparse lifecycle accounting remains
active; quota controls admission and pruning, while a denied fill falls back to a
no-retention read.
Caterva2 can inspect and report its stored shape, dtype, chunk, block, and proxy
metadata without contacting the source. Outbound resolution is disabled by
default.

The runtime supports public, credential-free HTTPS sources:

```toml
[server.remote_proxy]
enabled = true
allowed_hosts = ["datasets.example.org", "objects.example.org:8443"]
timeout = 30
max_nbytes = 1073741824
max_rank = 16
max_chunks = 10000000
max_concurrency = 8
```

The allowlist is mandatory and matches normalized host names and explicit
non-default ports exactly. Before connecting, Caterva2 resolves every address,
rejects loopback, private, link-local, multicast, and other non-public results,
and pins the accepted addresses into the HTTP connector. Redirects are disabled.
Source URLs containing user information, query parameters, or fragments are
also rejected. These checks are applied by the server even when the carrier was
created by a client that performed its own validation.

The limits validate the remote array's structure and bound connection time and
concurrent range fetches. They do not impose a network-work budget on each API
request. The proxy's own cache limit bounds its retained compressed payload,
while a configured customer `quota` additionally bounds stored dataset bytes,
including private sparse generations. A SQLite ledger shared with ordinary
writers admits ordinary publication work and sparse cache growth. A denied fill
does not fail a successfully fetched result.

Public S3 objects are supported through credential-free HTTPS object URLs.
Native `s3://` resolution, private-source credentials, and remote references
embedded inside persisted expressions are not enabled.

Physical downloads include valid warm proxy chunks by default. Clients can pass
`include_cache=false` to download a cold carrier without mutating the hosted
proxy. Logical `api/fetch` requests continue to return array data.

## Customer storage admission

With `[server] quota` enabled, `storage.sqlite` coordinates workers sharing one
customer's local state directory. It uses Python's standard-library `sqlite3`,
independently of authentication; no additional dependency is needed. Multi-host
or network-filesystem sharing is not supported. All workers must use the same
configuration, and configuration changes require quiescent writers.

The quota charges regular dataset files in `public`, `shared`, and `personal` by
their apparent length (`st_size`), including proxy headers and metadata. This is
an explicit change from the old whole-state-directory scan: peer-cache files
keep their separate `peer_cache_quota`; media, authentication state, SQLite/WAL,
directories and lock sidecars are operational storage, not customer data charges.
Dataset symlinks are rejected. Local `publish_root` must be outside the state
directory. Administrators must provision operational headroom separately.

Uploads, imports, expressions, append/chunk writes, HDF5 unfolding, notebooks,
copies, moves, deletions, and remote DISK fills share admission. Under pressure,
the server prunes least-recently-used sparse chunks in bounded batches. Ordinary
datasets are never automatically pruned. A proxy's own payload cap still applies.

`[server] quota_work_bytes` (default `"1G"`) bounds aggregate reserved staging
and export artifacts separately from customer quota. Sparse cache generations
charge allocated filesystem blocks, including frame metadata and directory
entries. This is not a hard RAM limit or a physical-volume guarantee; provision
and monitor the underlying volume accordingly.

Per-path OS locks and generation checks prevent stale candidates from replacing
newer data. Durable operation records survive worker death; recovery inspects the
atomic target and reclaims staging only after acquiring the dead owner's lock.
Startup reconciles offline changes. Do not edit managed files outside Caterva2
while the server is running; external writers cannot be covered by admission.
If usage exceeds a reduced quota, reads and shrinking operations remain allowed
but positive growth is denied. Ledger failure never permits an unaccounted write.

Directory/archive operations are admitted file by file, not as one transaction;
an error may leave earlier files completed. Moves currently copy then delete and
therefore need capacity for both copies. Ordinary quota denial returns HTTP 400,
generation conflicts return 409, and ledger errors return 503. Remote reads may
instead fall back to no retention. `StorageQuota.usage()` exposes committed,
reserved and disk working bytes for internal diagnostics; there is no new public
administration endpoint.

## Sparse remote cache lifecycle

Caterva2 uses private sparse runtime generations for retained DISK caches. The
implementation requires Python-Blosc2's authorized-source `with_sparse_cache`,
atomic `read_cached`, and offline `trim_sparse_cache` APIs, plus the C-Blosc2
sparse eviction improvements bundled after Python-Blosc2 `a77ac97b`.

Mutable DISK chunks live under `.remote-cache/<object UUID>/<generation UUID>`
outside dataset roots. The public file stays a portable carrier. First authorized
access copies valid warm seed chunks and then replaces the public carrier with a
cold copy preserving user metadata. Subsequent misses never reimport evicted seed
chunks. MEMORY and NONE continue without retention. Upload itself makes no remote
request.

The schema-v2 registry exists even when customer quota is disabled. Sparse files,
frame metadata, locks within generations, and generation/object directories count
by allocated filesystem blocks, falling back to apparent length where allocation
information is unavailable. Ordinary datasets retain their existing apparent-size
accounting. Builds and fills reserve estimates in the same ledger used by uploads.
Actual growth can overshoot customer quota; further fills are suspended until
reclamation reaches a 90% low watermark. Ordinary datasets are never evicted.

Removal/replacement retires the old generation; private bytes remain charged until
cleanup actually removes them. Copy and move give the destination a cold carrier
and independent identity. Maintenance wakes every 30 seconds, skips busy owners,
and retries retirement, interrupted-operation recovery, and pruning. Cache misses
still return source data if admission or local retention fails. Warm downloads use
private, immutable export artifacts with ETags and support ranges; completion and
cancellation release artifact ownership. `include_cache=false` stays cold and
non-mutating. UI consumers that require bytes retain their existing in-memory
response contract.

The implementation intentionally uses conservative settings: full-generation
measurement/fsync after writes, whole-generation discard after interrupted work,
at most 64 chunks/four generations per prune batch, and 1 GiB operational free-space
headroom for migration. One warm export reserves the full configured
`quota_work_bytes` budget until its response finishes. These choices establish an
endpoint benchmark baseline; incremental accounting, tighter staging estimates,
bounded inventories, and configurable maintenance tuning remain follow-up work.
Process-death recovery is covered; this does not claim power-loss atomicity or a
hard filesystem/RAM bound.

Schema v2 is the initial Caterva2 runtime schema. The previous contiguous-carrier
implementation is retained only in the benchmark report and is not a supported
runtime backend.
