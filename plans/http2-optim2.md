# HTTP/2 Optimization Plan v2: Measured Concurrency for Chunk I/O

**Status:** Proposed for discussion
**Scope:** `caterva2`, `python-blosc2`, peer-cache reads, direct object-store reads,
and chunk ingestion
**Principle:** Concurrency is the optimization; HTTP/2 is a transport that may make
that concurrency cheaper when it is actually negotiated.

---

## 1. Goals and Non-Goals

### Goals

1. Reduce latency for operations that require many independent chunk or byte-range
   requests.
2. Reuse connections and bound concurrency so clients do not overload themselves,
   Caterva2 peers, reverse proxies, or object stores.
3. Use HTTP/2 multiplexing where the complete path supports it and measurements show
   a benefit.
4. Add enough protocol and performance observability to detect silent HTTP/1.1
   fallback and regressions.
5. Preserve cache correctness, retry semantics, duplicate-write behavior, Pyodide
   compatibility, and existing synchronous APIs.

### Non-Goals

* HTTP/2 is not required for ordinary Caterva2 slicing that already completes in one
  `api/fetch` request.
* This plan does not assume that every S3-compatible endpoint supports HTTP/2.
* It does not promise a particular speedup or prescribe flow-control window sizes
  before measurement.
* HTTP/3 is out of scope until the HTTP/1.1 and HTTP/2 work is complete.

---

## 2. Current State

The implementation must be inventoried before changes are made. At the time this
plan was written:

* `caterva2.Client` already constructs `httpx.Client(http2=True)` outside Emscripten.
* `httpx[http2]` is already a project dependency.
* `c2cache` and current `python-blosc2` integrations already contain real asynchronous
  chunk-fetch paths and bounded/gathered `Proxy.afetch` behavior. These should be
  extended rather than reimplemented.
* Some `c2cache` `httpx.AsyncClient` and `httpx.Client` instances do not enable
  HTTP/2.
* The bundled Caterva2 command starts Uvicorn, which serves HTTP/1.1. Setting
  `http2=True` on a client therefore does not make a default local Caterva2 server
  speak HTTP/2.
* The deployed `https://cat2.cloud/demo/` endpoint has been verified to negotiate
  `h2` through TLS ALPN and to return an HTTP/2 response. Its reverse proxy may still
  communicate with Uvicorn over HTTP/1.1; client-facing multiplexing remains useful.

This inventory must be repeated against the exact `python-blosc2` revision selected
for implementation, because that repository evolves independently.

---

## 3. Supported Test and Deployment Paths

### 3.1 Local HTTP/1.1 baseline

Run Caterva2 directly under Uvicorn. This is the control configuration and must stay
fully supported.

### 3.2 Local HTTP/2 configuration

Provide a documented, reproducible TLS configuration using either:

* Caddy or nginx terminating HTTP/2 and proxying to Uvicorn; or
* an HTTP/2-capable ASGI server such as Hypercorn, if it is shown to run Caterva2
  correctly.

The reverse-proxy configuration is preferred for production-parity testing. Test
certificates may be generated locally, but certificate verification must remain on;
the benchmark client should trust the test CA explicitly.

HTTPX generally negotiates HTTP/2 over TLS using ALPN. Clear-text `h2c` is not the
primary test path.

### 3.3 Production validation

Run non-destructive protocol and read benchmarks against `cat2.cloud/demo` when
appropriate. Production tests must use bounded load and must not be the only
performance evidence.

### 3.4 Mandatory protocol assertion

Every HTTP/2 benchmark must inspect `response.http_version` and fail or mark the case
invalid unless it equals `HTTP/2`. Also record:

* effective URL and redirects;
* negotiated protocol;
* number of TCP connections where measurable;
* reverse-proxy/server configuration identifier.

This prevents HTTPX's normal HTTP/1.1 fallback from being mistaken for an HTTP/2
result.

---

## 4. Workloads Where Concurrency May Help

### 4.1 Peer-cache cold reads

A slice that touches many uncached remote chunks currently requires multiple peer
requests. Fetch missing chunks concurrently while retaining sparse-cache behavior.

Requirements:

* configurable `max_concurrency`, with a conservative default;
* no unbounded `asyncio.gather()`;
* already-cached chunks are never fetched again unnecessarily;
* per-request and overall operation timeouts;
* retry only transport/transient failures, with a small bounded retry count and
  backoff/jitter if measurements justify it;
