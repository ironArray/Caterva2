"""Shared RemoteStore benchmark over local range I/O, including Caterva2 admission.

Run in the blosc2 environment from the repository root:
    python examples/benchmark_remote_store.py --output /tmp/remote-store.json

The fixture maps one synthetic HTTPS host to a local file. No network latency,
TLS, or HTTP server is included. Source reads/bytes count actual file range I/O;
worker startup and fixture creation are excluded from measured phases.
"""

import argparse
import json
import multiprocessing
import os
import platform
import tempfile
import time
from pathlib import Path

import blosc2
import numpy as np
from fsspec.implementations.local import LocalFileSystem


class FixtureFS(LocalFileSystem):
    reads = 0
    nbytes = 0

    @classmethod
    def _strip_protocol(cls, path):
        return super()._strip_protocol(str(path).removeprefix("https://data.example"))

    def cat_file(self, *args, **kwargs):
        result = super().cat_file(*args, **kwargs)
        type(self).reads += 1
        type(self).nbytes += len(result)
        return result


def worker(engine, policy, source, state, barrier, output, size, rounds):
    try:
        blosc2.set_nthreads(1)
        fs = FixtureFS(skip_instance_cache=True)
        url = "https://data.example" + source
        expected = np.random.default_rng(42).integers(0, 1 << 24, size, dtype="i4")
        if engine == "caterva2":
            os.environ["CATERVA2_SECRET"] = "benchmark-fixture"
            from caterva2.services import remote_proxy, remote_store, server, storage_quota

            remote_proxy.policy = remote_proxy.Policy(enabled=True, allowed_hosts=("data.example",))
            remote_proxy._public_addresses = lambda *args: ("93.184.216.34",)
            remote_proxy._https_filesystem = lambda *args: fs
            quota = storage_quota.StorageQuota(state, 0, cache_backend="sparse")
            server.quota_coordinator = lambda: quota
            path = Path(state) / "public/store.b2z"
            adapter = remote_store.ServerRemoteStore(path, remote_store.inspect(path))
            arrays = [adapter.get("a"), adapter.get("b")]
            close = adapter.close
        else:
            if policy == "disk":
                store = blosc2.RemoteStore.with_sparse_cache(
                    url, state, _filesystem=fs, max_cache_bytes=64 << 20
                )
            else:
                store = blosc2.RemoteStore(url, cache_policy=blosc2.CachePolicy.NONE, _filesystem=fs)
            arrays = [store["a"], store["b"]]

            def close():
                for array in arrays:
                    array.close()
                store.close()

        phases = []
        for _ in range(2):
            barrier.wait(timeout=60)
            FixtureFS.reads = FixtureFS.nbytes = 0
            start = time.perf_counter()
            for _ in range(rounds):
                for i, array in enumerate(arrays):
                    np.testing.assert_array_equal(array[:], expected + i)
            phases.append(
                {
                    "seconds": time.perf_counter() - start,
                    "source_reads": FixtureFS.reads,
                    "source_bytes": FixtureFS.nbytes,
                }
            )
        close()
        output.put(phases)
    except BaseException as exc:
        output.put({"error": repr(exc)})


def benchmark(size=250_000, rounds=3):
    ctx = multiprocessing.get_context("spawn")
    results = []
    with tempfile.TemporaryDirectory(prefix="caterva2-store-bench-") as folder:
        root = Path(folder).resolve()
        source = root / "source.b2z"
        data = np.random.default_rng(42).integers(0, 1 << 24, size, dtype="i4")
        with blosc2.TreeStore(source, mode="w", threshold=0) as tree:
            for i, key in enumerate(("a", "b")):
                tree[key] = blosc2.asarray(data + i, chunks=(25000,), blocks=(5000,))
        for engine in ("upstream", "caterva2"):
            for workers in (1, 4):
                for policy in ("none", "disk"):
                    state = root / f"{engine}-{workers}-{policy}"
                    if engine == "caterva2":
                        (state / "public").mkdir(parents=True)
                        cache_policy = (
                            blosc2.CachePolicy.DISK if policy == "disk" else blosc2.CachePolicy.NONE
                        )
                        options = {"cache_dir": root / f"creator-{workers}"} if policy == "disk" else {}
                        with blosc2.RemoteStore(
                            "https://data.example" + str(source),
                            cache_policy=cache_policy,
                            _filesystem=FixtureFS(skip_instance_cache=True),
                            **options,
                        ) as store:
                            store.save(state / "public/store.b2z", include_cache=False)
                    barrier, output = ctx.Barrier(workers), ctx.Queue()
                    processes = [
                        ctx.Process(
                            target=worker,
                            args=(engine, policy, str(source), str(state), barrier, output, size, rounds),
                        )
                        for _ in range(workers)
                    ]
                    for process in processes:
                        process.start()
                    try:
                        measurements = [output.get(timeout=120) for _ in processes]
                        if any(isinstance(result, dict) for result in measurements):
                            raise RuntimeError(measurements)
                        for process in processes:
                            process.join(timeout=10)
                            if process.exitcode != 0:
                                raise RuntimeError(f"worker exit: {process.exitcode}")
                    finally:
                        for process in processes:
                            if process.is_alive():
                                process.terminate()
                                process.join()
                        output.close()
                    for phase in range(2):
                        seconds = max(result[phase]["seconds"] for result in measurements)
                        results.append(
                            {
                                "engine": engine,
                                "workers": workers,
                                "policy": policy,
                                "phase": "cold" if phase == 0 else "warm",
                                "seconds": seconds,
                                "MiB_per_second": workers * rounds * 2 * data.nbytes / seconds / 2**20,
                                "source_reads": sum(
                                    result[phase]["source_reads"] for result in measurements
                                ),
                                "source_bytes": sum(
                                    result[phase]["source_bytes"] for result in measurements
                                ),
                            }
                        )
    return {
        "platform": platform.platform(),
        "blosc2": blosc2.__version__,
        "elements_per_leaf": size,
        "rounds": rounds,
        "results": results,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--size", type=int, default=250_000)
    parser.add_argument("--rounds", type=int, default=3)
    args = parser.parse_args()
    result = json.dumps(benchmark(args.size, args.rounds), indent=2) + "\n"
    if args.output:
        args.output.write_text(result)
    print(result, end="")
