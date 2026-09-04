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

A persisted `blosc2.RemoteProxy` is a small B2ND carrier that asks Caterva2 to
read another dataset. Caterva2 can inspect and report the carrier's stored
shape, dtype, chunk, and block metadata without contacting that source.
Outbound resolution is disabled by default.

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

The limits bound each upstream request's time, source rank, logical
uncompressed size, chunk count, and concurrent range fetches. Set them for the
capacity of the installation; they are not inferred from untrusted carrier
metadata.

S3, private-source credential selection, and remote references embedded inside
persisted expressions are not enabled yet. Server credentials must eventually
be selected from an administrator-controlled destination mapping and must never
be accepted from a carrier.