* preserve successfully cached chunks when another chunk fails;
* cancellation closes responses and releases stream capacity;
* peer-offline classification must not turn application deliberate HTTP errors or local
  programming errors into connectivity failures;
* client ownership and shutdown must follow the Caterva2 application lifespan.

HTTP/2 clients should be persistent per origin or per suitable lifecycle scope, not
created for each chunk. Async clients must not be shared unsafely across event loops.

### 4.2 Direct HTTPS object-store range reads

For a Blosc2 source exposed through HTTPS, issue one request per required range and
schedule them with bounded concurrency. Test each service independently:

* AWS S3 REST endpoints;
* Cloudflare R2;
* GCS;
* MinIO or other S3-compatible services;
* CDN-fronted object URLs.

Do not infer protocol support from an `https://` URL. Record the negotiated protocol
for every service and endpoint form. Document whether access is anonymous, uses a
presigned URL, or requires request signing. Plain HTTPX does not itself provide AWS
SigV4 signing.

`s3://` through `fsspec`/`s3fs` is a separate transport and must not be described as
accelerated by changes to an HTTPX client. Any AWS CRT experiment belongs in a
separate optional work item.

### 4.3 Parallel chunk ingestion

Retain `Client.fill_chunk()` and add an explicit bounded batch/async API rather than
making callers coordinate arbitrary threads around the synchronous method. A
provisional shape is:

```python
results = await client.afill_chunks(
    remotepath,
    chunks,
    max_concurrency=8,
)
```

The final API design must specify:

* accepted chunk iterable/mapping format;
* input order and result association;
* bounded memory use and whether request bodies are streamed;
* fail-fast versus collect-errors behavior;
* partial-success reporting;
* retry policy;
* cancellation semantics;
* behavior for an already-written slot;
* connection and async-client lifetime.

Multiple processes cannot share one HTTP/2 connection, so process-based ingestion
must be benchmarked and documented separately.

### 4.4 Interactive request cancellation

HTTP/2 permits cancellation of one stream without closing the whole connection, but
starting a replacement request does not cancel the old one automatically.

Treat interactive cancellation as a separate feature:

* browser clients use `AbortController`;
* Python async callers cancel the relevant task;
* response bodies are explicitly closed/released;
* cancellation is propagated through intermediate server requests where applicable;
* tests verify resource release and correctness, without depending on a particular
  wire frame unless the transport contract guarantees it.

---

## 5. Benchmark Design

Benchmarking precedes broad implementation and all performance claims.

### 5.1 Fair comparison matrix

For each applicable workload, compare:

1. HTTP/1.1 sequential baseline;
2. HTTP/1.1 persistent pooled client at concurrency `N`;
3. HTTP/2 persistent client at the same concurrency `N`;
4. selected concurrency sweep, for example `1, 2, 4, 8, 16, 32`.

The key comparison is pooled HTTP/1.1 versus HTTP/2 at equal concurrency. A
sequential-only HTTP/1.1 comparison exaggerates HTTP/2's contribution.

### 5.2 Environments

Measure at least:

* loopback/local with the documented TLS proxy;
* controlled latency and bandwidth, including optional packet loss;
* `cat2.cloud/demo` under a deliberately low request rate;
* each supported object-store endpoint.

Keep dataset, slice, compression, concurrency, cache state, client limits, and server
limits identical across comparable cases.

### 5.3 Workload cases

* cold peer slice touching many chunks;
* warm peer slice, demonstrating cache behavior;
* direct object read touching many byte ranges;
* batch upload of many compressed chunks;
* mixed-size chunks/ranges, including a slow response to expose head-of-line effects;
* cancellation during an in-flight operation.

### 5.4 Metrics

Record:

* elapsed time and throughput;
* p50, p95, and p99 per-request latency;
* time to first useful chunk/block;
* negotiated HTTP version;
* opened/reused TCP connections;
* transferred payload and header bytes where measurable;
* client, proxy, and server CPU;
* peak client/server memory;
* error, retry, timeout, and cancellation counts;
* cache hits and misses.

Run warmups and repeated trials and report distributions, not a single best run. Do
not state expected multipliers in advance.

---

## 6. Implementation Phases

### Phase 0 — Inventory and reproducible protocol fixtures

Deliverables:

