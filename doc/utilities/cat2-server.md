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
the requested client limit for download. With a `DISK` cache, fetched compressed
chunks are retained inside the carrier up to the proxy's `max_cache_bytes` (or
unbounded when `max_cache_bytes` is `None`, still subject to customer quota if
configured). Caterva2 can inspect and report its stored shape, dtype, chunk, block, and proxy
metadata without contacting the source. Outbound resolution is disabled by
default.

The initial opt-in backend supports public, credential-free HTTPS sources:

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
while automatic carrier growth is also charged to the virtual server's existing
shared `quota`. If no quota remains, reads still succeed but misses are not
retained.

Public S3 objects are supported through credential-free HTTPS object URLs.
Native `s3://` resolution, private-source credentials, and remote references
embedded inside persisted expressions are not enabled.

Physical downloads include valid warm proxy chunks by default. Clients can pass
`include_cache=false` to download a cold carrier without mutating the hosted
proxy. Logical `api/fetch` requests continue to return array data.
