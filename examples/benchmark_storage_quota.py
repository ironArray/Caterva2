"""Compare staged quota fills with the existing non-quota in-place path.

Run from the checkout with ``python examples/benchmark_storage_quota.py``.
This local-memory upstream microbenchmark isolates storage overhead, not HTTP.
In-place timings are a baseline, not a quota-safe alternative implementation.
"""

import pathlib
import tempfile
import time

import blosc2
import fsspec
import numpy as np

from caterva2.services import remote_proxy, storage_quota


def benchmark(root, data, chunk, staged):
    source = blosc2.asarray(data, chunks=(chunk,), blocks=(chunk // 4,))
    url = "memory://quota-benchmark.b2nd"
    fsspec.filesystem("memory").pipe_file("quota-benchmark.b2nd", source.to_cframe())
    path = root / "public" / "proxy.b2nd"
    path.parent.mkdir(parents=True)
    creator = blosc2.RemoteArray(
        url, cache_policy=blosc2.CachePolicy.DISK, cache_path=path, max_cache_bytes=None
    )
    carrier, payload = remote_proxy.inspect(path)
    proxy = remote_proxy.ServerRemoteArray(
        creator.src, (source.shape, source.dtype, source.chunks, source.blocks), carrier, payload
    )
    quota = storage_quota.StorageQuota(root, data.nbytes * 4) if staged else None
    initial_bytes = path.stat().st_size
    samples = []
    staged_bytes = 0
    for start in range(0, len(data), chunk):
        selection = slice(start, start + chunk)
        tick = time.perf_counter()
        result = proxy.quota_read(quota, selection) if staged else proxy.read(selection)
        samples.append(time.perf_counter() - tick)
        np.testing.assert_array_equal(result, data[selection])
        if staged:
            staged_bytes += path.stat().st_size
    tick = time.perf_counter()
    for start in range(0, len(data), chunk):
        selection = slice(start, start + chunk)
        if staged:
            proxy.quota_read(quota, selection)
        else:
            proxy.read(selection)
    warm_ms = (time.perf_counter() - tick) * 1000 / len(samples)
    assert path.stat().st_size > initial_bytes, "benchmark did not retain any chunks"
    return {
        "chunk_bytes": chunk,
        "staged": staged,
        "cold_ms_per_fill": round(float(np.mean(samples)) * 1000, 3),
        "warm_ms_per_read": round(warm_ms, 3),
        "final_bytes": path.stat().st_size,
        "candidate_bytes_written": staged_bytes if staged else None,
    }


if __name__ == "__main__":
    data = np.random.default_rng(1).integers(0, 256, 8 << 20, dtype="u1")
    with tempfile.TemporaryDirectory(prefix="caterva2-quota-bench-") as directory:
        for chunk in (256 << 10, 1 << 20):
            for staged in (False, True):
                root = pathlib.Path(directory) / f"{chunk}-{staged}"
                print(benchmark(root, data, chunk, staged))