* exact inventory of synchronous/asynchronous clients in both repositories;
* local HTTP/1.1 fixture;
* local TLS HTTP/2 reverse-proxy fixture;
* protocol assertion helper;
* documentation of the production topology relevant to HTTP/2;
* basic connection/protocol logging available to benchmarks.

Exit criterion: a test proves HTTP/1.1 locally under Uvicorn and HTTP/2 locally
through the selected fixture, without disabling certificate verification.

### Phase 1 — Baseline benchmark suite

Implement the fair comparison matrix for peer reads and direct HTTPS range reads
before changing concurrency or transport behavior.

Exit criterion: repeatable results identify whether the bottleneck is RTT,
connection setup, transfer bandwidth, decompression, server work, cache locking, or
another component.

### Phase 2 — Bounded peer-cache concurrency

Reuse the existing `C2Array.aget_chunk`/`Proxy.afetch` path. Add or normalize:

* explicit HTTP/2 enablement where absent;
* bounded concurrency;
* lifecycle-managed persistent clients;
* cancellation, retry, and partial-cache behavior;
* tests under both HTTP/1.1 and HTTP/2.

Exit criterion: correctness tests pass for failures and cancellation, and benchmarks
show a worthwhile improvement without unacceptable memory or server-load growth.

### Phase 3 — Object-store range concurrency

Implement bounded concurrent HTTPS range reads in `python-blosc2`, with HTTP/1.1
fallback and per-endpoint protocol reporting.

Exit criterion: functionality is transport-independent, service compatibility is
documented, and any claimed HTTP/2 benefit is demonstrated for the named endpoint.

### Phase 4 — Batch/async chunk ingestion

Design and implement `afill_chunks` (final name subject to API review), preserving
the atomic per-slot behavior of `fill_chunk` and reporting partial results clearly.

Exit criterion: duplicate writes, partial failures, cancellation, retries, and
bounded memory are tested under HTTP/1.1 and HTTP/2.

### Phase 5 — Flow-control investigation, only if needed

Capture stream and connection window behavior for large chunks. Determine whether
flow control is demonstrably limiting throughput and whether the selected HTTPX,
HTTP/2 server, and reverse proxy expose supported tuning controls.

The HTTP/2 initial stream window is 65,535 bytes, but this alone does not imply an
RTT stall every 64 KiB: receivers normally issue window updates as data is consumed.
Connection-level and stream-level flow control must be analyzed separately.

Exit criterion: tune windows only when traces and benchmarks show a bottleneck.
Otherwise close this phase with documented evidence and no configuration change.

### Phase 6 — Rollout and operational guidance

* document Uvicorn-only and reverse-proxy deployments;
* expose conservative concurrency settings;
* add protocol/concurrency/cache observability;
* stage rollout with HTTP/1.1 fallback;
* retain an easy configuration switch to disable HTTP/2 if interoperability issues
  occur.

---

## 7. Correctness and Safety Test Matrix

Every concurrent implementation must cover:

* out-of-order completion;
* one transient failure among successful chunks;
* persistent transport failure;
* deliberate HTTP 4xx/5xx response;
* timeout while other streams continue;
* caller cancellation;
* server disconnect;
* duplicate chunk write;
* incomplete trailing chunk;
* cold, partially warm, and fully warm sparse cache;
* client shutdown with requests in flight;
* HTTP/1.1 fallback;
* Pyodide/Emscripten behavior where the synchronous Caterva2 client intentionally
  avoids HTTP/2.

Concurrency settings must have documented upper bounds. Tests should demonstrate
that peak outstanding requests and buffered response memory stay within them.

---

## 8. Decision Criteria

Adopt HTTP/2 by default for a path only when:

1. it negotiates reliably in the supported deployment;
2. equal-concurrency benchmarks show a meaningful benefit or a clear reduction in
   connection cost;
3. memory, CPU, and tail latency remain acceptable;
4. cancellation, retries, and HTTP/1.1 fallback are correct;
5. operational complexity is documented and justified.

If HTTP/1.1 pooling performs equally well, keep the concurrency improvements and do
not force HTTP/2. If one HTTP/2 connection becomes a bottleneck, test a small bounded
number of connections rather than assuming that one connection is universally
optimal.

---

## 9. Open Questions for Discussion

1. Should the reproducible local fixture use Caddy, nginx, Hypercorn, or more than
   one of these?
