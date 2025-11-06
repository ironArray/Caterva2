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
