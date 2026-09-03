"""Compare one large api/fetch response over persistent HTTP/1.1 and HTTP/2."""

import argparse
import statistics
import time

import blosc2
import httpx


def percentile(samples: list[float], fraction: float) -> float:
    ordered = sorted(samples)
    return ordered[min(len(ordered) - 1, int(fraction * len(ordered)))]


def fetch_once(
    client: httpx.Client, url: str, slice_: str, expected: str
) -> tuple[float, float, float | None, int | None]:
    started = time.perf_counter()
    response = client.get(url, params={"slice_": slice_})
    network = time.perf_counter() - started
    response.raise_for_status()
    if response.http_version != expected:
        raise RuntimeError(f"negotiated {response.http_version}, expected {expected}")

    started = time.perf_counter()
    array = blosc2.ndarray_from_cframe(response.content)
    parse = time.perf_counter() - started

    try:
        started = time.perf_counter()
        numpy_array = array[:]
        materialize = time.perf_counter() - started
    except RuntimeError:
        # Network and cframe timings remain useful when the local environment
        # lacks the codec needed to materialize this particular dataset.
        return network, parse, None, None
    return network, parse, materialize, numpy_array.nbytes


def report(label: str, samples: list[tuple[float, float, float | None, int | None]]) -> None:
    print(f"{label} trials={len(samples)} numpy_bytes={samples[0][3]}")
    for index, metric in enumerate(("network", "parse", "numpy")):
        values = [sample[index] for sample in samples if sample[index] is not None]
        if not values:
            print(f"  {metric}: unavailable (local codec could not materialize the cframe)")
            continue
        print(
            f"  {metric}: median={statistics.median(values):.6f}s "
            f"p95={percentile(values, 0.95):.6f}s min={min(values):.6f}s max={max(values):.6f}s"
        )
    totals = [sample[0] + sample[1] + sample[2] for sample in samples if sample[2] is not None]
    if totals:
        print(
            f"  end_to_end: median={statistics.median(totals):.6f}s "
            f"p95={percentile(totals, 0.95):.6f}s min={min(totals):.6f}s max={max(totals):.6f}s"
        )


def main(args: argparse.Namespace) -> None:
    url = f"{args.urlbase.rstrip('/')}/api/fetch/{args.path}"
    samples = {"http1": [], "http2": []}
    cases = {
        "http1": (httpx.Client(http1=True, http2=False, timeout=args.timeout), "HTTP/1.1"),
        "http2": (httpx.Client(http1=True, http2=True, timeout=args.timeout), "HTTP/2"),
    }
    try:
        # Establish both TLS connections and populate server/OS caches outside the
        # samples. The response body is consumed because Client.get buffers it.
        for label, (client, expected) in cases.items():
            warm = client.get(url, params={"slice_": args.slice})
            warm.raise_for_status()
            if warm.http_version != expected:
                raise RuntimeError(f"{label} negotiated {warm.http_version}, expected {expected}")

        for trial in range(args.repeat):
            order = ("http1", "http2") if trial % 2 == 0 else ("http2", "http1")
            for label in order:
                client, expected = cases[label]
                sample = fetch_once(client, url, args.slice, expected)
                samples[label].append(sample)
                print(
                    f"{label} trial={trial + 1} network={sample[0]:.6f}s "
                    f"parse={sample[1]:.6f}s "
                    f"numpy={f'{sample[2]:.6f}s' if sample[2] is not None else 'unavailable'}"
                )
                if args.pause:
                    time.sleep(args.pause)
    finally:
        for client, _ in cases.values():
            client.close()

    report("http1", samples["http1"])
    report("http2", samples["http2"])
    h1_network = statistics.median(sample[0] for sample in samples["http1"])
    h2_network = statistics.median(sample[0] for sample in samples["http2"])
    print(f"median_network_ratio_http1_over_http2={h1_network / h2_network:.3f}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--urlbase", default="https://cat2.cloud/demo")
    parser.add_argument("--path", default="@public/examples/lung-jpeg2000_10x.b2nd")
    parser.add_argument("--slice", default="5:9")
    parser.add_argument("--repeat", type=int, default=20)
    parser.add_argument("--timeout", type=float, default=30)
    parser.add_argument("--pause", type=float, default=0.25, help="seconds between measured requests")
    args = parser.parse_args()
    if args.repeat < 1 or args.timeout <= 0 or args.pause < 0:
        parser.error("--repeat and --timeout must be positive; --pause cannot be negative")
    return args


if __name__ == "__main__":
    main(parse_args())