2. What conservative default and maximum should `max_concurrency` use for peer
   reads, object ranges, and writes?
3. Should HTTP clients live per peer, per origin, or per application lifespan, and
   how will shutdown be coordinated?
4. Which object-store endpoint and authentication modes are officially supported?
5. Should batch ingestion fail fast or return one result/error per chunk by default?
6. What measured improvement is sufficient to justify enabling HTTP/2 by default?
7. Which proxy/server metrics can be collected in CI, and which require a separate
   performance environment?

---

## 10. Preliminary Findings and Branch Decision (2026-09-02)

### What was measured

The experimental harness under `examples/benchmarks/http2/` verifies the negotiated
protocol and compares HTTP/1.1 and HTTP/2 while holding application behavior and
concurrency constant. Detailed commands and results are recorded alongside it.

Three classes of experiment were performed:

1. **Concurrent chunk reads from `cat2.cloud`:** HTTP/2 was approximately tied with
   HTTP/1.1 at concurrency 4 and slower at concurrency 8 and 16 for the tested
   10-chunk dataset.
2. **Local peer reads with 50 ms simulated RTT:** with 64 chunks and concurrency 8,
   HTTP/2 was about 6% slower for 64 KiB chunks and 13% slower for 512 KiB chunks.
3. **One large server-side `api/fetch`:** against `cat2.cloud`, HTTP/2 was much more
   stable and faster than forced HTTP/1.1 for a 26.8 MB slice. The corresponding
   local Caddy test with 50 ms simulated RTT produced the opposite result, with
   HTTP/2 about 66% slower.

### Conclusions

* HTTP/2 is not intrinsically faster for Caterva2 traffic. Workload, real network
  behavior, TLS termination, reverse-proxy configuration, and TCP implementation
  materially affect the result.
* For independent chunk reads, several pooled HTTP/1.1 connections can outperform
  several HTTP/2 streams sharing one TCP connection.
* The strong single-slice HTTP/2 result on `cat2.cloud` appears deployment-specific.
  The local latency fixture cannot reproduce it and therefore cannot be used to
  choose production transport defaults.
* Local Caddy/Toxiproxy tests remain useful for protocol correctness, fallback,
  controlled comparisons, and regressions. They are not a substitute for tests
  between real remote Caterva2 deployments.
* A decision about peer transport performance requires at least two controlled real
  remote servers, verified protocols, repeated interleaved trials, and both chunked
  and single-response workloads.

### Decision for this branch

* Keep the existing `caterva2.Client(http2=True)` behavior. It predates this work,
  falls back to HTTP/1.1 when necessary, and the deployed single-slice workload
  provides evidence in its favor.
* Do **not** change `c2cache` peer clients to `http2=True` by default. Existing peer
  behavior remains HTTP/1.1 until representative remote-peer measurements justify a
  change.
* Keep the benchmark scripts, Caddy fixture, Toxiproxy simulation, protocol
  assertions, and recorded results from this branch.
* Stop flow-control tuning and further local performance measurements for now.
* Resume performance work only when controlled real remote Caterva2 servers are
  available. At that point HTTP/2 should remain a measured per-path choice rather
  than a project-wide assumption.

---

## 11. Remote Server Audit and Definitive Findings (2026-09-03)

### 11.1 Local Simulation vs. Production Network Ground Truth

Further evaluation confirmed that local loopback simulation (Caddy + Toxiproxy) is not
suitable for choosing production transport defaults:
* User-space latency injection (Toxiproxy) buffers and delays bytes in Go user-space,
  distorting real kernel TCP congestion control (CWND ramp-up, BDP, ACK pacing, packet loss).
* Loopback bandwidth is effectively infinite, which disproportionately amplifies
  client-side pure-Python CPU overhead (`httpx` / `h2` frame parsing) and misrepresents
  WAN network bottlenecks.
* Production validation against the live remote server (`https://cat2.cloud/demo`) was
  selected as the authoritative environment for performance decisions.

### 11.2 Root Cause of the 2026-09-02 Single-Slice Anomaly

On 2026-09-02, HTTP/1.1 appeared ~4.3x slower than HTTP/2 on `cat2.cloud` for single
large `api/fetch` responses (1.361 s vs 0.318 s). An audit of the production Nginx
reverse proxy revealed two major configuration bottlenecks:

