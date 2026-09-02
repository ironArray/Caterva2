# Local HTTP/2 peer-read fixture

This fixture puts Caddy in front of an ordinary Uvicorn Caterva2 server:

```text
benchmark --HTTPS/HTTP2--> Caddy --HTTP/1.1--> Uvicorn
```

It deliberately keeps direct Uvicorn access available as the HTTP/1.1 control.
Caddy is optional and is not a Caterva2 dependency.

## Start the fixture

Start a Caterva2 server on `127.0.0.1:8000`, with a multi-chunk dataset in its
`@public` root. Then, from the repository root, run:

```console
caddy run --config examples/benchmarks/http2/Caddyfile
```

Caddy's local CA must be trusted by HTTPX. `caddy trust` installs it in the local
trust store on supported systems. Alternatively, point `SSL_CERT_FILE` at the
exported Caddy root certificate. Do not disable certificate verification: doing
so would make this fixture less representative and could hide configuration
mistakes.

Check both paths before benchmarking:

```console
curl -sS -o /dev/null -w '%{http_version}\n' \
  http://127.0.0.1:8000/api/roots
curl -sS -o /dev/null -w '%{http_version}\n' \
  https://localhost:8443/api/roots
```

The expected outputs are `1.1` and `2`, respectively.

## Run the benchmark

Both URLs must address the same Caterva2 server and dataset:

```console
python examples/benchmarks/http2/peer_read.py \
  --http1-url http://127.0.0.1:8000 \
  --http2-url https://localhost:8443 \
  --path @public/example.b2nd \
  --concurrency 4 \
  --repeat 5
```

The script verifies the negotiated protocol before collecting timings. Each trial
uses a new sparse cache, so the timed operation is a cold peer read. HTTP/1.1 and
HTTP/2 use the same `RemoteSource` and `Proxy.afetch` implementation and the same
concurrency.

Run several times and compare the distributions. Loopback is useful for correctness
and protocol verification, but meaningful latency benefits require a controlled WAN
test or a genuinely remote peer.

## Simulated latency

With Caddy and Toxiproxy installed, the self-contained benchmark creates a temporary
64-chunk dataset and applies half the requested latency in each TCP direction:

```console
python examples/benchmarks/http2/simulated_latency.py \
  --rtt-ms 50 --concurrency 8 --repeat 5
```

Both protocols traverse the same Toxiproxy listener, TLS connection, Caddy instance,
and Uvicorn process. The only difference is whether HTTPX offers HTTP/2 during ALPN.
Temporary servers, proxies, certificates, data, and caches are removed after the run.

The same fixture can reproduce the single-large-response workload with approximately
the same 26.8 MB materialized slice as `get-slice.py`:

```console
python examples/benchmarks/http2/simulated_latency.py \
  --workload single-fetch --rtt-ms 50 \
  --chunks 10 --items-per-chunk 838860 --slice 5:9 \
  --repeat 12 --pause 0.25
```

## Single server-side slice

To compare the one-response workload in `examples/get-slice.py`, while separating
network transfer from cframe parsing and NumPy materialization:

```console
python examples/benchmarks/http2/single_fetch.py --repeat 20
```

Both persistent clients connect to the same endpoint. One forces HTTP/1.1 and the
other offers HTTP/2; every response is checked against the expected protocol.
