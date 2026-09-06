"""Measure real ASGI fetch/chunk routes against a deterministic local range source.

Run with PYTHONPATH selecting the desired Caterva2 checkout and the blosc2 conda
interpreter. The source transport is injected; endpoint resolution, serialization,
SQLite admission, locks, cache writes and fsync are real. No network latency is
simulated. Each sample uses a fresh state directory and validates all results.
"""

import argparse
import asyncio
import json
import os
import pathlib
import statistics
import tempfile
import time
import types
import uuid

os.environ.setdefault("CATERVA2_SECRET", "local-benchmark-only")
import blosc2
import fsspec
import httpx
import numpy as np
from blosc2.b2objects import make_b2object_carrier, write_b2object_payload

from caterva2.services import remote_proxy, server


async def sample(backend, workload, chunk_size, nchunks, cache_chunks):
    data = np.random.default_rng(7).integers(0, 256, chunk_size * nchunks, dtype="u1")
    array = blosc2.asarray(
        data,
        chunks=(chunk_size,),
        blocks=(chunk_size // 8,),
        cparams={"nthreads": 1},
        dparams={"nthreads": 1},
    )
    url = "https://data.example/benchmark.b2nd"
    fs = fsspec.filesystem("memory")
    fs.pipe_file(url, array.to_cframe())
    fields = {"enabled": True, "allowed_hosts": ("data.example",)}
    if backend == "sparse":
        fields["cache_backend"] = backend
    remote_proxy.policy = remote_proxy.Policy(**fields)
    remote_proxy._public_addresses = lambda *args: ("93.184.216.34",)
    remote_proxy._https_filesystem = lambda *args: fs
    user = types.SimpleNamespace(id=uuid.uuid4(), is_superuser=True, is_active=True)
    server.app.dependency_overrides[server.current_active_user] = lambda: user
    server.app.dependency_overrides[server.optional_user] = lambda: user
    with tempfile.TemporaryDirectory(prefix="cat2-bench-") as directory:
        root = pathlib.Path(directory)
        server.settings.statedir = root
        server.settings.quota = 1 << 30
        server.settings.publish_root = None
        server._quota_instances = {}
        for name in ("public", "shared", "personal"):
            path = root / name
            path.mkdir()
            setattr(server.settings, name, path)
        carrier = make_b2object_carrier(
            "remote_proxy", array.shape, array.dtype, chunks=array.chunks, blocks=array.blocks
        )
        limit = cache_chunks * (chunk_size + 256) if workload == "churn" else None
        write_b2object_payload(
            carrier,
            {
                "kind": "remote_proxy",
                "version": 1,
                "source": {"kind": "fsspec", "version": 1, "urlpath": url},
                "cache_policy": "disk",
                "max_cache_bytes": limit,
            },
        )
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=server.app), base_url="http://test"
        ) as client:
            response = await client.post(
                "/api/upload/@public/proxy.b2nd", files={"file": ("proxy.b2nd", carrier.to_cframe())}
            )
            assert response.status_code == 200, response.text

            async def read(n, block=None):
                lo = n * chunk_size + (block or 0) * (chunk_size // 8)
                length = chunk_size if block is None else chunk_size // 8
                if block is None:
                    response = await client.get("/api/chunk/@public/proxy.b2nd", params={"nchunk": n})
                    assert response.status_code == 200, response.text
                    actual = np.frombuffer(blosc2.decompress(response.content), dtype="u1")
                else:
                    response = await client.get(
                        "/api/fetch/@public/proxy.b2nd", params={"slice_": f"{lo}:{lo + length}"}
                    )
                    assert response.status_code == 200, response.text
                    actual = blosc2.ndarray_from_cframe(response.content)[:]
                np.testing.assert_array_equal(actual, data[lo : lo + length])

            if workload == "warm":
                for n in range(nchunks):
                    await read(n)
            operations = [(n, None) for n in range(nchunks)]
            if workload in ("churn", "warm"):
                operations *= 3
            elif workload == "partial":
                operations = [(n, b) for b in range(8) for n in range(nchunks)]
            times = []
            start = time.perf_counter()
            for n, b in operations:
                tick = time.perf_counter()
                await read(n, b)
                times.append(time.perf_counter() - tick)
            elapsed = time.perf_counter() - start
            usage = server.quota_coordinator().usage()
            private = root / ".remote-cache"
            return {
                "seconds": elapsed,
                "requests": len(times),
                "median_ms": statistics.median(times) * 1000,
                "p95_ms": float(np.quantile(times, 0.95)) * 1000,
                "private_entries": sum(1 for _ in private.rglob("*")) if private.exists() else 0,
                "usage": usage,
            }


async def main(args):
    result = {
        "backend": args.backend,
        "chunk_size": args.chunk_size,
        "nchunks": args.nchunks,
        "cache_chunks": args.cache_chunks,
        "source": "in-memory range transport; real ASGI routes",
        "results": {},
    }
    for workload in ("cold", "warm", "churn", "partial"):
        values = [
            await sample(args.backend, workload, args.chunk_size, args.nchunks, args.cache_chunks)
            for _ in range(args.repeats)
        ]
        result["results"][workload] = {
            "median_seconds": statistics.median(v["seconds"] for v in values),
            "samples": values,
        }
    pathlib.Path(args.output).write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps({k: v["median_seconds"] for k, v in result["results"].items()}, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--backend", choices=["contiguous", "sparse"], required=True)
    parser.add_argument("--chunk-size", type=int, default=262144)
    parser.add_argument("--nchunks", type=int, default=32)
    parser.add_argument("--cache-chunks", type=int, default=4)
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--output", required=True)
    asyncio.run(main(parser.parse_args()))
