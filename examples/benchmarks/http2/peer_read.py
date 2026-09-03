"""Compare cold peer reads over pooled HTTP/1.1 and multiplexed HTTP/2."""

import argparse
import asyncio
import pathlib
import statistics
import tempfile
import time

import blosc2
import httpx

from caterva2.c2cache.remote import RemoteSource


class BenchmarkRemoteSource(RemoteSource):
    """RemoteSource with a selectable transport, confined to this benchmark."""

    def __init__(self, path: str, urlbase: str, *, http2: bool):
        super().__init__(path, urlbase=urlbase, use_chunk_api=True)
        self._benchmark_http2 = http2

    async def aget_chunk(self, nchunk: int) -> bytes:
        if self._aclient is None:
            self._aclient = httpx.AsyncClient(http2=self._benchmark_http2, timeout=5)
        return await super().aget_chunk(nchunk)


async def assert_protocol(urlbase: str, expected: str, *, http2: bool) -> None:
    """Fail rather than silently benchmarking HTTPX's HTTP/1.1 fallback."""
    async with httpx.AsyncClient(http2=http2, follow_redirects=True, timeout=10) as client:
        response = await client.get(f"{urlbase.rstrip('/')}/api/roots")
        response.raise_for_status()
    if response.http_version != expected:
        raise RuntimeError(
            f"{urlbase} negotiated {response.http_version}, expected {expected}; "
            "benchmark result would be invalid"
        )


def parse_slice_tuple(slice_str: str | None) -> tuple[slice, ...] | None:
    if not slice_str:
        return None
    parts = []
    for s in slice_str.split(","):
        s = s.strip()
        if ":" in s:
            start, stop = (int(x.strip()) if x.strip() else None for x in s.split(":", 1))
            parts.append(slice(start, stop))
        else:
            v = int(s)
            parts.append(slice(v, v + 1))
    return tuple(parts)


async def cold_read(
    urlbase: str,
    path: str,
    concurrency: int,
    cache: pathlib.Path,
    *,
    http2: bool,
    slice_: tuple[slice, ...] | None = None,
) -> float:
    source = BenchmarkRemoteSource(path, urlbase=urlbase, http2=http2)
    proxy = blosc2.Proxy(source, urlpath=str(cache), mode="w")
    try:
        started = time.perf_counter()
        await proxy.afetch(slice_, max_concurrency=concurrency)
        return time.perf_counter() - started
    finally:
        # Proxy does not own the remote source's HTTP client.
        await source.aclose()


def report(label: str, samples: list[float]) -> None:
    ordered = sorted(samples)
    p95_index = min(len(ordered) - 1, int(0.95 * len(ordered)))
    print(
        f"{label} n={len(samples)} median={statistics.median(samples):.6f}s "
        f"p95={ordered[p95_index]:.6f}s min={ordered[0]:.6f}s max={ordered[-1]:.6f}s"
    )


async def main(args: argparse.Namespace) -> None:
    await assert_protocol(args.http1_url, "HTTP/1.1", http2=False)
    await assert_protocol(args.http2_url, "HTTP/2", http2=True)

    # Alternate order to reduce systematic server/OS-cache and network drift. Every
    # operation still gets a new sparse cache and HTTP client.
    samples = {"http1": [], "http2": []}
    cases = {
        "http1": (args.http1_url, False),
        "http2": (args.http2_url, True),
    }
    slice_tuple = parse_slice_tuple(args.slice)
    with tempfile.TemporaryDirectory(prefix="caterva2-http-benchmark-") as tmp:
        tmpdir = pathlib.Path(tmp)
        for trial in range(args.repeat):
            order = ("http1", "http2") if trial % 2 == 0 else ("http2", "http1")
            for label in order:
                urlbase, use_http2 = cases[label]
                sample = await cold_read(
                    urlbase,
                    args.path,
                    args.concurrency,
                    tmpdir / f"{label}-trial-{trial}.b2nd",
                    http2=use_http2,
                    slice_=slice_tuple,
                )
                samples[label].append(sample)
                print(f"{label} trial={trial + 1} seconds={sample:.6f}")
    http1, http2 = samples["http1"], samples["http2"]
    report("http1", http1)
    report("http2", http2)
    print(f"median_ratio_http1_over_http2={statistics.median(http1) / statistics.median(http2):.3f}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--http1-url", required=True)
    parser.add_argument("--http2-url", required=True)
    parser.add_argument("--path", required=True, help="remote dataset path, e.g. @public/example.b2nd")
    parser.add_argument(
        "--slice", default=None, help="optional slice string, e.g. '9500:10500, 9500:10500, 9500:10500'"
    )
    parser.add_argument("--concurrency", type=int, default=4)
    parser.add_argument("--repeat", type=int, default=5)
    args = parser.parse_args()
    if args.concurrency < 1 or args.repeat < 1:
        parser.error("--concurrency and --repeat must be positive")
    return args


if __name__ == "__main__":
    asyncio.run(main(parse_args()))
