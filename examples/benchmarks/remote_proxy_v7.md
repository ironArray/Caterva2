# RemoteProxy v5 versus sparse v7

Measured through the real `/api/chunk` and `/api/fetch` ASGI routes, using the
rebuilt Python-Blosc2 extension after `a77ac97b` (C-Blosc2 `a54e259`). V5 is the
unmodified Caterva2 checkout at `a1de98c`; v7 is the sparse-default implementation
in the current checkout. Both use the same Python-Blosc2 implementation and C library.
The source
transport is an injected deterministic in-memory range source, so these measure
server overhead rather than external HTTPS latency. Source policy resolution,
array/chunk serialization, locking, SQLite admission, filesystem writes, and fsync
are exercised. Every response is compared with the original random uint8 data.

Runs were sequential, with three fresh-state samples for every backend/workload.
The table gives median total seconds; speedup is v5 time divided by v7 time.
Values below 1 mean sparse is slower. Raw samples, request latency distributions,
account usage, and private entry counts are in `remote_proxy_v7_results/`.

- Small: 8 MiB source, 32 chunks of 256 KiB, eight blocks per chunk. Churn retains
  approximately 1 MiB (four chunks).
- Large: 64 MiB source, 64 chunks of 1 MiB, eight blocks per chunk. Churn retains
  approximately 32 MiB (32 chunks).
- Cold fills every chunk once. Warm measures three full passes after prefill.
  Churn measures three full passes with the stated limit. Partial fetches one
  block per chunk in round-robin order, for eight passes. Non-churn cases use an
  unlimited per-proxy payload cap; customer quota is 1 GiB throughout.

| Case | Workload | v5 seconds | sparse v7 seconds | Speedup |
| --- | --- | ---: | ---: | ---: |
| small | cold | 0.354 | 0.378 | 0.94x |
| small | warm | 0.916 | 0.379 | 2.42x |
| small | churn | 0.743 | 1.302 | 0.57x |
| small | partial | 2.567 | 1.296 | 1.98x |
| large | cold | 2.409 | 0.911 | 2.65x |
| large | warm | 9.020 | 1.023 | 8.82x |
| large | churn | 5.902 | 3.107 | 1.90x |
| large | partial | 18.827 | 2.799 | 6.73x |

At the server boundary, sparse wins warm hits even for small caches because v5
still snapshots and serializes the whole carrier on its read path. For a 1 MiB
resident cache, sparse's per-operation locking, metadata, fsync, and accounting
costs outweigh the saved copying during churn. At 32 MiB resident size, avoiding
whole-carrier work wins all four workloads. These results support sparse as the
runtime default while retaining the contiguous path for explicit comparison and
rollback testing.

This is a benchmark baseline, not the completion of every v7 acceptance
criterion. It uses a full-generation measurement/fsync fallback after mutation;
source authorization/transport is retained but external DNS/TLS/network time is
not measured. Multiple workers and process death have correctness tests, not yet
throughput benchmarks. Millions of logical chunks, long-running quota-pressure
convergence, peak RSS, and power-loss durability remain separate acceptance work.

Reproduce with the `blosc2` conda interpreter and `PYTHONPATH` selecting the
appropriate checkout; run each command sequentially. Use `--backend contiguous`
with the original v5 checkout, and `--backend sparse` with this implementation.
The backend switch belongs to the benchmark harness; Caterva2 runtime configuration
has no public backend selector and defaults to sparse.

```sh
python examples/benchmarks/remote_proxy_v7.py --backend sparse --repeats 3 --output small.json
python examples/benchmarks/remote_proxy_v7.py --backend sparse --chunk-size 1048576 --nchunks 64 --cache-chunks 32 --repeats 3 --output large.json
```