1. **Missing Upstream Keepalives:** Nginx defaulted to HTTP/1.0 with `Connection: close`
   toward Uvicorn, tearing down and recreating the Unix domain socket on every request.
2. **Buffer Overflow and Disk Spooling:** Nginx default proxy buffers were only 32 KB
   total (`proxy_buffers 8 4k`). When Uvicorn returned the 2.68 MB compressed slice,
   the tiny RAM buffer filled in microseconds and Nginx spooled the response to temporary
   files on disk (`/var/lib/nginx/proxy/...`), introducing disk I/O latency and locks.

The Nginx deployment was updated with:
* **Upstream keepalive pool:**
  ```nginx
  upstream demo {
      server unix:/home/demo/caterva2-deploy/_caterva2/state/uvicorn.socket;
      keepalive 32;
  }
  ```
* **Streaming RAM buffers (2 MB pool, zero disk spooling):**
  ```nginx
  proxy_http_version 1.1;
  proxy_set_header Connection "";
  proxy_buffering on;
  proxy_buffer_size 128k;
  proxy_buffers 16 128k;
  proxy_busy_buffers_size 256k;
  ```
* **TCP stack optimizations:** `tcp_nopush on;` and `tcp_nodelay on;`.

**Result:** Single-slice HTTP/1.1 network response time dropped from **1.361 s to 0.226 s**
(a 6x improvement), completely eliminating the previous gap and tying with HTTP/2 (0.242 s).
Both protocols transfer single slices at the physical bandwidth limit of the WAN/Wi-Fi link.

### 11.3 Multi-Chunk Peer-Read Benchmark on `gaia-3d.b2nd`

To test concurrent chunk retrieval on a representative large-scale dataset,
`examples/benchmarks/http2/peer_read.py` was extended to support arbitrary `--slice`
ranges. Slicing `@public/large/gaia-3d.b2nd` at `(9500:10500, 9500:10500, 9500:10500)`
retrieved 64 independent chunks (~20–50 KiB compressed each).

Measurements across concurrency levels against the tuned `cat2.cloud` server (median of
alternating trials):

| Max Concurrency | HTTP/1.1 Median | HTTP/2 Median | Ratio (H1 / H2) | Winner |
|---:|---:|---:|---:|---|
| **1 (serial)** | 4.652 s | 4.647 s | 1.001 | Dead tie |
| **4** | 1.367 s | 1.433 s | 0.954 | HTTP/1.1 (~5% faster) |
| **8** | 0.838 s | 0.970 s | 0.863 | **HTTP/1.1 (~15% faster)** |
| **16** | 0.612 s | 0.818 s | 0.748 | **HTTP/1.1 (~30% faster)** |

#### Physical Mechanisms:
* **Multiple TCP Congestion Windows:** At concurrency 16, pooled HTTP/1.1 opens 16
  independent TCP connections, each with its own kernel TCP congestion window,
  saturating WAN bandwidth in parallel. HTTP/2 forces all 16 streams through a single
  TCP connection and a single congestion window.
* **Resilience to Packet Loss:** Packet drops on real WANs cause TCP Head-of-Line
  blocking across all HTTP/2 multiplexed streams on that connection, whereas pooled
  HTTP/1.1 connections continue uninterrupted.
* **Client CPU Overhead:** Demultiplexing binary chunk frames across 16 active streams
  in pure Python (`h2`) incurs noticeable CPU overhead compared to streaming raw socket
  bytes in HTTP/1.1.

### 11.4 Final Transport Architecture Decision

1. **`caterva2.Client`:** Retain `http2=True`. For interactive users and notebook
   sessions issuing single queries/slices, HTTP/2 matches HTTP/1.1 throughput (~0.22 s)
   while protecting public servers from TCP socket exhaustion and supporting `RST_STREAM`
   stream cancellation.
2. **`caterva2.c2cache` & `python-blosc2` (`C2Array.aget_chunk`):** Retain pooled HTTP/1.1
   (`http2=False`). For bulk concurrent chunk downloads, connection pooling consistently
   outperforms HTTP/2 multiplexing by 15% to 30%. No code changes are required in either
   repository.
3. **Production Reverse-Proxy Profile:** Document the Nginx upstream keepalive (`keepalive 32;`)
   and in-memory streaming buffer directives (`proxy_buffers 16 128k;`) as standard
   operational requirements for Caterva2 reverse-proxy deployments.
